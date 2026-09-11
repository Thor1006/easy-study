"""Runtime behaviour on the fake adapter (no provider usage).

Covers elastic scaling, the filter cascade, steering races (Schematic §15),
dynamic shifting, the Swarm Governor, services, and recovery.
"""

import asyncio
import shutil
import time

from glass_membrane.adapters.base import ModelResult
from glass_membrane.adapters.fake import FakeAdapter, simulated_brain
from glass_membrane.registry import Config, merge
from glass_membrane.runtime import Runtime
from glass_membrane.swarm import SwarmGovernor

FAST = {"runtime": {"call_timeout_s": 5, "lease_ttl_s": 5, "watchdog_interval_s": 0.05, "fsync": False}}
SORT = "Compare 3 sorting algorithms for nearly-sorted data"
FRUIT = "Compare apples, oranges, pears, plums and grapes"
SINGLE = "Explain the tradeoffs of caching in web apps"


def make_rt(root, *, brain=None, brains=None, delays=None, overrides=None):
    adapters = {p: FakeAdapter(p, brain=(brains or {}).get(p, brain), delays=delays) for p in ("claude", "codex")}
    return Runtime(root, config=Config(merge(FAST, overrides or {})), adapters=adapters)


def calls(rt, role=None):
    return [c for a in rt.adapters.values() for c in a.calls if role is None or c.role == role]


def running_nodes(rt, run):
    if not run.task_id:
        return []
    return [n for n in rt.state.task(run.task_id)["nodes"].values() if n["status"] == "running"]


async def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.01)


def run_once(rt, text, **kwargs):
    async def main():
        await rt.start()
        try:
            run = await rt.submit(text, **kwargs)
            await rt.wait(run.id, timeout=15)
            return run
        finally:
            await rt.stop()
    return asyncio.run(main())


def efficiency_router(inv):
    """Simulated brain, but the filter recommends the efficiency tier for new work."""
    out = simulated_brain(inv)
    if inv.role == "router" and out.get("packet"):
        out["packet"]["recommended_tier"] = "efficiency"
    return out


# ---------------------------------------------------------------- elastic scaling

def test_2x2_is_answered_by_the_filter_with_one_process(tmp_path):
    rt = make_rt(tmp_path)
    run = run_once(rt, "2x2")
    assert run.status == "done" and run.answer == "4"
    assert run.answered_by.startswith("filter")
    assert len(calls(rt)) == 1 and run.peak_processes == 1
    assert run.task_id is None, "no kernel task, zero cores"
    assert rt.ledger.totals(run.id)["by_kind"]["filter"]["calls"] == 1
    assert rt.pools.total_live == 0


def test_three_way_comparison_peaks_at_three_parallel_workers(tmp_path):
    rt = make_rt(tmp_path, delays={"performance": 0.15})
    run = run_once(rt, SORT)
    assert run.status == "done", run.error
    assert set(rt.state.task(run.task_id)["nodes"]) == {"plan", "part-1", "part-2", "part-3", "synthesize"}
    assert run.peak_processes == 3
    assert "Timsort" in run.answer
    assert rt.pools.total_live == 0
    assert all(s.status == "idle" for s in rt.slots.cores.values())


def test_agent_cap_limits_parallel_admissions(tmp_path):
    rt = make_rt(tmp_path, delays={"performance": 0.1})
    run = run_once(rt, FRUIT, max_agents=2)
    assert run.status == "done", run.error
    assert len([n for n in rt.state.task(run.task_id)["nodes"] if n.startswith("part-")]) == 5
    assert run.peak_agents <= 2


def test_raising_the_cap_mid_run_admits_waiting_work(tmp_path):
    rt = make_rt(tmp_path, delays={"performance": 0.3})

    async def main():
        await rt.start()
        run = await rt.submit(FRUIT, max_agents=1)
        await wait_until(lambda: len(running_nodes(rt, run)) == 1)
        await rt.set_cap(run.id, 5)
        await rt.wait(run.id, timeout=10)
        await rt.stop()
        return run

    run = asyncio.run(main())
    assert run.status == "done"
    assert run.peak_agents >= 4


