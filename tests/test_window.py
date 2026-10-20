import sys
import threading
import types

from podbridge import window


class FakeState:
    def __init__(self):
        self.subscribers = []

    def subscribe(self, fn):
        self.subscribers.append(fn)


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
