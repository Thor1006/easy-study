"""Kernel data layer: refs, store, journal/recovery, state validation, leases, fabric."""

import asyncio

import pytest

from glass_membrane.blackboard import Blackboard
from glass_membrane.events import Envelope, EventType
from glass_membrane.fabric import Backpressure, Fabric
from glass_membrane.journal import Journal
from glass_membrane.leases import LeaseManager
from glass_membrane.refs import Ref
from glass_membrane.state import ResultProposal, StateManager
from glass_membrane.store import ImmutableWriteError, ObjectStore


def make_task(sm, nodes=None, task_id="t1"):
    sm.transact([{"op": "create_task", "task_id": task_id, "goal": "g"}])
    sm.transact([{"op": "add_nodes", "task_id": task_id, "nodes": nodes or [
        {"id": "a", "title": "A", "role": "efficiency", "tier": "efficiency"}]}])


def proposal(lease, version=1, deps=None, core="E0", node="a", ref="result://t1/a@v1"):
    return ResultProposal("t1", node, core, lease, version, deps or {}, ref, "efficiency")


# -- refs & store ----------------------------------------------------------------

def test_ref_roundtrip_and_validation():
    ref = Ref.parse("result://t1/a@v3")
    assert (ref.ns, ref.path, ref.version) == ("result", "t1/a", 3)
    assert str(ref) == "result://t1/a@v3"
    with pytest.raises(ValueError):
        Ref.parse("bogus://x")


def test_store_is_versioned_immutable_and_persistent(tmp_path):
    store = ObjectStore(tmp_path)
    r1 = store.put("result", "t1/a", {"x": 1})
    r2 = store.put("result", "t1/a", {"x": 2}, supersedes=r1)
    assert (r1.version, r2.version) == (1, 2)
    assert store.content(r1) == {"x": 1}
    assert store.get(r2)["supersedes"] == str(r1)
    reopened = ObjectStore(tmp_path)
    assert reopened.content(r2) == {"x": 2}
    assert reopened.latest("result", "t1/a") == r2
    assert reopened.put("result", "t1/a", {"x": 3}).version == 3
    with pytest.raises(ImmutableWriteError):
        store._write(r1, {"content": "overwrite attempt"})


# -- journal & recovery ------------------------------------------------------------

