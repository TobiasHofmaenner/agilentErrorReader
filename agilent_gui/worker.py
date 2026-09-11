"""Background thread: scan for a controller, then poll it until told to stop."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from agilent_turbo.reader import ConnectionLost, ControllerReader
from agilent_turbo.scanner import DEFAULT_BAUDRATES, Scanner
from agilent_turbo.transport import SerialTransport
from agilent_turbo.windows import Family


@dataclass
class ScanOptions:
    ports: Optional[List[str]] = None            # None = all ports
    baudrates: List[int] = field(default_factory=lambda: list(DEFAULT_BAUDRATES))
    scan_rs485: bool = True
    try_letter: bool = True
    deep: bool = False
    family_override: Optional[Family] = None
    poll_interval: float = 1.0
    log_raw: bool = False


class ControllerWorker(QThread):
    log_message = Signal(str)
    progress = Signal(str)
    controller_found = Signal(object)      # DiscoveredController
    scan_finished = Signal(bool)           # True when a controller was found
    state_ready = Signal(object)           # ControllerState
    connection_lost = Signal(str)

    def __init__(self, options: ScanOptions, parent=None):
        super().__init__(parent)
        self.options = options
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        o = self.options
        try:
            scanner = Scanner(ports=o.ports, baudrates=o.baudrates, scan_rs485=o.scan_rs485,
                              try_letter=o.try_letter, deep=o.deep,
                              log=self.log_message.emit, progress=self.progress.emit,
                              is_cancelled=lambda: self._stop)
            found = scanner.scan(stop_at_first=True)
        except Exception as exc:  # noqa: BLE001 - report anything to the GUI
            self.log_message.emit(f"Scan failed: {exc.__class__.__name__}: {exc}")
            self.scan_finished.emit(False)
            return
        if self._stop or not found:
            self.scan_finished.emit(False)
            return
        ctl = found[0]
        self.controller_found.emit(ctl)
        self.scan_finished.emit(True)
        try:
            with SerialTransport(ctl.port, ctl.baudrate, log=self.log_message.emit, log_raw=o.log_raw) as t:
                reader = ControllerReader(t, ctl, family=o.family_override, log=self.log_message.emit)
                while not self._stop:
                    t0 = time.monotonic()
                    state = reader.read_state()
                    if self._stop:
                        break
                    self.state_ready.emit(state)
                    while not self._stop and time.monotonic() - t0 < o.poll_interval:
                        time.sleep(0.05)
        except ConnectionLost as exc:
            self.connection_lost.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.connection_lost.emit(f"{exc.__class__.__name__}: {exc}")
