"""Test case edits retain their existing owned operation contract; targets use test_target_cards."""
import pytest
from flowcanvas import targets, testcases


@pytest.fixture(params=[testcases])
def card(request,tmp_path):
    """Create a ready unbound card through the real store for each supported panel."""
    module=request.param
    cases=module is testcases
    store=(testcases.Cases if cases else targets.Targets)(tmp_path)
    row=store.create("case" if cases else "group","entry",None,0,description="Original",
                     **({"title":"Scenario"} if cases else {}))
    def call(operation,owner="ai",**kwargs):
        """Call the same execution boundary used by the MCP bridge."""
        return module.execute(tmp_path,dict(operation=operation,target_id=row["id"],
                               expected_revision=store.read().revision,**kwargs),owner)
    return store,row["id"],call


def test_registration_is_required_for_every_ai_mutation(card):
    """Reject direct metadata and binding writes without changing the revision."""
    store,identity,call=card
    revision=store.read().revision
    for operation in ("update","bind","accept"):
        with pytest.raises(ValueError,match="begin_update"):
            call(operation,values={"description":"Bypass"} if operation=="update" else {})
        assert store.read().revision==revision
    call("begin_update")
    call("update",values={"description":"Changed"})
    assert store.read().targets[0].lifecycle=="updating"
    assert store.read().targets[0].operation_session=="ai"
    call("finish_update")
    assert store.read().targets[0].lifecycle=="ready"
    assert store.read().targets[0].description=="Changed"


def test_lock_blocks_other_owners_manual_writes_and_paused_updates(card):
    """An operation persists through pause and can be explicitly resumed."""
    store,identity,call=card
    call("begin_update")
    for owner in ("other",""):
        with pytest.raises(ValueError):
            call("update",owner=owner,values={"description":"Wrong owner"})
        with pytest.raises(ValueError):
            call("finish_update",owner=owner)
    store.reconcile_operations({})
    assert store.read().targets[0].operation_status=="paused"
    with pytest.raises(ValueError):
        call("update",values={"description":"Paused"})
    call("resume_operation")
    call("update",values={"description":"Resumed"})
    call("finish_update")


def test_manual_idle_edits_work_and_stale_registration_cannot_write(card):
    """Preserve direct user editing and optimistic revision protection."""
    store,identity,call=card
    call("update",owner="",values={"description":"Manual"})
    revision=store.read().revision
    call("update",owner="",values={"description":"New revision"})
    module=testcases if isinstance(store,testcases.Cases) else targets
    with pytest.raises(ValueError):
        module.execute(store.root,dict(operation="begin_update",target_id=identity,
                                      expected_revision=revision),owner="ai")
    assert store.read().targets[0].lifecycle=="ready"


def test_source_binding_must_be_reconciled_before_finishing(card):
    """A vanished source cannot be hidden by ending a modification operation."""
    store,identity,call=card
    call("begin_update")
    source=store.root/"coverage.py"
    source.write_text("def check_value(dut):\n    return dut.a == 0\n")
    node=store.inspect(["coverage.py"])[0]["nodes"][0]
    call("bind",values={"file":"coverage.py","start":node["start"],
                       "fingerprint":node["fingerprint"],"role":"check"})
    source.write_text(source.read_text().replace("== 0","== 1"))
    with pytest.raises(ValueError,match="bindings"):
        call("finish_update")
    assert store.read().targets[0].lifecycle=="updating"
    report=store.status()["reports"][identity]["bindings"][0]
    call("accept",values={report["id"]:report["fingerprint"]})
    call("finish_update")