def test_lowering_the_cap_never_kills_work_in_flight(tmp_path):
    rt = make_rt(tmp_path, delays={"performance": 0.3})

    async def main():
        await rt.start()
        run = await rt.submit(FRUIT)
        await wait_until(lambda: len(running_nodes(rt, run)) == 5)
        await rt.set_cap(run.id, 1)
        await rt.wait(run.id, timeout=10)
        await rt.stop()
        return run

    run = asyncio.run(main())
    nodes = rt.state.task(run.task_id)["nodes"]
    assert run.status == "done"
    assert sum(a.cancelled for a in rt.adapters.values()) == 0
    assert not any(n["rejections"] for n in nodes.values())


# ---------------------------------------------------------------- steering (Schematic §15)

def test_global_steer_rejects_results_computed_on_old_state(tmp_path):
    rt = make_rt(tmp_path, delays={"performance": 0.4}, overrides={"runtime": {"cancel_in_flight": False}})

    async def main():
        await rt.start()
        run = await rt.submit(SORT)
        await wait_until(lambda: len(running_nodes(rt, run)) == 3)
        summary = await rt.steer(run.id, "Answer in one sentence each")
        await rt.wait(run.id, timeout=10)
        await rt.stop()
        return run, summary

    run, summary = asyncio.run(main())
    task = rt.state.task(run.task_id)
    assert summary["scope"] == "global" and task["version"] == 2
    for nid in ("part-1", "part-2", "part-3"):
        node = task["nodes"][nid]
        assert node["status"] == "done" and node["contract_version"] == 2
        reasons = [r for rejection in node["rejections"] for r in rejection["reasons"]]
        assert any("revoked" in r or "stale state version" in r for r in reasons), reasons
        assert rt.store.content(node["result_ref"])["state_version"] == 2
    assert "Answer in one sentence each" in run.answer


def test_scoped_steer_keeps_unaffected_results(tmp_path):
    rt = make_rt(tmp_path, delays={"performance": 0.4}, overrides={"runtime": {"cancel_in_flight": False}})

    async def main():
        await rt.start()
        run = await rt.submit(SORT)
        await wait_until(lambda: len(running_nodes(rt, run)) == 3)
        summary = await rt.steer(run.id, "For Timsort, mention galloping mode")
        await rt.wait(run.id, timeout=10)
        await rt.stop()
        return run, summary

    run, summary = asyncio.run(main())
    nodes = rt.state.task(run.task_id)["nodes"]
    assert summary["scope"] == "nodes"
    assert set(summary["affected"]) == {"part-2", "synthesize"}
    for kept in ("part-1", "part-3"):
        assert nodes[kept]["contract_version"] == 1 and not nodes[kept]["rejections"]
        assert rt.store.content(nodes[kept]["result_ref"])["state_version"] == 1
    assert nodes["part-2"]["contract_version"] == 2 and nodes["part-2"]["rejections"]
    assert run.status == "done"


def test_steer_with_cancellation_stops_obsolete_generation(tmp_path):
    rt = make_rt(tmp_path, delays={"performance": 0.5})

    async def main():
        await rt.start()
        run = await rt.submit(SORT)
        await wait_until(lambda: len(running_nodes(rt, run)) == 3)
        await rt.steer(run.id, "Answer in one sentence each")
        await rt.wait(run.id, timeout=10)
        await rt.stop()
        return run

    run = asyncio.run(main())
    assert run.status == "done"
    assert sum(a.cancelled for a in rt.adapters.values()) >= 3
    assert any(e["failure"] == "CANCELLED" for e in rt.ledger.entries)


def test_steer_gets_through_when_cores_fill_a_provider(tmp_path):
    overrides = {"providers": {"claude": {"max_processes": 3}, "codex": {"max_processes": 3}},
                 "tiers": {"performance": {"bindings": [{"provider": "claude"}]},
                           "efficiency": {"bindings": [{"provider": "claude"}]}},
                 "runtime": {"cancel_in_flight": False}}
    rt = make_rt(tmp_path, delays={"performance": 1.0}, overrides=overrides)

    async def main():
        await rt.start()
        run = await rt.submit(FRUIT)
        await wait_until(lambda: len(running_nodes(rt, run)) == 2)
        started = time.monotonic()
        summary = await rt.steer(run.id, "Keep it brief")
        elapsed = time.monotonic() - started
        await rt.stop()
        return summary, elapsed

    summary, elapsed = asyncio.run(main())
    assert summary["applied"] and elapsed < 0.5
    assert rt.pools.pools["claude"].peak == 3, "work never takes the reserved control slot"


