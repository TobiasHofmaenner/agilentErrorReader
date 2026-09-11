"""Periodic state read-out of a discovered controller."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from .protocol import CODE_UNKNOWN_WINDOW, LETTER_MIN_INTERVAL, ProtocolError
from .scanner import DiscoveredController
from .transport import ResponseTimeout, SerialTransport, TransportError
from .windows import (
    Family, WindowDef, decode_error, decode_status, format_number, parse_number, status_severity, windows_for,
)

Logger = Callable[[str], None]


class ConnectionLost(Exception):
    pass


@dataclass
class Reading:
    key: str               # "205" for windows, "I.status" etc. for the letter protocol
    name: str
    raw: str
    value: str
    unit: str = ""
    group: str = "measure"
    alarm: bool = False
    numeric: Optional[float] = None

    def to_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "raw": self.raw, "value": self.value,
                "unit": self.unit, "group": self.group, "alarm": self.alarm}


@dataclass
class ControllerState:
    controller: DiscoveredController
    family: Family
    timestamp: float
    readings: List[Reading] = field(default_factory=list)
    status_code: Optional[int] = None
    status_text: str = "Unknown"
    error_code: Optional[int] = None
    error_texts: List[str] = field(default_factory=list)
    warning_texts: List[str] = field(default_factory=list)
    unsupported: Set[int] = field(default_factory=set)
    comm_errors: List[str] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if self.error_texts:
            return "fail"
        return status_severity(self.status_text)

    def reading(self, key: str) -> Optional[Reading]:
        for r in self.readings:
            if r.key == key:
                return r
        return None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "port": self.controller.port, "baudrate": self.controller.baudrate,
            "address": self.controller.address, "family": self.family.value,
            "model": self.controller.model, "firmware": self.controller.firmware,
            "status_code": self.status_code, "status": self.status_text,
            "error_code": self.error_code, "errors": self.error_texts, "warnings": self.warning_texts,
            "readings": [r.to_dict() for r in self.readings],
            "unsupported_windows": sorted(self.unsupported), "comm_errors": self.comm_errors,
        }


def make_reading(wd: WindowDef, raw: str) -> Reading:
    numeric = parse_number(raw) if wd.type != "A" else None
    if wd.decoder:
        value = wd.decoder(raw)
    elif wd.type == "N":
        value = format_number(raw)
    elif wd.type == "L":
        value = {"0": "Off", "1": "On"}.get(raw.strip(), raw.strip())
    else:
        value = raw.strip()
    return Reading(str(wd.number), wd.name, raw, value, wd.unit, wd.group, numeric=numeric)


class ControllerReader:
    """Reads all known windows (or letter commands) and builds a ControllerState."""

    MAX_CONSECUTIVE_FAILURES = 4

    def __init__(self, transport: SerialTransport, controller: DiscoveredController,
                 family: Optional[Family] = None, log: Optional[Logger] = None):
        self.t = transport
        self.controller = controller
        self.family = family or controller.family
        self.log = log or (lambda msg: None)
        self.unsupported: Set[int] = set()
        self.identity: Dict[int, Reading] = {}
        self._last: Dict[int, Reading] = {}
        self.cycle = 0
        self.t.timeout = 1.0
        if self.family == Family.LETTER:
            self.t.min_interval = LETTER_MIN_INTERVAL

    def read_state(self) -> ControllerState:
        self.cycle += 1
        if self.family == Family.LETTER:
            return self._read_letter()
        return self._read_windows()

    # -- window protocol -----------------------------------------------------
    def _read_windows(self) -> ControllerState:
        state = ControllerState(self.controller, self.family, time.time(), unsupported=self.unsupported)
        failures = 0
        for wd in windows_for(self.family):
            if wd.number in self.unsupported:
                continue
            if wd.rate == 0 and wd.number in self.identity:
                state.readings.append(self.identity[wd.number])
                continue
            if wd.rate > 1 and (self.cycle - 1) % wd.rate and wd.number in self._last:
                state.readings.append(self._last[wd.number])
                continue
            try:
                r = self.t.read_window(wd.number, address=self.controller.address)
            except (ResponseTimeout, ProtocolError, TransportError) as exc:
                failures += 1
                state.comm_errors.append(f"window {wd.number}: {exc}")
                if isinstance(exc, TransportError) and not isinstance(exc, ResponseTimeout):
                    raise ConnectionLost(str(exc)) from exc
                if failures >= self.MAX_CONSECUTIVE_FAILURES:
                    raise ConnectionLost(f"{failures} consecutive failures: {exc}") from exc
                continue
            failures = 0
            if r.has_data:
                reading = make_reading(wd, r.data)
                state.readings.append(reading)
                self._last[wd.number] = reading
                if wd.rate == 0:
                    self.identity[wd.number] = reading
            elif r.code == CODE_UNKNOWN_WINDOW:
                self.unsupported.add(wd.number)
                self.log(f"window {wd.number} ({wd.name}) not supported by this controller")
            else:
                state.comm_errors.append(f"window {wd.number}: {r.code_name}")
        self._finish(state)
        return state

    def _finish(self, state: ControllerState) -> None:
        st = state.reading("205")
        if st and st.numeric is not None:
            state.status_code = int(st.numeric)
            state.status_text = decode_status(self.family, state.status_code)
        err = state.reading("206")
        if err and err.numeric is not None:
            state.error_code = int(err.numeric)
            state.error_texts = decode_error(self.family, state.error_code)
            err.alarm = bool(state.error_texts)
        warn = state.reading("228")
        if warn and warn.numeric is not None and int(warn.numeric):
            state.warning_texts = [warn.value]
            warn.alarm = True
        if st:
            st.alarm = state.severity == "fail"

    # -- letter protocol -----------------------------------------------------
    def _read_letter(self) -> ControllerState:
        state = ControllerState(self.controller, Family.LETTER, time.time())
        failures = 0

        def query(letter: str) -> Optional[bytes]:
            nonlocal failures
            try:
                payload = self.t.query_letter(letter)
                failures = 0
                return payload
            except (ResponseTimeout, ProtocolError, TransportError) as exc:
                failures += 1
                state.comm_errors.append(f"command {letter}: {exc}")
                if isinstance(exc, TransportError) and not isinstance(exc, ResponseTimeout):
                    raise ConnectionLost(str(exc)) from exc
                if failures >= self.MAX_CONSECUTIVE_FAILURES:
                    raise ConnectionLost(f"{failures} consecutive failures: {exc}") from exc
                return None

        p = query("I")
        if p:
            b = p[0]
            code = b & 0x0F
            state.status_code = code
            state.status_text = decode_status(Family.LETTER, code)
            state.readings.append(Reading("I.status", "Pump status", f"0x{b:02X}", state.status_text, "", "status", numeric=code))
            state.readings.append(Reading("I.r2", "Set point R2", str((b >> 4) & 1), "On" if (b >> 4) & 1 else "Off", "", "measure"))
            state.readings.append(Reading("I.r1", "Set point R1", str((b >> 5) & 1), "On" if (b >> 5) & 1 else "Off", "", "measure"))
            if code == 6:
                state.error_texts = ["Controller reports FAILURE (letter protocol carries no error code)"]
        p = query("J")
        if p and len(p) >= 4:
            cur = p[0] * 2.5 / 255.0
            volt = p[1] * 130.0 / 255.0
            state.readings.append(Reading("J.current", "Pump current", str(p[0]), f"{cur:.2f}", "A", "measure", numeric=cur))
            state.readings.append(Reading("J.voltage", "Pump voltage", str(p[1]), f"{volt:.1f}", "V", "measure", numeric=volt))
            state.readings.append(Reading("J.speed", "Rotational speed", str(p[2]), str(p[2]), "krpm", "measure", numeric=p[2]))
            temp = "sensor fail" if p[3] == 255 else str(p[3])
            state.readings.append(Reading("J.temp", "Pump temperature", str(p[3]), temp, "°C", "measure", alarm=p[3] == 255, numeric=None if p[3] == 255 else p[3]))
        p = query("K")
        if p and len(p) >= 10:
            cycle_time = int.from_bytes(p[0:4], "big")
            life = int.from_bytes(p[4:8], "big")
            cycles = int.from_bytes(p[8:10], "big")
            state.readings.append(Reading("K.cycle_time", "Cycle time (byte order assumed big-endian)", p[0:4].hex(), str(cycle_time), "min", "counter", numeric=cycle_time))
            state.readings.append(Reading("K.life", "Pump life (byte order assumed big-endian)", p[4:8].hex(), str(life), "h", "counter", numeric=life))
            state.readings.append(Reading("K.cycles", "Cycle number", p[8:10].hex(), str(cycles), "", "counter", numeric=cycles))
        return state
