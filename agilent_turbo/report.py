"""Markdown report of a controller state (used by the GUI export button and the CLI)."""
from __future__ import annotations

import time
from typing import Optional

from .reader import ControllerState
from .windows import Family, GROUP_ORDER, GROUP_TITLES

IDENTITY_WINDOW_NAMES = {
    319: "Controller model / P/N", 320: "Pump model / P/N", 406: "Program listing code & revision",
    400: "Program listing CRC",
}


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def build_markdown_report(state: ControllerState, *, log_text: Optional[str] = None,
                          title: str = "Agilent turbo pump controller report") -> str:
    ctl = state.controller
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state.timestamp))
    lines = [f"# {title}", "", f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}  ", f"State read at: {ts}", ""]

    # ---- connection
    lines += ["## Connection", "", "| Item | Value |", "|---|---|"]
    lines.append(f"| Port | `{ctl.port}` |")
    lines.append(f"| Serial settings | {ctl.baudrate} baud, 8 data bits, no parity, 1 stop bit |")
    lines.append(f"| Protocol | {state.family.title} |")
    if state.family == Family.LETTER:
        lines.append("| Address | n/a (letter protocol) |")
    elif ctl.address == 0:
        lines.append("| Address | RS232 (0x80) |")
    else:
        lines.append(f"| Address | RS485 device {ctl.address} (0x{0x80 + ctl.address:02X}) |")
    lines.append(f"| Model | {_md_escape(ctl.model) or '-'} |")
    lines.append(f"| Firmware | {_md_escape(ctl.firmware) or '-'} |")
    for win, value in sorted(ctl.identity.items()):
        lines.append(f"| Window {win} ({IDENTITY_WINDOW_NAMES.get(win, 'identity')}) | `{_md_escape(value)}` |")
    lines.append("")

    # ---- status
    lines += ["## Status", ""]
    code = "" if state.status_code is None else f" (code {state.status_code})"
    lines.append(f"**Pump status: {state.status_text}**{code}  ")
    if state.error_code is None:
        lines.append("Error code: not available  ")
    elif state.family == Family.LEGACY:
        lines.append(f"Error code: {state.error_code}  ")
    else:
        lines.append(f"Error code: {state.error_code} (0x{state.error_code:X}, bits `{state.error_code:012b}`)  ")
    lines.append("")
    if state.error_texts:
        lines.append("### Active errors")
        lines.append("")
        lines += [f"- ⚠ {e}" for e in state.error_texts]
        lines.append("")
    elif state.error_code is not None:
        lines += ["No error reported.", ""]
    if state.warning_texts:
        lines += ["### Warnings", ""] + [f"- {w}" for w in state.warning_texts] + [""]

    # ---- readings by group
    lines += ["## Readings", ""]
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    groups = sorted({r.group for r in state.readings}, key=lambda g: order.get(g, 99))
    for group in groups:
        lines += [f"### {GROUP_TITLES.get(group, group.title())}", "",
                  "| Window | Parameter | Value | Unit | Raw |", "|---|---|---|---|---|"]
        for r in state.readings:
            if r.group != group:
                continue
            flag = " ⚠" if r.alarm else ""
            lines.append(f"| {r.key} | {_md_escape(r.name)} | {_md_escape(r.value)}{flag} | {r.unit} | `{_md_escape(r.raw)}` |")
        lines.append("")

    # ---- diagnostics
    lines += ["## Diagnostics", ""]
    if state.unsupported:
        lines.append("Windows reported as unknown by this controller (not polled): "
                     + ", ".join(str(w) for w in sorted(state.unsupported)) + "  ")
    else:
        lines.append("All requested windows were answered.  ")
    if state.comm_errors:
        lines += ["", "Communication problems during this read-out:", ""] + [f"- {e}" for e in state.comm_errors]
    else:
        lines.append("No communication problems during this read-out.")
    lines.append("")

    if log_text:
        lines += ["## Session log", "", "```text", log_text.rstrip(), "```", ""]

    lines += ["---", "Decoding follows the Agilent / Varian controller manuals; see PROTOCOLS.md in the application folder. "
              "Error bits 3 and 4 of window 206 are model dependent.", ""]
    return "\n".join(lines)


def default_report_name(state: ControllerState) -> str:
    port = state.controller.port.rsplit("/", 1)[-1].replace("\\", "_").replace(":", "")
    return f"pump_report_{port}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(state.timestamp))}.md"
