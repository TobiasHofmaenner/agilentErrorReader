"""Serial transport: sends requests, collects replies, validates frames."""
from __future__ import annotations

import time
from typing import Callable, Optional

import serial

from .protocol import (
    LETTER_REPLY_LENGTHS, WindowResponse, build_letter_request, build_window_request,
    extract_window_frame, parse_letter_response, parse_window_response,
)

Logger = Callable[[str], None]


class TransportError(Exception):
    """Serial level failure (port gone, no reply, ...)."""


class ResponseTimeout(TransportError):
    """No complete reply within the allowed time."""


class SerialTransport:
    """One open serial port. Not thread safe: use it from a single thread."""

    def __init__(self, port: str, baudrate: int, *, timeout: float = 0.5,
                 min_interval: float = 0.02, log: Optional[Logger] = None, log_raw: bool = False):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.min_interval = min_interval
        self.log = log
        self.log_raw = log_raw
        self._last_tx = 0.0
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = baudrate
        self.ser.bytesize = serial.EIGHTBITS
        self.ser.parity = serial.PARITY_NONE
        self.ser.stopbits = serial.STOPBITS_ONE
        self.ser.timeout = 0.05          # per read() call; overall timeouts are handled here
        self.ser.write_timeout = 1.0

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> "SerialTransport":
        if not self.ser.is_open:
            self.ser.open()
            try:
                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
            except serial.SerialException:
                pass
        return self

    def close(self) -> None:
        if self.ser.is_open:
            try:
                self.ser.close()
            except serial.SerialException:
                pass

    def __enter__(self) -> "SerialTransport":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self.ser.is_open

    # -- low level -----------------------------------------------------------
    def _raw(self, direction: str, data: bytes) -> None:
        if self.log_raw and self.log:
            self.log(f"{direction} {data.hex(' ')}")

    def _send(self, data: bytes) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_tx)
        if wait > 0:
            time.sleep(wait)
        try:
            self.ser.reset_input_buffer()
            self.ser.write(data)
            self.ser.flush()
        except serial.SerialException as exc:
            raise TransportError(f"{self.port}: write failed: {exc}") from exc
        self._last_tx = time.monotonic()
        self._raw("TX", data)

    def _read_some(self, max_bytes: int) -> bytes:
        try:
            waiting = self.ser.in_waiting
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"{self.port}: port lost: {exc}") from exc
        try:
            return self.ser.read(max(1, min(waiting, max_bytes)))
        except (serial.SerialException, OSError) as exc:
            raise TransportError(f"{self.port}: read failed: {exc}") from exc

    # -- window protocol -----------------------------------------------------
    def read_window(self, window: int, *, address: int = 0, timeout: Optional[float] = None) -> WindowResponse:
        return self._transact_window(build_window_request(window, address=address), timeout)

    def write_window(self, window: int, data: str, *, address: int = 0,
                     timeout: Optional[float] = None) -> WindowResponse:
        return self._transact_window(build_window_request(window, write=True, data=data, address=address), timeout)

    def _transact_window(self, request: bytes, timeout: Optional[float]) -> WindowResponse:
        timeout = self.timeout if timeout is None else timeout
        self._send(request)
        deadline = time.monotonic() + timeout
        buf = b""
        while True:
            chunk = self._read_some(256)
            if chunk:
                buf += chunk
                while True:
                    frame, buf = extract_window_frame(buf)
                    if frame is None:
                        break
                    self._raw("RX", frame)
                    if frame == request:          # half duplex adapter echoing our own frame
                        continue
                    return parse_window_response(frame)
            if time.monotonic() > deadline:
                if buf:
                    self._raw("RX(partial)", buf)
                raise ResponseTimeout(f"{self.port}: no reply to window {request[2:5].decode('ascii', 'replace')}")

    # -- letter protocol -----------------------------------------------------
    def query_letter(self, letter: str, data: bytes = b"", *, timeout: Optional[float] = None) -> bytes:
        """Send a letter command and return the reply payload (CRC verified and stripped)."""
        timeout = self.timeout if timeout is None else timeout
        request = build_letter_request(letter, data)
        expected = LETTER_REPLY_LENGTHS.get(letter, 2)
        self._send(request)
        deadline = time.monotonic() + timeout
        buf = b""
        while len(buf) < expected:
            chunk = self._read_some(expected - len(buf))
            if chunk:
                buf += chunk
                if buf.startswith(request) and len(buf) >= len(request):   # echo
                    buf = buf[len(request):]
            elif time.monotonic() > deadline:
                if buf:
                    self._raw("RX(partial)", buf)
                raise ResponseTimeout(f"{self.port}: no reply to letter command {letter!r}")
        self._raw("RX", buf)
        return parse_letter_response(buf)