def test_replay_applies_only_committed_transitions_once(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    sm = StateManager(journal)
    make_task(sm)
    sm.transact([{"op": "add_constraint", "task_id": "t1", "constraint_id": "c1", "text": "be brief"}])
    # Incomplete attempt: begin without commit.
    journal.begin([{"op": "add_constraint", "task_id": "t1", "constraint_id": "c2", "text": "never", "ts": 0}])
    # Torn final write.
    with open(journal.path, "a", encoding="utf-8") as f:
        f.write('{"seq": 99, "phase": "beg')

    recovered = StateManager.recover(Journal(tmp_path / "j.jsonl"))
    assert recovered.task("t1")["version"] == 2
    assert [c["id"] for c in recovered.task("t1")["constraints"]] == ["c1"]
    assert Journal(tmp_path / "j.jsonl").incomplete_transactions() == [4]

    # Checkpoint + replay must not re-apply transitions already in the checkpoint.
    recovered.checkpoint(tmp_path / "cp.json")
    again = StateManager.recover(Journal(tmp_path / "j.jsonl"), checkpoint=str(tmp_path / "cp.json"))
    assert again.task("t1")["version"] == 2
    # The journal is still appendable after the torn write.
    again.transact([{"op": "add_constraint", "task_id": "t1", "constraint_id": "c3", "text": "x"}])
    assert StateManager.recover(Journal(tmp_path / "j.jsonl")).task("t1")["version"] == 3


def test_crash_after_journal_write_recovers_the_transition(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    sm = StateManager(journal)
    make_task(sm)
    seq = journal.begin([{"op": "add_constraint", "task_id": "t1", "constraint_id": "c9", "text": "x", "ts": 1}])
    journal.commit(seq)
    assert sm.task("t1")["version"] == 1  # "crashed" before applying in memory
    assert StateManager.recover(Journal(tmp_path / "j.jsonl")).task("t1")["version"] == 2


# -- validation & leases ---------------------------------------------------------------

def test_valid_result_is_accepted_and_lease_released(clock):
    sm = StateManager(Journal(), clock=clock)
    make_task(sm)
    lease = LeaseManager(sm, clock).grant("t1", "a", "E0", ttl=10)
    decision = sm.propose_result(proposal(lease))
    assert decision.accepted, decision.reasons
    assert sm.node("t1", "a")["status"] == "done"
    assert sm.lease(lease)["released"]


def test_worker_after_lease_expiry_cannot_commit(clock):
    sm = StateManager(Journal(), clock=clock)
    make_task(sm)
    lease = LeaseManager(sm, clock).grant("t1", "a", "E0", ttl=10)
    clock.advance(11)
    decision = sm.propose_result(proposal(lease))
    assert not decision.accepted
    assert "lease expired" in decision.reasons
    assert sm.node("t1", "a")["rejections"], "rejections are retained for diagnosis"


def test_revoked_lease_and_wrong_owner_are_rejected(clock):
    sm = StateManager(Journal(), clock=clock)
    make_task(sm)
    leases = LeaseManager(sm, clock)
    lease = leases.grant("t1", "a", "E0", ttl=10)
    assert not sm.propose_result(proposal(lease, core="E1")).accepted
    leases.revoke(lease, "steer")
    decision = sm.propose_result(proposal(lease))
    assert not decision.accepted and any("revoked" in r for r in decision.reasons)


def test_stale_contract_version_is_rejected(clock):
    sm = StateManager(Journal(), clock=clock)
    make_task(sm)
    lease = LeaseManager(sm, clock).grant("t1", "a", "E0", ttl=10)
    sm.transact([{"op": "add_constraint", "task_id": "t1", "constraint_id": "c", "text": "new"},
                 {"op": "update_node", "task_id": "t1", "node_id": "a", "fields": {"contract_version": 2}}])
    decision = sm.propose_result(proposal(lease, version=1))
    assert not decision.accepted and any("stale state version" in r for r in decision.reasons)


def test_changed_dependency_is_rejected(clock):
    sm = StateManager(Journal(), clock=clock)
    make_task(sm, [{"id": "a", "tier": "efficiency"}, {"id": "b", "deps": ["a"]}])
    leases = LeaseManager(sm, clock)
    la = leases.grant("t1", "a", "E0", ttl=10)
    assert sm.propose_result(proposal(la)).accepted
    lb = leases.grant("t1", "b", "E1", ttl=10)
    stale = ResultProposal("t1", "b", "E1", lb, 1, {"a": "result://t1/a@v0"}, "result://t1/b@v1", "performance")
    assert not sm.propose_result(stale).accepted
    fresh = ResultProposal("t1", "b", "E1", lb, 1, {"a": "result://t1/a@v1"}, "result://t1/b@v1", "performance")
    assert sm.propose_result(fresh).accepted


def test_graph_proposals_are_validated(clock):
    sm = StateManager(Journal(), clock=clock)
    sm.transact([{"op": "create_task", "task_id": "t1", "goal": "g"}])
    assert not sm.propose_graph("t1", 1, [{"id": "x", "deps": ["y"]}, {"id": "y", "deps": ["x"]}]).accepted
    assert not sm.propose_graph("t1", 1, [{"id": "x", "deps": ["missing"]}]).accepted
    assert not sm.propose_graph("t1", 0, [{"id": "x"}]).accepted  # stale
    assert sm.propose_graph("t1", 1, [{"id": "x"}, {"id": "y", "deps": ["x"]}]).accepted
    board = Blackboard(sm)
    assert [n["id"] for n in board.ready_nodes("t1")] == ["x"]
    assert board.dependents("t1", "x") == {"y"}


# -- fabric ------------------------------------------------------------------------------

def test_control_is_delivered_before_a_flooded_data_queue():
    async def main():
        fabric = Fabric()
        mailbox = fabric.mailbox("E0", {"control": 4, "result": 4, "data": 4})
        for _ in range(4):
            assert fabric.send(Envelope(EventType.DATA_READY, "t1", None, "svc", to="E0"))
        assert not fabric.send(Envelope(EventType.DATA_READY, "t1", None, "svc", to="E0")), "bounded"
        assert fabric.send(Envelope(EventType.CANCEL, "t1", None, "kernel", to="E0"))
        first = await mailbox.get(timeout=1)
        assert first.type == EventType.CANCEL
        assert mailbox.refused["data"] == 1

    asyncio.run(main())


def test_full_queue_applies_backpressure_until_drained():
    async def main():
        fabric = Fabric()
        mailbox = fabric.mailbox("E0", {"data": 1})
        await mailbox.put(Envelope(EventType.DATA_READY, "t1", None, "svc", to="E0"))
        with pytest.raises(Backpressure):
            await mailbox.put(Envelope(EventType.DATA_READY, "t1", None, "svc", to="E0"), timeout=0.05)
        waiter = asyncio.create_task(
            mailbox.put(Envelope(EventType.DATA_READY, "t1", None, "svc", to="E0"), timeout=1))
        await asyncio.sleep(0)
        await mailbox.get(timeout=1)
        await waiter
        assert mailbox.pending()["data"] == 1

    asyncio.run(main())


def test_publish_reaches_task_subscribers_only():
    fabric = Fabric()
    fabric.subscribe("E0", "task:t1")
    fabric.subscribe("E1", "task:t2")
    delivered = fabric.publish(Envelope(EventType.CONSTRAINT_CHANGED, "t1", None, "kernel", topic="task:t1"))
    assert delivered == {"E0": True}
