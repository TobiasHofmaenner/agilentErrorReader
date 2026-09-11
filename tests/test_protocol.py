"""Frame examples taken verbatim from the Agilent manuals."""
import unittest

from agilent_turbo.protocol import (
    ACK, CODE_UNKNOWN_WINDOW, build_letter_request, build_window_code_reply, build_window_data_reply,
    build_window_request, extract_window_frame, letter_crc, parse_letter_response, parse_window_response, CrcError,
)


class WindowProtocolTests(unittest.TestCase):
    def test_start_command_example(self):
        # TwisTorr 305 FS remote manual: START -> 02 80 30 30 30 31 31 03 42 33
        self.assertEqual(build_window_request(0, write=True, data="1"),
                         bytes.fromhex("02 80 30 30 30 31 31 03 42 33"))

    def test_soft_start_off_example(self):
        # 02 80 31 30 30 31 30 03 42 33
        self.assertEqual(build_window_request(100, write=True, data="0"),
                         bytes.fromhex("02 80 31 30 30 31 30 03 42 33"))

    def test_ack_reply_example(self):
        frame = bytes.fromhex("02 80 06 03 38 35")
        self.assertEqual(build_window_code_reply(ACK), frame)
        r = parse_window_response(frame)
        self.assertEqual(r.code, ACK)
        self.assertEqual(r.address, 0)
        self.assertFalse(r.has_data)

    def test_status_read_at_address_3(self):
        # request 02 83 32 30 35 30 03 38 37 ; reply carries data 000000 with CRC 87
        req = build_window_request(205, address=3)
        self.assertEqual(req, bytes.fromhex("02 83 32 30 35 30 03 38 37"))
        reply = bytes.fromhex("02 83 32 30 35 30 30 30 30 30 30 30 03 38 37")
        r = parse_window_response(reply)
        self.assertEqual((r.address, r.window, r.write, r.data), (3, 205, False, "000000"))
        self.assertEqual(build_window_data_reply(205, "000000", address=3), reply)

    def test_serial_type_read_example(self):
        # 02 83 35 30 34 30 03 38 31 -> reply 02 83 35 30 34 30 31 03 42 30
        self.assertEqual(build_window_request(504, address=3), bytes.fromhex("02 83 35 30 34 30 03 38 31"))
        r = parse_window_response(bytes.fromhex("02 83 35 30 34 30 31 03 42 30"))
        self.assertEqual((r.window, r.data), (504, "1"))

    def test_bad_crc_rejected(self):
        with self.assertRaises(CrcError):
            parse_window_response(bytes.fromhex("02 80 06 03 38 36"))

    def test_unknown_window_code(self):
        r = parse_window_response(build_window_code_reply(CODE_UNKNOWN_WINDOW, address=5))
        self.assertEqual((r.address, r.code, r.code_name), (5, CODE_UNKNOWN_WINDOW, "Unknown window"))

    def test_extract_frame_with_garbage_and_remainder(self):
        frame = build_window_data_reply(205, "000005")
        buf = b"\xff\x00" + frame + b"\x02\x80"
        got, rest = extract_window_frame(buf)
        self.assertEqual(got, frame)
        self.assertEqual(rest, b"\x02\x80")
        got, rest = extract_window_frame(rest)
        self.assertIsNone(got)
        self.assertEqual(rest, b"\x02\x80")


class LetterProtocolTests(unittest.TestCase):
    def test_crc_examples_from_301ag_manual(self):
        self.assertEqual(letter_crc(b"A"), 0xBF)
        self.assertEqual(letter_crc(b"B"), 0xBE)
        self.assertEqual(letter_crc(b"K"), 0xB5)
        self.assertEqual(letter_crc(bytes([0x06])), 0xFA)   # ACK
        self.assertEqual(letter_crc(bytes([0x15])), 0xEB)   # NACK

    def test_request_and_reply(self):
        self.assertEqual(build_letter_request("I"), b"I\xb7")
        self.assertEqual(parse_letter_response(bytes([0x06, 0xFA])), bytes([0x06]))
        with self.assertRaises(CrcError):
            parse_letter_response(bytes([0x06, 0xFB]))


if __name__ == "__main__":
    unittest.main()
