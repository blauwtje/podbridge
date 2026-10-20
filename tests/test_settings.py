import json

import pytest

from podbridge import aap, aap_inbound, settings
from podbridge.settings import CATALOG, CONFIRM_TIMEOUT_S, SettingsState


def test_catalog_has_unique_keys_and_apple_only_entries():
    keys = [s.key for s in CATALOG]
    assert len(keys) == len(set(keys))
    assert {s.key for s in CATALOG if s.apple_only} == {
        "firmware_update", "find_my", "hearing_test", "ear_tip_fit_test"}
    assert not {0x29, 0x37, 0x38, 0x14, 0x15, 0x20, 0x36, 0x39, 0x41, 0x3A} & {s.id for s in CATALOG}


def test_write_is_pending_then_confirmed_by_echo():
    st = SettingsState()
    frame = st.write("conversation_awareness", 2, 10.0)
    assert frame == aap.CONVERSATIONAL_AWARENESS_OFF
    assert st.settings["conversation_awareness"] == {"value": 2, "status": "pending"}
    st.apply(frame)
    assert st.settings["conversation_awareness"] == {"value": 2, "status": "confirmed"}
    st.tick(99.0)
    assert st.settings["conversation_awareness"]["status"] == "confirmed"


def test_timeout_reverts_to_last_confirmed_as_unsupported():
    st = SettingsState()
    st.apply(aap.control(0x28, 1))
    st.write("conversation_awareness", 2, 10.0)
    st.tick(10.0 + CONFIRM_TIMEOUT_S - 0.1)
    assert st.settings["conversation_awareness"]["status"] == "pending"
    st.tick(10.0 + CONFIRM_TIMEOUT_S)
    assert st.settings["conversation_awareness"] == {"value": 1, "status": "unsupported"}


def test_mismatched_echo_reverts():
    st = SettingsState()
    st.apply(aap.control(0x0D, 2))
    st.write("listening_mode", 3, 0.0)
    st.apply(aap.control(0x0D, 2))
    assert st.settings["listening_mode"] == {"value": 2, "status": "unsupported"}


def test_unsolicited_echo_updates_value():
    st = SettingsState()
    st.apply(aap.control(0x0D, 4))
    assert st.settings["listening_mode"] == {"value": 4, "status": "confirmed"}


def test_two_byte_setting_writes_and_confirms_both_bytes():
    st = SettingsState()
    frame = st.write("mute_end_call", (0x22, 0x03), 0.0)
    assert frame == aap.control(0x24, 0x22, 0x03)
    st.apply(frame)
    assert st.settings["mute_end_call"] == {"value": (0x22, 0x03), "status": "confirmed"}


@pytest.mark.parametrize("key,value", [
    ("listening_mode", 5), ("press_hold_cycle", 1), ("press_hold_cycle", 3 | 16),
    ("adaptive_level", 30), ("tone_volume", 14), ("tone_volume", 101),
    ("press_hold_action", 1), ("find_my", 1), ("nope", 1)])
def test_invalid_writes_raise_and_stay_unsent(key, value):
    st = SettingsState()
    with pytest.raises(ValueError):
        st.write(key, value, 0.0)
    assert st.settings == {}


def test_valid_bitmask_and_range():
    st = SettingsState()
    assert st.write("press_hold_cycle", 6, 0.0) == aap.control(0x1A, 6)
    assert st.write("tone_volume", 15, 0.0) == aap.control(0x1F, 15)


def test_inbound_events_and_hearing_assist_visibility():
    st = SettingsState()
    st.apply(aap_inbound.Battery(4, 80, 1))
    st.apply(aap_inbound.EarState(0, 1))
    st.apply(aap_inbound.DeviceInfo("n", "m", "s", "f"))
    assert st.battery[4].level == 80 and st.ear.secondary == 1 and st.device.serial == "s"
    assert not st.visible("hearing_assist") and st.visible("listening_mode")
    st.apply(aap.control(0x2C, 1))
    assert st.visible("hearing_assist")


def test_ca_choice_round_trip_and_format(tmp_path):
    path = tmp_path / "podbridge" / "settings.json"
    assert settings.load_ca_choice(path) is None
    settings.save_ca_choice(1, path)
    assert json.loads(path.read_text()) == {"conversation_awareness": 1}
    assert settings.load_ca_choice(path) == 1


@pytest.mark.parametrize("text", ["", "{", "[]", "{}", '{"conversation_awareness": 3}',
                                  '{"conversation_awareness": true}', '{"conversation_awareness": "1"}'])
def test_ca_choice_corrupt_reads_none(tmp_path, text):
    path = tmp_path / "settings.json"
    path.write_text(text)
    assert settings.load_ca_choice(path) is None


def test_ca_choice_default_path_uses_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    settings.save_ca_choice(2)
    assert (tmp_path / "podbridge" / "settings.json").is_file()
    assert settings.load_ca_choice() == 2
    with pytest.raises(ValueError):
        settings.save_ca_choice(3)


def test_view_matches_what_the_window_reads():
    from podbridge import aap_inbound
    from podbridge.settings import SettingsState

    state = SettingsState()
    assert state.view() == {}
    for event in (
        aap_inbound.Battery(4, 80, 1),
        aap_inbound.Battery(8, 50, 2),
        aap_inbound.EarState(0, 0),
        aap_inbound.DeviceInfo("Pods", "Pro", "SN1", "8A"),
    ):
        state.apply(event)
    state.write("listening_mode", 2, 0.0)
    view = state.view()
    assert view["battery"] == {"left": {"level": 80, "charging": True}, "case": {"level": 50, "charging": False}}
    assert view["in_ear"] == {"left": True, "right": True}
    assert (view["name"], view["firmware"]) == ("Pods", "8A")
    assert view["settings"]["listening_mode"] == {"value": 2, "status": "pending"}
    assert "enrolled" not in view
