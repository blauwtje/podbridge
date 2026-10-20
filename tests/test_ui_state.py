import json

import pytest

from podbridge.events import Event
from podbridge.ui_state import Api, UiState


def test_initial_steps_ordered_pending():
    snap = UiState().snapshot()
    names = [s["name"] for s in snap["steps"]]
    assert names == ["checks", "driver", "firmware", "radio", "pairing", "connecting", "connected"]
    assert {s["status"] for s in snap["steps"]} == {"pending"}
    assert snap["error"] is None and snap["notices"] == [] and snap["airpods"] == {}


def test_notice_and_connecting_attempt():
    state = UiState()
    state.apply(Event("connect_retry", "retrying", "wait", attempt=3))
    snap = state.snapshot()
    assert snap["notices"][0]["kind"] == "connect_retry"
    assert snap["steps"][5]["attempt"] == 3
    assert snap["error"] is None


def test_fatal_sets_error_and_fails_active_step():
    state = UiState()
    state.set_step("radio", "active")
    state.apply(Event("radio_timeout", "no answer", "replug"))
    snap = state.snapshot()
    assert snap["error"]["message"] == "no answer"
    assert snap["error"]["next_step"] == "replug"
    assert "radio_timeout" in snap["error"]["details"]
    assert snap["steps"][3]["status"] == "failed"


def test_unknown_kind_becomes_notice():
    state = UiState()
    state.apply(Event("nonsense", "odd"))
    assert state.snapshot()["notices"][0]["message"] == "odd"


def test_set_step_rejects_bad_input():
    with pytest.raises(ValueError):
        UiState().set_step("nope", "done")


def test_subscribe_and_json_safe():
    state = UiState()
    seen = []
    state.subscribe(seen.append)
    state.set_airpods({"battery": {"left": 80}, "raw": b"x"})
    state.set_step("checks", "done")
    assert len(seen) == 2
    json.dumps(seen[-1])


def test_api_set_setting_and_details():
    calls = []
    state = UiState()
    api = Api(state, lambda k, v: calls.append((k, v)))
    assert api.set_setting("mode", 2) == {"ok": True, "error": None}
    assert calls == [("mode", 2)]

    def boom(k, v):
        raise RuntimeError("bad")

    assert Api(state, boom).set_setting("a", 1) == {"ok": False, "error": "bad"}
    state.apply(Event("failed", "boom", "retry"))
    assert "boom" in api.copy_details() and "retry" in api.copy_details()
    assert api.get_snapshot()["error"]["message"] == "boom"
