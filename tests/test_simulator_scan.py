"""End-to-end: scanner + reader against the pty simulator (no hardware needed)."""
import sys
import unittest

from agilent_turbo.reader import ControllerReader
from agilent_turbo.scanner import Scanner
from agilent_turbo.simulator import SimulatedController
from agilent_turbo.transport import SerialTransport
from agilent_turbo.windows import Family


@unittest.skipIf(sys.platform == "win32", "pty is POSIX only")
class SimulatorScanTests(unittest.TestCase):
    def _scan(self, sim, **kw):
        path = sim.serve_pty()
        self.addCleanup(sim.stop)
        scanner = Scanner(ports=[path], baudrates=kw.pop("baudrates", (9600,)), log=print, **kw)
        return path, scanner.scan()

    def test_modern_controller_found_and_read(self):
        sim = SimulatedController(Family.MODERN, status=5, error=0b10000010)
        path, found = self._scan(sim, try_letter=False, scan_rs485=False)
        self.assertEqual(len(found), 1)
        ctl = found[0]
        self.assertEqual((ctl.family, ctl.address, ctl.baudrate), (Family.MODERN, 0, 9600))
        self.assertIn("X350864001", ctl.model)
        with SerialTransport(path, 9600) as t:
            reader = ControllerReader(t, ctl)
            state = reader.read_state()
        self.assertEqual(state.status_text, "Normal")
        self.assertEqual(state.error_texts, ["Pump over-temperature", "Too high load"])
        self.assertEqual(state.reading("203").value, "1010")
        self.assertEqual(state.reading("108").value, "9600 baud (code 4)")
        self.assertIn(228, state.unsupported)          # 305-IC only window
        self.assertEqual(state.severity, "fail")

    def test_legacy_controller_detected_by_missing_windows(self):
        sim = SimulatedController(Family.LEGACY, status=3, error=4)
        path, found = self._scan(sim, try_letter=False, scan_rs485=False)
        self.assertEqual(found[0].family, Family.LEGACY)
        with SerialTransport(path, 9600) as t:
            state = ControllerReader(t, found[0]).read_state()
        self.assertEqual(state.status_text, "Normal")
        self.assertEqual(state.error_texts, ["Too high load"])
        self.assertEqual(state.reading("203").unit, "krpm")

    def test_rs485_address_sweep(self):
        sim = SimulatedController(Family.MODERN, address=7, status=2)
        path, found = self._scan(sim, try_letter=False, scan_rs485=True)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0].family, found[0].address), (Family.MODERN, 7))
        with SerialTransport(path, 9600) as t:
            state = ControllerReader(t, found[0]).read_state()
        self.assertEqual(state.status_text, "Starting (ramp)")

    def test_letter_protocol_controller(self):
        sim = SimulatedController(Family.LETTER, status=3)
        path, found = self._scan(sim, try_letter=True, scan_rs485=False)
        self.assertEqual(found[0].family, Family.LETTER)
        with SerialTransport(path, 9600) as t:
            state = ControllerReader(t, found[0]).read_state()
        self.assertEqual(state.status_text, "Normal operation")
        self.assertEqual(state.reading("I.r1").value, "On")
        self.assertEqual(state.reading("J.speed").value, "56")
        self.assertEqual(state.reading("K.life").value, "12345")

    def test_nothing_on_port(self):
        import pty, os
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        scanner = Scanner(ports=[os.ttyname(slave)], baudrates=(9600,), scan_rs485=False, try_letter=False, log=print)
        self.assertEqual(scanner.scan(), [])


if __name__ == "__main__":
    unittest.main()
