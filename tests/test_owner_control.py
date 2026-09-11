import asyncio
import copy
import time
import tkinter as tk

import pytest

from glass_membrane.adapters.fake import FakeAdapter
from glass_membrane.api import Host
from glass_membrane.cli import Client
from glass_membrane.owner_control import OwnerControl
from glass_membrane.runtime import Runtime


def runtime(tmp_path):
    return Runtime(tmp_path, adapters={p: FakeAdapter(p, default_delay=.15) for p in ("claude", "codex")}, demo=True)


def test_configuration_is_validated_before_mutation(tmp_path):
    rt = runtime(tmp_path)
    control = OwnerControl(rt)
    before = copy.deepcopy(rt.config.data)
    invalid = copy.deepcopy(before)
    invalid["filter"]["answer_threshold"] = 2
    with pytest.raises(ValueError):
        control.apply_config(invalid)
    assert rt.config.data == before and control.audit == []
    valid = copy.deepcopy(before)
    valid["runtime"]["context_max_chars"] = 4321
    valid["budget"]["max_calls_per_run"] = 77
    valid["filter"]["answer_threshold"] = .9
    control.apply_config(valid)
    assert rt.context.max_chars == 4321
    assert rt.budget.max_calls == 77
    assert rt.registry.config.filter["answer_threshold"] == .9
    assert (tmp_path / "logs" / "owner-controls.jsonl").exists()


def test_core_enable_and_minimum(tmp_path):
    rt = runtime(tmp_path)
    control = OwnerControl(rt)
    for core in list(rt.slots.cores)[:-1]:
        control.core_enabled(core, False)
    assert rt.slots.idle_core_count() == 1
    assert rt.slots.idle_core("efficiency").id == "F1"
    with pytest.raises(ValueError):
        control.core_enabled("F1", False)


def test_live_revision_revokes_lease_and_dispatches_binding(tmp_path):
    async def check():
        rt = runtime(tmp_path)
        control = OwnerControl(rt)
        await rt.start()
        try:
            run = await rt.submit("Explain a process", filter_bypass=True)
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                if run.task_id and any(n["lease_id"] for n in rt.state.task(run.task_id)["nodes"].values()):
                    break
                await asyncio.sleep(.01)
            assert run.status == "running"
            task = rt.state.task(run.task_id)
            node = next(n for n in task["nodes"].values() if n["lease_id"])
            old_lease = node["lease_id"]
            version = task["version"]
            control.pause(run.id, True)
            with pytest.raises(ValueError):
                control.apply_config(copy.deepcopy(rt.config.data))
            control.edit_node(run.id, node["id"], "flagship", "Explain with a simple example.",
                              {"provider": "codex", "model": None, "effort": "high"}, "F1", version)
            assert not rt.state.lease_is_live(old_lease)
            assert node["result_ref"] is None
            with pytest.raises(ValueError, match="Task changed"):
                control.edit_node(run.id, node["id"], "efficiency", "Stale edit", expected_version=version)
            with pytest.raises(ValueError, match="Clear active"):
                control.core_enabled("F1", False)
            control.pause(run.id, False)
            await rt.wait(run.id, timeout=5)
            assert run.status == "done"
            output = rt.store.content(node["result_ref"])
            assert output["binding"]["provider"] == "codex"
            assert output["core"] == "F1"
        finally:
            await rt.stop()
    asyncio.run(check())


def test_override_widgets(tmp_path):
    from glass_membrane.override_desktop import OverrideSilicate
    host = Host(runtime(tmp_path)).start()
    host.serve_in_thread()
    app = None
    try:
        try:
            app = OverrideSilicate(Client({"port": host.port, "token": host.token}), host, demo=True)
        except tk.TclError as exc:
            pytest.skip(str(exc))
        app.withdraw()
        app.update_idletasks()
        assert len(app.tabs.tabs()) == 4
        app.render({"id": "sample", "text": "Question", "answer": "4", "status": "done",
                    "route": {"attempts": [{"slot": "EF0", "ok": True, "mode": "answer"}]}, "events": [], "info": {}})
        assert "output" in app.data_flow.nodes
        assert len(app.data_flow.canvas.find_withtag("edge")) == 2
        app.data_flow.inspect("output")
        assert '"answer": "4"' in app.data_flow.details.get("1.0", "end")
        app.new()
        assert not app.data_flow.nodes
        from tkinter import ttk
        assert not any(isinstance(child, ttk.Button) for child in app.content.winfo_children())
        app.force_route.set(True)
        assert app.submission_options()["force_route"]
        assert not app.submission_options()["filter_bypass"]
    finally:
        if app:
            app.closing = True
            app.destroy()
        host.close()
