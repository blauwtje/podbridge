"""Self-update: find the newest GitHub build, verify it and start its setup."""
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.request
from typing import NamedTuple

from . import paths

REPO = "blauwtje/podbridge"
API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
ASSET = "podbridge-setup.exe"
API_TIMEOUT_S = 10
DOWNLOAD_TIMEOUT_S = 60
TAG = re.compile(r"(build-[0-9]+|v[0-9A-Za-z.-]+)")
NUMBERED = re.compile(r"build-([0-9]+)")
DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
SETUP_ARGS = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG"]


class Release(NamedTuple):
    tag: str
    url: str
    sha256: str


class Skip(Exception):
    """A reason the update is skipped; run continues."""


def get_json(url: str):
    headers = {"User-Agent": "podbridge", "Accept": "application/vnd.github+json"}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=API_TIMEOUT_S) as r:
        return json.loads(r.read())


def download(url: str) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "podbridge"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as response:
        return response.read(), response.geturl()


def start(command: list[str]) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(command, creationflags=flags, close_fds=True)


def own_build() -> str:
    try:
        return (paths.install_dir() / "build.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def candidate(entry) -> tuple[str, Release] | None:
    """The release of one API entry, or None when it is not a valid update."""
    if not isinstance(entry, dict) or entry.get("draft"):
        return None
    tag = entry.get("tag_name")
    if not isinstance(tag, str) or not TAG.fullmatch(tag):
        return None
    assets = entry.get("assets")
    for asset in assets if isinstance(assets, list) else []:
        if isinstance(asset, dict) and asset.get("name") == ASSET:
            published = entry.get("published_at")
            url = asset.get("browser_download_url")
            digest = asset.get("digest")
            release = Release(tag, url if isinstance(url, str) else "", digest if isinstance(digest, str) else "")
            return (published if isinstance(published, str) else "", release)
    return None


def newest(releases) -> Release | None:
    if not isinstance(releases, list):
        raise Skip("unexpected releases reply")
    found = [c for c in map(candidate, releases) if c]
    return max(found, key=lambda c: c[0])[1] if found else None


def older(tag: str, build: str) -> bool:
    """True when both are numbered builds and `tag` is the lower number, so a newer local build is not downgraded."""
    tag_number, build_number = NUMBERED.fullmatch(tag), NUMBERED.fullmatch(build)
    return bool(tag_number and build_number and int(tag_number[1]) < int(build_number[1]))


def verified(release: Release, data: bytes, final_url: str) -> None:
    """Raise Skip unless the download is the asset GitHub describes."""
    if not release.url.startswith(DOWNLOAD_PREFIX):
        raise Skip("download url outside the repo")
    match = DIGEST.fullmatch(release.sha256)
    if not match:
        raise Skip("missing or malformed digest")
    if not final_url.startswith("https://"):
        raise Skip("download left https")
    if hashlib.sha256(data).hexdigest() != match.group(1):
        raise Skip("digest mismatch")


def install(release: Release) -> None:
    folder = paths.install_dir() / "update"
    setup = folder / ASSET
    if not release.url.startswith(DOWNLOAD_PREFIX):
        raise Skip("download url outside the repo")
    if not DIGEST.fullmatch(release.sha256):
        raise Skip("missing or malformed digest")
    folder.mkdir(parents=True, exist_ok=True)
    try:
        setup.write_bytes(b"")  # fails here, before a 60 s download, when the folder is read-only
        data, final_url = download(release.url)
        setup.write_bytes(data)
        verified(release, data, final_url)
        (folder / "attempted.txt").write_text(release.tag + "\n", encoding="utf-8")
    except BaseException:
        setup.unlink(missing_ok=True)
        raise
    start([str(setup), *SETUP_ARGS])


def apply(args) -> bool:
    """Start the setup of a newer build; True when it started, False to run on."""
    build = own_build()
    if (
        not getattr(sys, "frozen", False)
        or not build
        or os.environ.get("PODBRIDGE_NO_UPDATE")
        or getattr(args, "no_update", False)
    ):
        return False
    try:
        release = newest(get_json(API_URL))
        if release is None or release.tag == build or older(release.tag, build):
            return False
        attempted = paths.install_dir() / "update" / "attempted.txt"
        if attempted.is_file() and attempted.read_text(encoding="utf-8").strip() == release.tag:
            print(f"podbridge: update skipped ({release.tag} was already tried)")
            return False
        install(release)
    except (Skip, OSError, ValueError, http.client.HTTPException) as error:
        print(f"podbridge: update skipped ({error})")
        return False
    print(f"podbridge: installing {release.tag}; podbridge starts again when it is done")
    return True
