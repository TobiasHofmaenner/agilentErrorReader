# Agilent turbo pump controller reader

PySide6 desktop tool that finds an Agilent (ex Varian) turbo pump controller on any serial port,
at any baud rate / RS485 address, and shows its status, error code and measurements. It can
export a Markdown report of everything it read.

* `PROTOCOLS.md` – comparison of the serial protocols across all controller manuals (`manuals/`).
* `agilent_turbo/` – protocol, transport, scanner, reader, report, pty simulator, CLI.
* `agilent_gui/` – PySide6 GUI.
* `packaging/` – PyInstaller spec and icon generator for the release binaries.

## Run from source

The environment is locked with [uv](https://docs.astral.sh/uv/) (`pyproject.toml` + `uv.lock`),
the same set CI tests and the release binaries use:

```bash
uv sync                          # creates .venv with the locked PySide6 / pyserial
uv run main.py                   # GUI
uv run python -m agilent_turbo.cli                     # terminal read-out (auto scan)
uv run python -m agilent_turbo.cli --port /dev/ttyUSB0 --baud 9600 --once --report pump.md
```

`make help` lists the shortcuts (`make run`, `make test`, `make build`, …).
Without uv, the system packages work too: `python3 main.py` needs PySide6 and pyserial installed.

On Linux the user must be allowed to open serial ports (`sudo usermod -aG dialout $USER`, re-login).

## Test without hardware

```bash
uv run python -m agilent_turbo.simulator --family modern --status 6 --error 130   # prints /dev/pts/N
uv run python -m agilent_turbo.cli --port /dev/pts/N --baud 9600 --once           # or type /dev/pts/N in the GUI
make test                                                                         # protocol + simulator + UI smoke
```

## How detection works

1. Every port, every baud rate (9600 first): read window 205 with RS232 address 0x80.
2. Every port at 9600: legacy letter protocol (`I` + CRC).
3. Every port at 9600: RS485 addresses 1..31.
4. Remaining baud rates for the letter protocol (and, with "deep scan", for RS485 addresses).

Once a controller answers, windows 503/504/404/319 decide whether it is a modern Agilent controller
or a legacy Varian HT/ICE controller (different status / error semantics); the family can be forced
in the GUI. Windows the controller reports as unknown are dropped from the poll list.

## Releases

Pushing a tag builds one-file binaries for Windows and Linux and attaches them to a GitHub Release
(`.github/workflows/build.yml`); `-rc` tags become pre-releases. The version shown in the app is
stamped from the tag, nothing is bumped by hand:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

`.github/workflows/tests.yml` runs the suite on every push (Linux, Windows, and offscreen Qt UI smoke).
A local binary for the current OS: `uv sync --group package && make build` → `dist/AgilentTurboReader`.
