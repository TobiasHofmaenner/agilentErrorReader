"""Main window of the Agilent turbo pump controller reader."""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Dict, Optional

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QAction, QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QToolBar, QVBoxLayout, QWidget,
)

from agilent_turbo import __version__
from agilent_turbo.reader import ControllerState
from agilent_turbo.report import build_markdown_report, default_report_name
from agilent_turbo.scanner import DEFAULT_BAUDRATES, DiscoveredController, list_serial_ports
from agilent_turbo.windows import Family, GROUP_ORDER, GROUP_TITLES

from .worker import ControllerWorker, ScanOptions

SEVERITY_COLORS = {
    "ok": "#2e7d32", "busy": "#f9a825", "warn": "#ef6c00", "fail": "#c62828",
    "off": "#616161", "unknown": "#455a64",
}
ALARM_BG = QColor("#ffcdd2")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Agilent turbo pump controller reader {__version__}")
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1100, 800)
        self.worker: Optional[ControllerWorker] = None
        self.controller: Optional[DiscoveredController] = None
        self._rows: Dict[str, int] = {}
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self.start_scan)
        self._build_ui()
        self.refresh_ports()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        tb = QToolBar("Connection")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.scan_button = QPushButton("Scan && connect")
        self.scan_button.clicked.connect(self.start_scan)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_worker)
        tb.addWidget(self.scan_button)
        tb.addWidget(self.stop_button)
        self.report_button = QPushButton("Export report")
        self.report_button.setToolTip("Write a Markdown report with connection, status, all readings and the log")
        self.report_button.setEnabled(False)
        self.report_button.clicked.connect(self.export_report)
        tb.addWidget(self.report_button)
        tb.addSeparator()

        tb.addWidget(QLabel(" Port: "))
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        self.port_combo.setMinimumWidth(220)
        self.port_combo.setToolTip("Auto = scan every serial port. You can also type a device path.")
        tb.addWidget(self.port_combo)
        refresh = QPushButton("⟳")
        refresh.setFixedWidth(28)
        refresh.setToolTip("Refresh port list")
        refresh.clicked.connect(self.refresh_ports)
        tb.addWidget(refresh)

        tb.addWidget(QLabel(" Baud: "))
        self.baud_combo = QComboBox()
        self.baud_combo.addItem("Auto", None)
        for b in DEFAULT_BAUDRATES:
            self.baud_combo.addItem(str(b), b)
        tb.addWidget(self.baud_combo)

        tb.addWidget(QLabel(" Decode as: "))
        self.family_combo = QComboBox()
        self.family_combo.addItem("Auto-detect", None)
        for f in Family:
            self.family_combo.addItem(f.title, f)
        tb.addWidget(self.family_combo)

        self.addToolBarBreak()
        tb = QToolBar("Options")
        tb.setMovable(False)
        self.addToolBar(tb)
        self.rs485_check = QCheckBox("Sweep RS485 addresses")
        self.rs485_check.setChecked(True)
        tb.addWidget(self.rs485_check)
        self.letter_check = QCheckBox("Try legacy letter protocol")
        self.letter_check.setChecked(True)
        tb.addWidget(self.letter_check)
        self.deep_check = QCheckBox("Deep scan")
        self.deep_check.setToolTip("Also sweep RS485 addresses at every baud rate (slow)")
        tb.addWidget(self.deep_check)
        self.reconnect_check = QCheckBox("Auto reconnect")
        self.reconnect_check.setChecked(True)
        tb.addWidget(self.reconnect_check)
        tb.addWidget(QLabel(" Poll every "))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.2, 60.0)
        self.interval_spin.setValue(1.0)
        self.interval_spin.setSuffix(" s")
        self.interval_spin.setDecimals(1)
        tb.addWidget(self.interval_spin)
        self.raw_check = QCheckBox("Log raw frames")
        tb.addWidget(self.raw_check)

        # menu
        file_menu = self.menuBar().addMenu("&File")
        report = QAction("Export report (Markdown)…", self)
        report.setShortcut("Ctrl+E")
        report.triggered.connect(self.export_report)
        file_menu.addAction(report)
        snap = QAction("Save state snapshot (JSON)…", self)
        snap.triggered.connect(self.save_snapshot)
        file_menu.addAction(snap)
        save_log = QAction("Save log…", self)
        save_log.triggered.connect(self.save_log)
        file_menu.addAction(save_log)
        file_menu.addSeparator()
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About / protocol notes", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

        # central layout
        splitter = QSplitter(Qt.Vertical)
        self.setCentralWidget(splitter)

        top = QWidget()
        top_layout = QHBoxLayout(top)

        conn_box = QGroupBox("Controller")
        form = QFormLayout(conn_box)
        self.lbl_port = QLabel("-")
        self.lbl_protocol = QLabel("-")
        self.lbl_protocol.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_address = QLabel("-")
        self.lbl_model = QLabel("-")
        self.lbl_firmware = QLabel("-")
        self.lbl_updated = QLabel("-")
        form.addRow("Port / baud:", self.lbl_port)
        form.addRow("Protocol:", self.lbl_protocol)
        form.addRow("Address:", self.lbl_address)
        form.addRow("Model:", self.lbl_model)
        form.addRow("Firmware:", self.lbl_firmware)
        form.addRow("Last update:", self.lbl_updated)
        top_layout.addWidget(conn_box, 1)

        status_box = QGroupBox("Pump status")
        status_layout = QVBoxLayout(status_box)
        self.status_label = QLabel("NOT CONNECTED")
        self.status_label.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setPointSize(22)
        f.setBold(True)
        self.status_label.setFont(f)
        self.status_label.setMinimumHeight(70)
        self._set_status_color("unknown")
        status_layout.addWidget(self.status_label)
        self.error_label = QLabel("Error code: -")
        status_layout.addWidget(self.error_label)
        self.error_list = QListWidget()
        self.error_list.setMaximumHeight(110)
        status_layout.addWidget(self.error_list)
        top_layout.addWidget(status_box, 1)
        splitter.addWidget(top)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Window", "Group", "Parameter", "Value", "Unit"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        splitter.addWidget(self.table)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.TypeWriter)
        self.log_view.setFont(mono)
        splitter.addWidget(self.log_view)
        splitter.setSizes([220, 380, 200])

        self.statusBar().showMessage("Idle")

    # ------------------------------------------------------------------ actions
    @Slot()
    def refresh_ports(self) -> None:
        current = self.port_combo.currentText()
        self.port_combo.clear()
        self.port_combo.addItem("Auto (scan all ports)", None)
        for p in list_serial_ports():
            self.port_combo.addItem(p.label, p.device)
        if current and current != "Auto (scan all ports)":
            idx = self.port_combo.findText(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
            else:
                self.port_combo.setEditText(current)

    def _selected_ports(self) -> Optional[list]:
        data = self.port_combo.currentData()
        text = self.port_combo.currentText().strip()
        if data:
            return [data]
        if text and not text.startswith("Auto"):
            return [text.split(" (")[0]]
        return None

    def _options(self) -> ScanOptions:
        baud = self.baud_combo.currentData()
        return ScanOptions(
            ports=self._selected_ports(),
            baudrates=[baud] if baud else list(DEFAULT_BAUDRATES),
            scan_rs485=self.rs485_check.isChecked(),
            try_letter=self.letter_check.isChecked(),
            deep=self.deep_check.isChecked(),
            family_override=self.family_combo.currentData(),
            poll_interval=self.interval_spin.value(),
            log_raw=self.raw_check.isChecked(),
        )

    @Slot()
    def start_scan(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        self._reconnect_timer.stop()
        self.controller = None
        self._rows.clear()
        self.table.setRowCount(0)
        self.error_list.clear()
        self.status_label.setText("SCANNING…")
        self._set_status_color("unknown")
        for lbl in (self.lbl_port, self.lbl_protocol, self.lbl_address, self.lbl_model, self.lbl_firmware, self.lbl_updated):
            lbl.setText("-")
        self.worker = ControllerWorker(self._options(), self)
        self.worker.log_message.connect(self.append_log)
        self.worker.progress.connect(self.statusBar().showMessage)
        self.worker.controller_found.connect(self.on_found)
        self.worker.scan_finished.connect(self.on_scan_finished)
        self.worker.state_ready.connect(self.on_state)
        self.worker.connection_lost.connect(self.on_lost)
        self.worker.finished.connect(self.on_worker_finished)
        self.scan_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.append_log("Scan started.")
        self.worker.start()

    @Slot()
    def stop_worker(self) -> None:
        self._reconnect_timer.stop()
        if self.worker:
            self.worker.request_stop()
            self.statusBar().showMessage("Stopping…")

    @Slot(object)
    def on_found(self, ctl: DiscoveredController) -> None:
        self.controller = ctl
        self.lbl_port.setText(f"{ctl.port} @ {ctl.baudrate} baud, 8N1")
        self.lbl_protocol.setText(ctl.family.title)
        self.lbl_address.setText("RS232 / 0x80" if ctl.address == 0 else f"RS485 device {ctl.address} (0x{0x80 + ctl.address:02X})")
        self.lbl_model.setText(ctl.model or "-")
        self.lbl_firmware.setText(ctl.firmware or "-")
        self.status_label.setText("CONNECTED")
        self.setWindowTitle(f"Agilent turbo controller reader {__version__} – {ctl.port}")

    @Slot(bool)
    def on_scan_finished(self, found: bool) -> None:
        if not found:
            self.status_label.setText("NO CONTROLLER FOUND")
            self._set_status_color("off")
            self.statusBar().showMessage("No controller found")

    @Slot(object)
    def on_state(self, state: ControllerState) -> None:
        self._last_state = state
        self.report_button.setEnabled(True)
        self.status_label.setText(state.status_text.upper())
        self._set_status_color(state.severity)
        if state.error_code is None:
            self.error_label.setText("Error code: -")
        elif state.family == Family.LEGACY:
            self.error_label.setText(f"Error code: {state.error_code}")
        else:
            self.error_label.setText(f"Error code: {state.error_code} (0x{state.error_code:X}, bits {state.error_code:012b})")
        self.error_list.clear()
        for e in state.error_texts:
            item = QListWidgetItem("⚠ " + e)
            item.setForeground(QColor(SEVERITY_COLORS["fail"]))
            self.error_list.addItem(item)
        for w in state.warning_texts:
            item = QListWidgetItem("⚠ Warning: " + w)
            item.setForeground(QColor(SEVERITY_COLORS["warn"]))
            self.error_list.addItem(item)
        if not state.error_texts and not state.warning_texts and state.error_code is not None:
            self.error_list.addItem("No error")
        for e in state.comm_errors:
            self.error_list.addItem("comm: " + e)
        self._update_table(state)
        self.lbl_updated.setText(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.timestamp)))
        if state.unsupported:
            self.statusBar().showMessage(f"Polling ({len(state.readings)} values; windows not supported by this controller: "
                                         + ", ".join(str(w) for w in sorted(state.unsupported)) + ")")
        else:
            self.statusBar().showMessage(f"Polling ({len(state.readings)} values)")

    def _update_table(self, state: ControllerState) -> None:
        order = {g: i for i, g in enumerate(GROUP_ORDER)}
        readings = sorted(state.readings, key=lambda r: (order.get(r.group, 99), _sort_key(r.key)))
        if [r.key for r in readings] != list(self._rows.keys()):
            self.table.setRowCount(0)
            self._rows.clear()
            for r in readings:
                row = self.table.rowCount()
                self.table.insertRow(row)
                self._rows[r.key] = row
                for col, text in enumerate((r.key, GROUP_TITLES.get(r.group, r.group), r.name, r.value, r.unit)):
                    item = QTableWidgetItem(text)
                    if col == 3:
                        item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    self.table.setItem(row, col, item)
        for r in readings:
            row = self._rows[r.key]
            item = self.table.item(row, 3)
            if item.text() != r.value:
                item.setText(r.value)
            bg = ALARM_BG if r.alarm else QColor(Qt.transparent)
            for col in range(5):
                self.table.item(row, col).setBackground(bg)

    @Slot(str)
    def on_lost(self, message: str) -> None:
        self.append_log(f"Connection lost: {message}")
        self.status_label.setText("CONNECTION LOST")
        self._set_status_color("fail")
        if self.reconnect_check.isChecked():
            self.append_log("Rescanning in 3 s…")
            self._reconnect_timer.start(3000)

    @Slot()
    def on_worker_finished(self) -> None:
        self.scan_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        if self.status_label.text() in ("SCANNING…", "CONNECTED") or self.status_label.text() not in ("NO CONTROLLER FOUND", "CONNECTION LOST"):
            if self.controller is None:
                self.status_label.setText("NOT CONNECTED")
                self._set_status_color("unknown")
            else:
                self.status_label.setText("STOPPED")
                self._set_status_color("off")
        if not self._reconnect_timer.isActive():
            self.statusBar().showMessage("Idle")

    @Slot(str)
    def append_log(self, message: str) -> None:
        self.log_view.appendPlainText(time.strftime("%H:%M:%S ") + message)

    def _set_status_color(self, severity: str) -> None:
        color = SEVERITY_COLORS.get(severity, SEVERITY_COLORS["unknown"])
        self.status_label.setStyleSheet(f"background:{color}; color:white; border-radius:6px; padding:8px;")

    # ------------------------------------------------------------------ files
    _last_state: Optional[ControllerState] = None

    @Slot()
    def save_snapshot(self) -> None:
        if not self._last_state:
            QMessageBox.information(self, "No data", "No controller state has been read yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save snapshot", "pump_state.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._last_state.to_dict(), fh, indent=1, ensure_ascii=False)
            self.append_log(f"Snapshot saved to {path}")

    @Slot()
    def export_report(self) -> None:
        if not self._last_state:
            QMessageBox.information(self, "No data", "No controller state has been read yet.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export report", default_report_name(self._last_state),
                                              "Markdown (*.md);;All files (*)")
        if path:
            self.write_report(path)

    def write_report(self, path: str) -> str:
        text = build_markdown_report(self._last_state, log_text=self.log_view.toPlainText())
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.append_log(f"Report exported to {path}")
        return text

    @Slot()
    def save_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save log", "pump_log.txt", "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.log_view.toPlainText())

    @Slot()
    def show_about(self) -> None:
        QMessageBox.information(
            self, "About",
            f"Agilent / Varian turbo pump controller reader {__version__}\n\n"
            "Scans every serial port at every baud rate until a controller answers, then polls its state.\n\n"
            "Supported: window protocol (all current Agilent TwisTorr / Turbo-V controllers, RS232 or RS485),\n"
            "legacy Varian HT/ICE window semantics, and the single-letter protocol of the Turbo-V 301-AG / V70.\n"
            "See PROTOCOLS.md for the manual comparison.")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._reconnect_timer.stop()
        if self.worker and self.worker.isRunning():
            self.worker.request_stop()
            self.worker.wait(3000)
        event.accept()


def _sort_key(key: str):
    try:
        return (0, int(key), "")
    except ValueError:
        return (1, 0, key)


def run(argv=None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Agilent turbo controller reader")
    win = MainWindow()
    win.show()
    return app.exec()
