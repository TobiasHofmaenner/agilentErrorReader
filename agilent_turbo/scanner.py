"""Automatic discovery of a controller on any serial port, at any baud rate and address."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional, Sequence

import serial
from serial.tools import list_ports

from .protocol import CODE_UNKNOWN_WINDOW, LETTER_MIN_INTERVAL, ProtocolError
from .transport import SerialTransport, TransportError
from .windows import Family

DEFAULT_BAUDRATES: Sequence[int] = (9600, 19200, 38400, 4800, 2400, 1200, 600)
RS485_ADDRESSES: Sequence[int] = tuple(range(1, 32))
PROBE_WINDOW = 205            # pump status, implemented by every window-protocol controller
FAMILY_PROBE_WINDOWS = (503, 504, 404, 319)     # exist on the modern generation only
IDENTITY_WINDOWS = (319, 320, 406, 400)

Logger = Callable[[str], None]


@dataclass
class PortInfo:
    device: str
    description: str = ""
    hwid: str = ""

    @property
    def label(self) -> str:
        return f"{self.device} ({self.description})" if self.description and self.description != "n/a" else self.device


def list_serial_ports() -> List[PortInfo]:
    """All serial ports, USB adapters first (on-board ttyS ports are usually empty)."""
    infos = []
    for p in list_ports.comports():
        infos.append(PortInfo(p.device, p.description or "", p.hwid or ""))

    def rank(pi: PortInfo):
        d = pi.device
        usb = any(k in d for k in ("ttyUSB", "ttyACM", "usbserial", "usbmodem", "cu.SLAB", "cu.wch")) or "USB" in pi.hwid.upper()
        return (0 if usb else 1, d)

    infos.sort(key=rank)
    return infos


def probe_timeout(baudrate: int) -> float:
    """Reply timeout for one probe: line time for ~25 bytes plus controller latency."""
    return 0.35 + 250.0 / baudrate


@dataclass
class DiscoveredController:
    port: str
    baudrate: int
    family: Family
    address: int = 0
    model: str = ""
    firmware: str = ""
    identity: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        parts = [f"{self.port} @ {self.baudrate}"]
        if self.family == Family.LETTER:
            parts.append("letter protocol")
        else:
            parts.append(f"window protocol, address {self.address}")
        if self.model:
            parts.append(self.model)
        return ", ".join(parts)


class ScanCancelled(Exception):
    pass


class Scanner:
    """Probe ports until a controller answers.

    Pass 1: every port, every baud rate, window protocol at address 0 (RS232).
    Pass 2: every port, letter protocol at 9600 baud.
    Pass 3: every port, window protocol RS485 addresses 1..31 at 9600 baud.
    Pass 4: every port, letter protocol at the remaining baud rates.
    Pass 5 (deep scan only): RS485 addresses at the remaining baud rates.
    """

    def __init__(self, *, ports: Optional[Iterable[str]] = None,
                 baudrates: Sequence[int] = DEFAULT_BAUDRATES,
                 scan_rs485: bool = True, rs485_addresses: Sequence[int] = RS485_ADDRESSES,
                 try_letter: bool = True, deep: bool = False,
                 log: Optional[Logger] = None, progress: Optional[Logger] = None,
                 is_cancelled: Optional[Callable[[], bool]] = None):
        self.ports = list(ports) if ports else None
        self.baudrates = list(baudrates)
        self.scan_rs485 = scan_rs485
        self.rs485_addresses = list(rs485_addresses)
        self.try_letter = try_letter
        self.deep = deep
        self._log = log or (lambda msg: None)
        self._progress = progress or (lambda msg: None)
        self._is_cancelled = is_cancelled or (lambda: False)
        self._unopenable: set = set()

    # -- helpers -------------------------------------------------------------
    def _check_cancel(self) -> None:
        if self._is_cancelled():
            raise ScanCancelled()

    def _open(self, port: str, baudrate: int) -> Optional[SerialTransport]:
        if port in self._unopenable:
            return None
        t = SerialTransport(port, baudrate, timeout=probe_timeout(baudrate), log=self._log)
        try:
            t.open()
        except (serial.SerialException, OSError) as exc:
            self._log(f"{port}: cannot open ({exc})")
            self._unopenable.add(port)
            return None
        return t

    def _probe_window(self, t: SerialTransport, address: int) -> bool:
        try:
            r = t.read_window(PROBE_WINDOW, address=address)
        except (TransportError, ProtocolError):
            return False
        return r.has_data or r.code is not None

    def _probe_letter(self, t: SerialTransport) -> bool:
        """Two consecutive valid 'I' replies with the two unused status bits clear."""
        t.min_interval = LETTER_MIN_INTERVAL
        for _ in range(2):
            try:
                payload = t.query_letter("I")
            except (TransportError, ProtocolError):
                return False
            if len(payload) != 1 or payload[0] & 0xC0:
                return False
        return True

    def _identify(self, t: SerialTransport, address: int) -> DiscoveredController:
        family = Family.LEGACY
        for w in FAMILY_PROBE_WINDOWS:
            self._check_cancel()
            try:
                r = t.read_window(w, address=address)
            except (TransportError, ProtocolError):
                continue
            if r.has_data:
                family = Family.MODERN
                break
        identity = {}
        for w in IDENTITY_WINDOWS:
            self._check_cancel()
            try:
                r = t.read_window(w, address=address)
            except (TransportError, ProtocolError):
                continue
            if r.has_data and r.data.strip():
                identity[w] = r.data.strip()
        model = identity.get(319, "")
        if identity.get(320):
            model = f"{model} / pump {identity[320]}".strip(" /")
        firmware = identity.get(406) or identity.get(400) or ""
        return DiscoveredController(t.port, t.baudrate, family, address, model, firmware, identity)

    # -- passes --------------------------------------------------------------
    def _pass_window(self, port: str, baudrates: Sequence[int], addresses: Sequence[int]) -> Optional[DiscoveredController]:
        for baud in baudrates:
            t = self._open(port, baud)
            if t is None:
                return None
            with t:
                for addr in addresses:
                    self._check_cancel()
                    self._progress(f"{port} @ {baud}: window protocol, address {addr}")
                    if self._probe_window(t, addr):
                        self._log(f"{port} @ {baud}: window-protocol controller answered at address {addr}")
                        ctl = self._identify(t, addr)
                        return ctl
        return None

    def _pass_letter(self, port: str, baudrates: Sequence[int]) -> Optional[DiscoveredController]:
        for baud in baudrates:
            t = self._open(port, baud)
            if t is None:
                return None
            with t:
                self._check_cancel()
                self._progress(f"{port} @ {baud}: letter protocol")
                if self._probe_letter(t):
                    self._log(f"{port} @ {baud}: letter-protocol controller answered")
                    return DiscoveredController(port, baud, Family.LETTER, 0, "Turbo-V 301-AG / V70 class", "")
        return None

    def scan(self, stop_at_first: bool = True) -> List[DiscoveredController]:
        ports = self.ports if self.ports is not None else [p.device for p in list_serial_ports()]
        if not ports:
            self._log("No serial ports found.")
            return []
        self._log("Scanning ports: " + ", ".join(ports))
        found: List[DiscoveredController] = []
        other_bauds = [b for b in self.baudrates if b != 9600]

        passes = [("RS232 / address 0", lambda p: self._pass_window(p, self.baudrates, [0]))]
        if self.try_letter and 9600 in self.baudrates:
            passes.append(("letter protocol @ 9600", lambda p: self._pass_letter(p, [9600])))
        if self.scan_rs485:
            passes.append(("RS485 addresses @ 9600", lambda p: self._pass_window(p, [9600] if 9600 in self.baudrates else self.baudrates[:1], self.rs485_addresses)))
        if self.try_letter and other_bauds:
            passes.append(("letter protocol, other baud rates", lambda p: self._pass_letter(p, other_bauds)))
        if self.scan_rs485 and self.deep and other_bauds:
            passes.append(("RS485 addresses, other baud rates", lambda p: self._pass_window(p, other_bauds, self.rs485_addresses)))

        try:
            for title, fn in passes:
                self._log(f"Pass: {title}")
                for port in ports:
                    if any(c.port == port for c in found):
                        continue
                    ctl = fn(port)
                    if ctl:
                        found.append(ctl)
                        self._log(f"Found: {ctl.label} [{ctl.family.title}]")
                        if stop_at_first:
                            return found
        except ScanCancelled:
            self._log("Scan cancelled.")
        if not found:
            self._log("No controller found.")
        return found
