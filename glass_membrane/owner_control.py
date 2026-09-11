"""Owner-only in-process control plane; deliberately not exposed by the HTTP API.

Call methods on Host.loop (Host.sync/call). All overrides expire with this
dedicated desktop session unless the owner explicitly exports a profile.
"""
import copy
import json
import math
import time

from .blackboard import TIERS
from .registry import Config, Binding
from .refs import new_id


class OwnerControl:
    def __init__(self, runtime):
        self.rt = runtime
        self.baseline = copy.deepcopy(runtime.config.data)
        self.audit = []

    def record(self, action, before=None, after=None):
        entry = {"time": time.time(), "actor": "desktop-owner", "action": action,
                 "scope": "this override session", "before": before, "after": after}
        # Write the audit before changing in-memory settings.
        if self.rt.data_dir:
            path = self.rt.data_dir / "logs" / "owner-controls.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, default=str) + "\n")
        self.audit.append(entry)
        self.rt.emit("owner_override", message=action, data=entry)

    def snapshot(self):
        return copy.deepcopy({"config": self.rt.config.data, "runtime": self.rt.snapshot(),
                              "state": self.rt.state.state,
                              "mailboxes": {key: {"pending": box.pending(), "capacities": box.capacities,
                                                  "messages": {k: [str(e) for e in q] for k, q in box.queues.items()}}
                                            for key, box in self.rt.fabric.mailboxes.items()},
                              "audit": self.audit[-200:]})

    def idle(self):
        if any(r.status in ("filtering", "running") or r.inflight for r in self.rt.runs.values()) or self.rt.pools.total_live:
            raise ValueError("Finish or cancel active runs before applying session configuration.")

    def binding(self, value):
        if not isinstance(value, dict) or set(value) - {"provider", "model", "effort"}:
            raise ValueError("Binding accepts provider, model and effort only.")
        if value.get("provider") not in self.rt.adapters:
            raise ValueError("Binding provider must be available in this session.")
        for field in ("model", "effort"):
            if value.get(field) is not None and (not isinstance(value[field], str) or len(value[field]) > 200):
                raise ValueError(f"Invalid {field}")
        if value.get("effort") not in (None, "", "low", "medium", "high", "xhigh", "max"):
            raise ValueError("Unknown effort; provider support still depends on the selected model.")
        return Binding(**value).to_dict()

    def apply_config(self, data):
        self.idle()
        if not isinstance(data, dict) or set(data) != set(self.baseline):
            raise ValueError("Use the complete configuration with its existing sections.")
        for section in ("runtime", "filter", "swarm", "budget"):
            values, expected = data.get(section), self.baseline[section]
            if not isinstance(values, dict) or set(values) != set(expected):
                raise ValueError(f"Unexpected fields in {section}")
            for key, value in values.items():
                default = expected[key]
                if isinstance(default, bool):
                    if type(value) is not bool:
                        raise ValueError(f"{section}.{key} must be boolean")
                else:
                    if type(value) not in (int, float) or not math.isfinite(value):
                        raise ValueError(f"{section}.{key} must be finite")
                    if isinstance(default, int) and type(value) is not int:
                        raise ValueError(f"{section}.{key} must be an integer")
                    minimum = 0 if key in ("max_escalations", "control_headroom", "max_children_total", "max_children_per_core") or section == "filter" else .01
                    if not minimum <= value <= (1 if section == "filter" else 10000000):
                        raise ValueError(f"{section}.{key} is outside its valid range")
        if not isinstance(data.get("providers"), dict) or set(data["providers"]) != set(self.baseline["providers"]):
            raise ValueError("Provider definitions must match the session.")
        for provider, original in self.baseline["providers"].items():
            current = data["providers"][provider]
            if not isinstance(current, dict) or set(current) != set(original):
                raise ValueError("Unexpected provider fields")
            if current.get("command") != original.get("command"):
                raise ValueError("Provider executable changes require editing models.toml and restarting.")
            if type(current.get("max_processes")) is not int or not 1 <= current["max_processes"] <= 256:
                raise ValueError("Provider process ceiling must be 1–256")
        if not isinstance(data.get("tiers"), dict) or set(data["tiers"]) != set(TIERS):
            raise ValueError("All three capability tiers are required.")
        for tier in TIERS:
            settings = data["tiers"][tier]
            if not isinstance(settings, dict) or set(settings) != {"bindings"}:
                raise ValueError("A tier contains bindings only")
            bindings = settings["bindings"]
            if not isinstance(bindings, list) or not 1 <= len(bindings) <= 16:
                raise ValueError("Each tier needs 1–16 bindings")
            # Unavailable configured providers may be retained unchanged; at least
            # one binding must remain usable, so the scheduler cannot deadlock.
            usable = []
            for binding in bindings:
                if not isinstance(binding, dict):
                    raise ValueError("Each binding must be an object")
                if binding.get("provider") in self.rt.adapters:
                    usable.append(self.binding(binding))
                elif binding not in self.baseline["tiers"][tier]["bindings"]:
                    raise ValueError("New binding uses an unavailable provider")
            if not usable:
                raise ValueError(f"{tier} needs an available provider")
        self.record("apply configuration", copy.deepcopy(self.rt.config.data), copy.deepcopy(data))
        rt = self.rt
        rt.config.data = Config(data).data
        rt.budget.max_calls = int(data["budget"]["max_calls_per_run"])
        rt.context.max_chars = int(data["runtime"]["context_max_chars"])
        rt.leases.default_ttl = float(data["runtime"]["lease_ttl_s"])
        rt.state.journal.fsync = data["runtime"]["fsync"]
        for name, pool in rt.pools.pools.items():
            pool.ceiling = data["providers"][name]["max_processes"]
            pool.cap = min(pool.cap, pool.ceiling)
            pool.headroom = int(data["runtime"]["control_headroom"])
        rt.signal.notify()
        return "Configuration applied to this session. Saved profiles can be imported later."

    def pause(self, run_id, paused):
        run = self.rt.runs[run_id]
        if run.status != "running":
            raise ValueError("Pause dispatch is available after routing, while the run is active.")
        self.record("pause dispatch" if paused else "resume dispatch", run.paused, bool(paused))
        run.paused = bool(paused)
        run.status_line = "Dispatch paused; in-flight calls may finish" if paused else "Dispatch resumed"
        self.rt.signal.notify()
        return run.status_line

    def edit_node(self, run_id, node_id, tier, instructions, binding=None, core=None, expected_version=None):
        rt = self.rt
        run = rt.runs[run_id]
        if run.status != "running" or not run.task_id:
            raise ValueError("Select a node in an active routed run.")
        if expected_version is not None and rt.state.task(run.task_id)["version"] != expected_version:
            raise ValueError("Task changed since you loaded it. Refresh and reload the node before applying.")
        if tier not in TIERS or not isinstance(instructions, str) or len(instructions) > 20000:
            raise ValueError("Invalid tier or instructions")
        node = rt.state.node(run.task_id, node_id)
        if not node or node.get("service") or node["role"] == "planner":
            raise ValueError("Select a reasoning node; planner/service rewrites are unsupported.")
        binding = self.binding(binding) if binding else None
        if core and (core not in rt.slots.cores or not rt.slots.cores[core].enabled):
            raise ValueError("Choose an enabled reasoning core or leave the core blank for automatic allocation.")
        affected = {node_id} | rt.board.dependents(run.task_id, node_id)
        task = rt.state.task(run.task_id)
        leases = [task["nodes"][nid]["lease_id"] for nid in affected if task["nodes"][nid]["lease_id"]]
        fields = {"tier": tier, "instructions": instructions, "binding_override": binding, "preferred_core": core,
                  "checkpoint_ref": None, "failure": None, "failure_detail": None}
        self.record("revise and requeue node", copy.deepcopy(node), fields)
        ops = [{"op": "add_constraint", "task_id": run.task_id, "constraint_id": new_id("owner-"),
                "text": f"Owner revised assignment {node_id}.", "scope": node_id}]
        ops += [{"op": "revoke_lease", "lease_id": lease, "reason": "owner revised assignment"} for lease in leases]
        ops += [{"op": "update_node", "task_id": run.task_id, "node_id": node_id, "fields": fields},
                {"op": "reissue_nodes", "task_id": run.task_id, "node_ids": sorted(affected),
                 "reason": "owner revised assignment", "count_attempt": False}]
        rt.state.transact(ops, {"kind": "owner_node_override"})
        for lease in leases:
            if lease in run.inflight:
                run.inflight[lease].cancel()
        rt.signal.notify()
        return f"Requeued {node_id} and {len(affected)-1} dependent nodes with version checks."

    def core_enabled(self, core_id, enabled):
        core = self.rt.slots.cores[core_id]
        if not enabled and sum(c.enabled for c in self.rt.slots.cores.values()) <= 1 and core.enabled:
            raise ValueError("Keep at least one core enabled; use pause to stop dispatch.")
        if not enabled:
            for task in self.rt.state.state["tasks"].values():
                if task["status"] == "open" and any(n.get("preferred_core") == core_id for n in task["nodes"].values()):
                    raise ValueError("Clear active node assignments to this core first.")
        self.record("core availability", {core_id: core.enabled}, {core_id: bool(enabled)})
        core.enabled = bool(enabled)
        self.rt.signal.notify()
        return "Core availability updated for future assignments; current work can finish."

    def checkpoint(self):
        self.record("save checkpoint")
        self.rt.save_checkpoint()
        return "Checkpoint saved."
