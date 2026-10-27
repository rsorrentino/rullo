# -*- mode: python -*-
# PyInstaller build for Rullo: `uv run pyinstaller packaging/rullo.spec`.
# One folder per platform (faster start and fewer antivirus false alarms than
# one-file builds); the release workflow zips it.
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = collect_data_files("rullo")  # web UI, fonts, bundled routes and workouts
datas += collect_data_files("libusb_package")  # libusb for ANT+ sticks (Windows, macOS)
# bleak picks its backend (BlueZ, WinRT, CoreBluetooth) at runtime, and openant
# and pyusb load their USB backends dynamically, so collect them explicitly.
hiddenimports = (
    collect_submodules("bleak")
    + collect_submodules("openant")
    + collect_submodules("usb")
    + collect_submodules("rullo")
)

a = Analysis(
    ["launch.py"],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest", "mypy", "ruff", "fitdecode"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="rullo",
    console=True,  # the window shows the address and stays open while riding
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="rullo")
