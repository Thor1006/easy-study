"""Silicate desktop: native Tkinter client of the existing local runtime API."""
from __future__ import annotations

import argparse
import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .illustrations import answer_parts

BG, PAPER, INK, MUTED, ACCENT, SOFT = "#f0f0f0", "#ffffff", "#202020", "#606060", "#47739b", "#f0f0f0"


class Silicate(tk.Tk):
    def __init__(self, client=None, owned_host=None, *, demo=False):
        super().__init__()
        self.client, self.owned_host, self.demo = client, owned_host, demo
        self.events = queue.Queue()
        self.selected = None
        self.runs = []
        self.detail = None
        self.signature = None
        self.closing = False
        self.busy = False
        self.polling = False
        self.title("Silicate")
        self.geometry("1080x780")
        self.minsize(820, 600)
        self.option_add("*Font", "TkDefaultFont")
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build()
        self.after(80, self._drain)
        if client:
            self.after(100, self.poll)

    def label(self, parent, text, size=11, color=INK, **kw):
        return ttk.Label(parent, text=text, **kw)

    def button(self, parent, text, command, primary=False):
        return ttk.Button(parent, text=text, command=command)

    def _build(self):
        main = ttk.Panedwindow(self, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=8)
        sidebar = ttk.Frame(main, padding=6)
        main.add(sidebar, weight=1)
        ttk.Label(sidebar, text="Questions").pack(anchor="w", pady=(0, 6))
        self.button(sidebar, "New question", self.new).pack(fill="x", pady=(0, 8))
        self.history = ttk.Treeview(sidebar, show="tree", selectmode="browse", height=20)
        self.history.pack(fill="both", expand=True)
        self.history.bind("<<TreeviewSelect>>", self.select)
        content = ttk.Frame(main, padding=6)
        main.add(content, weight=4)
        ttk.Label(content, text="Silicate — Demo (simulated)" if self.demo else "Silicate — Live (uses subscriptions)").pack(anchor="w")
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(content, textvariable=self.status).pack(fill="x", pady=8)
        self.tabs = tabs = ttk.Notebook(content)
        tabs.pack(fill="both", expand=True)
        page = ttk.Frame(tabs)
        tabs.add(page, text="Answer")
        self.canvas = tk.Canvas(page, bg=PAPER, highlightthickness=0)
        scroll = ttk.Scrollbar(page, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.canvas.pack(fill="both", expand=True)
        self.content = ttk.Frame(self.canvas, padding=12)
        self.window = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-int(e.delta / 120), "units"))
        self.bind("<MouseWheel>", self._scroll_answer)
        activity = ttk.Frame(tabs)
        tabs.add(activity, text="Activity")
        self.activity = tk.Text(activity, wrap="word", relief="sunken", borderwidth=1)
        activity_scroll = ttk.Scrollbar(activity, command=self.activity.yview)
        self.activity.configure(yscrollcommand=activity_scroll.set, state="disabled")
        activity_scroll.pack(side="right", fill="y")
        self.activity.pack(fill="both", expand=True)
        tools = ttk.Frame(content)
        tools.pack(fill="x", pady=8)
        self.button(tools, "Copy", self.copy).pack(side="left")
        self.button(tools, "Save…", self.save).pack(side="left", padx=6)
        self.button(tools, "Stop", lambda: self.action("cancel", {})).pack(side="right")
        ttk.Label(content, text="Message").pack(anchor="w")
        self.input = tk.Text(content, height=3, wrap="word", relief="sunken", borderwidth=1)
        self.input.pack(fill="x", pady=5)
        self.input.bind("<Control-Return>", lambda e: self.send())
        bottom = ttk.Frame(content)
        bottom.pack(fill="x")
        self.steer = tk.BooleanVar()
        ttk.Checkbutton(bottom, text="Steer selected run", variable=self.steer).pack(side="left")
        ttk.Label(bottom, text="Agent limit").pack(side="left", padx=(10, 4))
        self.cap = tk.StringVar(value="12")
        ttk.Spinbox(bottom, from_=1, to=24, textvariable=self.cap, width=4).pack(side="left")
        self.button(bottom, "Apply", self.apply_cap).pack(side="left", padx=5)
        self.send_button = self.button(bottom, "Send", self.send)
        self.send_button.pack(side="right")
        ttk.Label(content, text="Ctrl+Enter to send. Charts, diagrams and tables are supported.").pack(anchor="w", pady=(6, 0))
        self.welcome()

    def _scroll_answer(self, event):
        # Labels/cards receive pointer events too; scroll only the answer subtree.
        widget = event.widget
        while widget is not None and widget is not self:
            if widget is self.content:
                self.canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"
            widget = getattr(widget, "master", None)

    def _resize(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        for child in self.content.winfo_children():
            if isinstance(child, (tk.Label, ttk.Label)):
                child.configure(wraplength=max(200, event.width - 60))

    def clear(self):
        for child in self.content.winfo_children():
            child.destroy()

    def welcome(self):
        self.clear()
        self.label(self.content, "Enter a question below.").pack(anchor="w", pady=12)
        self.label(self.content, "Explore an idea, compare your options, or see how something works.\nI'll keep the explanation and its visuals together.", color=MUTED,
                   justify="left").pack(anchor="w", pady=(0, 22))

    def fill(self, text):
        self.input.delete("1.0", "end")
        self.input.insert("1.0", text)
        self.input.focus_set()

    def new(self):
        self.selected, self.detail, self.signature = None, None, None
        self.history.selection_remove(*self.history.selection())
        self.steer.set(False)
        self.welcome()
        self.input.focus_set()

    def select(self, event=None):
        indices = self.history.selection()
        if indices and indices[0] != self.selected:
            self.selected = indices[0]
            self.signature = None
            self.poll()

    def background(self, fn, callback):
        def work():
            try:
                result = fn()
                self.events.put((callback, result, None))
            except (Exception, SystemExit) as exc:
                self.events.put((callback, None, str(exc)))
        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        if self.closing:
            return
        while True:
            try:
                callback, result, error = self.events.get_nowait()
            except queue.Empty:
                break
            callback(result, error)
        self.after(80, self._drain)

    def poll(self):
        if self.closing or self.polling or not self.client:
            return
        self.polling = True
        selected = self.selected
        def fetch():
            return self.client.get("/api/state"), self.client.get(f"/api/runs/{selected}") if selected else None
        def done(data, error):
            self.polling = False
            if error:
                self.status.set("Connection interrupted. Retrying… " + error[:100])
            else:
                snapshot, detail = data
                self.runs = snapshot["runs"]
                current_ids = set(self.history.get_children())
                new_ids = {r["id"] for r in self.runs}
                for rid in current_ids - new_ids:
                    self.history.delete(rid)
                for i, run in enumerate(self.runs):
                    title = run["text"][:45]
                    if run["id"] in current_ids:
                        self.history.item(run["id"], text=title)
                    else:
                        self.history.insert("", "end", iid=run["id"], text=title)
                    self.history.move(run["id"], "", i)
                if self.selected in new_ids:
                    self.history.selection_set(self.selected)
                if selected == self.selected and detail:
                    self.detail = detail
                    self.status.set(detail.get("status_line") or detail["status"])
                    signature = json.dumps(detail, sort_keys=True)
                    if signature != self.signature:
                        self.signature = signature
                        self.render(detail)
                elif not self.selected:
                    self.status.set("Ready when you are.")
            self.after(900, self.poll)
        self.background(fetch, done)

    def limit(self):
        value = int(self.cap.get())
        if not 1 <= value <= 24:
            raise ValueError("Choose a team limit between 1 and 24.")
        return value

    def submission_options(self):
        return {}

    def send(self):
        if self.busy or not self.client:
            return
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        try:
            limit = self.limit()
        except ValueError as exc:
            messagebox.showerror("Team limit", str(exc), parent=self)
            return
        if self.steer.get():
            if not self.selected or not self.detail or self.detail["status"] not in ("running", "filtering"):
                messagebox.showinfo("Guide current work", "Select work that is still running first.", parent=self)
                return
            path, payload = f"/api/runs/{self.selected}/steer", {"text": text, "source": "desktop"}
        else:
            path, payload = "/api/runs", {"text": text, "max_agents": limit, "source": "desktop", **self.submission_options()}
        self.busy = True
        self.send_button.configure(state="disabled")
        def done(data, error):
            self.busy = False
            self.send_button.configure(state="normal")
            if error:
                messagebox.showerror("Could not send", error, parent=self)
                return
            if isinstance(data.get("run"), dict):
                self.selected = data["run"]["id"]
            if self.input.get("1.0", "end").strip() == text:
                self.input.delete("1.0", "end")
            self.signature = None
            self.poll()
        self.background(lambda: self.client.post(path, payload), done)

    def action(self, action, payload):
        if not self.selected or not self.client:
            return
        path = f"/api/runs/{self.selected}/{action}"
        def done(data, error):
            if error:
                messagebox.showerror("Request unsuccessful", error, parent=self)
            self.poll()
        self.background(lambda: self.client.post(path, payload), done)

    def apply_cap(self):
        try:
            self.action("cap", {"n": self.limit()})
        except ValueError as exc:
            messagebox.showerror("Team limit", str(exc), parent=self)

    def render(self, detail):
        position = self.canvas.yview()[0]
        self.clear()
        self.label(self.content, detail["text"], 17, wraplength=650, justify="left").pack(anchor="w", pady=(0, 20))
        for part in answer_parts(detail.get("answer")):
            if part["type"] == "text":
                widget = self.label(self.content, part["text"].strip(), wraplength=max(200, self.canvas.winfo_width()-60), justify="left")
                widget.pack(anchor="w", fill="x", pady=8)
            else:
                self.visual(part)
        if not detail.get("answer"):
            self.label(self.content, detail.get("error") or detail.get("status_line") or "Thinking…", color=MUTED,
                       wraplength=600, justify="left").pack(anchor="w")
        for warning in detail.get("info", {}).get("uncertainty", []):
            self.label(self.content, "Uncertainty: " + warning, color="#936325", wraplength=600).pack(anchor="w")
        events = "\n".join(e.get("message", "") for e in detail.get("events", []) if e.get("message"))
        ledger = detail.get("ledger_totals", {})
        report = f"Status: {detail['status']}\nProvider calls: {detail.get('calls', 0)}\nToken usage: {json.dumps(ledger)}\n\n{events}"
        self.activity.configure(state="normal")
        self.activity.delete("1.0", "end")
        self.activity.insert("1.0", report)
        self.activity.configure(state="disabled")
        self.canvas.yview_moveto(position)

    def visual(self, spec):
        card = ttk.LabelFrame(self.content, text=spec["title"], padding=10)
        card.pack(fill="x", pady=12)
        self.label(card, spec["title"], 14).pack(anchor="w", pady=(0, 12))
        if spec["type"] == "table":
            grid = ttk.Frame(card)
            grid.pack(fill="x")
            for r, row in enumerate([spec["columns"], *spec["rows"]]):
                for c, cell in enumerate(row):
                    ttk.Label(grid, text=cell, wraplength=130, padding=6, anchor="w", justify="left", relief="solid").grid(row=r, column=c, sticky="nsew", padx=1, pady=1)
                    grid.columnconfigure(c, weight=1, uniform="cells")
        elif spec["type"] == "flow":
            for i, step in enumerate(spec["steps"]):
                if i:
                    self.label(card, "↓", 16, ACCENT).pack()
                ttk.Label(card, text=step, padding=8, wraplength=450, relief="solid").pack(fill="x")
        else:
            chart = tk.Canvas(card, height=55 * len(spec["values"]) + 20, bg=SOFT, highlightthickness=0)
            chart.pack(fill="x")
            def draw(event=None):
                chart.delete("all")
                width = max(300, chart.winfo_width())
                lo, hi = min(0, *spec["values"]), max(0, *spec["values"])
                span = hi - lo or 1
                left, right = 135, width - 65
                zero = left + (-lo / span) * (right-left)
                chart.create_line(zero, 0, zero, 55*len(spec["values"]), fill="#a8c5b5")
                for i, (label, value) in enumerate(zip(spec["labels"], spec["values"])):
                    y = i*55 + 25
                    x = left + ((value-lo)/span)*(right-left)
                    chart.create_text(0, y, text=label, anchor="w", width=125, fill=INK)
                    chart.create_rectangle(min(zero,x), y-11, max(zero,x), y+11, fill=ACCENT, outline="")
                    chart.create_text(right+8, y, text=f"{value:g}", anchor="w", fill=INK)
            chart.bind("<Configure>", draw)
        self.label(card, spec["caption"], 10, MUTED, wraplength=550, justify="left").pack(anchor="w", pady=(12, 0))

    def copy(self):
        if self.detail and self.detail.get("answer"):
            self.clipboard_clear()
            self.clipboard_append(self.detail["answer"])
            self.status.set("Answer copied, including its illustration data.")

    def save(self):
        if not self.detail or not self.detail.get("answer"):
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".md", initialfile="Silicate answer.md", filetypes=[("Markdown", "*.md")])
        if path:
            try:
                from pathlib import Path
                Path(path).write_text(self.detail["answer"], encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Could not save", str(exc), parent=self)

    def close(self):
        if self.owned_host and any(r["status"] in ("running", "filtering") for r in self.runs):
            if not messagebox.askokcancel("Work is still running", "Closing this window stops its runtime and active work. Close now?", parent=self):
                return
        self.closing = True
        self.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Silicate native desktop")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    from .cli import Client, find_host
    from .api import Host
    from .runtime import build_runtime, data_dir_for
    owned = None
    try:
        client = find_host(args.demo)
        if client is None:
            owned = Host(build_runtime(demo=args.demo), state_file=data_dir_for(args.demo)/"host.json").start()
            owned.serve_in_thread()
            client = Client({"port": owned.port, "token": owned.token})
        app = Silicate(client, owned, demo=args.demo)
        app.mainloop()
    finally:
        if owned:
            owned.close()


if __name__ == "__main__":
    main()
