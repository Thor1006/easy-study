"""Deterministic shared services (Arch §7, Schematic §4, §11).

Calculation, hashing, and counting run here rather than on a model. Services
with external effects take an action id so a retry after a lost response
reconciles instead of repeating the effect (Arch §13).
"""

from __future__ import annotations

import ast
import hashlib
import operator
import re
import threading

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def normalize_arithmetic(text: str) -> str | None:
    """Turn '2x2', 'what is 3 × 4?' or '2^8' into a Python arithmetic expression."""
    t = text.strip().lower()
    t = re.sub(r"^(what\s+is|what's|calculate|compute|evaluate)\s+", "", t)
    t = t.rstrip("?.!= ").strip()
    t = re.sub(r"(?<=[\d)])\s*[x×]\s*(?=[\d(])", "*", t)
    t = t.replace("÷", "/").replace("^", "**")
    if not t or not re.fullmatch(r"[\d\s.+\-*/()%]+", t):
        return None
    if not re.search(r"\d", t) or not re.search(r"[+\-*/%]", t.lstrip("-")):
        return None
    return t


def safe_calc(text: str):
    expr = normalize_arithmetic(text)
    if expr is None:
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 64:
                raise ValueError("exponent too large")
            return _BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](ev(node.operand))
        raise ValueError("unsupported expression")

    try:
        value = ev(tree)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    if isinstance(value, float):
        if value != value or abs(value) > 1e100:
            return None
        value = int(value) if value.is_integer() else round(value, 10)
    return value


def _output(answer: str, summary: str, **extra) -> dict:
    return {"answer": answer, "summary": summary, "confidence": 1.0, "uncertainty": None,
            "status": "COMPLETE", "shift": None, **extra}


class Services:
    names = ("calc", "sha256", "word_count", "append_note")

    def __init__(self) -> None:
        self._effects: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.effect_log: list[str] = []

    def run(self, name: str, args: dict, action_id: str | None = None) -> dict:
        if name == "calc":
            value = safe_calc(str(args.get("expression", "")))
            if value is None:
                raise ValueError("not a supported arithmetic expression")
            return _output(str(value), f"{args['expression']} = {value}")
        if name == "sha256":
            digest = hashlib.sha256(str(args.get("text", "")).encode("utf-8")).hexdigest()
            return _output(digest, f"sha256 = {digest[:12]}…")
        if name == "word_count":
            count = len(str(args.get("text", "")).split())
            return _output(str(count), f"{count} words")
        if name == "append_note":
            if not action_id:
                raise ValueError("services with external effects need an action id")
            with self._lock:
                if action_id in self._effects:
                    # The effect already happened; return the recorded outcome instead of repeating it.
                    return {**self._effects[action_id], "reconciled": True}
                self.effect_log.append(str(args.get("text", "")))
                out = _output("noted", f"Appended a note ({action_id})", reconciled=False)
                self._effects[action_id] = out
                return out
        raise KeyError(f"unknown service {name!r}")
