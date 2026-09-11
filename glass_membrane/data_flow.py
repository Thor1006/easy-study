"""Read-only live task/dependency map for the owner desktop."""
import json
import tkinter as tk
from tkinter import ttk

COLORS = {"running": "#dceafa", "filtering": "#dceafa", "done": "#e0eddf",
          "failed": "#f4dddd", "cancelled": "#e5e5e5", "ready": "#fff1d5", "waiting": "#f4f4f4"}


def flow_graph(detail):
    """Return display nodes and directed edges from API data, never inferred work."""
    if not detail:
        return {}, []
    nodes = {"input": {"title": "Input", "phase": "done", "subtitle": detail.get("text", ""), "data": {"text": detail.get("text", "")}}}
    edges = []
    previous = "input"
    route = detail.get("route") or {}
    for index, attempt in enumerate(route.get("attempts") or []):
        key = f"route:{index}"
        nodes[key] = {"title": f"Filter {attempt.get('slot', '')}", "phase": "done" if attempt.get("ok") else "failed",
                      "subtitle": f"{attempt.get('provider', '')} · {attempt.get('mode') or attempt.get('failure', '')}", "data": attempt}
        edges.append((previous, key, False))
        previous = key
    if detail.get("experiment") or route.get("note"):
        nodes["routing"] = {"title": "Routing control", "phase": "done", "subtitle": route.get("note") or detail["experiment"], "data": route}
        edges.append((previous, "routing", False))
        previous = "routing"
    task = detail.get("task") or {}
    actual = task.get("nodes") or {}
    planner = next((key for key, n in actual.items() if n.get("role") == "planner"), None)
    for key, node in actual.items():
        binding = node.get("binding") or {}
        phase = node.get("phase") or node.get("status", "waiting")
        retry = node.get("attempt", 0)
        suffix = f" · retry {retry}" if retry or node.get("superseded_results") or node.get("rejections") else ""
        nodes["task:"+key] = {"title": node.get("title", key), "phase": phase,
                              "subtitle": f"{phase} · {node.get('tier', '')} · {node.get('owner') or binding.get('provider') or ''}{suffix}",
                              "data": node}
        deps = [dep for dep in node.get("deps", []) if dep in actual]
        if deps:
            edges.extend(("task:"+dep, "task:"+key, False) for dep in deps)
        elif planner and key != planner:
            edges.append(("task:"+planner, "task:"+key, True))
        else:
            edges.append((previous, "task:"+key, False))
    if detail.get("status") in ("done", "failed", "cancelled"):
        nodes["output"] = {"title": "Answer" if detail["status"] == "done" else detail["status"].capitalize(),
                           "phase": detail["status"], "subtitle": detail.get("answered_by") or detail.get("error") or "",
                           "data": {"answer": detail.get("answer"), "error": detail.get("error"), "info": detail.get("info")}}
        sinks = ["task:"+key for key in actual if not any(key in n.get("deps", []) for n in actual.values()) and (key != planner or len(actual) == 1)]
        edges.extend((key, "output", False) for key in (sinks or [previous]))
    elif not actual:
        nodes["pending"] = {"title": "Processing", "phase": detail.get("status", "running"),
                            "subtitle": detail.get("status_line", ""), "data": route}
        edges.append((previous, "pending", False))
    return nodes, edges


def layout(nodes, edges):
    """Layer a DAG; malformed cyclic snapshots still get a finite readable layout."""
    depths, pending = {}, set(nodes)
    while pending:
        ready = sorted(key for key in pending if all(a in depths for a, b, _ in edges if b == key))
        if not ready:
            ready = sorted(pending)
        for key in ready:
            depths[key] = 1 + max((depths.get(a, -1) for a, b, _ in edges if b == key), default=-1)
            pending.remove(key)
    columns = {}
    for key in nodes:
        columns.setdefault(depths[key], []).append(key)
    positions = {}
    for depth, items in columns.items():
        for row, key in enumerate(items):
            positions[key] = (20 + depth * 270, 20 + row * 120)
    return positions