def test_expired_lease_is_reclaimed_by_the_watchdog(tmp_path):
    attempts = {"n": 0}

    def delay(inv):
        attempts["n"] += 1
        return 0.6 if attempts["n"] == 1 else 0.01

    rt = make_rt(tmp_path, delays={"performance": delay},
                 overrides={"runtime": {"lease_ttl_s": 0.2, "heartbeat": False, "cancel_in_flight": False}})
    run = run_once(rt, SINGLE)
    node = rt.state.task(run.task_id)["nodes"]["work"]
    assert run.status == "done"
    assert any(h.get("reason") == "lease expired" for h in node["history"])
    leases = [l for l in rt.state.state["leases"].values() if l["node_id"] == "work"]
    assert leases[0]["revoked"] and leases[0]["revoked_reason"] == "lease expired"


# ---------------------------------------------------------------- filter cascade

def test_low_routing_confidence_escalates_up_the_cascade(tmp_path):
    run = run_once(make_rt(tmp_path), "An ambiguous question about database indexing")
    assert [a["tier"] for a in run.route["attempts"]] == ["efficiency", "performance"]
    assert run.status == "done"


def test_low_confidence_answer_escalates_before_being_returned(tmp_path):
    def brain(inv):
        if inv.role == "router":
            conf = 0.6 if inv.data["tier"] == "efficiency" else 0.95
            return {"mode": "answer", "answer_text": "Paris", "answer_confidence": conf, "routing_confidence": 0.9,
                    "kind": "new_task", "packet": None, "steer": None}
        return simulated_brain(inv)

    run = run_once(make_rt(tmp_path, brain=brain), "What is the capital of France?")
    assert run.answer == "Paris" and run.answered_by == "filter · R4 performance"
    assert len(run.route["attempts"]) == 2


def test_experiment_controls_route_around_the_filter_answer(tmp_path):
    forced = run_once(make_rt(tmp_path / "a"), "2x2", force_route=True)
    assert forced.answered_by.startswith("kernel") and forced.experiment == "force-route"
    rt = make_rt(tmp_path / "b")
    bypassed = run_once(rt, "2x2", filter_bypass=True)
    assert calls(rt, "router") == [] and bypassed.status == "done"


# ---------------------------------------------------------------- dynamic shifting & rebinding

def test_justified_escalation_rebinds_to_a_stronger_tier(tmp_path):
    def brain(inv):
        if inv.role in ("efficiency", "performance"):
            return {"answer": "", "summary": "derivations disagree", "confidence": 0.3, "uncertainty": "conflict",
                    "status": "CAPABILITY_LIMIT",
                    "shift": {"type": "ESCALATION_REQUEST", "reason": "Two derivations disagree.",
                              "attempted": "Direct proof and a counter-example search.",
                              "requested_capability": "stronger reasoning",
                              "expected_benefit": "Resolve the disputed step.",
                              "checkpoint_next_action": "Re-derive step 2.", "subtask_title": "",
                              "swarm_children": 0}}
        return efficiency_router(inv)

    rt = make_rt(tmp_path, brain=brain)
    run = run_once(rt, "Prove that the sum of two even numbers is even")
    node = rt.state.task(run.task_id)["nodes"]["work"]
    assert run.status == "done", run.error
    assert node["tier"] == "flagship" and node["escalations"] == 2 and node["checkpoint_ref"]
    leases = [l for l in rt.state.state["leases"].values() if l["node_id"] == "work"]
    assert len(leases) == 3 and all(l["revoked"] for l in leases[:2]) and leases[2]["released"]
    assert rt.store.content(node["result_ref"])["binding"] == {"provider": "claude", "model": "opus", "effort": "high"}


