"""Deterministic scheduler (Arch §5, §7, Ops §4–§6, Schematic §4).

For each ready node: use a deterministic service if one suffices; otherwise
choose a capability tier, fit the work to lawful capacity (per-run agent cap,
provider pool, idle core), and issue a focus contract plus a bounded lease.
Model outputs — results, shift requests, subtask and swarm requests — are
proposals; this module and the State Manager decide.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time

from . import assembler
from .adapters.base import Invocation, ModelResult, Usage
from .blackboard import FAILED, RUNNING, TIERS, WAITING
from .events import Envelope, EventType, FailureCode
from .focus import FocusContract
from .registry import TIER_DOWN, TIER_UP
from .roles import load_skill, render_core_prompt, schema_for
from .state import ResultProposal

log = logging.getLogger("glass_membrane.scheduler")

_FAILURE_STATUSES = {"BLOCKED", "INSUFFICIENT_EVIDENCE", "CONFLICT", "CAPABILITY_LIMIT"}
_PLANNER_ROLES = {"retrieval", "efficiency", "performance", "flagship", "verifier", "synthesizer"}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return slug[:40] or "part"


class Scheduler:
    def __init__(self, rt) -> None:
        self.rt = rt

    @property
    def cfg(self) -> dict:
        return self.rt.config.runtime

    # ------------------------------------------------------------------ main loop
    async def execute(self, run, task_id: str) -> None:
        rt = self.rt
        idle_loops = 0
        try:
            while True:
                task = rt.state.task(task_id)
                if task is None or task["status"] != "open":
                    return
                if rt.board.is_complete(task_id):
                    self._finish(run, task_id)
                    return
                failed = rt.board.failed_nodes(task_id)
                if failed and not run.inflight:
                    self._fail(run, task_id, failed)
                    return
                since = rt.signal.version
                if not failed:
                    self._dispatch(run, task_id)
                if not run.inflight and not rt.board.ready_nodes(task_id):
                    idle_loops += 1
                    if idle_loops > 40:
                        self._fail(run, task_id, [], FailureCode.BLOCKED.value, "no runnable work remains")
                        return
                else:
                    idle_loops = 0
                await rt.signal.wait(timeout=0.25, since=since)
        finally:
            await self._drain(run)

    def _spawn(self, run, lease_id: str, coro) -> None:
        task = asyncio.create_task(coro, name=f"gm-{lease_id}")
        run.inflight[lease_id] = task

        def done(_t, lease=lease_id):
            run.inflight.pop(lease, None)
            self.rt.signal.notify()

        task.add_done_callback(done)

    def _acquire_binding(self, tier: str, avoid: str | None = None):
        for binding in self.rt.registry.bindings(tier, avoid=avoid):
            if self.rt.pools.try_acquire(binding.provider, "work"):
                return binding
        return None

    def _dispatch(self, run, task_id: str) -> None:
        rt = self.rt
        if run.paused:
            return
        for node in sorted(rt.board.ready_nodes(task_id), key=lambda n: n["id"]):
            nid = node["id"]
            if node.get("service"):
                lease = rt.leases.grant(task_id, nid, "service", ttl=self.cfg["lease_ttl_s"],
                                        binding={"provider": "service"})
                self._spawn(run, lease, self._run_service(run, task_id, nid, lease))
                continue
            ok, why = rt.budget.admit(run)
            if not ok:
                self._fail(run, task_id, [], FailureCode.BUDGET_EXHAUSTED.value, why)
                return
            if run.live_agents + 1 > run.max_agents:
                run.status_line = f"Waiting: agent limit {run.max_agents} reached"
                return
            slot = rt.slots.idle_core(node["tier"])
            if slot is None:
                return
            binding = self._acquire_binding(node["tier"], avoid=node.get("avoid_provider"))
            if binding is None:
                candidates = rt.registry.bindings(node["tier"])
                if candidates and not any(rt.pools.available(b.provider) for b in candidates):
                    until = min(rt.pools.pools[b.provider].unavailable_until for b in candidates)
                    self._fail(run, task_id, [], FailureCode.BUDGET_EXHAUSTED.value,
                               f"every provider for the {node['tier']} tier is out of quota until "
                               f"{time.strftime('%H:%M', time.localtime(until))}")
                return
            grant = 0
            swarm = node.get("swarm_request")
            if swarm and int(swarm.get("children", 0)) > 0:
                decision = rt.swarm.consider(run, node, int(swarm["children"]), swarm.get("purpose", ""),
                                             binding.provider)
                rt.emit("SWARM_REQUEST", run=run.id, task=task_id, node=nid,
                        message=f"Swarm Governor: {decision.action} {decision.children} for {node['title']} "
                                f"({decision.reason})", data=decision.to_dict())
                if decision.action == "redirect":
                    rt.pools.release(binding.provider)
                    self._redirect_to_cores(run, task_id, node, int(swarm["children"]))
                    return
                if decision.action in ("grant", "reduce"):
                    grant = decision.children
            rt.budget.charge(run)
            rt.swarm.acquire(grant)
            lease = rt.leases.grant(task_id, nid, slot.id, ttl=self.cfg["lease_ttl_s"],
                                    binding={**binding.to_dict(), "swarm_grant": grant})
            run.live_agents += 1 + grant
            run.live_processes += 1
            run.peak_agents = max(run.peak_agents, run.live_agents)
            run.peak_processes = max(run.peak_processes, run.live_processes)
            rt.slots.occupy(slot.id, run_id=run.id, task_id=task_id, node_id=nid,
                            binding=binding.to_dict(), lease_id=lease, children=grant)
            rt.fabric.send(Envelope(EventType.TASK_ASSIGNED, task_id, nid, "scheduler", to=slot.id,
                                    state_version=node["contract_version"], payload={"lease_id": lease}))
            rt.emit("TASK_ASSIGNED", run=run.id, task=task_id, node=nid,
                    message=f"{slot.id} → {node['title']} on {binding.label()}"
                            + (f" with {grant} child agent(s)" if grant else ""),
                    data={"core": slot.id, "binding": binding.to_dict(), "lease": lease, "children": grant,
                          "tier": node["tier"]})
            self._spawn(run, lease, self._run_core(run, task_id, nid, slot.id, binding, lease, grant))

    # ------------------------------------------------------------------ core execution
    async def _heartbeat(self, lease_id: str) -> None:
        ttl = float(self.cfg["lease_ttl_s"])
        while True:
            await asyncio.sleep(max(0.05, ttl / 3))
            if not self.rt.leases.renew(lease_id, ttl):
                return

    async def _invoke_with_control(self, core_id: str, lease_id: str, adapter, inv):
        """Run the provider call while watching the core's control queue for CANCEL."""
        mailbox = self.rt.fabric.mailbox(core_id)
        call = asyncio.create_task(adapter.invoke(inv))
        try:
            while True:
                control = asyncio.create_task(mailbox.get())
                done, _ = await asyncio.wait({call, control}, return_when=asyncio.FIRST_COMPLETED)
                if call in done:
                    control.cancel()
                    return call.result(), False
                env = control.result()
                if env.type == EventType.CANCEL and env.payload.get("lease_id") == lease_id:
                    call.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await call
                    return None, True
                # Other control traffic (e.g. a STEER that could not interrupt the provider)
                # is left to validation: an obsolete result cannot be accepted.
        except asyncio.CancelledError:
            call.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await call
            raise

    def _contract(self, run, task_id, node, core_id, lease_id, ctx_ref, package, grant) -> FocusContract:
        task = self.rt.state.task(task_id)
        deps = {d["node"]: d["ref"] for d in package["dependencies"]}
        return FocusContract(
            task_ref=f"task://{task_id}", node_ref=f"task://{task_id}/{node['id']}", core_ref=f"core://{core_id}",
            role=node["role"], goal=task["goal"],
            job=node["title"] + (f": {node['instructions']}" if node["instructions"] else ""),
            state_version=node["contract_version"],
            constraints=[c["text"] for c in task["constraints"]],
            input_refs=[str(ctx_ref)] + [r for r in deps.values() if r], dependencies=deps,
            permissions=self.rt.permissions.actions(node["role"]),
            budget={"max_usd_estimate": self.rt.config.budget.get("max_usd_per_call"), "swarm_children": grant},
            lease={"id": lease_id, "ttl_s": self.cfg["lease_ttl_s"]}, partition=node.get("partition"))

    def _invocation(self, run, core_id, node, contract, package, binding, grant) -> Invocation:
        env, extra = self.rt.swarm.provider_settings(binding.provider, grant)
        return Invocation(
            run_id=run.id, label=core_id, role=node["role"], system_prompt=load_skill(node["role"]),
            prompt=render_core_prompt(contract, package, node["role"]), schema=schema_for(node["role"]),
            model=binding.model, effort=binding.effort, timeout_s=float(self.cfg["call_timeout_s"]),
            swarm_grant=grant, max_budget_usd=self.rt.config.budget.get("max_usd_per_call"),
            env=env, extra_args=extra,
            data={"node": node["id"], "title": node["title"], "instructions": node["instructions"],
                  "tier": node["tier"], "goal": package["goal"], "constraints": package["constraints"],
                  "deps": package["dependencies"], "packet": package.get("packet"),
                  "checkpoint": package.get("checkpoint"), "partition": node.get("partition"),
                  "attempt": node["attempt"]})

    async def _run_core(self, run, task_id, nid, core_id, binding, lease_id, grant) -> None:
        rt = self.rt
        result: ModelResult | None = None
        cancelled = False
        contract = None
        package: dict = {}
        started = time.monotonic()
        heartbeat = asyncio.create_task(self._heartbeat(lease_id)) if self.cfg["heartbeat"] else None
        try:
            node = rt.state.node(task_id, nid)
            ctx_ref, package = rt.context.build(task_id, nid)
            contract = self._contract(run, task_id, node, core_id, lease_id, ctx_ref, package, grant)
            inv = self._invocation(run, core_id, node, contract, package, binding, grant)
            result, cancelled = await self._invoke_with_control(core_id, lease_id, rt.adapters[binding.provider], inv)
        except asyncio.CancelledError:
            cancelled = True
        except Exception as exc:  # adapter or runtime bug: report, don't crash the run
            log.exception("core %s failed on %s", core_id, nid)
            result = ModelResult(ok=False, failure=FailureCode.TOOL_FAILURE.value, detail=f"runtime error: {exc!r}")
        finally:
            if heartbeat:
                heartbeat.cancel()
            rt.pools.release(binding.provider)
            rt.swarm.release(grant)
            rt.slots.free(core_id)
            run.live_agents -= 1 + grant
            run.live_processes -= 1
            rt.signal.notify()

        duration = time.monotonic() - started
        if cancelled or result is None:
            rt.ledger.record(run_id=run.id, provider=binding.provider, model=binding.model, label=core_id,
                             kind="core", node=nid, usage=Usage(), children=None if grant else 0,
                             duration_s=duration, ok=False, failure="CANCELLED")
            rt.emit("core_cancelled", run=run.id, task=task_id, node=nid,
                    message=f"{core_id} stopped: its work on {nid} was cancelled", data={"lease": lease_id})
            return
        rt.budget.record(run, result.usage)
        rt.ledger.record(run_id=run.id, provider=binding.provider, model=binding.model, label=core_id,
                         kind="core", node=nid, usage=result.usage, children=result.children_spawned,
                         duration_s=duration, ok=result.ok, failure=result.failure)
        self._handle_result(run, task_id, nid, core_id, binding, lease_id, grant, contract, package, result)

    def _handle_result(self, run, task_id, nid, core_id, binding, lease_id, grant, contract, package, result):
        rt = self.rt
        node = rt.state.node(task_id, nid)
        owner = node is not None and node["lease_id"] == lease_id and rt.state.lease_is_live(lease_id)

        if result.rate_limited:
            if result.retry_after:
                rt.pools.mark_unavailable(binding.provider, result.retry_after)
                until = time.strftime("%H:%M", time.localtime(result.retry_after))
                rt.emit("provider_unavailable", run=run.id, task=task_id, node=nid,
                        message=f"{binding.provider} is out of quota until {until}; shifting work to other providers",
                        status=f"{binding.provider} is out of quota — shifting work",
                        data={"provider": binding.provider, "until": result.retry_after})
            else:
                rt.pools.on_rate_limit(binding.provider)
                cap = rt.pools.pools[binding.provider].cap
                rt.emit("rate_limited", run=run.id, task=task_id, node=nid,
                        message=f"{binding.provider} is rate limiting; its cap is now {cap} and work shifts elsewhere",
                        status=f"{binding.provider} is busy — shifting work",
                        data={"provider": binding.provider, "cap": cap})
            if owner:
                self._requeue(task_id, nid, lease_id, "provider rate limited",
                              fields={"avoid_provider": binding.provider}, count_attempt=False)
            return
        if not result.ok:
            rt.emit("core_failed", run=run.id, task=task_id, node=nid,
                    message=f"{core_id} on {binding.provider}: {result.failure} {result.detail[:120]}",
                    data={"failure": result.failure, "detail": result.detail[:500]})
            if owner:
                self._retry_or_fail(run, task_id, nid, lease_id, result.failure or FailureCode.TOOL_FAILURE.value,
                                    result.detail, avoid=binding.provider)
            return
        rt.pools.on_success(binding.provider)
        output = result.output or {}

        if result.children_spawned is not None and result.children_spawned > grant:
            reason = f"spawned {result.children_spawned} native children but the grant was {grant}"
            rt.state.transact([{"op": "reject_result", "task_id": task_id, "node_id": nid, "result_ref": None,
                                "reasons": [reason], "core": core_id}], {"kind": "reject_result"})
            rt.emit("swarm_violation", run=run.id, task=task_id, node=nid, message=reason)
            if owner:
                self._requeue(task_id, nid, lease_id, reason, fields={"swarm_request": None})
            return

        shift = output.get("shift") if isinstance(output.get("shift"), dict) else None
        if shift and owner and shift.get("type") in ("ESCALATION_REQUEST", "DOWNSHIFT_REQUEST", "SWARM_REQUEST"):
            if self._handle_shift(run, task_id, node, lease_id, binding, output, shift):
                return

        status = output.get("status", "COMPLETE")
        if status in _FAILURE_STATUSES and not str(output.get("answer") or "").strip() and node["role"] != "planner":
            if owner:
                self._retry_or_fail(run, task_id, nid, lease_id, status, output.get("uncertainty") or status)
            return

        dep_refs = {d["node"]: d["ref"] for d in package.get("dependencies", [])}
        previous = rt.store.latest("result", f"{task_id}/{nid}")
        ref = rt.store.put("result", f"{task_id}/{nid}", {
            "output": output, "binding": binding.to_dict(), "core": core_id,
            "state_version": contract.state_version, "dep_refs": dep_refs,
            "usage": result.usage.to_dict(), "children": result.children_spawned,
        }, supersedes=previous, meta={"run": run.id, "lease": lease_id})
        rt.fabric.publish(Envelope(EventType.RESULT_READY, task_id, nid, core_id, topic=f"task:{task_id}",
                                   state_version=contract.state_version, refs=[str(ref)]))
        passed, detail = self._acceptance(node, output)
        decision = rt.state.propose_result(ResultProposal(
            task_id, nid, core_id, lease_id, contract.state_version, dep_refs, str(ref), node["role"],
            passed, detail))
        if decision.accepted:
            rt.emit("result_accepted", run=run.id, task=task_id, node=nid,
                    message=f"Accepted {node['title']} ({ref})", data={"ref": str(ref)})
            if node["role"] == "planner":
                self._apply_plan(run, task_id, nid, output)
            elif shift and shift.get("type") == "SUBTASK_REQUEST":
                self._handle_subtask(run, task_id, nid, shift)
        else:
            rt.emit("result_rejected", run=run.id, task=task_id, node=nid,
                    message=f"Rejected {node['title']}: {decision.reasons[0]}",
                    data={"reasons": decision.reasons, "ref": str(ref)})
            current = rt.state.node(task_id, nid)
            if current and current["lease_id"] == lease_id and current["status"] == RUNNING:
                self._retry_or_fail(run, task_id, nid, lease_id, "REJECTED", "; ".join(decision.reasons))

    @staticmethod
    def _acceptance(node: dict, output: dict) -> tuple[bool, str]:
        if node["role"] == "planner":
            nodes = output.get("nodes")
            return bool(nodes) and isinstance(nodes, list), "planner returned no nodes"
        text = str(output.get("answer") or output.get("summary") or "").strip()
        return bool(text), "empty answer"

    # ------------------------------------------------------------------ requeue / failure
    def _requeue(self, task_id, nid, lease_id, reason, *, fields=None, count_attempt=True, validity=None):
        ops = [{"op": "revoke_lease", "lease_id": lease_id, "reason": reason[:200]}]
        if fields:
            ops.append({"op": "update_node", "task_id": task_id, "node_id": nid, "fields": fields})
        ops.append({"op": "reissue_nodes", "task_id": task_id, "node_ids": [nid], "validity": validity,
                    "reason": reason[:200], "count_attempt": count_attempt})
        self.rt.state.transact(ops, {"kind": "requeue"})
        self.rt.signal.notify()

    def _retry_or_fail(self, run, task_id, nid, lease_id, failure, detail, avoid=None):
        rt = self.rt
        node = rt.state.node(task_id, nid)
        if node["attempt"] + 1 < int(self.cfg["max_attempts"]):
            self._requeue(task_id, nid, lease_id, f"{failure}: {detail}",
                          fields={"avoid_provider": avoid} if avoid else None)
            rt.emit("retry", run=run.id, task=task_id, node=nid,
                    message=f"Retrying {node['title']} ({failure})"
                            + (f" on a different provider than {avoid}" if avoid else ""))
            return
        rt.state.transact([
            {"op": "revoke_lease", "lease_id": lease_id, "reason": failure},
            {"op": "update_node", "task_id": task_id, "node_id": nid,
             "fields": {"status": FAILED, "failure": failure, "failure_detail": str(detail)[:500],
                        "owner": None, "lease_id": None}},
        ], {"kind": "node_failed"})
        rt.emit("CORE_FAILED", run=run.id, task=task_id, node=nid,
                message=f"{node['title']} failed after {node['attempt'] + 1} attempt(s): {failure}")
        rt.signal.notify()

    # ------------------------------------------------------------------ dynamic shifting
    def _checkpoint(self, task_id, node, binding, output, shift):
        task = self.rt.state.task(task_id)
        return self.rt.store.put("context", f"{task_id}/{node['id']}/checkpoint", {
            "goal": task["goal"], "task_node": node["id"], "state_version": node["contract_version"],
            "focus_contract_ref": f"task://{task_id}/{node['id']}",
            "completed": output.get("summary") or "", "partial_answer": output.get("answer") or "",
            "unconfirmed": output.get("uncertainty"), "assumptions": [],
            "evidence_refs": [task["nodes"][d]["result_ref"] for d in node["deps"]],
            "dependencies": list(node["deps"]), "pending_requests": [shift.get("type")],
            "pending_tool_effects": [],
            "next_action": shift.get("checkpoint_next_action") or shift.get("requested_capability") or "",
            "from_binding": binding.to_dict(),
        })

    def _handle_shift(self, run, task_id, node, lease_id, binding, output, shift) -> bool:
        rt = self.rt
        kind = shift.get("type")
        nid, tier = node["id"], node["tier"]

        def text(key: str) -> str:
            return str(shift.get(key) or "").strip()

        if kind == "SWARM_REQUEST":
            requested = int(shift.get("swarm_children") or 0)
            decision = rt.swarm.consider(run, node, requested, text("reason"), binding.provider)
            if decision.action == "deny":
                rt.emit("SWARM_REQUEST", run=run.id, task=task_id, node=nid,
                        message=f"Swarm request for {node['title']} denied: {decision.reason}",
                        data=decision.to_dict())
                return False
            checkpoint = self._checkpoint(task_id, node, binding, output, shift)
            self._requeue(task_id, nid, lease_id, "swarm request queued for re-dispatch", count_attempt=False,
                          fields={"swarm_request": {"children": requested, "purpose": text("reason")},
                                  "checkpoint_ref": str(checkpoint)})
            rt.emit("SWARM_REQUEST", run=run.id, task=task_id, node=nid,
                    message=f"{node['title']}: swarm request accepted ({decision.action})", data=decision.to_dict())
            return True

        if kind == "ESCALATION_REQUEST":
            new_tier = TIER_UP.get(tier)
            checks = [("no stronger tier", new_tier), ("missing reason", text("reason")),
                      ("missing attempted work", text("attempted")),
                      ("missing expected benefit", text("expected_benefit")),
                      ("escalation limit reached", node["escalations"] < int(self.cfg["max_escalations"]))]
        else:
            new_tier = TIER_DOWN.get(tier)
            checks = [("no cheaper tier", new_tier),
                      ("missing continuation checkpoint", text("checkpoint_next_action"))]
        problems = [message for message, ok in checks if not ok]
        if problems:
            rt.emit(kind, run=run.id, task=task_id, node=nid,
                    message=f"{kind.replace('_', ' ').title()} denied for {node['title']}: {', '.join(problems)}",
                    data={"granted": False, "problems": problems})
            return False

        checkpoint = self._checkpoint(task_id, node, binding, output, shift)
        fields = {"tier": new_tier, "checkpoint_ref": str(checkpoint), "avoid_provider": None}
        if node["role"] in TIERS:
            fields["role"] = new_tier
        if kind == "ESCALATION_REQUEST":
            fields["escalations"] = node["escalations"] + 1
        self._requeue(task_id, nid, lease_id, f"{kind}: {tier} → {new_tier}", fields=fields, count_attempt=False)
        rt.emit(kind, run=run.id, task=task_id, node=nid,
                message=f"{node['title']}: {tier} → {new_tier} ({text('reason') or text('checkpoint_next_action')})",
                status=f"Moving {node['title']} to a {new_tier} model",
                data={"granted": True, "from": tier, "to": new_tier, "checkpoint": str(checkpoint)})
        return True

    def _handle_subtask(self, run, task_id, parent_id, shift) -> None:
        rt = self.rt
        task = rt.state.task(task_id)
        title = str(shift.get("subtask_title") or "").strip()
        problems = [m for m, ok in (("missing subtask title", title),
                                    ("missing purpose", str(shift.get("reason") or "").strip()),
                                    ("missing expected benefit", str(shift.get("expected_benefit") or "").strip()))
                    if not ok]
        consumers = [n for n in task["nodes"].values() if n["role"] == "synthesizer" and n["status"] == WAITING]
        if not consumers:
            problems.append("no waiting synthesis step can consume the subtask")
        if not problems:
            parent = task["nodes"][parent_id]
            index = sum(1 for k in task["nodes"] if k.startswith(f"{parent_id}-sub")) + 1
            spec = {"id": f"{parent_id}-sub{index}", "title": title, "instructions": str(shift.get("reason")),
                    "role": parent["role"] if parent["role"] in TIERS else "performance",
                    "tier": parent["tier"], "deps": list(parent["deps"])}
            decision = rt.state.propose_graph(task_id, task["version"], [spec], max_nodes=int(self.cfg["max_nodes"]))
            if decision.accepted:
                rt.state.transact([{"op": "update_node", "task_id": task_id, "node_id": c["id"],
                                    "fields": {"deps": c["deps"] + [spec["id"]]}} for c in consumers],
                                  {"kind": "subtask"})
                rt.emit("SUBTASK_REQUEST", run=run.id, task=task_id, node=parent_id,
                        message=f"Added subtask “{title}”", data={"granted": True, "node": spec["id"]})
                return
            problems += decision.reasons
        rt.emit("SUBTASK_REQUEST", run=run.id, task=task_id, node=parent_id,
                message=f"Subtask request denied: {', '.join(problems)}", data={"granted": False, "problems": problems})

    def _redirect_to_cores(self, run, task_id, node, n: int) -> None:
        """Swarm Governor redirect: split the node across idle logical cores instead of native children."""
        rt = self.rt
        task = rt.state.task(task_id)
        nid = node["id"]
        role = node["role"] if node["role"] in TIERS or node["role"] == "retrieval" else "performance"
        parts = [{"id": f"{nid}-p{k}", "title": f"{node['title']} (part {k}/{n})",
                  "instructions": f"{node['instructions']} Handle partition {k} of {n}; avoid overlap.".strip(),
                  "role": role, "tier": node["tier"], "deps": list(node["deps"]), "partition": f"{k}/{n}"}
                 for k in range(1, n + 1)]
        decision = rt.state.propose_graph(task_id, task["version"], parts, max_nodes=int(self.cfg["max_nodes"]))
        if decision.accepted:
            rt.state.transact([{"op": "update_node", "task_id": task_id, "node_id": nid, "fields": {
                "deps": list(node["deps"]) + [p["id"] for p in parts], "swarm_request": None,
                "role": "synthesizer",
                "instructions": f"{node['instructions']} Merge the partition results into one answer.".strip()}}],
                {"kind": "swarm_redirect"})
            rt.emit("SWARM_REQUEST", run=run.id, task=task_id, node=nid,
                    message=f"Split {node['title']} across {n} idle cores instead of native children",
                    data={"action": "redirect", "parts": [p["id"] for p in parts]})
        else:
            rt.state.transact([{"op": "update_node", "task_id": task_id, "node_id": nid,
                                "fields": {"swarm_request": None}}], {"kind": "swarm_redirect"})
        rt.signal.notify()

    def _apply_plan(self, run, task_id, plan_id, output) -> None:
        rt = self.rt
        task = rt.state.task(task_id)
        max_nodes = int(self.cfg["max_nodes"])
        raw_nodes = [n for n in (output.get("nodes") or []) if isinstance(n, dict)][: max_nodes - 1]
        id_map, specs = {}, []
        for raw in raw_nodes:
            nid = _slug(raw.get("id") or raw.get("title"))
            while nid in id_map.values() or nid in task["nodes"]:
                nid = f"{nid}-{len(specs) + 1}"
            id_map[str(raw.get("id") or raw.get("title"))] = nid
            role = raw.get("role") if raw.get("role") in _PLANNER_ROLES else "performance"
            tier = raw.get("tier") if raw.get("tier") in TIERS else "performance"
            spec = {"id": nid, "title": str(raw.get("title") or nid)[:120],
                    "instructions": str(raw.get("instructions") or "")[:2000], "role": role, "tier": tier,
                    "_deps": [str(d) for d in raw.get("deps") or []]}
            children = int(raw.get("swarm_children") or 0)
            if children > 0:
                spec["swarm_request"] = {"children": children, "purpose": spec["title"]}
            specs.append(spec)
        for spec in specs:
            spec["deps"] = [id_map[d] for d in spec.pop("_deps") if d in id_map and id_map[d] != spec["id"]]
        sinks = [s["id"] for s in specs if not any(s["id"] in o["deps"] for o in specs)]
        if len(sinks) > 1 and not any(s["role"] == "synthesizer" for s in specs):
            specs.append({"id": "synthesize", "title": "Synthesize findings", "role": "synthesizer",
                          "tier": "flagship", "instructions": "Combine the accepted results.", "deps": sinks})
        decision = rt.state.propose_graph(task_id, task["version"], specs, max_nodes=max_nodes)
        if decision.accepted:
            parallel = sum(1 for s in specs if not s["deps"])
            rt.emit("graph_committed", run=run.id, task=task_id, message=f"Plan accepted: {len(specs)} nodes",
                    status=f"Working on {parallel} parallel part(s)" if parallel > 1 else "Working on it",
                    data={"nodes": [s["id"] for s in specs]})
            return
        rt.emit("graph_rejected", run=run.id, task=task_id, message=f"Plan rejected: {decision.reasons[0]}",
                data={"reasons": decision.reasons})
        plan = rt.state.node(task_id, plan_id)
        if plan["attempt"] + 1 < int(self.cfg["max_attempts"]):
            rt.state.transact([{"op": "reissue_nodes", "task_id": task_id, "node_ids": [plan_id],
                                "validity": "INVALID", "reason": "graph proposal rejected"}], {"kind": "replan"})
        else:
            rt.state.transact([{"op": "update_node", "task_id": task_id, "node_id": plan_id,
                                "fields": {"status": FAILED, "failure": "CONFLICT",
                                           "failure_detail": "; ".join(decision.reasons)}}], {"kind": "plan_failed"})
        rt.signal.notify()

    # ------------------------------------------------------------------ services
    async def _run_service(self, run, task_id, nid, lease_id) -> None:
        await asyncio.sleep(0)
        rt = self.rt
        node = rt.state.node(task_id, nid)
        service = node["service"]
        action_id = f"{task_id}/{nid}/v{node['contract_version']}"
        dep_refs = {d: rt.state.node(task_id, d)["result_ref"] for d in node["deps"]}
        try:
            output = rt.services.run(service["name"], service.get("args", {}), action_id=action_id)
        except Exception as exc:
            rt.emit("core_failed", run=run.id, task=task_id, node=nid, message=f"service {service['name']}: {exc}")
            if rt.state.node(task_id, nid)["lease_id"] == lease_id:
                self._retry_or_fail(run, task_id, nid, lease_id, FailureCode.TOOL_FAILURE.value, repr(exc))
            return
        compute_ref = rt.store.put("compute", f"{task_id}/{nid}",
                                   {"service": service, "action_id": action_id, "output": output})
        ref = rt.store.put("result", f"{task_id}/{nid}", {
            "output": output, "compute_ref": str(compute_ref), "core": "service",
            "state_version": node["contract_version"], "dep_refs": dep_refs,
        }, supersedes=rt.store.latest("result", f"{task_id}/{nid}"))
        decision = rt.state.propose_result(ResultProposal(task_id, nid, "service", lease_id,
                                                          node["contract_version"], dep_refs, str(ref), "service"))
        rt.emit("result_accepted" if decision.accepted else "result_rejected", run=run.id, task=task_id, node=nid,
                message=f"Service {service['name']} → {output.get('answer')}"
                        + ("" if decision.accepted else f" (rejected: {decision.reasons[0]})"))

    # ------------------------------------------------------------------ completion
    def _finish(self, run, task_id) -> None:
        rt = self.rt
        out = assembler.assemble(rt, task_id)
        n = out["nodes"]
        answered_by = (f"kernel · {n} node{'s' if n != 1 else ''} · peak {run.peak_processes} "
                       f"process{'es' if run.peak_processes != 1 else ''}")
        rt.state.transact([{"op": "finish_task", "task_id": task_id, "status": "done",
                            "answer": out["answer"], "answered_by": answered_by}], {"kind": "finish"})
        run.finish("done", answer=out["answer"], answered_by=answered_by, info=out)
        rt.emit("run_done", run=run.id, task=task_id, message="Done", status="Done", data=out)

    def _fail(self, run, task_id, failed, code=None, detail="") -> None:
        rt = self.rt
        codes = code or ", ".join(sorted({n.get("failure") or "FAILED" for n in failed})) or "FAILED"
        detail = detail or "; ".join(f"{n['title']}: {n.get('failure_detail', '')}" for n in failed)
        if rt.state.task(task_id)["status"] == "open":
            rt.state.transact([{"op": "finish_task", "task_id": task_id, "status": "failed",
                                "answer": None, "answered_by": None}], {"kind": "fail"})
        run.finish("failed", error=f"{codes}: {detail}".strip())
        rt.emit("run_failed", run=run.id, task=task_id, message=f"Failed — {codes}", status=f"Failed: {codes}",
                data={"failure": codes, "detail": detail})

    async def _drain(self, run) -> None:
        """Release anything still in flight once the task has ended (no live processes after a run)."""
        rt = self.rt
        leftovers = list(run.inflight.items())
        for lease_id, task in leftovers:
            if rt.state.lease_is_live(lease_id):
                rt.leases.revoke(lease_id, "run ended")
            task.cancel()
        if leftovers:
            await asyncio.gather(*(t for _, t in leftovers), return_exceptions=True)
