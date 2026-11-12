"""Debug-only path trace: the input state and output update of every graph node, in one file (path_trace.json).

`traced(name, fn)` wraps a node when the graph is built (graph.py); the node itself is untouched. The wrapper
only reads what goes in and out, never raises, and never changes the node's return value, so bot behaviour is
identical with tracing on or off. Phone, name and address values are redacted with the same helper as trace.json.

File: {"session_id", "message", "nodes": [...]} at the repo root, like trace.json it is overwritten for every message:
it holds only the nodes that were called in the latest turn, in call order. A turn starts at the "input" node.
Disable with the environment variable PATH_TRACE=off; change the location with PATH_TRACE_FILE.
"""
import functools
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

FIRST_NODE = "input"  # every turn starts here (graph.py: START -> input)
_SEP = "=" * 60

MAX_TEXT = 1500  # longer strings are cut in the file
HISTORY_TAIL = 4  # input history: only the last messages, the rest is a count
PATH = Path(os.getenv("PATH_TRACE_FILE") or Path(__file__).resolve().parents[2] / "path_trace.json")

_lock = threading.Lock()  # specialists of one superstep run in parallel threads
_turn: dict | None = None  # the turn being written; the file always holds exactly this
_llm_calls: list[dict] = []  # LLM calls for the current turn


def _short(v, max_chars: int = 120) -> str:
    if isinstance(v, str):
        v = v.replace("\n", " ").strip()
        return v if len(v) <= max_chars else v[:max_chars] + "…"
    if isinstance(v, list):
        return "[" + ", ".join(_short(i, 60) for i in v[:6]) + (", …" if len(v) > 6 else "") + "]"
    if isinstance(v, dict):
        pairs = [f"{k}={_short(vv, 40)}" for k, vv in list(v.items())[:4]]
        return "{" + ", ".join(pairs) + ("…" if len(v) > 4 else "") + "}"
    return str(v)


def _fmt_facts(kf: dict) -> str:
    if not kf:
        return "(none)"
    parts = []
    for k, f in kf.items():
        val = f.get("value") if isinstance(f, dict) else f
        parts.append(f"{k}={_short(val)}")
    return "  |  ".join(parts)


def _fmt_interp(ci: dict | None) -> str:
    if not ci:
        return "  (no interpretation)"
    tasks = ci.get("tasks") or []
    t_str = "; ".join(f"{t.get('domain')}:{t.get('action')}(car={t.get('car_model')})" for t in tasks) or "(none)"
    presented = ci.get("presented_cars") or []
    lines = [f"  tasks:     {t_str}"]
    if presented:
        lines.append(f"  presented: {presented}")
    lines.append(f"  complexity:{ci.get('complexity')}  uncertain:{ci.get('uncertain')}")
    return "\n".join(lines)


def _fmt_node(node: dict) -> str:
    name = node["node"]
    dur = node.get("duration_ms", "?")
    inp = node.get("input") or {}
    out = node.get("output") or {}
    err = node.get("error")
    llm_calls = node.get("llm_calls") or []
    lines = [f"{_SEP} {name} ({dur}ms) {_SEP}"]

    if name == "input":
        kf = inp.get("known_facts") or {}
        cl = kf.get("car_list", {})
        lines += [
            f"USER TEXT : {_short(inp.get('user_text', ''))}",
            f"mode      : {inp.get('mode')}  turn:{inp.get('turn')}",
            f"car_list  : {_short(cl.get('value', []) if isinstance(cl, dict) else [])}",
            f"history   : {inp.get('history', {}).get('messages_total', '?')} msgs",
        ]
    elif name == "preprocess":
        kf = inp.get("known_facts") or {}
        cl = kf.get("car_list", {})
        pending = inp.get("pending_interactions") or []
        lines += [
            f"USER TEXT : {_short(inp.get('user_text', ''))}",
            f"car_list  : {_short(cl.get('value', []) if isinstance(cl, dict) else [])}",
            f"pending   : {len(pending)} open",
        ]
    elif name in ("policy_guard", "router"):
        lines.append(_fmt_interp(inp.get("conversation_interpretation")))
    elif name in ("sales", "appointment", "contract", "knowledge", "after_sales", "conversation"):
        lines += [
            f"task_text : {_short(inp.get('task_text') or inp.get('user_text', ''))}",
            f"task_ids  : {inp.get('task_ids')}",
            _fmt_interp(inp.get("conversation_interpretation")),
        ]
    elif name in ("validation", "compose"):
        for a in (inp.get("answers") or []):
            lines.append(f"  [{a.get('agent')}] {_short(a.get('text', ''))}")
    elif name == "output_guard":
        lines.append(f"draft     : {_short(inp.get('draft_reply', ''))}")
    elif name == "human_handover":
        lines.append(f"reason    : {inp.get('handover_reason')}")
    elif name == "respond":
        lines.append(f"final     : {_short(inp.get('final_reply', ''))}")

    # Display LLM calls
    if llm_calls:
        lines.append("--- LLM INPUT ---")
        for i, call in enumerate(llm_calls, 1):
            if len(llm_calls) > 1:
                lines.append(f"[Call {i}]")
            lines.append(f"SYSTEM: {_short(call.get('system', ''), 200)}")
            lines.append(f"USER  : {_short(call.get('user', ''), 200)}")
            if call.get('summary'):
                lines.append(f"SUMMARY ({len(call['summary'])} chars):")
                lines.append(f"  {_short(call['summary'], 300)}")
            window = call.get('window', [])
            if window:
                lines.append(f"HISTORY WINDOW ({call.get('window_size', '?')} msgs, showing last {len(window)}):")
                for msg in window:
                    role = msg.get('role', '?')
                    content = _short(msg.get('content', ''), 150)
                    lines.append(f"  [{role}] {content}")

    lines.append("--- OUT ---")

    if name == "input":
        lines.append(f"known_facts: {_fmt_facts(out.get('known_facts') or {})}")
    elif name == "preprocess":
        lines.append(_fmt_interp(out.get("conversation_interpretation")))
        kf_out = out.get("known_facts") or {}
        cl_out = kf_out.get("car_list")
        if cl_out:
            lines.append(f"car_list  : {_short(cl_out.get('value', []) if isinstance(cl_out, dict) else [])}")
        lines.append(f"route     : {out.get('route')}  ({out.get('route_reason', '')})")
    elif name == "policy_guard":
        lines.append(f"route     : {out.get('route')}  ({out.get('route_reason', '')})")
    elif name == "router":
        lines.append(f"route     : {out.get('route')}  plan:{out.get('plan')}")
    elif name in ("sales", "appointment", "contract", "knowledge", "after_sales", "conversation"):
        for a in (out.get("answers") or []):
            lines.append(f"  reply [{a.get('agent')}]: {_short(a.get('text', ''))}")
        if out.get("allowed_amounts"):
            lines.append(f"  allowed_amounts: {out.get('allowed_amounts')}")
    elif name == "validation":
        lines.append(f"route     : {out.get('route')}  feedback:{_short(str(out.get('validation_feedback') or ''))}")
    elif name == "compose":
        lines.append(f"draft     : {_short(out.get('draft_reply', ''))}")
    elif name == "output_guard":
        lines.append(f"needs_human: {out.get('needs_human')}  final:{_short(out.get('final_reply', ''))}")
    elif name == "respond":
        lines.append(f"final_reply: {_short(out.get('final_reply', ''))}")

    if err:
        lines.append(f"!!! ERROR: {err}")
    return "\n".join(lines)


