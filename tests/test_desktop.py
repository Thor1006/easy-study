"""Hidden native widget smoke check; uses no live providers."""
import tkinter as tk
from tkinter import ttk

import pytest

from glass_membrane.desktop import Silicate


def test_desktop_widgets_render_all_visual_types():
    try:
        app = Silicate(demo=True)
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    app.withdraw()
    try:
        app.clear()
        for visual in (
            {"type": "bar", "title": "Signed values", "labels": ["Loss", "Gain"], "values": [-5, 2], "caption": "Example"},
            {"type": "flow", "title": "Steps", "steps": ["Input", "Output"], "caption": "Example"},
            {"type": "table", "title": "Options", "columns": ["Name", "Benefit"], "rows": [["A", "Simple"]], "caption": "Example"},
        ):
            app.visual(visual)
        app.update_idletasks()
        assert len(app.content.winfo_children()) == 3
        app.render({"text": "Question", "answer": "Readable answer", "status": "done", "events": [], "info": {}})
        assert any(isinstance(c, ttk.Label) and c.cget("text") == "Readable answer" for c in app.content.winfo_children())
        assert isinstance(app.history, ttk.Treeview)
        assert isinstance(app.send_button, ttk.Button)
        app.new()
        assert app.selected is None
    finally:
        app.destroy()
