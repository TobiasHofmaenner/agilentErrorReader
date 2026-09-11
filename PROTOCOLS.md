# Agilent / Varian turbo pump controller serial protocols – comparison

Source: 33 user manuals downloaded into `manuals/` (text extracted to `manuals/txt/`).
Agilent's own CDN (agilent.com) blocks scripted downloads, so the copies come from the
idealvac.com and ajvs.com mirrors; document numbers (87-90x-xxx-xx) are the Agilent originals.

## Bottom line

There are **two protocol families**, and the newer family has **two flavours of value semantics**:

| Family | Framing | Used by | Status / error semantics |
|---|---|---|---|
| **A. Window protocol** (modern) | `STX ADDR WIN(3) COM DATA ETX CRC(2)` | TwisTorr 74 FS, 84 FS, 304 FS, 305 FS (on‑board / remote / rack), 305‑IC, 704 FS (Medium‑TMP controllers), TwisTorr Medium‑TMP rack/on‑board, Turbo‑V 81‑AG rack & Navigator, 301 Navigator, 551/701 Navigator, 1001 Navigator/rack, 750/850 TwisTorr, Mini‑TASK, Turbo‑V 81 PCB | 205 = 0 Stop, 1 Waiting interlock, 2 Starting/Ramp, 3 Auto‑tuning, 4 Braking, 5 Normal, 6 Fail (750/850: 7 Leak check). 206 = **bit mask** |
| **A'. Window protocol** (legacy Varian) | identical framing | RS485 side of Turbo‑V 300HT, 550, 700HT, 1000HT, 700 ICE, 1000 ICE, 2000 ICE | 205 = 0 Stop, 1 Waiting interlock, 2 Starting, 3 Normal, 4 High load, 5 Failure, 6 Approaching. 206 = **enumeration** 0..7. 200 in A (not mA), 203 in krpm (not Hz) |
| **B. Letter protocol** | 1 ASCII letter (+ data) + 1 byte CRC, no framing | Turbo‑V 301‑AG rack, Turbo‑V 70 controller, RS232 side of Turbo‑V 300HT/550/700HT/1000HT | `I` = status byte (0 Stop, 1 Waiting interlock, 2 Starting, 3 Normal, 4/5 High load, 6 Failure, 7 Approaching low speed) |

So: **one implementation of the window protocol covers every controller Agilent currently sells**, but a
robust reader needs (1) a second decoding table for the legacy Varian HT/ICE controllers on RS485 and
(2) a second, tiny protocol driver for the letter‑based controllers. The app implements all three.

## A. Window protocol details (identical in every manual checked)

* Serial: 8 data bits, no parity, 1 stop bit. Baud 600/1200/2400/4800/9600 (older) plus 19200/38400
  (Medium‑TMP, 1001 rack). **Factory default 9600.** Window 108 selects baud (0=600 … 4=9600, 5=19200, 6=38400).
* `STX` = 0x02, `ADDR` = 0x80 (RS232) or 0x80 + device number 0..31 (RS485), `WIN` = 3 ASCII digits,
  `COM` = '0' read / '1' write, `DATA` = L: 1 char, N: 6 chars right justified with '0', A: 10 chars,
  `ETX` = 0x03, `CRC` = XOR of every byte after STX up to and including ETX, sent as two upper‑case hex ASCII chars.
* Master/slave; host sends, controller answers. Read answer = same frame with DATA filled.
  Write answer / error answer = `STX ADDR <code> ETX CRC` with code 0x06 ACK, 0x15 NACK,
  0x32 unknown window, 0x33 data type error, 0x34 out of range, 0x35 window disabled.
* Verified against the manual examples: START request `02 80 30 30 30 31 31 03 42 33` (CRC "B3"),
  ACK reply `02 80 06 03 38 35` (CRC "85"), status reply at address 3 `02 83 32 30 35 30 30 30 30 30 30 30 03 38 37` (CRC "87").
* Legacy V550/HT/ICE manuals add `ADDR` = 0xFF broadcast (no answer).
* On RS485 half‑duplex adapters the request may be echoed; the driver discards an echo of its own frame.

### Windows read by the app (modern semantics)

| Win | Meaning | Unit / decoding |
|---|---|---|
| 205 | Pump status | enum above |
| 206 | Error code | bit 0 no connection, 1 pump over‑temp, 2 controller over‑temp, 3 power fail / Vdc undervoltage / run‑up time (model dependent), 4 aux fail / output fail / override / run‑up time (model dependent), 5 over‑voltage, 6 short circuit, 7 too high load; 305‑IC adds 8 rotor locked, 10 body HW over‑temp, 11 run‑up time |
| 228 | Warning code (305‑IC only) | bit mask, see `windows.py` |
| 200 / 201 / 202 | current mA / voltage V / power W | numeric |
| 203 / 226 | driving frequency Hz / rotation speed rpm | numeric |
| 204 / 211 / 216 | pump / controller heatsink / controller air temperature °C | numeric |
| 224 / 257 | pressure (X.X E XX) / gauge status | when a gauge is fitted |
| 300 / 301 / 302 / 307 | cycle time min / cycle number / pump life h / controller life h | numeric |
| 319 / 320 / 323 / 400 / 402 / 404 / 406 / 407 | controller model, pump model, serial number, firmware CRCs & codes | alphanumeric, model dependent |
| 0 / 1 / 8 / 100 / 106 / 107 / 108 / 110 / 117 / 120 / 121 / 155 / 157 / 503 / 504 | start‑stop, low speed, serial/remote, soft start, cooling, active stop, baud, interlock, low‑speed Hz, set frequency, max frequency, power limit, gas load, RS485 address, RS232/RS485 | configuration |

Windows that a given controller does not implement answer 0x32 (unknown window); the app marks them
unsupported and stops polling them.

### Distinguishing modern (A) from legacy (A') at run time

Legacy HT/ICE tables end at window 402 and have no 5xx windows. The app reads 503 / 504 / 404 / 319:
if any returns data the controller is treated as modern, otherwise as legacy. The family can also be
forced in the GUI.

## B. Letter protocol details (Turbo‑V 301‑AG rack, Turbo‑V 70, HT/ICE on RS232)

* 8N1, 600–9600 baud, factory 9600. **Requests must be at least 1 s apart** at ≥2400 baud or the
  controller hangs, so the scanner and reader pace letter traffic to 1 s.
* Request = letter + CRC, CRC = sum of the preceding bytes with inverted sign (two's complement):
  'A' (0x41) → 0xBF, ACK (0x06) → 0xFA, NACK (0x15) → 0xEB.
* Letters: A start, B stop, C/D low speed on/off, E operational parameters (22 bytes),
  F counters reset, G parameters reading (11 bytes), H parameters writing, I status (2 bytes),
  J numerical readings (5 bytes: current 0‑255 → 2.5 A full scale, voltage 0‑255 → 130 V, speed krpm,
  temperature °C with 255 = fail, CRC), K counters (11 bytes: cycle time, pump life, cycle number, CRC).
* Status byte layout is "--XXXXXX": low bits = status code, then R2 and R1 set‑point flags.
  The multi‑byte integer byte order is not stated in the manual; the app assumes big‑endian.

## Manuals with no serial section

Turbo‑V 81‑M, 81‑T and Turbo‑V 250 manuals are pump manuals and refer to the 81‑AG / other controllers.
The TwisTorr 704 FS pump manual refers to the Medium‑TMP rack / on‑board controller manual for the windows.
The 305‑IC manual notes that the "485P" (Profinet) variant uses a different, undocumented interface.
