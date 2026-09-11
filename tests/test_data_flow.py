from glass_membrane.data_flow import flow_graph, layout


def test_direct_answer_route():
    nodes, edges = flow_graph({"status": "done", "text": "2x2", "answer": "4",
                               "route": {"attempts": [{"slot": "EF0", "provider": "claude", "mode": "answer", "ok": True}]}})
    assert list(nodes) == ["input", "route:0", "output"]
    assert edges == [("input", "route:0", False), ("route:0", "output", False)]
    assert nodes["output"]["data"]["answer"] == "4"


def test_parallel_graph_and_retry_metadata():
    task = {"nodes": {
        "plan": {"role": "planner", "deps": [], "phase": "done"},
        "a": {"role": "performance", "deps": [], "phase": "running", "owner": "P0", "attempt": 1},
        "b": {"role": "performance", "deps": [], "phase": "done"},
        "merge": {"role": "synthesizer", "deps": ["a", "b"], "phase": "waiting"}}}
    nodes, edges = flow_graph({"status": "running", "task": task})
    assert ("task:plan", "task:a", True) in edges
    assert ("task:a", "task:merge", False) in edges
    assert ("task:b", "task:merge", False) in edges
    assert "retry 1" in nodes["task:a"]["subtitle"]
    assert "P0" in nodes["task:a"]["subtitle"]
    positions = layout(nodes, edges)
    assert positions["task:a"][0] == positions["task:b"][0]
    assert positions["task:a"][0] < positions["task:merge"][0]
    assert positions["task:a"][1] != positions["task:b"][1]


def test_empty_and_cycle_terminate():
    assert flow_graph(None) == ({}, [])
    assert set(layout({"a": {}, "b": {}}, [("a", "b", False), ("b", "a", False)])) == {"a", "b"}
