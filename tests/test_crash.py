from bumble.core import ProtocolError

from podbridge.crash import describe, restore_failure, restore_pending, write_log
from podbridge.winusb import DriverError


def test_describe_each_error_kind():
    for exc in (DriverError("no\ndriver"), ProtocolError(1), ValueError("boom")):
        e = describe(exc)
        assert e.kind == "failed" and e.next_step == "run podbridge doctor"
        assert "\n" not in e.message
    assert "no driver" in describe(DriverError("no\ndriver")).message


def test_write_log_appends_traceback(tmp_path):
    p = tmp_path / "sub" / "run.log"
    for msg in ("one", "two"):
        try:
            raise ValueError(msg)
        except ValueError as exc:
            write_log(exc, p)
    text = p.read_text()
    assert "ValueError: one" in text and "ValueError: two" in text and "Traceback" in text


def test_write_log_error_ignored(tmp_path):
    (tmp_path / "f").write_text("x")
    write_log(ValueError("x"), tmp_path / "f" / "run.log")


def test_restore_failure_names_next_step():
    e = restore_failure(DriverError("busy\nnow"))
    assert e.kind == "driver_restore_failed" and "busy now" in e.message
    assert "podbridge restore" in e.next_step


def test_restore_pending_only_when_state_exists(tmp_path):
    p = tmp_path / "driver-state.json"
    assert restore_pending(p) is None
    p.write_text("{}")
    e = restore_pending(p)
    assert e.kind == "driver_restore_pending" and "podbridge restore" in e.next_step


def test_describe_names_a_radio_error_and_its_own_next_step():
    from podbridge.radio import RadioError

    e = describe(RadioError("radio did not\nanswer", "try a full shutdown"))
    assert e.kind == "failed" and e.message == "Radio problem: radio did not answer"
    assert e.next_step == "try a full shutdown"