def _write_pretty(turn: dict) -> None:
    try:
        out_lines = [f"SESSION : {turn.get('session_id')}", f"USER MSG: {turn.get('message')}", ""]
        for node in turn.get("nodes", []):
            out_lines.append(_fmt_node(node))
            out_lines.append("")
        PATH.with_suffix(".txt").write_text("\n".join(out_lines), encoding="utf-8")
    except Exception:
        logger.exception("path_trace: could not write pretty trace")


def enabled() -> bool:
    return os.getenv("PATH_TRACE", "on").strip().casefold() not in {"0", "off", "false", "no"}


def log_llm_input(system: str, user: str, history: list[dict] | None) -> None:
    """Record LLM call details for the current turn."""
    global _llm_calls
    if not enabled():
        return
    with _lock:
        summary = None
        window = []
        if history:
            for msg in history:
                if msg.get("role") == "summary":
                    summary = msg.get("content", "")
                else:
                    window.append(msg)
        _llm_calls.append({
            "system": system[:500] + "..." if len(system) > 500 else system,
            "user": user[:500] + "..." if len(user) > 500 else user,
            "summary": summary[:800] + "..." if summary and len(summary) > 800 else summary,
            "window_size": len(window),
            "window": window[-3:] if window else []  # last 3 messages
        })


def _clip(value):
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else f"{value[:MAX_TEXT]}... [+{len(value) - MAX_TEXT} chars]"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def _snapshot(value, is_input: bool = False):
    """JSON-safe, redacted, size-bounded copy of a node's input state or output update."""
    from app.agent.nodes.preprocess import _trace_safe  # same redaction rules as trace.json

    if is_input and isinstance(value, dict) and isinstance(value.get("history"), list):
        history = value["history"]
        value = {**value, "history": {"messages_total": len(history), "last": history[-HISTORY_TAIL:]}}
    safe = _trace_safe(value)
    return _clip(json.loads(json.dumps(safe, ensure_ascii=False, default=str)))


def _record(entry: dict, new_turn: bool) -> None:
    """Add a node entry to the current turn; a new turn (or another session) starts the file over."""
    global _turn, _llm_calls
    with _lock:
        if new_turn or _turn is None or _turn["session_id"] != entry["session_id"]:
            _turn = {"session_id": entry["session_id"], "message": entry["input"].get("user_text"),
                     "nodes": [], "llm_calls": []}
            _llm_calls = []
        # Attach any LLM calls that happened during this node
        if _llm_calls:
            entry["llm_calls"] = list(_llm_calls)
            _llm_calls = []
        _turn["nodes"].append(entry)
        PATH.write_text(json.dumps(_turn, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_pretty(_turn)


def traced(name: str, fn: Callable) -> Callable:
    """Wrap a graph node so its input and output are appended to path_trace.json."""
    if not enabled():
        return fn

    @functools.wraps(fn)
    def wrapper(state):
        started = time.perf_counter()
        error = None
        try:
            output = fn(state)
        except BaseException as exc:  # recorded, then re-raised unchanged (incl. LangGraph control-flow errors)
            output, error = None, f"{type(exc).__name__}: {exc}"
            raise
        finally:
            try:
                _record({
                    "time": datetime.now().isoformat(timespec="milliseconds"),
                    "session_id": state.get("session_id") if hasattr(state, "get") else None,
                    "node": name,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                    "input": _snapshot(state, is_input=True),
                    "output": _snapshot(output),
                    **({"error": error} if error else {}),
                }, new_turn=name == FIRST_NODE)
            except Exception:
                logger.exception("path_trace: could not record node %s", name)
        return output

    return wrapper
