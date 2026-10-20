import asyncio
import json

import pytest
from bumble.hci import HCI_AUTHENTICATION_FAILURE_ERROR, HCI_PIN_OR_KEY_MISSING_ERROR, HCI_Error

from podbridge import bond, host

ADDR = "AA:BB:CC:DD:EE:FF"


class Rec:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


class FakeHost:
    def __init__(self, exc):
        self.exc = exc

    async def connect(self, address):
        raise self.exc


def test_is_auth_failure():
    assert bond.is_auth_failure(HCI_Error(HCI_AUTHENTICATION_FAILURE_ERROR))
    assert bond.is_auth_failure(HCI_Error(HCI_PIN_OR_KEY_MISSING_ERROR))
    assert not bond.is_auth_failure(HCI_Error(0x04))
    assert not bond.is_auth_failure(ValueError())


def test_drop_key_removes_only_that_address(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"ns": {ADDR + "/P": {"a": 1}, "11:22:33:44:55:66/P": {"b": 2}}}))
    bond.drop_key(path, ADDR)
    assert json.loads(path.read_text()) == {"ns": {"11:22:33:44:55:66/P": {"b": 2}}}
    assert not (tmp_path / "keys.json.tmp").exists()


@pytest.mark.parametrize("content", [None, "not json"])
def test_drop_key_missing_or_unreadable(tmp_path, content):
    path = tmp_path / "keys.json"
    if content:
        path.write_text(content)
    bond.drop_key(path, ADDR)


def test_connect_bonded_drops_and_asks_repair(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"ns": {ADDR: {}}}))
    monkeypatch.setattr(host, "keys_path", lambda: path)
    rec = Rec()
    with pytest.raises(bond.RePairNeeded):
        asyncio.run(bond.connect_bonded(FakeHost(HCI_Error(HCI_PIN_OR_KEY_MISSING_ERROR)), ADDR, rec))
    assert json.loads(path.read_text()) == {"ns": {}}
    assert rec.events[0].kind == "bond_dropped"
    assert "hold the case button" in rec.events[0].next_step


def test_connect_bonded_passes_other_errors(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"ns": {ADDR: {}}}))
    monkeypatch.setattr(host, "keys_path", lambda: path)
    with pytest.raises(HCI_Error):
        asyncio.run(bond.connect_bonded(FakeHost(HCI_Error(0x04)), ADDR, Rec()))
    assert ADDR in json.loads(path.read_text())["ns"]


LINK_KEY = {"link_key": {"value": "00" * 16, "authenticated": False}}


def test_key_saved_finds_the_link_key_in_bumbles_namespace(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"__DEFAULT__": {ADDR + "/P": LINK_KEY}}))
    assert bond.key_saved(path, ADDR.lower())
    assert not bond.key_saved(path, "11:22:33:44:55:66")


@pytest.mark.parametrize(
    "content",
    [None, {}, {"__DEFAULT__": {ADDR + "/P": {}}}, {"one": {ADDR: LINK_KEY}, "two": {}}],
    ids=["no file", "empty", "no link key", "namespace Bumble does not read"],
)
def test_key_saved_is_false_without_a_usable_link_key(tmp_path, content):
    path = tmp_path / "keys.json"
    if content is not None:
        path.write_text(json.dumps(content))
    assert not bond.key_saved(path, ADDR)


@pytest.mark.parametrize("text", ["{not json", "[]"])
def test_key_saved_sets_an_unreadable_file_aside_so_pairing_can_store_a_key(tmp_path, text):
    path = tmp_path / "keys.json"
    path.write_text(text)
    assert not bond.key_saved(path, ADDR)
    assert not path.exists()
    assert (tmp_path / "keys.json.rejected").read_text() == text
