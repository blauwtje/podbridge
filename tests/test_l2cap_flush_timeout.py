"""The AirPods' Configure Request carries Flush Timeout (0x02); Bumble must accept it, not answer UNKNOWN_OPTIONS."""
from types import SimpleNamespace

from bumble import l2cap
from bumble.l2cap import L2CAP_Configure_Request, L2CAP_Configure_Response

from podbridge import host

# From the PC trace: MTU 2582, FLUSH_TIMEOUT 30, basic-mode Retransmission and Flow Control.
AIRPODS_OPTIONS = bytes.fromhex("0102160a" "02021e00" "0409" + "00" * 9)
RESULT = L2CAP_Configure_Response.Result


class Null:
    def set_packet_sink(self, sink): ...
    def on_packet(self, packet): ...


class Manager:
    extended_features: set = set()

    def __init__(self):
        self.sent = []

    def send_control_frame(self, connection, cid, frame):
        self.sent.append(frame)


def configure_response(options: bytes):
    host.build_device(Null(), Null())  # podbridge's Bumble setup runs before any connection
    manager = Manager()
    channel = l2cap.ClassicChannel(manager, SimpleNamespace(), 1, 0x1001, 0x40, l2cap.ClassicChannelSpec(psm=0x1001, mtu=4096))
    channel.destination_cid = 0x2107
    channel.state = channel.State.WAIT_CONFIG_REQ
    channel.on_configure_request(L2CAP_Configure_Request(identifier=7, destination_cid=0x40, flags=0, options=options))
    (response,) = manager.sent
    return channel, response


def test_configure_request_with_mtu_and_flush_timeout_gets_a_success_response(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "keys_path", lambda: tmp_path / "keys.json")
    channel, response = configure_response(AIRPODS_OPTIONS)
    assert response.result == RESULT.SUCCESS
    assert response.identifier == 7
    assert channel.peer_mtu == 2582
    assert channel.state == channel.State.OPEN


def test_other_unknown_options_are_still_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "keys_path", lambda: tmp_path / "keys.json")
    qos = bytes.fromhex("03020000")
    channel, response = configure_response(AIRPODS_OPTIONS[:4] + qos)
    assert response.result == RESULT.FAILURE_UNKNOWN_OPTIONS
    assert channel.state != channel.State.OPEN
