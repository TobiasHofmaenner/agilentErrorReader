import sys
import unittest

from agilent_turbo.reader import ControllerReader
from agilent_turbo.report import build_markdown_report, default_report_name
from agilent_turbo.scanner import Scanner
from agilent_turbo.simulator import SimulatedController
from agilent_turbo.transport import SerialTransport
from agilent_turbo.windows import Family


@unittest.skipIf(sys.platform == "win32", "pty is POSIX only")
class ReportTests(unittest.TestCase):
    def test_report_contains_all_sections(self):
        sim = SimulatedController(Family.MODERN, status=6, error=0b01000001)
        path = sim.serve_pty()
        self.addCleanup(sim.stop)
        ctl = Scanner(ports=[path], baudrates=(9600,), try_letter=False, scan_rs485=False).scan()[0]
        with SerialTransport(path, 9600) as t:
            state = ControllerReader(t, ctl).read_state()
        md = build_markdown_report(state, log_text="12:00:00 Scan started.")
        for needle in ("# Agilent turbo pump controller report", "## Connection", f"`{path}`", "9600 baud",
                       "**Pump status: Fail** (code 6)", "Error code: 65 (0x41", "- ⚠ No connection to pump",
                       "- ⚠ Short circuit", "### Measurements", "| 203 | Driving frequency | 1010 | Hz | `001010` |",
                       "### Identification", "X350864001", "## Diagnostics", "228", "## Session log", "Scan started."):
            self.assertIn(needle, md)
        self.assertTrue(default_report_name(state).startswith("pump_report_"))
        self.assertTrue(default_report_name(state).endswith(".md"))


if __name__ == "__main__":
    unittest.main()
