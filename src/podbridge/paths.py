"""Folders podbridge reads and writes."""
import os
import sys
from pathlib import Path


def install_dir() -> Path:
    """Folder of the installed exe, or the source tree when run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
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
