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
    """Downloaded firmware when `podbridge firmware` filled it, else the bundled folder."""
    downloaded = appdata_dir() / "firmware"
    if downloaded.is_dir() and any(downloaded.iterdir()):
        return downloaded
    return install_dir() / "firmware"