def test_unjustified_escalation_is_denied(tmp_path):
    def brain(inv):
        out = efficiency_router(inv)
        if inv.role == "efficiency":
            out["shift"] = {"type": "ESCALATION_REQUEST", "reason": "I would like a bigger model", "attempted": "",
                            "requested_capability": "", "expected_benefit": "", "checkpoint_next_action": "",
                            "subtask_title": "", "swarm_children": 0}
        return out

    rt = make_rt(tmp_path, brain=brain)
    run = run_once(rt, SINGLE)
    node = rt.state.task(run.task_id)["nodes"]["work"]
    assert run.status == "done" and node["tier"] == "efficiency" and node["escalations"] == 0
    denial = [e for e in rt.events if e["type"] == "ESCALATION_REQUEST"]
    assert denial and denial[0]["data"]["granted"] is False


def test_rate_limit_lowers_only_that_providers_cap_and_shifts_work(tmp_path):
    limited = {"done": False}

    def codex_brain(inv):
        if inv.role == "performance" and not limited["done"]:
            limited["done"] = True
            return ModelResult(ok=False, rate_limited=True, failure="RATE_LIMITED", detail="429")
        return simulated_brain(inv)

    rt = make_rt(tmp_path, brains={"codex": codex_brain})
    run = run_once(rt, SINGLE)
    codex, claude = rt.pools.pools["codex"], rt.pools.pools["claude"]
    assert run.status == "done"
    assert codex.rate_limit_events == 1 and codex.cap < codex.ceiling
    assert claude.rate_limit_events == 0 and claude.cap == claude.ceiling
    assert rt.state.task(run.task_id)["nodes"]["work"]["binding"]["provider"] == "claude"


def test_provider_failure_rebinds_to_the_other_provider(tmp_path):
    def claude_brain(inv):
        if inv.role == "efficiency":
            return ModelResult(ok=False, failure="TOOL_FAILURE", detail="provider crashed mid-call")
        return efficiency_router(inv)

    rt = make_rt(tmp_path, brain=efficiency_router, brains={"claude": claude_brain})
    run = run_once(rt, SINGLE)
    node = rt.state.task(run.task_id)["nodes"]["work"]
    assert run.status == "done"
    assert node["binding"]["provider"] == "codex" and node["attempt"] == 1


def test_node_fails_cleanly_after_max_attempts(tmp_path):
    def broken(inv):
        if inv.role == "router":
            return efficiency_router(inv)
        return ModelResult(ok=False, failure="TOOL_FAILURE", detail="down")

    rt = make_rt(tmp_path, brain=broken)
    run = run_once(rt, SINGLE)
    assert run.status == "failed" and "TOOL_FAILURE" in run.error
    assert len(calls(rt, "efficiency")) == 3
    assert rt.pools.total_live == 0


# ---------------------------------------------------------------- Swarm Governor

def planner_with(node):
    def brain(inv):
        if inv.role == "planner":
            return {"rationale": "test plan", "nodes": [node]}
        return simulated_brain(inv)
    return brain


def test_swarm_request_is_redirected_to_idle_cores(tmp_path):
    brain = planner_with({"id": "scan", "title": "Scan sources", "instructions": "Scan all sources",
                          "role": "retrieval", "tier": "efficiency", "deps": [], "swarm_children": 3})
    rt = make_rt(tmp_path, brain=brain)
    run = run_once(rt, FRUIT)
    nodes = rt.state.task(run.task_id)["nodes"]
    assert run.status == "done", run.error
    assert nodes["scan"]["role"] == "synthesizer"
    assert nodes["scan"]["deps"] == ["scan-p1", "scan-p2", "scan-p3"]
    assert all(c.swarm_grant == 0 for c in calls(rt))


def test_swarm_grant_is_enforced_through_provider_settings(tmp_path):
    brain = planner_with({"id": "survey", "title": "Survey", "instructions": "", "role": "flagship",
                          "tier": "flagship", "deps": [], "swarm_children": 2})
    rt = make_rt(tmp_path, brain=brain, overrides={"swarm": {"prefer_idle_cores": False}})
    rt.adapters["claude"].overspawn = 1
    run = run_once(rt, FRUIT)
    survey = [c for c in calls(rt) if c.data.get("node") == "survey"]
    assert survey[0].swarm_grant == 2
    assert survey[0].env == {"CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "2", "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "1"}
    assert survey[1].swarm_grant == 0
    node = rt.state.task(run.task_id)["nodes"]["survey"]
    assert any("grant was 2" in r for rejection in node["rejections"] for r in rejection["reasons"])
    assert node["status"] == "done"
    assert [e["children"] for e in rt.ledger.entries if e["node"] == "survey"] == [3, 0]
    assert rt.swarm.live_children == 0


