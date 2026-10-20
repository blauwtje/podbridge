import dataclasses

import pytest

from podbridge.events import KINDS, ConsoleReporter, Event


def test_event_defaults_and_frozen():
    event = Event("failed", "boom")
    assert (event.next_step, event.attempt) == ("", 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.message = "x"


def test_kinds_vocabulary():
    assert len(KINDS) == 18
    assert "driver_restore_failed" in KINDS


def test_console_prints_message_and_next_step(capsys):
    ConsoleReporter().emit(Event("open_case", "Case closed", "Open the case"))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Case closed\nOpen the case\n"


def test_unknown_kind_is_printed_not_raised(capsys):
    ConsoleReporter().emit(Event("nonsense", "odd"))
    assert capsys.readouterr().err == "odd\n"
