"""Command line reader: scan, connect, print the controller state.

    python -m agilent_turbo.cli                 # auto-scan all ports
    python -m agilent_turbo.cli --port /dev/ttyUSB0 --baud 9600 --once --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .reader import ConnectionLost, ControllerReader
from .report import build_markdown_report
from .scanner import DEFAULT_BAUDRATES, Scanner, list_serial_ports
from .transport import SerialTransport
from .windows import Family


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read the state of an Agilent turbo pump controller")
    ap.add_argument("--port", action="append", help="serial port(s) to scan (default: all)")
    ap.add_argument("--baud", action="append", type=int, help="baud rate(s) to try (default: all)")
    ap.add_argument("--no-rs485", action="store_true", help="do not sweep RS485 addresses 1..31")
    ap.add_argument("--no-letter", action="store_true", help="do not try the legacy letter protocol")
    ap.add_argument("--deep", action="store_true", help="sweep RS485 addresses at every baud rate")
    ap.add_argument("--family", choices=[f.value for f in Family], help="force the decoding family")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between read-outs")
    ap.add_argument("--once", action="store_true", help="read once and exit")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    ap.add_argument("--raw", action="store_true", help="log raw frames")
    ap.add_argument("--list", action="store_true", help="list serial ports and exit")
    ap.add_argument("--report", metavar="FILE.md", help="write a Markdown report after each read-out")
    args = ap.parse_args(argv)

    log = lambda msg: print(f"# {msg}", file=sys.stderr, flush=True)

    if args.list:
        for p in list_serial_ports():
            print(p.label)
        return 0

    scanner = Scanner(ports=args.port, baudrates=args.baud or DEFAULT_BAUDRATES,
                      scan_rs485=not args.no_rs485, try_letter=not args.no_letter, deep=args.deep, log=log)
    found = scanner.scan(stop_at_first=True)
    if not found:
        return 1
    ctl = found[0]
    log(f"Connected: {ctl.label} [{ctl.family.title}]")

    with SerialTransport(ctl.port, ctl.baudrate, log=log, log_raw=args.raw) as t:
        reader = ControllerReader(t, ctl, family=Family(args.family) if args.family else None, log=log)
        try:
            while True:
                state = reader.read_state()
                if args.json:
                    print(json.dumps(state.to_dict(), indent=1, ensure_ascii=False), flush=True)
                else:
                    ts = time.strftime("%H:%M:%S", time.localtime(state.timestamp))
                    print(f"\n[{ts}] {ctl.port} @ {ctl.baudrate}  status: {state.status_text}"
                          + (f"  ERRORS: {'; '.join(state.error_texts)}" if state.error_texts else ""))
                    for r in state.readings:
                        flag = " !" if r.alarm else ""
                        print(f"  {r.key:>12}  {r.name:<44} {r.value:>14} {r.unit}{flag}")
                    for e in state.comm_errors:
                        print(f"  comm: {e}")
                if args.report:
                    with open(args.report, "w", encoding="utf-8") as fh:
                        fh.write(build_markdown_report(state))
                    log(f"Report written to {args.report}")
                if args.once:
                    break
                time.sleep(args.interval)
        except ConnectionLost as exc:
            log(f"Connection lost: {exc}")
            return 2
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
