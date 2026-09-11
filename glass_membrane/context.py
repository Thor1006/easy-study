"""Context as a service (Arch §10).

Builds a bounded, task-relevant package for one assignment instead of handing
every core the whole cache. Truncated evidence stays reachable by reference.
"""

from __future__ import annotations

from .refs import Ref


class ContextService:
    def __init__(self, state, store, max_chars: int = 12000) -> None:
        self.state = state
        self.store = store
        self.max_chars = max_chars

    def build(self, task_id: str, node_id: str) -> tuple[Ref, dict]:
        task = self.state.task(task_id)
        node = task["nodes"][node_id]
        per_dep = max(400, self.max_chars // max(1, len(node["deps"])))
        deps, omitted = [], []
        for dep_id in node["deps"]:
            dep = task["nodes"][dep_id]
            ref = dep["result_ref"]
            output = self.store.content(ref)["output"] if ref else {}
            text = output.get("answer") or output.get("summary") or ""
            truncated = len(text) > per_dep
            if truncated:
                omitted.append(ref)
            deps.append({"node": dep_id, "title": dep["title"], "ref": ref,
                         "summary": output.get("summary", ""), "answer": text[:per_dep],
                         "truncated": truncated, "uncertainty": output.get("uncertainty"),
                         "validity": dep["validity"]})
        checkpoint = self.store.content(node["checkpoint_ref"]) if node.get("checkpoint_ref") else None
        package = {
            "task": task_id, "node": node_id, "goal": task["goal"],
            "constraints": [c["text"] for c in task["constraints"]],
            "state_version": node["contract_version"], "dependencies": deps,
            "checkpoint": checkpoint, "omitted_refs": omitted, "packet": task.get("packet"),
        }
        return self.store.put("context", f"{task_id}/{node_id}", package), package
