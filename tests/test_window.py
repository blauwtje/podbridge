import sys
import threading
import time
import types
from pathlib import Path

from podbridge import paths, window


class FakeState:
    def __init__(self):
        self.subscribers = []
        self.stopped = []

    def subscribe(self, fn):
        self.subscribers.append(fn)

    def write_setting(self, key, value):
        pass

    def stop(self):
        self.stopped.append(True)


def fake_webview(monkeypatch, start=None):
    calls = {}
    win = types.SimpleNamespace(js=[], evaluate_js=lambda code: calls["win"].js.append(code))
    calls["win"] = win

    def create_window(title, url, **kw):
        calls["create"] = (title, url, kw)
        return win

    def default_start(func, **kw):
        calls["start"] = kw
        func()

    mod = types.SimpleNamespace(create_window=create_window, start=start or default_start)
    monkeypatch.setitem(sys.modules, "webview", mod)
    return calls


def test_open_false_without_webview2(monkeypatch):
    monkeypatch.setattr(window, "webview2_present", lambda: False)
    assert window.open(lambda: None, FakeState()) is False


def test_open_creates_window_runs_session_and_pushes(monkeypatch, tmp_path):
    monkeypatch.setattr(window, "webview2_present", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = fake_webview(monkeypatch)
    ran = threading.Event()
    state = FakeState()
    assert window.open(ran.set, state) is True
    assert ran.wait(2)
    title, _, kw = calls["create"]
    assert (title, kw["width"], kw["height"]) == ("podbridge", 420, 640)
    assert calls["start"]["storage_path"] == str(tmp_path / "podbridge" / "webview")
    state.subscribers[0]({"a": 1})
    assert calls["win"].js == ['window.__pb.emit({"a": 1})']


def test_open_false_when_start_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(window, "webview2_present", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def boom(func, **kw):
        raise RuntimeError("no gui")

    fake_webview(monkeypatch, start=boom)
    assert window.open(lambda: None, FakeState()) is False


def test_web_dir_prefers_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert window.web_dir() == tmp_path / "podbridge" / "web"


def test_web_dir_source_tree(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert window.web_dir().parts[-3:] == ("src", "podbridge", "web")


def source_tree(monkeypatch, tmp_path, built: bool) -> Path:
    """Point window.py at a fake source package whose UI is built or not."""
    package = tmp_path / "src" / "podbridge"
    package.mkdir(parents=True)
    if built:
        (package / "web").mkdir()
        (package / "web" / "index.html").write_text("source")
    monkeypatch.setattr(window, "__file__", str(package / "window.py"))
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    return package / "web"


def installed_ui(monkeypatch, tmp_path) -> Path:
    web = tmp_path / "install" / paths.BUNDLE_CONTENTS / "podbridge" / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text("installed")
    monkeypatch.setenv(paths.INSTALL_DIR_ENV, str(tmp_path / "install"))
    return web


def test_source_run_without_a_built_ui_uses_the_installed_one(monkeypatch, tmp_path):
    source_tree(monkeypatch, tmp_path, built=False)
    installed = installed_ui(monkeypatch, tmp_path)
    assert window.web_dir() == installed


def test_source_run_prefers_its_own_built_ui(monkeypatch, tmp_path):
    source = source_tree(monkeypatch, tmp_path, built=True)
    installed_ui(monkeypatch, tmp_path)
    assert window.web_dir() == source


def test_source_run_without_any_built_ui_names_the_source_folder(monkeypatch, tmp_path):
    source = source_tree(monkeypatch, tmp_path, built=False)
    monkeypatch.setenv(paths.INSTALL_DIR_ENV, str(tmp_path / "install"))
    assert window.web_dir() == source


def test_open_exposes_the_js_api(monkeypatch, tmp_path):
    monkeypatch.setattr(window, "webview2_present", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    calls = fake_webview(monkeypatch)
    state = FakeState()
    assert window.open(lambda: None, state) is True
    api = calls["create"][2]["js_api"]
    assert {"get_snapshot", "set_setting", "copy_details"} <= set(dir(api))


def test_closing_the_window_stops_the_session_and_waits_for_it(monkeypatch, tmp_path):
    monkeypatch.setattr(window, "webview2_present", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    fake_webview(monkeypatch)
    state = FakeState()
    finished = []

    def session():
        while not state.stopped:
            pass
        finished.append(True)

    assert window.open(session, state) is True
    assert finished == [True]


def test_a_failed_start_after_the_session_began_stops_it_and_logs_why(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(window, "webview2_present", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def boom(func, **kw):
        func()
        raise RuntimeError("no gui")

    fake_webview(monkeypatch, start=boom)
    state = FakeState()
    finished = []

    def session():
        while not state.stopped:
            pass
        finished.append(True)

    assert window.open(session, state) is False
    assert finished == [True] and "no gui" in caplog.text


def test_the_console_fallback_waits_for_a_session_that_is_slow_to_stop(monkeypatch, tmp_path):
    monkeypatch.setattr(window, "webview2_present", lambda: True)
    monkeypatch.setattr(window, "JOIN_TIMEOUT_S", 0.05)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    def boom(func, **kw):
        func()
        raise RuntimeError("no gui")

    fake_webview(monkeypatch, start=boom)
    state = FakeState()
    finished = []

    def session():
        while not state.stopped:
            time.sleep(0.01)
        time.sleep(0.3)
        finished.append(True)

    assert window.open(session, state) is False
    assert finished == [True]
