"""Window definitions and value decoders for the controller generations.

MODERN  : window protocol, Agilent generation (TwisTorr FS/IC, Turbo-V xx-AG, Navigator, ...)
LEGACY  : window protocol, Varian HT/ICE generation (RS485 side) - different enumerations/units
LETTER  : single letter protocol (Turbo-V 301-AG rack, Turbo-V 70, HT/ICE RS232 side)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional


class Family(str, Enum):
    MODERN = "modern"
    LEGACY = "legacy"
    LETTER = "letter"

    @property
    def title(self) -> str:
        return {
            Family.MODERN: "Window protocol (Agilent generation)",
            Family.LEGACY: "Window protocol (legacy Varian HT/ICE)",
            Family.LETTER: "Letter protocol (Turbo-V 301-AG / V70 / HT-ICE RS232)",
        }[self]


Decoder = Callable[[str], str]


@dataclass(frozen=True)
class WindowDef:
    number: int
    name: str
    type: str                       # 'L' logic, 'N' numeric, 'A' alphanumeric
    unit: str = ""
    group: str = "measure"          # status | measure | counter | identity | config
    decoder: Optional[Decoder] = None
    rate: int = 1                   # 1 = every poll, 0 = once, n = every n-th poll


# ------------------------------------------------------------------ enumerations

MODERN_STATUS: Dict[int, str] = {
    0: "Stop",
    1: "Waiting interlock",
    2: "Starting (ramp)",
    3: "Auto-tuning",
    4: "Braking",
    5: "Normal",
    6: "Fail",
    7: "Leak check",           # Turbo-V 750/850 only
}
LEGACY_STATUS: Dict[int, str] = {
    0: "Stop",
    1: "Waiting interlock",
    2: "Starting",
    3: "Normal",
    4: "High load",
    5: "Failure",
    6: "Approaching (low speed)",
}
LETTER_STATUS: Dict[int, str] = {
    0: "Stop",
    1: "Waiting interlock",
    2: "Starting",
    3: "Normal operation",
    4: "High load",
    5: "High load",
    6: "Failure",
    7: "Approaching low speed",
}

# Window 206 on the modern generation is a bit mask. Bits 3 and 4 differ between models.
MODERN_ERROR_BITS: Dict[int, str] = {
    0: "No connection to pump",
    1: "Pump over-temperature",
    2: "Controller over-temperature",
    3: "Power fail / Vdc under-voltage / run-up time (model dependent)",
    4: "Aux fail / output fail / override / run-up time (model dependent)",
    5: "Over-voltage",
    6: "Short circuit",
    7: "Too high load",
    8: "Rotor locked (305-IC)",
    9: "Reserved (305-IC)",
    10: "Body HW over-temperature (305-IC)",
    11: "Run-up time (305-IC)",
}
# Window 206 on the legacy HT/ICE generation is an enumeration.
LEGACY_ERROR_CODES: Dict[int, str] = {
    0: "No error",
    1: "Over-voltage",
    2: "Short circuit",
    3: "Check connection to pump",
    4: "Too high load",
    5: "Override",
    6: "Pump over-temperature",
    7: "Controller over-temperature",
}
WARNING_BITS_305IC: Dict[int, str] = {
    0: "DO2 overload", 1: "DO1 overload", 2: "A2 overload", 3: "A1 overload",
    4: "DO2 underload", 5: "DO1 underload", 6: "A2 underload", 7: "A1 underload",
    8: "Analog out out of tolerance", 9: "24 Vdc under-voltage warning",
    10: "DO2 thermal fail", 11: "DO1 thermal fail", 12: "A2 thermal fail", 13: "A1 thermal fail",
}
GAUGE_STATUS: Dict[int, str] = {
    0: "No gauge connected",
    1: "Gauge connected",
    2: "Under range / gauge error",
    3: "Over range / gauge error",
    4: "RID unknown",
}
BAUD_CODES: Dict[int, int] = {0: 600, 1: 1200, 2: 2400, 3: 4800, 4: 9600, 5: 19200, 6: 38400}

# Status severities drive the colour of the big status label in the GUI.
SEVERITY = {
    "Stop": "off",
    "Waiting interlock": "warn",
    "Starting (ramp)": "busy", "Starting": "busy", "Auto-tuning": "busy", "Braking": "busy",
    "Approaching (low speed)": "busy", "Approaching low speed": "busy", "Leak check": "busy",
    "Normal": "ok", "Normal operation": "ok",
    "High load": "warn",
    "Fail": "fail", "Failure": "fail",
}


# ------------------------------------------------------------------ helpers

def parse_number(raw: str) -> Optional[float]:
    s = raw.strip()
    if not s:
        return None
    try:
        return float(int(s))
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return None


def format_number(raw: str) -> str:
    v = parse_number(raw)
    if v is None:
        return raw.strip() or "-"
    if v.is_integer():
        return str(int(v))
    return f"{v:g}"


def _enum_decoder(table: Dict[int, str]) -> Decoder:
    def decode(raw: str) -> str:
        v = parse_number(raw)
        if v is None:
            return raw.strip() or "-"
        return table.get(int(v), f"{int(v)} (unknown)")
    return decode


def _bits_decoder(table: Dict[int, str], none_text: str) -> Decoder:
    def decode(raw: str) -> str:
        v = parse_number(raw)
        if v is None:
            return raw.strip() or "-"
        names = decode_bits(int(v), table)
        return ", ".join(names) if names else none_text
    return decode


def decode_bits(value: int, table: Dict[int, str]) -> List[str]:
    names = []
    bit = 0
    while value >> bit:
        if value & (1 << bit):
            names.append(table.get(bit, f"bit {bit}"))
        bit += 1
    return names


def _onoff(on: str, off: str) -> Decoder:
    def decode(raw: str) -> str:
        s = raw.strip()
        return on if s == "1" else off if s == "0" else s or "-"
    return decode


def _baud(raw: str) -> str:
    v = parse_number(raw)
    if v is None:
        return raw
    return f"{BAUD_CODES.get(int(v), '?')} baud (code {int(v)})"


def decode_status(family: Family, code: int) -> str:
    table = {Family.MODERN: MODERN_STATUS, Family.LEGACY: LEGACY_STATUS, Family.LETTER: LETTER_STATUS}[family]
    return table.get(code, f"Unknown status {code}")


def decode_error(family: Family, code: int) -> List[str]:
    """List of active error descriptions (empty when there is no error)."""
    if family == Family.LEGACY:
        if code == 0:
            return []
        return [LEGACY_ERROR_CODES.get(code, f"Unknown error code {code}")]
    return decode_bits(code, MODERN_ERROR_BITS)


def status_severity(text: str) -> str:
    return SEVERITY.get(text, "unknown")


# ------------------------------------------------------------------ window tables

MODERN_WINDOWS: List[WindowDef] = [
    WindowDef(205, "Pump status", "N", "", "status", _enum_decoder(MODERN_STATUS)),
    WindowDef(206, "Error code", "N", "", "status", _bits_decoder(MODERN_ERROR_BITS, "No error")),
    WindowDef(228, "Warning code", "N", "", "status", _bits_decoder(WARNING_BITS_305IC, "No warning")),
    WindowDef(200, "Pump current", "N", "mA"),
    WindowDef(201, "Pump voltage", "N", "V"),
    WindowDef(202, "Pump power", "N", "W"),
    WindowDef(203, "Driving frequency", "N", "Hz"),
    WindowDef(226, "Rotation speed", "N", "rpm"),
    WindowDef(204, "Pump temperature", "N", "°C"),
    WindowDef(211, "Controller heatsink temperature", "N", "°C"),
    WindowDef(216, "Controller air temperature", "N", "°C"),
    WindowDef(234, "Bus voltage", "N", "V"),
    WindowDef(224, "Pressure", "A", ""),
    WindowDef(257, "Gauge status", "N", "", "measure", _enum_decoder(GAUGE_STATUS)),
    WindowDef(221, "Set point output", "L", "", "measure", _onoff("Active", "Inactive")),
    WindowDef(300, "Cycle time", "N", "min", "counter"),
    WindowDef(301, "Cycle number", "N", "", "counter"),
    WindowDef(302, "Pump life", "N", "h", "counter"),
    WindowDef(307, "Controller life", "N", "h", "counter"),
    WindowDef(319, "Controller model / P/N", "A", "", "identity", rate=0),
    WindowDef(320, "Pump model / P/N", "A", "", "identity", rate=0),
    WindowDef(321, "Modified model number", "A", "", "identity", rate=0),
    WindowDef(323, "Controller serial number", "A", "", "identity", rate=0),
    WindowDef(406, "Program listing code & revision", "A", "", "identity", rate=0),
    WindowDef(407, "Parameter listing code", "A", "", "identity", rate=0),
    WindowDef(400, "Program listing CRC", "A", "", "identity", rate=0),
    WindowDef(402, "Parameter listing CRC", "A", "", "identity", rate=0),
    WindowDef(404, "Parameter structure CRC", "A", "", "identity", rate=0),
    WindowDef(0, "Start / stop", "L", "", "config", _onoff("Start", "Stop")),
    WindowDef(1, "Low speed", "L", "", "config", _onoff("On", "Off")),
    WindowDef(8, "Control mode", "L", "", "config", _onoff("Remote (1)", "Serial (0)"), rate=10),
    WindowDef(100, "Soft start", "L", "", "config", _onoff("Enabled", "Disabled"), rate=10),
    WindowDef(106, "Cooling mode", "L", "", "config", _onoff("Water", "Air"), rate=10),
    WindowDef(107, "Active stop", "L", "", "config", _onoff("Enabled", "Disabled"), rate=10),
    WindowDef(108, "Baud rate", "N", "", "config", _baud, rate=10),
    WindowDef(110, "Interlock type", "L", "", "config", _onoff("Continuous", "Impulse"), rate=10),
    WindowDef(117, "Low speed frequency", "N", "Hz", "config", rate=10),
    WindowDef(120, "Rotational frequency setting", "N", "Hz", "config", rate=10),
    WindowDef(121, "Maximum rotational frequency", "N", "Hz", "config", rate=10),
    WindowDef(155, "Power limit", "N", "W", "config", rate=10),
    WindowDef(157, "Gas load type", "N", "", "config", _enum_decoder({0: "Ar (heavy)", 1: "N2 (medium)", 2: "H2 / He (light)"}), rate=10),
    WindowDef(503, "RS485 address", "N", "", "config", rate=10),
    WindowDef(504, "Serial type", "L", "", "config", _onoff("RS485", "RS232"), rate=10),
]

LEGACY_WINDOWS: List[WindowDef] = [
    WindowDef(205, "Pump state", "N", "", "status", _enum_decoder(LEGACY_STATUS)),
    WindowDef(206, "Error code", "N", "", "status", _enum_decoder(LEGACY_ERROR_CODES)),
    WindowDef(200, "Pump current", "N", "A"),
    WindowDef(201, "Pump voltage", "N", "V"),
    WindowDef(202, "Pump power", "N", "W"),
    WindowDef(203, "Rotational speed", "N", "krpm"),
    WindowDef(204, "Pump temperature", "N", "°C"),
    WindowDef(207, "Set point R1", "L", "", "measure", _onoff("On", "Off")),
    WindowDef(208, "Set point R2", "L", "", "measure", _onoff("On", "Off")),
    WindowDef(300, "Cycle time", "N", "min", "counter"),
    WindowDef(301, "Cycle number", "N", "", "counter"),
    WindowDef(302, "Pump life", "N", "h", "counter"),
    WindowDef(400, "Program listing CRC", "A", "", "identity", rate=0),
    WindowDef(402, "Parameter listing CRC", "A", "", "identity", rate=0),
    WindowDef(0, "Start / stop", "L", "", "config", _onoff("Start", "Stop")),
    WindowDef(1, "Low speed", "L", "", "config", _onoff("On", "Off")),
    WindowDef(100, "Soft start", "L", "", "config", _onoff("Yes", "No"), rate=10),
    WindowDef(101, "Dead time", "L", "", "config", _onoff("Yes", "No"), rate=10),
    WindowDef(102, "Water cooling", "L", "", "config", _onoff("Yes", "No"), rate=10),
    WindowDef(103, "Speed threshold", "N", "", "config", rate=10),
    WindowDef(104, "Run-up time", "N", "s", "config", rate=10),
    WindowDef(107, "Mode", "N", "", "config", _enum_decoder({0: "Front", 1: "Remote", 2: "Serial"}), rate=10),
    WindowDef(108, "Baud rate", "N", "", "config", _baud, rate=10),
]

GROUP_ORDER = ["status", "measure", "counter", "config", "identity"]
GROUP_TITLES = {
    "status": "Status", "measure": "Measurements", "counter": "Counters",
    "config": "Configuration", "identity": "Identification",
}


def windows_for(family: Family) -> List[WindowDef]:
    if family == Family.MODERN:
        return MODERN_WINDOWS
    if family == Family.LEGACY:
        return LEGACY_WINDOWS
    return []


def window_by_number(family: Family, number: int) -> Optional[WindowDef]:
    for wd in windows_for(family):
        if wd.number == number:
            return wd
    return None
