"""Firmware choice: only an EPATCH v1 file (what the pinned Bumble reads) is ever used."""
import asyncio
import hashlib
import os
import re
from pathlib import Path

import pytest

from podbridge import app, doctor, host, paths

V1 = b"Realtech" + bytes(range(256)) * 4
V2 = b"RTBTCore" + bytes(range(256)) * 4
V1_OTHER = b"Realtech" + b"\xff" * 1024  # right header, not the pinned file
HTML = b"<!DOCTYPE html><html><body>404 Not Found</body></html>"
NAME = "rtl8852bu_fw.bin"
WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "release.yml"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setattr(paths, "install_dir", lambda: tmp_path / "install")
    monkeypatch.setattr(app, "FIRMWARE_SHA256", hashlib.sha256(V1).hexdigest(), raising=False)
    monkeypatch.delenv("BUMBLE_RTK_FIRMWARE_DIR", raising=False)
    return tmp_path


def appdata_fw():
    return paths.appdata_dir() / "firmware"


def install_fw(tmp_path):
    return tmp_path / "install" / "firmware"


def put(folder, data, name=NAME):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)


def serve(monkeypatch, body):
    """Fake network: every download returns `body` (or raises it when it is an exception)."""
    urls = []

    def download(url):
        urls.append(url)
        if isinstance(body, Exception):
            raise body
        return body

    monkeypatch.setattr(app, "download", download)
    return urls


def stage():
    return asyncio.run(doctor.firmware_files(doctor.Context()))


def set_aside(folder):
    """Files kept next to the firmware file under another name."""
    return [p for p in folder.iterdir() if p.name != NAME and p.name.startswith(NAME)]


def test_pinned_source_is_a_commit_and_a_sha256():
    assert re.search(r"\?id=[0-9a-f]{40}$", app.FIRMWARE_URL.format(name=NAME))
    assert re.fullmatch(r"[0-9a-f]{64}", app.FIRMWARE_SHA256)


def test_workflow_pins_the_same_commit_and_sha256(monkeypatch):
    monkeypatch.undo()  # the autouse fixture swaps FIRMWARE_SHA256 for the fake file's
    text = WORKFLOW.read_text(encoding="utf-8")
    commit = re.search(r"id=([0-9a-f]{40})", app.FIRMWARE_URL).group(1)
    assert commit in text
    assert app.FIRMWARE_SHA256 in text


def test_v2_in_appdata_is_set_aside_and_replaced_by_the_download(env, monkeypatch):
    put(appdata_fw(), V2)
    urls = serve(monkeypatch, V1)
    assert stage() == str(appdata_fw())
    assert (appdata_fw() / NAME).read_bytes() == V1
    assert [p.read_bytes() for p in set_aside(appdata_fw())] == [V2]
    assert len(urls) == 1


def test_v2_in_appdata_falls_back_to_the_installer_copy(env, monkeypatch):
    put(appdata_fw(), V2)
    put(install_fw(env), V1)
    urls = serve(monkeypatch, V1)
    assert stage() == str(install_fw(env))
    assert urls == []
    assert [p.read_bytes() for p in set_aside(appdata_fw())] == [V2]
    assert host.use_firmware_folder() == str(install_fw(env))
    assert os.environ["BUMBLE_RTK_FIRMWARE_DIR"] == str(install_fw(env))


def test_html_in_appdata_is_rejected_and_replaced(env, monkeypatch):
    put(appdata_fw(), HTML)
    serve(monkeypatch, V1)
    assert stage() == str(appdata_fw())
    assert (appdata_fw() / NAME).read_bytes() == V1
    assert [p.read_bytes() for p in set_aside(appdata_fw())] == [HTML]


def test_short_file_in_appdata_is_rejected(env, monkeypatch):
    put(appdata_fw(), b"Real")
    serve(monkeypatch, V1)
    assert stage() == str(appdata_fw())
    assert (appdata_fw() / NAME).read_bytes() == V1


def test_sha_mismatch_is_rejected_even_with_the_right_header(env, monkeypatch):
    put(appdata_fw(), V1_OTHER)
    serve(monkeypatch, V1)
    assert stage() == str(appdata_fw())
    assert (appdata_fw() / NAME).read_bytes() == V1
    assert [p.read_bytes() for p in set_aside(appdata_fw())] == [V1_OTHER]


def test_a_download_that_fails_validation_is_never_written(env, monkeypatch):
    serve(monkeypatch, V1_OTHER)
    with pytest.raises(doctor.StageFailed, match="sha256"):
        stage()
    assert not (appdata_fw() / NAME).exists()
    assert not appdata_fw().exists() or list(appdata_fw().iterdir()) == []


def test_valid_v1_in_appdata_is_used_untouched(env, monkeypatch):
    put(appdata_fw(), V1)
    put(install_fw(env), V1)
    urls = serve(monkeypatch, V1)
    assert stage() == str(appdata_fw())
    assert urls == []
    assert [p.name for p in appdata_fw().iterdir()] == [NAME]


def test_valid_installer_copy_is_used_without_a_download(env, monkeypatch):
    put(install_fw(env), V1)
    urls = serve(monkeypatch, V1)
    assert stage() == str(install_fw(env))
    assert urls == []


def test_download_goes_to_the_pinned_url_and_leaves_no_temp_file(env, monkeypatch):
    urls = serve(monkeypatch, V1)
    stage()
    assert urls == [app.FIRMWARE_URL.format(name=NAME)]
    assert [p.name for p in appdata_fw().iterdir()] == [NAME]


def test_every_source_bad_gives_a_message_that_names_file_reason_and_remedy(env, monkeypatch):
    put(appdata_fw(), V2)
    put(install_fw(env), HTML)
    serve(monkeypatch, V2)
    with pytest.raises(doctor.StageFailed) as info:
        stage()
    message = str(info.value)
    assert NAME in message
    assert "EPATCH v2" in message
    assert "Bumble reads only v1" in message
    assert re.search(r"internet|reinstall", message)
    assert not (appdata_fw() / NAME).exists()
    assert [p.read_bytes() for p in set_aside(appdata_fw())] == [V2]


def test_nothing_anywhere_and_offline_keeps_the_internet_hint(env, monkeypatch):
    serve(monkeypatch, OSError("offline"))
    with pytest.raises(doctor.StageFailed, match="offline.*internet.*open podbridge again"):
        stage()


def test_firmware_dir_ignores_a_folder_that_is_only_non_empty(env):
    put(appdata_fw(), V2)
    put(appdata_fw(), b"config", "rtl8852bu_config.bin")
    assert paths.firmware_dir() == install_fw(env)
    put(appdata_fw(), V1)
    assert paths.firmware_dir() == appdata_fw()


def test_firmware_command_rejects_a_v2_download(env, monkeypatch, capsys):
    serve(monkeypatch, V2)
    assert app.firmware(None) == 1
    assert not (appdata_fw() / NAME).exists()
    assert "EPATCH v2" in capsys.readouterr().err


def test_firmware_command_saves_a_valid_download(env, monkeypatch):
    def download(url):
        if "config" in url:
            raise OSError("404")
        return V1

    monkeypatch.setattr(app, "download", download)
    assert app.firmware(None) == 0
    assert (appdata_fw() / NAME).read_bytes() == V1
