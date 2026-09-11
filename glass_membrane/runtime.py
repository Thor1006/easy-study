"""Runtime host: wires the kernel components together (Arch §3, Schematic §2, §5).

One asyncio loop hosts every component. Runs are user inputs; a run either
ends at the filter (a direct answer), applies a steer, or drives a kernel task.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .blackboard import CANCELLED, DONE, RUNNING, TIERS, WAITING, Blackboard
from .budget import BudgetGovernor
from .context import ContextService
from .cores import SlotTable
from .events import Envelope, EventType, Signal
from .fabric import Fabric
from .gateway import Gateway
from .interrupt import InterruptController
from .journal import Journal
from .leases import LeaseManager
from .ledger import Ledger
from .permissions import Permissions
from .pools import Pools
from .refs import new_id
from .registry import Config, Registry
from .router import RouteDecision, Router, default_packet
from .scheduler import Scheduler
from .services import Services
from .state import StateManager
from .store import ObjectStore
from .swarm import SwarmGovernor
from .watchdog import Watchdog

log = logging.getLogger("glass_membrane")


@dataclass
class Run:
    id: str
    text: str
    created_at: float
    max_agents: int
    source: str = "api"
    status: str = "filtering"       # filtering | running | done | failed | cancelled
    kind: str = "request"           # request | steer
    answer: str | None = None
    answered_by: str | None = None
    task_id: str | None = None
    target_run: str | None = None
    error: str | None = None
    route: dict | None = None
    info: dict = field(default_factory=dict)
    experiment: str | None = None
    live_agents: int = 0
    peak_agents: int = 0
    live_processes: int = 0
    peak_processes: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    paused: bool = False
    status_line: str = "Received"
    finished_at: float | None = None
    inflight: dict = field(default_factory=dict, repr=False)
    done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    process_task: asyncio.Task | None = field(default=None, repr=False)

    def finish(self, status: str, *, answer=None, answered_by=None, error=None, info=None) -> None:
        self.status = status
        if answer is not None:
            self.answer = answer
        if answered_by is not None:
            self.answered_by = answered_by
        if error is not None:
            self.error = error
        if info:
            self.info = info
        self.finished_at = time.time()
        self.done_event.set()

    def to_dict(self) -> dict:
        from .illustrations import answer_parts
        data = {k: getattr(self, k) for k in (
            "id", "text", "created_at", "max_agents", "source", "status", "kind", "answer", "answered_by",
            "task_id", "target_run", "error", "route", "info", "experiment", "live_agents", "peak_agents",
            "live_processes", "peak_processes", "calls", "cost_usd", "status_line", "finished_at")}
        data["answer_parts"] = answer_parts(self.answer)
        return data


class Runtime:
    def __init__(self, data_dir: str | Path | None = None, *, config: Config | None = None,
                 adapters: dict | None = None, demo: bool = False) -> None:
        """`data_dir` holds state/ and cache/ (None = in memory only)."""
        self.data_dir = Path(data_dir) if data_dir else None
        self.config = config or Config.load()
        self.demo = demo
        rdir = self.data_dir
        self.store = ObjectStore(rdir / "cache" if rdir else None)
        journal = Journal(rdir / "state" / "journal" / "journal.jsonl" if rdir else None,
                          fsync=bool(self.config.runtime["fsync"]))
        self.checkpoint_path = rdir / "state" / "checkpoint" / "state.json" if rdir else None
        self.permissions = Permissions()
        checkpoint = str(self.checkpoint_path) if self.checkpoint_path and self.checkpoint_path.exists() else None
        self.state = StateManager.recover(journal, checkpoint=checkpoint, permissions=self.permissions)
        self.board = Blackboard(self.state)
        self.leases = LeaseManager(self.state, default_ttl=float(self.config.runtime["lease_ttl_s"]))
        self.signal = Signal()
        self.fabric = Fabric()
        self.adapters = dict(adapters or {})
        self.registry = Registry(self.config, self.adapters)
        self.pools = Pools({p: self.config.provider_ceiling(p) for p in self.adapters},
                           headroom=int(self.config.runtime["control_headroom"]), signal=self.signal)
        self.slots = SlotTable()
        for core_id in self.slots.cores:
            self.fabric.mailbox(core_id)
        self.ledger = Ledger()
        self.budget = BudgetGovernor(int(self.config.budget["max_calls_per_run"]))
        self.services = Services()
        self.context = ContextService(self.state, self.store, int(self.config.runtime["context_max_chars"]))
        self.swarm = SwarmGovernor(self)
        self.gateway = Gateway()
        self.router = Router(self)
        self.interrupt = InterruptController(self)
        self.scheduler = Scheduler(self)
        self.watchdog = Watchdog(self)
        self.runs: dict[str, Run] = {}
        self.events: collections.deque = collections.deque(maxlen=3000)
        self.event_seq = 0
        self._subscribers: list = []
        self._background: set[asyncio.Task] = set()
        self._watchdog_task: asyncio.Task | None = None
        self.recovery = self._reconcile_after_recovery()

    # ------------------------------------------------------------------ lifecycle
    def _reconcile_after_recovery(self) -> dict:
        """Processes from a previous session are gone: revoke their leases and mark open tasks interrupted."""
        ops, leases, tasks = [], 0, 0
        for lease in self.state.state["leases"].values():
            if not lease["revoked"] and not lease["released"]:
                ops.append({"op": "revoke_lease", "lease_id": lease["id"], "reason": "runtime restarted"})
                leases += 1
        for task in self.state.state["tasks"].values():
            if task["status"] == "open":
                ops.append({"op": "finish_task", "task_id": task["id"], "status": "interrupted"})
                tasks += 1
        if ops:
            self.state.transact(ops, {"kind": "recovery"})
        return {"revoked_leases": leases, "interrupted_tasks": tasks,
                "incomplete_journal_attempts": self.state.journal.incomplete_transactions()}

    async def start(self) -> None:
        if self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(self.watchdog.run(), name="gm-watchdog")

    async def stop(self) -> None:
        tasks = [r.process_task for r in self.runs.values() if r.process_task and not r.process_task.done()]
        for run in self.runs.values():
            tasks += list(run.inflight.values())
        if self._watchdog_task:
            tasks.append(self._watchdog_task)
            self._watchdog_task = None
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.save_checkpoint()

    def save_checkpoint(self) -> None:
        if self.checkpoint_path:
            self.state.checkpoint(self.checkpoint_path)

    # ------------------------------------------------------------------ events
    def subscribe(self, fn) -> None:
        self._subscribers.append(fn)

    def unsubscribe(self, fn) -> None:
        if fn in self._subscribers:
            self._subscribers.remove(fn)

    def emit(self, type: str, *, run: str | None = None, task: str | None = None, node: str | None = None,
             message: str = "", status: str | None = None, data: dict | None = None) -> dict:
        self.event_seq += 1
        event = {"seq": self.event_seq, "ts": time.time(), "type": type, "run": run, "task": task,
                 "node": node, "message": message, "data": data or {}}
        self.events.append(event)
        if status and run in self.runs:
            self.runs[run].status_line = status
        for fn in list(self._subscribers):
            try:
                fn(event)
            except Exception:
                log.exception("event subscriber failed")
        return event

    # ------------------------------------------------------------------ helpers
    @property
    def default_max_agents(self) -> int:
        return sum(self.config.provider_ceiling(p) for p in self.adapters) or 1

    def run_for_task(self, task_id: str) -> Run | None:
        return next((r for r in self.runs.values() if r.task_id == task_id), None)

    def task_summary(self, task_id: str) -> dict:
        task = self.state.task(task_id)
        return {"task_id": task_id, "run_id": task.get("run_id"), "goal": task["goal"],
                "nodes": [{"id": n["id"], "title": n["title"], "status": n["status"]}
                          for n in task["nodes"].values()]}

    def _active_task(self) -> tuple[Run, dict] | None:
        active = [r for r in self.runs.values() if r.status == "running" and r.task_id
                  and self.state.task(r.task_id)["status"] == "open"]
        if not active:
            return None
        run = max(active, key=lambda r: r.created_at)
        return run, self.task_summary(run.task_id)

    def new_run(self, text: str, *, max_agents: int | None = None, source: str = "api") -> Run:
        run = Run(id=new_id("run-"), text=text, created_at=time.time(),
                  max_agents=max(1, int(max_agents or self.default_max_agents)), source=source)
        self.runs[run.id] = run
        return run

    def create_task(self, run: Run, goal: str, nodes: list[dict], packet: dict | None = None) -> str:
        task_id = new_id("task-")
        self.state.transact([{"op": "create_task", "task_id": task_id, "goal": goal, "run_id": run.id,
                              "packet": packet}], {"kind": "create_task"})
        decision = self.state.propose_graph(task_id, 1, nodes, max_nodes=int(self.config.runtime["max_nodes"]))
        if not decision.accepted:
            raise ValueError(f"initial graph rejected: {decision.reasons}")
        run.task_id = task_id
        run.status = "running"
        return task_id

    @staticmethod
    def initial_graph(packet: dict) -> list[dict]:
        tier = packet.get("recommended_tier") if packet.get("recommended_tier") in TIERS else "performance"
        if packet.get("parallelizable"):
            planner_tier = "flagship" if packet.get("difficulty") == "high" else "performance"
            return [{"id": "plan", "title": "Plan the work", "role": "planner", "tier": planner_tier,
                     "instructions": "Decompose the goal into a task graph."}]
        return [{"id": "work", "title": "Answer the request", "role": tier, "tier": tier, "instructions": ""}]

    # ------------------------------------------------------------------ operator API
    async def submit(self, text: str, *, max_agents: int | None = None, source: str = "api",
                     force_route: bool = False, filter_bypass: bool = False) -> Run:
        inp = self.gateway.normalize(text, source=source)
        run = self.new_run(inp.text, max_agents=max_agents, source=source)
        if force_route or filter_bypass:
            run.experiment = "filter-bypass" if filter_bypass else "force-route"
        self.emit("run_submitted", run=run.id, message=inp.text[:200], status="Reading your request",
                  data={"max_agents": run.max_agents, "experiment": run.experiment})
        run.process_task = asyncio.create_task(self._process(run, inp, force_route, filter_bypass),
                                               name=f"gm-run-{run.id}")
        return run

    async def _process(self, run: Run, inp, force_route: bool, filter_bypass: bool) -> None:
        try:
            active = self._active_task()
            if filter_bypass:
                decision = RouteDecision("route", packet=default_packet(inp.text),
                                         note="experiment: filter bypassed into the scheduler")
                self.emit("experiment", run=run.id, message="Filter bypass (experiment control)")
            else:
                decision = await self.router.filter(run, inp, active_task=active[1] if active else None,
                                                    allow_answer=not force_route)
            run.route = decision.to_dict()

            if decision.mode == "answer":
                answered_by = f"filter · {decision.slot} {decision.tier}"
                run.finish("done", answer=decision.answer_text, answered_by=answered_by,
                           info={"answer_confidence": decision.answer_confidence})
                self.emit("run_done", run=run.id, message=f"Answered by {decision.slot}",
                          status="Filter answered directly", data={"answered_by": answered_by})
                return
            if decision.mode == "steer" and active:
                target, _summary = active
                result = self.interrupt.apply(target, target.task_id, inp.text, decision.steer)
                run.kind, run.target_run = "steer", target.id
                run.finish("done", answer=result.get("message", "Steer applied"),
                           answered_by="filter → interrupt controller", info=result)
                self.emit("run_done", run=run.id, message="Applied as a steer", status="Applied as a steer")
                return

            packet = decision.packet or default_packet(inp.text)
            nodes = self.initial_graph(packet)
            task_id = self.create_task(run, inp.text, nodes, packet)
            self.emit("task_created", run=run.id, task=task_id,
                      message=f"Routed to the kernel ({packet.get('recommended_tier')} tier)",
                      status="Planning the work" if nodes[0]["role"] == "planner" else "Working on it with one core",
                      data={"packet": packet})
            await self.scheduler.execute(run, task_id)
            if run.status == "running":
                task = self.state.task(task_id)
                run.finish(task["status"] if task["status"] != "open" else "failed", error=run.error)
        except asyncio.CancelledError:
            if run.status not in ("done", "failed"):
                run.finish("cancelled", error="cancelled")
            raise
        except Exception as exc:
            log.exception("run %s failed", run.id)
            run.finish("failed", error=f"{type(exc).__name__}: {exc}")
            self.emit("run_failed", run=run.id, message=str(exc), status="Failed")
        finally:
            self.signal.notify()
            self.save_checkpoint()

    async def steer(self, run_id: str, text: str, *, source: str = "api") -> dict:
        run = self.runs[run_id]
        if run.status != "running" or not run.task_id:
            raise ValueError("run is not active")
        inp = self.gateway.normalize(text, kind="steer", target_run=run_id, source=source)
        self.emit("STEER", run=run_id, task=run.task_id, message=f"Steer received: {inp.text[:160]}",
                  status="Checking what your steer affects")
        decision = await self.router.filter(run, inp, active_task=self.task_summary(run.task_id), kind_hint="steer")
        return self.interrupt.apply(run, run.task_id, inp.text, decision.steer if decision.mode == "steer" else None)

    async def set_cap(self, run_id: str, n: int) -> dict:
        run = self.runs[run_id]
        n = int(n)
        if not 1 <= n <= self.default_max_agents:
            raise ValueError(f"agent limit must be between 1 and {self.default_max_agents}")
        old, run.max_agents = run.max_agents, n
        if run.task_id:
            self.fabric.publish(Envelope(EventType.CAP_CHANGED, run.task_id, None, "operator",
                                         topic=f"task:{run.task_id}", payload={"old": old, "new": n}))
        self.emit("CAP_CHANGED", run=run_id, task=run.task_id, message=f"Agent limit {old} → {n}",
                  status=f"Agent limit set to {n}", data={"old": old, "new": n})
        self.signal.notify()
        return {"run": run_id, "max_agents": n, "previous": old}

    async def cancel(self, run_id: str, reason: str = "cancelled by operator") -> dict:
        run = self.runs[run_id]
        if run.task_id and self.state.task(run.task_id)["status"] == "open":
            task = self.state.task(run.task_id)
            ops, notices = [], []
            for node in task["nodes"].values():
                if node["status"] == RUNNING and node["lease_id"]:
                    ops.append({"op": "revoke_lease", "lease_id": node["lease_id"], "reason": reason})
                    notices.append((node["id"], node["owner"], node["lease_id"]))
                if node["status"] in (RUNNING, WAITING):
                    ops.append({"op": "update_node", "task_id": run.task_id, "node_id": node["id"],
                                "fields": {"status": CANCELLED, "owner": None, "lease_id": None}})
            ops.append({"op": "finish_task", "task_id": run.task_id, "status": "cancelled"})
            self.state.transact(ops, {"kind": "cancel"})
            for nid, owner, lease in notices:
                if owner:
                    self.fabric.send(Envelope(EventType.CANCEL, run.task_id, nid, "operator", to=owner,
                                              payload={"lease_id": lease, "reason": reason}))
        for task in list(run.inflight.values()):
            task.cancel()
        if run.process_task and not run.process_task.done() and run.process_task is not asyncio.current_task():
            run.process_task.cancel()
        if run.status not in ("done", "failed"):
            run.finish("cancelled", error=reason)
        self.emit("CANCEL", run=run_id, task=run.task_id, message=reason, status="Cancelled")
        self.signal.notify()
        return {"run": run_id, "status": run.status}

    async def wait(self, run_id: str, timeout: float | None = None) -> Run:
        run = self.runs[run_id]
        await asyncio.wait_for(run.done_event.wait(), timeout)
        if run.process_task:
            await asyncio.gather(run.process_task, return_exceptions=True)
        return run

    # ------------------------------------------------------------------ views
    def snapshot(self) -> dict:
        runs = sorted(self.runs.values(), key=lambda r: r.created_at, reverse=True)
        return {
            "version": __version__, "demo": self.demo, "event_seq": self.event_seq,
            "providers": self.pools.snapshot(), "slots": self.slots.snapshot(),
            "swarm": self.swarm.snapshot(), "ledger": self.ledger.totals(),
            "limits": {"default_max_agents": self.default_max_agents,
                       "provider_ceilings": {p: self.config.provider_ceiling(p) for p in self.adapters}},
            "profiles": {p: self.registry.profile(p) for p in self.adapters},
            "runs": [r.to_dict() for r in runs[:50]],
            "recovery": self.recovery,
        }

    def run_detail(self, run_id: str) -> dict:
        run = self.runs[run_id]
        detail = run.to_dict()
        detail["task"] = self.board.view(run.task_id) if run.task_id and self.state.task(run.task_id) else None
        detail["events"] = [e for e in self.events if e["run"] == run_id][-400:]
        detail["ledger"] = self.ledger.for_run(run_id)
        detail["ledger_totals"] = self.ledger.totals(run_id)
        return detail

    def result_content(self, ref: str) -> dict:
        return self.store.get(ref)


LIVE_DATA_DIR = Path(__file__).resolve().parent.parent / "runtime"
DEMO_DATA_DIR = LIVE_DATA_DIR / "demo"


def data_dir_for(demo: bool) -> Path:
    return DEMO_DATA_DIR if demo else LIVE_DATA_DIR


def build_live_adapters(config: Config, data_dir: str | Path = LIVE_DATA_DIR) -> dict:
    """Adapters for the installed Claude Code / Codex CLIs (no runtime state is touched)."""
    import shutil

    from .adapters.claude_code import ClaudeCodeAdapter
    from .adapters.codex import CodexAdapter

    sandbox = Path(data_dir) / "sandbox"
    adapters = {}
    claude_cmd = config.providers.get("claude", {}).get("command", "claude")
    codex_cmd = config.providers.get("codex", {}).get("command", "codex")
    if shutil.which(claude_cmd):
        adapters["claude"] = ClaudeCodeAdapter(claude_cmd, sandbox / "claude")
    if shutil.which(codex_cmd):
        adapters["codex"] = CodexAdapter(codex_cmd, sandbox / "codex")
    return adapters


def build_runtime(*, demo: bool = False, data_dir: str | Path | None = None,
                  config: Config | None = None) -> Runtime:
    """Create a runtime with simulated adapters (demo) or the real Claude Code / Codex CLIs."""
    from .adapters.fake import FakeAdapter, demo_delays

    config = config or Config.load()
    data_dir = Path(data_dir) if data_dir else data_dir_for(demo)
    if demo:
        adapters = {p: FakeAdapter(p, delays=demo_delays()) for p in ("claude", "codex")}
    else:
        adapters = build_live_adapters(config, data_dir)
        if not adapters:
            raise RuntimeError("Neither the claude nor the codex CLI was found on PATH. "
                               "Install one, or use --demo for simulated models.")
    return Runtime(data_dir, config=config, adapters=adapters, demo=demo)
