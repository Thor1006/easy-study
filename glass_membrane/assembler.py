"""Output assembly (Schematic §3): present current accepted results only.

Deterministic. It never accepts stale results and never hides unresolved
conflicts or material uncertainty.
"""

from __future__ import annotations

from .blackboard import DONE


def assemble(rt, task_id: str) -> dict:
    task = rt.state.task(task_id)
    nodes = task["nodes"]
    sinks = [n for nid, n in nodes.items()
             if n["status"] == DONE and n["role"] != "planner"
             and not any(nid in other["deps"] for other in nodes.values())]
    outputs = [(n, rt.store.content(n["result_ref"])["output"]) for n in sinks]
    if len(outputs) == 1:
        answer = outputs[0][1].get("answer") or outputs[0][1].get("summary") or ""
    else:
        answer = "\n\n".join(f"{n['title']}:\n{o.get('answer') or o.get('summary')}" for n, o in outputs)

    uncertainty, conflicts = [], []
    for node in nodes.values():
        if node["status"] != DONE or not node["result_ref"]:
            continue
        output = rt.store.content(node["result_ref"])["output"]
        if output.get("uncertainty"):
            uncertainty.append(f"{node['title']}: {output['uncertainty']}")
        if output.get("status") == "CONFLICT":
            conflicts.append(node["title"])
    return {
        "answer": answer,
        "uncertainty": uncertainty,
        "conflicts": conflicts,
        "result_refs": [n["result_ref"] for n, _ in outputs],
        "rejected_results": sum(len(n["rejections"]) for n in nodes.values()),
        "rerun_nodes": sum(1 for n in nodes.values() if n["superseded_results"]),
        "nodes": len(nodes),
    }
