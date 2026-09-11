import json
import time

import pytest

from glass_membrane.illustrations import answer_parts, validate_visual
from glass_membrane.roles import load_skill
from glass_membrane.runtime import Runtime
from glass_membrane.adapters.fake import FakeAdapter
from glass_membrane.api import Host
from glass_membrane.cli import Client, build_parser


def block(value):
    return "```silicate\n" + json.dumps(value) + "\n```"


def test_visuals_preserve_order_and_text():
    visual = {"type": "bar", "labels": ["Loss", "Gain"], "values": [-2, 4]}
    parts = answer_parts("Before\n" + block(visual) + "\nAfter")
    assert [p["type"] for p in parts] == ["text", "bar", "text"]
    assert parts[1]["values"] == [-2, 4]
    assert parts[2]["text"] == "\nAfter"


@pytest.mark.parametrize("value", [
    {"type": "html", "html": "<script>alert(1)</script>"},
    {"type": "bar", "labels": ["A"], "values": [float("nan")]},
    {"type": "bar", "labels": ["A"], "values": [True]},
    {"type": "bar", "labels": ["A"], "values": [1, 2]},
    {"type": "flow", "steps": ["step"]*13},
    {"type": "table", "columns": ["A", "B"], "rows": [["x"]]},
])
def test_invalid_visual_is_readable_not_executable(value):
    with pytest.raises(ValueError):
        validate_visual(value)
    text = block(value)
    assert answer_parts(text) == [{"type": "text", "text": text}]


def test_cap_and_invalid_json():
    text = block({"type": "flow", "steps": ["A"]})*6
    assert sum(p["type"] == "flow" for p in answer_parts(text)) == 4
    malformed = '```silicate\n{bad json}\n```'
    assert answer_parts(malformed)[0]["text"] == malformed


def test_prompt_and_desktop_entry():
    assert "```silicate" in load_skill("router")
    assert "```silicate" in load_skill("synthesizer")
    assert build_parser().parse_args(["desktop", "--demo"]).demo


def test_simulated_answer_reaches_api_with_visual(tmp_path):
    rt = Runtime(tmp_path, adapters={"claude": FakeAdapter()}, demo=True)
    host = Host(rt).start()
    host.serve_in_thread()
    try:
        client = Client({"port": host.port, "token": host.token})
        run = client.post("/api/runs", {"text": "Illustrate the runtime"})["run"]
        deadline = time.monotonic()+10
        while time.monotonic() < deadline:
            detail = client.get('/api/runs/'+run["id"])
            if detail["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(.02)
        assert detail["status"] == "done"
        assert any(p["type"] == "flow" for p in detail["answer_parts"])
        assert "Simulated example" in detail["answer"]
    finally:
        host.close()
