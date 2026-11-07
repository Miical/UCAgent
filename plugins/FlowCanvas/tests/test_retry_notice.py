"""Transient backend retry notices clear only after same-turn recovery."""
from flowcanvas.chat import ChatService


def make_session(tmp_path):
    """Create an in-memory running conversation without connecting a backend."""
    service=ChatService(tmp_path)
    session=service.create()
    session.update(thread="thread",turn="current",status="running")
    return service,session


def test_retry_notice_clears_on_output_from_recovered_turn(tmp_path):
    """A retry followed by streamed text must not leave a permanent red banner."""
    service,session=make_session(tmp_path)
    service.event(dict(kind="error",thread="thread",turn="current",error="Reconnecting... 1/5",retrying=True))
    assert session["error"]
    service.event(dict(kind="delta",thread="thread",turn="current",id="text",type="text",text="Recovered"))
    assert session["error"]==""
    assert session["status"]=="running"
    assert session["items"][-1]["text"]=="Recovered"


def test_retry_does_not_clear_for_other_turn_or_hide_fatal_error(tmp_path):
    """Unrelated activity cannot dismiss a notice; terminal errors stay visible."""
    service,session=make_session(tmp_path)
    service.event(dict(kind="error",thread="thread",turn="current",error="Retry",retrying=True))
    service.event(dict(kind="delta",thread="thread",turn="older",id="old",type="text",text="Old output"))
    assert session["error"]=="Retry"
    service.event(dict(kind="error",thread="thread",turn="current",error="Connection failed",retrying=False))
    service.event(dict(kind="delta",thread="thread",turn="current",id="text",type="text",text="Late output"))
    assert session["error"]=="Connection failed"
    assert session["status"]=="error"


def test_completed_turn_clears_transient_notice(tmp_path):
    """Normal completion is also recovery evidence even without another text delta."""
    service,session=make_session(tmp_path)
    service.event(dict(kind="error",thread="thread",turn="current",error="Retry",retrying=True))
    service.event(dict(kind="turn",thread="thread",turn="current",status="completed",error=""))
    assert session["error"]==""
    assert not session.get("retry_error")


def test_old_notice_recovers_without_a_persisted_retry_marker(tmp_path):
    """Older active snapshots recover on progress from the current turn."""
    service,session=make_session(tmp_path)
    session["error"]="Reconnecting... 1/5"
    service.event(dict(kind="delta",thread="thread",turn="current",id="text",type="text",text="Recovered"))
    assert session["error"]==""


def test_completed_legacy_snapshot_drops_only_retry_notice(tmp_path):
    """A loaded successful turn must not retain old retry banner text."""
    service,session=make_session(tmp_path)
    session.update(status="completed",error="Reconnecting... 1/5")
    service.save(session)
    restored=ChatService(tmp_path).get(session["id"])
    assert restored["error"]==""
