# Agilent turbo controller reader dev tasks. `make help` lists targets. Everything
# python runs through `uv run` (the locked .venv from pyproject/uv.lock).
.DEFAULT_GOAL := help
QT := QT_QPA_PLATFORM=offscreen
UV := uv run

.PHONY: help sync test test-core test-ui run cli sim build icon

help:  ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

sync:  ## create/refresh the locked environment (.venv)
	uv sync --all-groups

test:  ## run the whole suite (protocol + simulator + UI smoke, offscreen Qt)
	$(QT) $(UV) pytest -ra

test-core:  ## fast gate: protocol + simulator tests (no Qt)
	$(UV) pytest -m "not ui" -ra

test-ui:  ## UI smoke tests only (offscreen Qt)
	$(QT) $(UV) pytest -m ui -ra

run:  ## launch the GUI
	$(UV) python main.py

cli:  ## terminal read-out (auto scan); ARGS="--port /dev/ttyUSB0 --once"
	$(UV) python -m agilent_turbo.cli $(ARGS)

sim:  ## start a simulated controller on a pty; ARGS="--status 6 --error 130"
	$(UV) python -m agilent_turbo.simulator $(ARGS)

build:  ## one-file binary for THIS OS into dist/ (needs: uv sync --group package)
	$(QT) $(UV) pyinstaller packaging/agilent_turbo_reader.spec

icon:  ## regenerate agilent_gui/assets/app.png and packaging/app.ico
	$(UV) --group icon python packaging/make_icon.py
