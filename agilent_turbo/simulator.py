"""Software stand-in for a controller, for tests and GUI development without hardware.

    python -m agilent_turbo.simulator --family modern --status 5
prints a pseudo terminal path (e.g. /dev/pts/4); point the GUI or CLI at that port.
"""
from __future__ import annotations

import argparse
import os
import threading
import time
from typing import Dict, Optional

from .protocol import (
    ACK, CODE_DATA_TYPE_ERROR, CODE_UNKNOWN_WINDOW, ADDR_BASE, LETTER_REPLY_LENGTHS, ProtocolError,
    build_letter_request, build_window_code_reply, build_window_data_reply, extract_window_frame,
    letter_crc, parse_window_response,
)
from .windows import Family


def _fmt(type_: str, value) -> str:
    if type_ == "L":
        return "1" if value else "0"
    if type_ == "N":
        if isinstance(value, float) and not value.is_integer():
            return f"{value:06.1f}"[-6:]
        return f"{int(value):06d}"
    return str(value)[:10].ljust(10)


class SimulatedController:
    def __init__(self, family: Family = Family.MODERN, *, address: int = 0, status: int = 5,
                 error: int = 0, model: str = "X350864001", respond_to_addr0: bool = True):
        self.family = family
        self.address = address
        self.respond_to_addr0 = respond_to_addr0 and address == 0
        self._buf = b""
        self.windows: Dict[int, list] = {}
        if family == Family.MODERN:
            self.windows = {
                0: ["L", status not in (0, 1)], 1: ["L", 0], 8: ["L", 1], 100: ["L", 1], 107: ["L", 0],
                108: ["N", 4], 110: ["L", 0], 117: ["N", 700], 120: ["N", 1010], 155: ["N", 120],
                200: ["N", 350], 201: ["N", 31], 202: ["N", 11], 203: ["N", 1010], 204: ["N", 38],
                205: ["N", status], 206: ["N", error], 211: ["N", 41], 216: ["N", 29], 226: ["N", 60600],
                300: ["N", 123], 301: ["N", 42], 302: ["N", 5678],
                319: ["A", model], 320: ["A", "X3502-64000"], 323: ["A", "IT2201234"],
                400: ["A", "QE7A1B2C3"], 402: ["A", "PA7D4E5F6"], 404: ["A", "SC7000123"],
                406: ["A", "QE70010 A1"], 503: ["N", address], 504: ["L", address != 0],
            }
        elif family == Family.LEGACY:
            self.windows = {
                0: ["L", status not in (0, 1)], 1: ["L", 0], 100: ["L", 1], 101: ["L", 0], 102: ["L", 0],
                103: ["N", 90], 104: ["N", 900], 107: ["N", 2], 108: ["N", 4],
                200: ["N", 1.2], 201: ["N", 48], 202: ["N", 57], 203: ["N", 56], 204: ["N", 35],
                205: ["N", status], 206: ["N", error], 207: ["L", 1], 208: ["L", 0],
                300: ["N", 77], 301: ["N", 9], 302: ["N", 12345], 400: ["A", "CRC1234"], 402: ["A", "CRC5678"],
            }
        self.letter_status = status
        self.letter_error = error
        self._thread: Optional[threading.Thread] = None
        self._stop = False
        self._master: Optional[int] = None
        self.slave_path: Optional[str] = None

    # -- protocol handling --------------------------------------------------
    def feed(self, data: bytes) -> bytes:
        self._buf += data
        if self.family == Family.LETTER:
            return self._feed_letter()
        out = b""
        while True:
            frame, self._buf = extract_window_frame(self._buf)
            if frame is None:
                break
            out += self._handle_frame(frame)
        if len(self._buf) > 64:          # garbage without ETX
            self._buf = b""
        return out

    def _handle_frame(self, frame: bytes) -> bytes:
        try:
            req = parse_window_response(frame)
        except ProtocolError:
            return b""
        if req.address != self.address and not (req.address == 0 and self.respond_to_addr0):
            return b""
        if req.window is None:
            return b""
        entry = self.windows.get(req.window)
        if entry is None:
            return build_window_code_reply(CODE_UNKNOWN_WINDOW, address=req.address)
        if req.write:
            typ, _ = entry
            data = (req.data or "").strip()
            if typ == "L" and data in ("0", "1"):
                entry[1] = data == "1"
            elif typ == "N":
                try:
                    entry[1] = float(data)
                except ValueError:
                    return build_window_code_reply(CODE_DATA_TYPE_ERROR, address=req.address)
            else:
                entry[1] = data
            return build_window_code_reply(ACK, address=req.address)
        return build_window_data_reply(req.window, _fmt(entry[0], entry[1]), address=req.address)

    def _feed_letter(self) -> bytes:
        out = b""
        while len(self._buf) >= 2:
            letter, crc = self._buf[0:1], self._buf[1]
            if letter_crc(letter) != crc:
                self._buf = self._buf[1:]
                continue
            self._buf = self._buf[2:]
            out += self._letter_reply(letter.decode("ascii", "replace"))
        return out

    def _letter_reply(self, letter: str) -> bytes:
        def framed(payload: bytes) -> bytes:
            return payload + bytes([letter_crc(payload)])
        if letter == "I":
            return framed(bytes([(self.letter_status & 0x0F) | (1 << 5)]))    # R1 on
        if letter == "J":
            return framed(bytes([122, 94, 56, 35]))
        if letter == "K":
            return framed((77).to_bytes(4, "big") + (12345).to_bytes(4, "big") + (9).to_bytes(2, "big"))
        if letter in ("A", "B", "C", "D", "F"):
            return framed(bytes([ACK]))
        return b""

    # -- pty serving --------------------------------------------------------
    def serve_pty(self) -> str:
        import pty  # POSIX only; imported lazily so the module loads on Windows
        master, slave = pty.openpty()
        self._master = master
        self.slave_path = os.ttyname(slave)
        self._stop = False
        self._thread = threading.Thread(target=self._serve, args=(master,), daemon=True)
        self._thread.start()
        return self.slave_path

    def _serve(self, master: int) -> None:
        import select
        while not self._stop:
            r, _, _ = select.select([master], [], [], 0.1)
            if not r:
                continue
            try:
                data = os.read(master, 256)
            except OSError:
                break
            if not data:
                continue
            reply = self.feed(data)
            if reply:
                time.sleep(0.01)
                os.write(master, reply)

    def stop(self) -> None:
        self._stop = True
        if self._thread:
            self._thread.join(timeout=1)
        if self._master is not None:
            try:
                os.close(self._master)
            except OSError:
                pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Agilent turbo controller simulator on a pseudo terminal")
    ap.add_argument("--family", choices=[f.value for f in Family], default="modern")
    ap.add_argument("--address", type=int, default=0, help="RS485 address (0 = RS232 mode)")
    ap.add_argument("--status", type=int, default=5)
    ap.add_argument("--error", type=int, default=0)
    args = ap.parse_args(argv)
    sim = SimulatedController(Family(args.family), address=args.address, status=args.status, error=args.error)
    path = sim.serve_pty()
    print(f"Simulated {args.family} controller (address {args.address}) on {path}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    sim.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
