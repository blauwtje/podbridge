import logging

from podbridge import firmware_watch


class Rec:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_skipped_patch_warning_becomes_event_then_remove_stops_it():
    rec = Rec()
    handler = firmware_watch.install(rec)
    log = logging.getLogger("bumble.drivers.rtk")
    log.warning("no valid patch found for rom version 5")
    log.warning("something else")
    assert [e.kind for e in rec.events] == ["firmware_unverified"]
    assert "skipped" in rec.events[0].message
    assert "log file" in rec.events[0].next_step
    firmware_watch.remove(handler)
    log.warning("no valid patch found again")
    assert len(rec.events) == 1
