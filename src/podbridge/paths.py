"""Folders podbridge reads and writes."""
import os
import sys
from pathlib import Path


INSTALL_DIR_ENV = "PODBRIDGE_INSTALL_DIR"
BUNDLE_CONTENTS = "_internal"  # PyInstaller 6 onedir: sys._MEIPASS is <install>/_internal


def install_dir() -> Path:
    """Folder of the installed exe; from source, the install named by PODBRIDGE_INSTALL_DIR, else the source tree.

    A source run that sets PODBRIDGE_INSTALL_DIR to an installed podbridge uses that install's
    firmware, wdi-simple.exe and driver state, so it needs no build of its own. A frozen exe
    always uses its own folder and ignores the variable.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    override = os.environ.get(INSTALL_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def appdata_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "podbridge"


def firmware_dir() -> Path:
    """The downloaded folder when it holds a valid firmware file, else the folder the installer ships."""
    from podbridge import app

    downloaded = appdata_dir() / "firmware"
    if (downloaded / app.FIRMWARE_FILE).is_file() and app.file_problem(downloaded / app.FIRMWARE_FILE) is None:
        return downloaded
    return install_dir() / "firmware"
