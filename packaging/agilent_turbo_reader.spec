# PyInstaller spec for the Agilent turbo controller reader.
#
# Build from the repo root (PyInstaller does not cross-compile: build on the
# target OS, or let the release workflow do it):
#
#     uv sync --group package
#     uv run pyinstaller packaging/agilent_turbo_reader.spec
#
# Result: dist/AgilentTurboReader(.exe) — one-file, windowed, x86-64.

import os

from PyInstaller.utils.hooks import collect_submodules

# The spec lives in packaging/; the app lives one level up.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# pyserial picks its port-enumeration backend at runtime by platform, so the
# Windows one (serial.tools.list_ports_windows) is invisible to static analysis;
# pull every serial submodule in or the frozen app can't list COM ports at all.
hiddenimports = collect_submodules("serial")
hiddenimports += collect_submodules("agilent_turbo") + collect_submodules("agilent_gui")

datas = []
_ICON_PNG = os.path.join(ROOT, "agilent_gui", "assets", "app.png")
if os.path.exists(_ICON_PNG):
    datas.append((_ICON_PNG, "agilent_gui/assets"))

# Keep only PySide6 (no second toolkit), and none of the heavy optional stacks.
excludes = [
    "PyQt5", "PyQt6", "PySide2",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtMultimedia",
    "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtQml", "PySide6.QtQuick",
    "tkinter", "matplotlib", "IPython", "pytest", "numpy",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# Never ship the host graphics stack, the GCC runtime or the xcb family (Linux):
# the target's GPU drivers must load against the TARGET's own libGL/libstdc++/
# libxcb family. Bundling the builder's copies makes Qt's GLX init abort on
# newer distros — the canonical frozen-Qt-on-Linux failure (see ferroDAC).
_NEVER_BUNDLE = ("libGL.so", "libGLX", "libEGL.so", "libGLdispatch",
                 "libOpenGL.so", "libgbm", "libdrm", "libglapi",
                 "libstdc++", "libgcc_s", "libxcb")
a.binaries = [(name, path, kind) for (name, path, kind) in a.binaries
              if not os.path.basename(name).startswith(_NEVER_BUNDLE)]

pyz = PYZ(a.pure)

_ICO = os.path.join(ROOT, "packaging", "app.ico")

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AgilentTurboReader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,        # set True for a debug console
    disable_windowed_traceback=False,
    icon=_ICO if os.path.exists(_ICO) else None,
)
