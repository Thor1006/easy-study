"""Bounded, declarative answer illustrations. Never execute model-supplied code."""
import json
import math
import re

FENCE = re.compile(r"```silicate\s*\n(.*?)\n```", re.S)
VISUAL_INSTRUCTIONS = '''
When a visual materially helps explain your answer, include a fenced silicate
JSON block INSIDE the answer string (answer_text for a direct router answer).
Always explain it in ordinary prose as well. Supported formats:
```silicate
{"type":"bar","title":"Comparison","labels":["A","B"],"values":[3,5],"caption":"Illustrative values, units: hours"}
```
Or {"type":"flow","title":"Process","steps":["Input","Transform","Output"],"caption":"Read top to bottom"}
Or {"type":"table","title":"Options","columns":["Option","Trade-off"],"rows":[["A","Simple"],["B","Flexible"]],"caption":"Qualitative comparison"}.
Use at most four visuals, twelve bars/steps/rows, six columns. Keep labels short.
Only use supported data, never HTML, scripts, URLs or executable code. Do not
invent quantitative measurements. Label illustrative data explicitly. Prefer a
flow or qualitative table if you lack measured numbers. Omit visuals when unhelpful.
'''


def validate_visual(value):
    if not isinstance(value, dict):
        raise ValueError("Illustration must be an object")
    def strings(items, limit):
        if not isinstance(items, list) or not 1 <= len(items) <= limit:
            raise ValueError("Invalid illustration size")
        if any(not isinstance(x, str) or len(x) > 160 for x in items):
            raise ValueError("Invalid label")
        return items
    kind = value.get("type")
    title, caption = value.get("title", ""), value.get("caption", "")
    if not isinstance(title, str) or len(title) > 200 or not isinstance(caption, str) or len(caption) > 1000:
        raise ValueError("Invalid illustration description")
    out = {"type": kind, "title": title, "caption": caption}
    if kind == "bar":
        out["labels"] = strings(value.get("labels"), 12)
        vals = value.get("values")
        if not isinstance(vals, list) or len(vals) != len(out["labels"]):
            raise ValueError("Bar labels and values must match")
        if any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e100 for x in vals):
            raise ValueError("Bar values must be finite numbers")
        out["values"] = vals
    elif kind == "flow":
        out["steps"] = strings(value.get("steps"), 12)
    elif kind == "table":
        out["columns"] = strings(value.get("columns"), 6)
        rows = value.get("rows")
        if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
            raise ValueError("Invalid rows")
        out["rows"] = [strings(row, 6) for row in rows]
        if any(len(row) != len(out["columns"]) for row in out["rows"]):
            raise ValueError("Table cells must match columns")
    else:
        raise ValueError("Unsupported illustration")
    return out


def answer_parts(answer):
    """Invalid blocks remain readable text; valid parts preserve source order."""
    answer = answer or ""
    parts, offset, count = [], 0, 0
    for match in FENCE.finditer(answer):
        if count >= 4 or len(match[1]) > 16000:
            continue
        try:
            visual = validate_visual(json.loads(match[1]))
        except (ValueError, TypeError, OverflowError, RecursionError):
            continue
        if match.start() > offset:
            parts.append({"type": "text", "text": answer[offset:match.start()]})
        parts.append(visual)
        offset = match.end()
        count += 1
    if offset < len(answer):
        parts.append({"type": "text", "text": answer[offset:]})
    return parts
