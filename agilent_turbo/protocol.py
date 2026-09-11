"""Framing, checksums and parsing for the two Agilent/Varian turbo controller protocols.

Window protocol (every current Agilent controller, legacy HT/ICE on RS485):
    <STX><ADDR><WIN><COM><DATA><ETX><CRC>
Letter protocol (Turbo-V 301-AG rack, Turbo-V 70, legacy HT/ICE on RS232):
    <letter>[data]<CRC>
See PROTOCOLS.md for the manual references.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

STX = 0x02
ETX = 0x03
ACK = 0x06
NACK = 0x15
ADDR_BASE = 0x80

CODE_UNKNOWN_WINDOW = 0x32
CODE_DATA_TYPE_ERROR = 0x33
CODE_OUT_OF_RANGE = 0x34
CODE_WINDOW_DISABLED = 0x35

RESPONSE_CODE_NAMES = {
    ACK: "ACK",
    NACK: "NACK",
    CODE_UNKNOWN_WINDOW: "Unknown window",
    CODE_DATA_TYPE_ERROR: "Data type error",
    CODE_OUT_OF_RANGE: "Out of range",
    CODE_WINDOW_DISABLED: "Window disabled / read only",
}


class ProtocolError(Exception):
    """A frame that cannot be parsed."""


class CrcError(ProtocolError):
    """A frame whose checksum does not match."""


# --------------------------------------------------------------------------- window protocol

def window_crc(body: bytes) -> bytes:
    """XOR of all bytes after STX up to and including ETX, as two upper-case hex ASCII chars."""
    x = 0
    for b in body:
        x ^= b
    return f"{x:02X}".encode("ascii")


def build_window_request(window: int, *, write: bool = False, data: str = "", address: int = 0) -> bytes:
    if not 0 <= window <= 999:
        raise ValueError(f"window out of range: {window}")
    if not 0 <= address <= 31:
        raise ValueError(f"address out of range: {address}")
    body = (
        bytes([ADDR_BASE + address])
        + f"{window:03d}".encode("ascii")
        + (b"1" if write else b"0")
        + data.encode("ascii")
        + bytes([ETX])
    )
    return bytes([STX]) + body + window_crc(body)


def build_window_data_reply(window: int, data: str, *, address: int = 0) -> bytes:
    """What a controller sends back to a read request."""
    return build_window_request(window, write=False, data=data, address=address)


def build_window_code_reply(code: int, *, address: int = 0) -> bytes:
    """Single-byte reply (ACK, NACK, error code)."""
    body = bytes([ADDR_BASE + address, code, ETX])
    return bytes([STX]) + body + window_crc(body)


@dataclass
class WindowResponse:
    address: int
    raw: bytes
    code: Optional[int] = None       # set for single-byte replies (ACK / NACK / error codes)
    window: Optional[int] = None
    write: Optional[bool] = None
    data: Optional[str] = None       # set for read replies

    @property
    def has_data(self) -> bool:
        return self.data is not None

    @property
    def code_name(self) -> str:
        if self.code is None:
            return "data"
        return RESPONSE_CODE_NAMES.get(self.code, f"code 0x{self.code:02X}")


def parse_window_response(frame: bytes) -> WindowResponse:
    if len(frame) < 6 or frame[0] != STX:
        raise ProtocolError(f"frame does not start with STX: {frame!r}")
    if frame[-3] != ETX:
        raise ProtocolError(f"frame does not end with ETX+CRC: {frame!r}")
    body = frame[1:-2]
    crc = frame[-2:]
    expected = window_crc(body)
    if crc.upper() != expected:
        raise CrcError(f"bad CRC {crc!r}, expected {expected!r} in {frame!r}")
    addr = body[0]
    if addr < ADDR_BASE:
        raise ProtocolError(f"invalid address byte 0x{addr:02X} in {frame!r}")
    payload = body[1:-1]
    resp = WindowResponse(address=addr - ADDR_BASE, raw=bytes(frame))
    if len(payload) == 1:
        resp.code = payload[0]
        return resp
    if len(payload) < 4:
        raise ProtocolError(f"payload too short: {frame!r}")
    try:
        resp.window = int(payload[0:3].decode("ascii"))
    except ValueError as exc:
        raise ProtocolError(f"invalid window field in {frame!r}") from exc
    resp.write = payload[3:4] == b"1"
    resp.data = payload[4:].decode("ascii", errors="replace")
    return resp


def extract_window_frame(buf: bytes) -> Tuple[Optional[bytes], bytes]:
    """Return (frame, remainder). frame is None while no complete frame is buffered."""
    start = buf.find(bytes([STX]))
    if start < 0:
        return None, b""
    end = buf.find(bytes([ETX]), start + 1)
    if end < 0 or len(buf) < end + 3:
        return None, buf[start:]
    return buf[start:end + 3], buf[end + 3:]


# --------------------------------------------------------------------------- letter protocol

LETTER_COMMANDS = {
    "A": "Start", "B": "Stop", "C": "Low speed on", "D": "Low speed off",
    "E": "Operational parameters", "F": "Pump times zeroing", "G": "Parameters reading",
    "H": "Parameters writing", "I": "Operating status", "J": "Numerical readings",
    "K": "Counters readings",
}
LETTER_REPLY_LENGTHS = {
    "A": 2, "B": 2, "C": 2, "D": 2, "F": 2, "H": 2,
    "I": 2, "J": 5, "K": 11, "G": 11, "E": 22,
}
# Minimum spacing between requests demanded by the manuals (>= 2400 baud).
LETTER_MIN_INTERVAL = 1.0


def letter_crc(payload: bytes) -> int:
    """Sum of the bytes with inverted sign (two's complement): 'A' (0x41) -> 0xBF."""
    return (-sum(payload)) & 0xFF


def build_letter_request(letter: str, data: bytes = b"") -> bytes:
    body = letter.encode("ascii") + data
    return body + bytes([letter_crc(body)])


def parse_letter_response(buf: bytes) -> bytes:
    """Validate the trailing CRC and return the payload without it."""
    if len(buf) < 2:
        raise ProtocolError(f"letter reply too short: {buf!r}")
    if letter_crc(buf[:-1]) != buf[-1]:
        raise CrcError(f"bad letter CRC in {buf!r}")
    return bytes(buf[:-1])
