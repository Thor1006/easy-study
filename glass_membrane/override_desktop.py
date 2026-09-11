"""Separate, session-scoped owner console for the real runtime."""
import argparse
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from uuid import uuid4

from .desktop import Silicate
from .owner_control import OwnerControl


class OverrideSilicate(Silicate):
    def __init__(self, client, host, *, demo=False):
        self.control = OwnerControl(host.rt)
        self.owner_snapshot = None
        super().__init__(client, host, demo=demo)
        self.title("Silicate — Owner Override" + (" (Demo)" if demo else " (Live)"))
        self.refresh_owner()

    def _build(self):
        super()._build()
        from .data_flow import DataFlow
        self.data_flow = DataFlow(self.tabs)
        self.tabs.add(self.data_flow, text="Data flow")
        page = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(page, text="Owner controls")
        ttk.Label(page, text="Separate session • changes expire on exit • live requests use subscriptions").pack(anchor="w")
        tools = ttk.Frame(page)
        tools.pack(fill="x", pady=6)
        self.button(tools, "Refresh state", self.refresh_owner).pack(side="left")
        self.button(tools, "Pause dispatch", lambda: self.owner_call("pause", self.selected, True)).pack(side="left", padx=4)
        self.button(tools, "Resume", lambda: self.owner_call("pause", self.selected, False)).pack(side="left")
        self.button(tools, "Checkpoint", lambda: self.owner_call("checkpoint")).pack(side="left", padx=4)
        self.force_route, self.bypass = tk.BooleanVar(), tk.BooleanVar()
        ttk.Checkbutton(page, text="Force reasoning (next question)", variable=self.force_route).pack(anchor="w")
        ttk.Checkbutton(page, text="Bypass routing filter (next question)", variable=self.bypass).pack(anchor="w")
        sections = ttk.Notebook(page)
        sections.pack(fill="both", expand=True, pady=8)
        config = ttk.Frame(sections, padding=6)
        sections.add(config, text="Configuration")
        ttk.Label(config, text="Edit JSON. Apply only when all runs are finished or cancelled. Provider commands require restart.", wraplength=680).pack(anchor="w")
        self.config_text = self.text_area(config)
        actions = ttk.Frame(config)
        actions.pack(fill="x", pady=6)
        for label, command in (("Load current", self.load_config), ("Load startup", self.defaults),
                               ("Apply session", self.apply_config), ("Import…", self.import_config), ("Export…", self.export_config)):
            self.button(actions, label, command).pack(side="left", padx=2)
        nodes = ttk.Frame(sections, padding=6)
        sections.add(nodes, text="Nodes / cores")
        ttk.Label(nodes, text="Select a question, refresh state, then select its reasoning node. Changes requeue dependent work.", wraplength=680).pack(anchor="w")
        row = ttk.Frame(nodes)
        row.pack(fill="x", pady=6)
        self.node_id = tk.StringVar()
        self.node_choice = ttk.Combobox(row, textvariable=self.node_id, state="readonly", width=24)
        self.node_choice.pack(side="left")
        self.node_choice.bind("<<ComboboxSelected>>", lambda e: self.load_node())
        self.tier = tk.StringVar(value="performance")
        ttk.Combobox(row, textvariable=self.tier, values=("efficiency", "performance", "flagship"), state="readonly", width=14).pack(side="left", padx=4)
        self.core = tk.StringVar()
        ttk.Combobox(row, textvariable=self.core, values=("", *[f"E{i}" for i in range(6)], *[f"P{i}" for i in range(4)], "F0", "F1"), state="readonly", width=6).pack(side="left")
        ttk.Label(nodes, text="Core blank = automatic. Provider blank = tier bindings. Model/effort must be supported by your provider.").pack(anchor="w")
        row = ttk.Frame(nodes)
        row.pack(fill="x", pady=6)
        self.provider, self.model, self.effort = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for title, variable, values in (("Provider", self.provider, ("", *self.control.rt.adapters)), ("Model", self.model, None),
                                         ("Effort", self.effort, ("", "low", "medium", "high", "xhigh", "max"))):
            ttk.Label(row, text=title).pack(side="left", padx=3)
            widget = ttk.Combobox(row, textvariable=variable, values=values, state="readonly", width=12) if values else ttk.Entry(row, textvariable=variable, width=16)
            widget.pack(side="left")
        ttk.Label(nodes, text="Focus instructions").pack(anchor="w")
        self.instructions = self.text_area(nodes, height=5)
        self.button(nodes, "Revise / requeue selected node", self.revise_node).pack(anchor="w", pady=6)
        row = ttk.Frame(nodes)
        row.pack(fill="x")
        self.availability_core = tk.StringVar(value="E0")
        ttk.Combobox(row, textvariable=self.availability_core, values=tuple(self.control.rt.slots.cores), state="readonly", width=6).pack(side="left")
        self.button(row, "Enable core", lambda: self.owner_call("core_enabled", self.availability_core.get(), True)).pack(side="left", padx=4)
        self.button(row, "Disable new assignments", lambda: self.owner_call("core_enabled", self.availability_core.get(), False)).pack(side="left")
        inspect = ttk.Frame(sections, padding=6)
        sections.add(inspect, text="State / queues / audit")
        self.inspect_text = self.text_area(inspect)
        self.inspect_text.configure(state="disabled")
        self.button(inspect, "Export snapshot…", self.export_snapshot).pack(anchor="w", pady=4)
        self.owner_status = tk.StringVar(value="Refresh to inspect this session.")
        ttk.Label(page, textvariable=self.owner_status, wraplength=700).pack(fill="x")

    def render(self, detail):
        super().render(detail)
        self.data_flow.update_run(detail)

    def new(self):
        super().new()
        self.data_flow.update_run(None)

    def select(self, event=None):
        previous = self.selected
        super().select(event)
        if previous != self.selected:
            self.data_flow.update_run(None)

    def text_area(self, parent, height=12):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=5)
        text = tk.Text(frame, height=height, width=40, wrap="none", undo=True)
        sy = ttk.Scrollbar(frame, command=text.yview)
        sx = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        sy.pack(side="right", fill="y")
        sx.pack(side="bottom", fill="x")
        text.pack(fill="both", expand=True)
        return text

    def put(self, widget, data):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", json.dumps(data, indent=2, ensure_ascii=False))

    def submission_options(self):
        return {"force_route": self.force_route.get(), "filter_bypass": self.bypass.get()}

    def refresh_owner(self):
        def done(data, error):
            if error:
                self.owner_status.set(error)
                return
            self.owner_snapshot = data
            self.put(self.inspect_text, data)
            self.inspect_text.configure(state="disabled")
            tasks = data["state"]["tasks"].values()
            task = next((t for t in tasks if t.get("run_id") == self.selected), None)
            self.node_choice.configure(values=list(task["nodes"]) if task else [])
            if not self.config_text.get("1.0", "end").strip():
                self.load_config()
            self.owner_status.set("Snapshot refreshed. Configuration draft preserved. Select a node to load its latest values.")
        self.background(lambda: self.owned_host.sync(self.control.snapshot), done)

    def owner_call(self, method, *args):
        def done(result, error):
            self.owner_status.set(error or str(result))
            if not error:
                self.poll()
        self.background(lambda: self.owned_host.sync(getattr(self.control, method), *args), done)

    def load_config(self):
        if self.owner_snapshot:
            self.put(self.config_text, self.owner_snapshot["config"])

    def defaults(self):
        self.put(self.config_text, self.control.baseline)

    def apply_config(self):
        try:
            data = json.loads(self.config_text.get("1.0", "end"))
        except ValueError as exc:
            self.owner_status.set(str(exc))
            return
        self.owner_call("apply_config", data)

    def import_config(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("JSON profile", "*.json")])
        if path:
            try:
                self.put(self.config_text, json.loads(Path(path).read_text(encoding="utf-8")))
                self.owner_status.set("Profile loaded into draft. Apply session to activate it.")
            except (ValueError, OSError) as exc:
                self.owner_status.set(str(exc))

    def export_config(self):
        try:
            data = json.loads(self.config_text.get("1.0", "end"))
            self.export_json(data, "Silicate override profile.json")
        except ValueError as exc:
            self.owner_status.set(str(exc))

    def export_snapshot(self):
        if self.owner_snapshot:
            self.export_json(self.owner_snapshot, "Silicate state snapshot.json")

    def export_json(self, data, filename):
        path = filedialog.asksaveasfilename(parent=self, initialfile=filename, defaultextension=".json", filetypes=[("JSON", "*.json")])
        if path:
            try:
                Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                self.owner_status.set("Saved " + path)
            except OSError as exc:
                self.owner_status.set(str(exc))

    def load_node(self):
        if not self.owner_snapshot:
            return
        task = next((t for t in self.owner_snapshot["state"]["tasks"].values() if t.get("run_id") == self.selected), None)
        if not task or self.node_id.get() not in task["nodes"]:
            return
        self.edit_version = task["version"]
        self.edit_run = self.selected
        node = task["nodes"][self.node_id.get()]
        self.tier.set(node["tier"])
        self.core.set(node.get("preferred_core") or "")
        binding = node.get("binding_override") or {}
        for field in ("provider", "model", "effort"):
            getattr(self, field).set(binding.get(field) or "")
        self.instructions.delete("1.0", "end")
        self.instructions.insert("1.0", node["instructions"])

    def revise_node(self):
        if getattr(self, "edit_run", None) != self.selected:
            self.owner_status.set("Refresh and select a node in this question first.")
            return
        binding = {"provider": self.provider.get(), "model": self.model.get() or None, "effort": self.effort.get() or None} if self.provider.get() else None
        self.owner_call("edit_node", self.selected, self.node_id.get(), self.tier.get(),
                        self.instructions.get("1.0", "end").strip(), binding, self.core.get() or None, self.edit_version)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Silicate owner override desktop (separate session)")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    from .api import Host
    from .cli import Client
    from .runtime import build_runtime, data_dir_for
    directory = data_dir_for(args.demo) / "owner-sessions" / uuid4().hex
    host = Host(build_runtime(demo=args.demo, data_dir=directory)).start()
    host.serve_in_thread()
    try:
        app = OverrideSilicate(Client({"port": host.port, "token": host.token}), host, demo=args.demo)
        app.mainloop()
    finally:
        host.close()


if __name__ == "__main__":
    main()