class DataFlow(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=6)
        self.nodes, self.edges = {}, []
        self.selected = None
        self.zoom = 1.0
        self.run_id = None
        self.status = tk.StringVar(value="Select a question to see its live data flow.")
        ttk.Label(self, textvariable=self.status, wraplength=750).pack(anchor="w")
        toolbar = ttk.Frame(self)
        toolbar.pack(fill="x", pady=5)
        ttk.Label(toolbar, text="Node").pack(side="left")
        self.node_id = tk.StringVar()
        self.picker = ttk.Combobox(toolbar, textvariable=self.node_id, state="readonly", width=28)
        self.picker.pack(side="left", padx=6)
        self.picker.bind("<<ComboboxSelected>>", lambda e: self.inspect(self.node_id.get()))
        ttk.Button(toolbar, text="−", width=3, command=lambda: self.scale(.8)).pack(side="right")
        ttk.Button(toolbar, text="+", width=3, command=lambda: self.scale(1.25)).pack(side="right")
        ttk.Button(toolbar, text="Reset view", command=self.reset).pack(side="right", padx=5)
        panes = ttk.Panedwindow(self, orient="vertical")
        panes.pack(fill="both", expand=True)
        graph = ttk.Frame(panes)
        panes.add(graph, weight=3)
        self.canvas = tk.Canvas(graph, bg="white", highlightthickness=0, height=220)
        sx = ttk.Scrollbar(graph, orient="horizontal", command=self.canvas.xview)
        sy = ttk.Scrollbar(graph, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        sx.pack(side="bottom", fill="x")
        sy.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta/120), "units"))
        self.canvas.bind("<ButtonPress-2>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B2-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        inspector = ttk.Frame(panes)
        panes.add(inspector, weight=1)
        self.details = tk.Text(inspector, height=5, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(inspector, command=self.details.yview)
        self.details.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.details.pack(fill="both", expand=True)
        ttk.Label(self, text="Blue: running   Green: accepted   Yellow: ready   Red: failed   Dashed: planner dispatch\nClick a node for bindings, dependencies, validity, result references and rejection history.").pack(anchor="w", pady=4)

    def update_run(self, detail):
        run_id = (detail or {}).get("id")
        if run_id != self.run_id:
            self.selected = None
            self.run_id = run_id
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        self.nodes, self.edges = flow_graph(detail)
        self.picker.configure(values=list(self.nodes))
        if detail:
            task = detail.get("task") or {}
            constraints = "; ".join(c["text"] for c in task.get("constraints", []))
            self.status.set(f"{detail.get('status_line') or detail['status']} · state v{task.get('version', '—')}" + (f"\nConstraints: {constraints}" if constraints else ""))
        else:
            self.status.set("Select a question to see its live data flow.")
        if self.selected not in self.nodes:
            self.selected = next(iter(self.nodes), None)
        self.draw()
        self.inspect(self.selected)

    def scale(self, factor):
        self.zoom = min(1.6, max(.4, self.zoom*factor))
        self.draw()

    def reset(self):
        self.zoom = 1
        self.draw()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)

    def draw(self):
        self.canvas.delete("all")
        positions = layout(self.nodes, self.edges)
        z = self.zoom
        for a, b, planned in self.edges:
            if a not in positions or b not in positions:
                continue
            x, y = positions[a]
            xx, yy = positions[b]
            self.canvas.create_line((x+225)*z, (y+43)*z, xx*z, (yy+43)*z,
                                    arrow="last", fill="#707070", dash=(4, 3) if planned else (), tags="edge")
        for index, (key, node) in enumerate(self.nodes.items()):
            x, y = positions[key]
            tag = f"node{index}"
            self.canvas.create_rectangle(x*z, y*z, (x+225)*z, (y+86)*z,
                                         fill=COLORS.get(node["phase"], "#f4f4f4"),
                                         outline="#202020" if key == self.selected else "#aaaaaa", tags=tag)
            title = node["title"][:65]
            subtitle = node["subtitle"][:100]
            self.canvas.create_text((x+10)*z, (y+10)*z, anchor="nw", text=title, width=205*z,
                                    font=("Segoe UI", max(8, int(10*z)), "bold"), tags=tag)
            self.canvas.create_text((x+10)*z, (y+44)*z, anchor="nw", text=subtitle, width=205*z,
                                    font=("Segoe UI", max(7, int(9*z))), tags=tag)
            self.canvas.tag_bind(tag, "<Button-1>", lambda e, k=key: self.inspect(k))
        self.canvas.configure(scrollregion=self.canvas.bbox("all") or (0, 0, 1, 1))

    def inspect(self, key):
        self.selected = key
        self.node_id.set(key or "")
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        if key in self.nodes:
            self.details.insert("1.0", json.dumps(self.nodes[key]["data"], indent=2, ensure_ascii=False))
        self.details.configure(state="disabled")
        self.draw()
