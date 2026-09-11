"""Offscreen PySide6 smoke test: scan a simulated controller through the real GUI
worker, check the status banner / error list / readings table and the report export."""
import os
import sys

import pytest

pytestmark = pytest.mark.ui
pytest.importorskip("PySide6")

if sys.platform == "win32":
    pytest.skip("pty simulator is POSIX only", allow_module_level=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_gui_scans_simulated_controller_and_exports_report(tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from agilent_gui.main_window import MainWindow
    from agilent_turbo.simulator import SimulatedController
    from agilent_turbo.windows import Family

    sim = SimulatedController(Family.MODERN, status=5, error=0)
    path = sim.serve_pty()
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    win.port_combo.setEditText(path)
    win.baud_combo.setCurrentIndex(1)          # 9600
    win.letter_check.setChecked(False)
    win.rs485_check.setChecked(False)
    win.interval_spin.setValue(0.3)

    states = []

    def on_state(state):
        states.append(state)
        if len(states) == 2:                   # flip the simulated pump into FAIL
            sim.windows[205][1] = 6
            sim.windows[206][1] = 0b01000001
        if len(states) >= 4:
            win.stop_worker()

    win.start_scan()
    win.worker.state_ready.connect(on_state)
    win.worker.finished.connect(lambda: QTimer.singleShot(100, app.quit))
    QTimer.singleShot(60_000, app.quit)
    app.exec()
    sim.stop()

    assert len(states) >= 4
    assert win.lbl_protocol.text() == Family.MODERN.title
    assert "X350864001" in win.lbl_model.text()
    assert win.error_label.text().startswith("Error code: 65")
    errors = [win.error_list.item(i).text() for i in range(win.error_list.count())]
    assert errors == ["⚠ No connection to pump", "⚠ Short circuit"]
    assert win.table.rowCount() > 20
    assert win.table.item(0, 3).text() == "Fail"
    assert win.status_label.text() == "STOPPED"

    report = tmp_path / "report.md"
    text = win.write_report(str(report))
    assert report.exists()
    assert "**Pump status: Fail** (code 6)" in text
    assert "## Session log" in text
    win.close()