def test_cancelling_a_parent_releases_its_children(tmp_path):
    brain = planner_with({"id": "survey", "title": "Survey", "instructions": "", "role": "flagship",
                          "tier": "flagship", "deps": [], "swarm_children": 2})
    rt = make_rt(tmp_path, brain=brain, delays={"flagship": 3.0}, overrides={"swarm": {"prefer_idle_cores": False}})

    async def main():
        await rt.start()
        run = await rt.submit(FRUIT)
        await wait_until(lambda: rt.swarm.live_children == 2)
        await rt.cancel(run.id)
        await wait_until(lambda: rt.pools.total_live == 0)
        await rt.stop()
        return run

    run = asyncio.run(main())
    assert rt.swarm.live_children == 0
    assert rt.adapters["claude"].cancelled >= 1
    cancelled = [e for e in rt.ledger.entries if e["failure"] == "CANCELLED"]
    assert cancelled and cancelled[0]["children"] is None, "child usage of a killed parent is unknown, not zero"
    assert rt.state.task(run.task_id)["status"] == "cancelled"


def test_governor_refuses_recursion_and_unbounded_requests(tmp_path):
    rt = make_rt(tmp_path)
    run = rt.new_run("x")
    assert rt.swarm.consider(run, {"id": "c", "role": "swarm-child"}, 2, "more", "claude").action == "deny"
    assert rt.swarm.consider(run, {"id": "n", "role": "performance"}, 2, "", "claude").action == "deny"


def test_provider_settings_translate_grants():
    assert SwarmGovernor.provider_settings("codex", 3) == ({}, ["-c", "agents.max_concurrent_threads_per_session=3"])
    assert SwarmGovernor.provider_settings("codex", 0) == ({}, ["--disable", "multi_agent"])
    assert SwarmGovernor.provider_settings("claude", 0) == ({}, [])


# ---------------------------------------------------------------- services & recovery

def test_lost_service_response_is_reconciled_not_repeated(tmp_path):
    rt = make_rt(tmp_path)
    original = rt.services.run
    seen = {"n": 0}

    def flaky(name, args, action_id=None):
        out = original(name, args, action_id=action_id)
        seen["n"] += 1
        if seen["n"] == 1:
            raise ConnectionError("response lost after the effect was applied")
        return out

    rt.services.run = flaky

    async def main():
        await rt.start()
        run = rt.new_run("note")
        task_id = rt.create_task(run, "write a note", [{
            "id": "note", "title": "Write note", "role": "service", "tier": "efficiency",
            "service": {"name": "append_note", "args": {"text": "hello"}}}])
        await rt.scheduler.execute(run, task_id)
        await rt.stop()
        return task_id

    task_id = asyncio.run(main())
    node = rt.state.node(task_id, "note")
    assert node["status"] == "done"
    assert rt.services.effect_log == ["hello"], "the effect happened exactly once"
    assert rt.store.content(node["result_ref"])["output"]["reconciled"] is True


def test_completed_state_survives_a_restart(tmp_path):
    rt = make_rt(tmp_path)
    run = run_once(rt, "Compare apples and oranges")
    before = rt.state.task(run.task_id)
    again = make_rt(tmp_path).state.task(run.task_id)
    assert again["status"] == "done" and again["answer"] == before["answer"]
    assert again["version"] == before["version"]


def test_open_work_is_marked_interrupted_after_a_crash(tmp_path):
    rt = make_rt(tmp_path / "live", delays={"performance": 5})
    crash_copy = tmp_path / "crash"

    async def main():
        await rt.start()
        run = await rt.submit(FRUIT)
        await wait_until(lambda: running_nodes(rt, run))
        shutil.copytree(tmp_path / "live" / "runtime", crash_copy / "runtime")  # state as a crash left it
        await rt.stop()
        return run

    run = asyncio.run(main())
    recovered = make_rt(crash_copy)
    assert recovered.state.task(run.task_id)["status"] == "interrupted"
    assert recovered.recovery["revoked_leases"] >= 1
    assert not [l for l in recovered.state.state["leases"].values() if not l["revoked"] and not l["released"]]
