from bumble.core import ProtocolError

from podbridge.crash import describe, write_log
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
