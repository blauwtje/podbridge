import hashlib
import http.client
import sys

import pytest

from podbridge import paths, update

DATA = b"setup-bytes"
DIGEST = "sha256:" + hashlib.sha256(DATA).hexdigest()
URL = update.DOWNLOAD_PREFIX + "build-5/podbridge-setup.exe"


def entry(tag="build-5", **fields):
    fields = {"published": "2026-01-02T00:00:00Z", "url": URL, "digest": DIGEST, "draft": False, "name": "podbridge-setup.exe"} | fields
    return {
        "tag_name": tag,
        "draft": fields["draft"],
        "published_at": fields["published"],
        "assets": [{"name": fields["name"], "browser_download_url": fields["url"], "digest": fields["digest"]}],
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths, "install_dir", lambda: tmp_path)
    monkeypatch.delenv("PODBRIDGE_NO_UPDATE", raising=False)
    (tmp_path / "build.txt").write_text("build-4\n")
    state = {"releases": [entry()], "calls": [], "started": [], "final": URL, "data": DATA}

    def get_json(url):
        state["calls"].append(url)
        if isinstance(state["releases"], Exception):
            raise state["releases"]
        return state["releases"]

    def download(url):
        state["calls"].append(url)
        return state["data"], state["final"]

    monkeypatch.setattr(update, "get_json", get_json)
    monkeypatch.setattr(update, "download", download)
    monkeypatch.setattr(update, "start", state["started"].append)
    state["dir"] = tmp_path
    return state


class Args:
    no_update = False


def test_newer_build_is_downloaded_verified_and_started(env):
    assert update.apply(Args()) is True
    setup = env["dir"] / "update" / "podbridge-setup.exe"
    assert setup.read_bytes() == DATA
    assert (env["dir"] / "update" / "attempted.txt").read_text().strip() == "build-5"
    assert env["started"] == [[str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/LOG"]]


def test_latest_published_valid_release_wins(env):
    env["releases"] = [
        entry("build-9", published="2026-03-01T00:00:00Z", digest="bad", draft=True),
        entry("build-7", published="2026-02-01T00:00:00Z", url=update.DOWNLOAD_PREFIX + "build-7/x"),
        entry("build-6", published="2026-01-01T00:00:00Z"),
        entry("evil;tag", published="2026-04-01T00:00:00Z"),
        entry("build-8", published="2026-05-01T00:00:00Z", name="other.exe"),
    ]
    assert update.newest(env["releases"]).tag == "build-7"


def test_same_build_does_nothing(env):
    env["releases"] = [entry("build-4")]
    assert update.apply(Args()) is False
    assert env["started"] == []


@pytest.mark.parametrize("digest", ["", "sha256:abc", "md5:" + "a" * 64, "sha256:" + "A" * 64])
def test_malformed_digest_starts_nothing(env, digest, capsys):
    env["releases"] = [entry(digest=digest)]
    assert update.apply(Args()) is False
    assert env["started"] == []
    assert not (env["dir"] / "update" / "podbridge-setup.exe").exists()
    assert "update skipped" in capsys.readouterr().out


def test_digest_mismatch_deletes_file_and_starts_nothing(env, capsys):
    env["data"] = b"tampered"
    assert update.apply(Args()) is False
    assert env["started"] == []
    assert not (env["dir"] / "update" / "podbridge-setup.exe").exists()
    assert not (env["dir"] / "update" / "attempted.txt").exists()
    assert "digest mismatch" in capsys.readouterr().out


def test_download_url_outside_repo_is_not_fetched(env):
    env["releases"] = [entry(url="https://evil.example/podbridge-setup.exe")]
    assert update.apply(Args()) is False
    assert env["calls"] == [update.API_URL]
    assert env["started"] == []


def test_redirect_to_http_is_rejected(env):
    env["final"] = "http://cdn.example/podbridge-setup.exe"
    assert update.apply(Args()) is False
    assert env["started"] == []
    assert not (env["dir"] / "update" / "podbridge-setup.exe").exists()


@pytest.mark.parametrize("error", [OSError("offline"), TimeoutError("slow"), ValueError("bad json"), http.client.IncompleteRead(b"")])
def test_api_error_fails_closed(env, error, capsys):
    env["releases"] = error
    assert update.apply(Args()) is False
    assert env["started"] == []
    assert "update skipped" in capsys.readouterr().out


def test_non_list_reply_fails_closed(env):
    env["releases"] = {"message": "rate limited"}
    assert update.apply(Args()) is False
    assert env["started"] == []


def test_tag_tried_before_is_not_tried_again(env):
    assert update.apply(Args()) is True
    env["started"].clear()
    env["calls"].clear()
    assert update.apply(Args()) is False
    assert env["started"] == []
    assert env["calls"] == [update.API_URL]


def test_failed_setup_start_is_skipped(env, monkeypatch):
    def boom(command):
        raise OSError("no exec")

    monkeypatch.setattr(update, "start", boom)
    assert update.apply(Args()) is False


def test_failed_attempt_write_starts_nothing(env):
    (env["dir"] / "update").mkdir()
    (env["dir"] / "update" / "attempted.txt").mkdir()
    assert update.apply(Args()) is False
    assert env["started"] == []
    assert not (env["dir"] / "update" / "podbridge-setup.exe").exists()


def test_no_network_call_when_disabled(env, monkeypatch):
    class NoUpdate:
        no_update = True

    monkeypatch.setenv("PODBRIDGE_NO_UPDATE", "1")
    assert update.apply(Args()) is False
    monkeypatch.delenv("PODBRIDGE_NO_UPDATE")
    assert update.apply(NoUpdate()) is False
    (env["dir"] / "build.txt").write_text("\n")
    assert update.apply(Args()) is False
    (env["dir"] / "build.txt").unlink()
    assert update.apply(Args()) is False
    monkeypatch.setattr(sys, "frozen", False)
    (env["dir"] / "build.txt").write_text("build-4\n")
    assert update.apply(Args()) is False
    assert env["calls"] == []


def test_non_list_assets_fails_closed(env, capsys):
    env["releases"] = [{"tag_name": "build-5", "assets": 5}]
    assert update.apply(Args()) is False
    assert env["started"] == []
    assert "update skipped" not in capsys.readouterr().out


def test_failed_setup_write_leaves_no_file(env, monkeypatch):
    def partial(self, data):
        with open(self, "wb") as handle:
            handle.write(data[:3])
        raise OSError("disk full")

    monkeypatch.setattr(type(env["dir"]), "write_bytes", partial)
    assert update.apply(Args()) is False
    assert env["started"] == []
    assert not (env["dir"] / "update" / "podbridge-setup.exe").exists()


def test_source_run_against_an_install_never_updates(tmp_path, monkeypatch):
    """dev.ps1 points a source run at the install, whose build.txt names a build: still no check."""
    (tmp_path / "build.txt").write_text("build-4\n")
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delenv("PODBRIDGE_NO_UPDATE", raising=False)
    monkeypatch.setenv(paths.INSTALL_DIR_ENV, str(tmp_path))
    monkeypatch.setattr(update, "get_json", lambda url: pytest.fail("checked GitHub"))
    monkeypatch.setattr(update, "start", lambda command: pytest.fail("started setup"))
    assert update.own_build() == "build-4"
    assert update.apply(Args()) is False
    assert not (tmp_path / "update").exists()
