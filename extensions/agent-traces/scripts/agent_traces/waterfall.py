# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Span waterfall for one trace: a tree of spans with bars on a shared timeline.

By default only the spans that explain a turn are drawn (agent, model, tool,
plus roots); HTTP and A2A transport internals are hidden and their children
re-attached to the nearest drawn ancestor. ``all_spans=True`` draws everything.
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from datetime import datetime

from agent_traces.model import parse_ts, span_error
from agent_traces.render import Style, width

MAX_NAME = 48  # span names longer than this are truncated

# kind -> (plain tag, emoji). Emoji are all East-Asian-Wide, i.e. 2 columns.
KINDS = {
    "turn": ("[turn] ", "🚀"),
    "agent": ("[agent]", "🤖"),
    "llm": ("[llm]  ", "💬"),
    "model": ("[model]", "🧠"),
    "tool": ("[tool] ", "🔧"),
    "error": ("[ERROR]", "❌"),
    "other": ("       ", "  "),
}


def span_kind(name: str) -> str:
    if name in ("invocation",) or name.startswith("invoke_workflow"):
        return "turn"
    if name.startswith("invoke_agent"):
        return "agent"
    if name.startswith("call_llm"):
        return "llm"
    if name.startswith("generate_content"):
        return "model"
    if name.startswith("execute_tool"):
        return "tool"
    return "other"


@dataclass
class Node:
    span: dict
    start: datetime
    end: datetime
    kind: str
    error: str | None
    children: list["Node"] = field(default_factory=list)


def _build_tree(spans: list[dict], all_spans: bool) -> list[Node]:
    by_id: dict[str, dict] = {s["spanId"]: s for s in spans if s.get("startTime") and s.get("endTime")}
    nodes: dict[str, Node] = {}
    for sid, s in by_id.items():
        nodes[sid] = Node(
            s, parse_ts(s["startTime"]), parse_ts(s["endTime"]), span_kind(s.get("name", "")),
            span_error(s.get("labels", {})),
        )

    def is_root(sid: str) -> bool:
        return by_id[sid].get("parentSpanId") not in by_id

    def visible(sid: str) -> bool:
        return all_spans or is_root(sid) or nodes[sid].kind != "other" or nodes[sid].error is not None

    def drawn_parent(sid: str) -> str | None:
        parent = by_id[sid].get("parentSpanId")
        while parent in by_id and not visible(parent):
            parent = by_id[parent].get("parentSpanId")
        return parent if parent in by_id else None

    roots: list[Node] = []
    for sid, node in nodes.items():
        if not visible(sid):
            continue
        parent = drawn_parent(sid)
        (nodes[parent].children if parent else roots).append(node)

    def order(ns: list[Node]) -> None:
        ns.sort(key=lambda n: (n.start, n.span["spanId"]))
        for n in ns:
            order(n.children)

    order(roots)
    return roots


def _fmt_duration(seconds: float) -> str:
    if seconds >= 1:
        return f"{seconds:.2f}s"
    ms = seconds * 1000
    return f"{ms:.1f}ms" if ms < 10 else f"{ms:.0f}ms"


def _truncate(st: Style, text: str, limit: int) -> str:
    if width(text) <= limit:
        return text
    ell = "…" if st.rich else "..."
    return text[: limit - len(ell)] + ell


def _label(st: Style, prefix: str, node: Node) -> str:
    kind = "error" if node.error else node.kind
    tag, icon = KINDS[kind]
    name = _truncate(st, node.span.get("name", ""), MAX_NAME)
    if not st.rich:
        return f"{prefix}{tag} {name}"
    # Calm palette: names stay in the terminal's text color so they're easy to
    # read; agents are bold, failures red, transport internals dim. Color is
    # carried by the bars.
    paint = {"agent": st.bold, "error": st.red, "other": st.dim}.get(kind, lambda s: s)
    return f"{st.dim(prefix)}{icon} {paint(name)}"


# Bar color per span type, chosen to stay distinct on a dark background.
BAR_COLORS = {"agent": "35", "llm": "36", "model": "36", "tool": "32", "error": "31"}
DEFAULT_BAR = "94"  # bright blue: turn root and other spans
SLOW_SECONDS = 5.0


def _bar(st: Style, node: Node, t0: datetime, total: float, bar_width: int) -> str:
    offset = round((node.start - t0).total_seconds() / total * bar_width) if total else 0
    length = round((node.end - node.start).total_seconds() / total * bar_width) if total else bar_width
    offset = min(max(offset, 0), bar_width - 1)
    length = min(max(length, 1), bar_width - offset)
    rest = bar_width - offset - length
    if not st.rich:
        return "." * offset + "#" * length + "." * rest
    kind = "error" if node.error else node.kind
    fill = st.color(BAR_COLORS.get(kind, DEFAULT_BAR), "█" * length)
    return st.dim("·" * offset) + fill + st.dim("·" * rest)


def _details(st: Style, node: Node) -> str:
    parts = []
    labels = node.span.get("labels", {})
    if node.kind == "model":
        tin = labels.get("gen_ai.usage.input_tokens")
        tout = labels.get("gen_ai.usage.output_tokens")
        if tin or tout:
            parts.append(st.dim(f"{tin or 0} → {tout or 0} tok") if st.rich else f"{tin or 0}/{tout or 0} tok")
    if node.error:
        parts.append(st.red(node.error))
    return "".join("  " + p for p in parts)


def render_waterfall(
    trace: dict, style: Style, *, bar_width: int = 32, all_spans: bool = False
) -> list[str]:
    return [line for line, _ in waterfall_rows(trace, style, bar_width=bar_width, all_spans=all_spans)]


def waterfall_rows(
    trace: dict, style: Style, *, bar_width: int = 32, all_spans: bool = False
) -> list[tuple[str, Node]]:
    """Each drawn line with the span it stands for (the interactive view opens it)."""
    spans = [s for s in trace.get("spans", []) if s.get("startTime") and s.get("endTime")]
    if not spans:
        return []
    t0 = min(parse_ts(s["startTime"]) for s in spans)
    total = (max(parse_ts(s["endTime"]) for s in spans) - t0).total_seconds()
    roots = _build_tree(spans, all_spans)

    tee, last, pipe, blank = ("├─ ", "└─ ", "│  ", "   ") if style.rich else ("|- ", "`- ", "|  ", "   ")
    rows: list[tuple[str, Node]] = []

    def walk(node: Node, prefix: str, connector: str) -> None:
        rows.append((_label(style, prefix + connector, node), node))
        child_prefix = prefix + ("" if not connector else blank if connector == last else pipe)
        for i, child in enumerate(node.children):
            walk(child, child_prefix, last if i == len(node.children) - 1 else tee)

    for root in roots:
        walk(root, "", "")

    labels = [label for label, _ in rows]
    label_width = max(width(label) for label in labels)
    out = []
    for label, (_, node) in zip(labels, rows):
        pad = " " * max(label_width - width(label), 0)
        seconds = (node.end - node.start).total_seconds()
        dur = _fmt_duration(seconds).rjust(6)
        if style.rich and seconds >= SLOW_SECONDS:
            dur = style.yellow(dur)
        out.append((f"{label}{pad}  {_bar(style, node, t0, total, bar_width)}  {dur}{_details(style, node)}", node))
    return out


def _field(key: str, value: str, key_width: int, cols: int, sep: str = "  ") -> list[str]:
    """``key  value`` with the value wrapped under itself, keeping its own line breaks."""
    head = f"  {key.ljust(key_width)}{sep}"
    room = max(20, cols - len(head))
    wrapped = [
        piece
        for raw in (str(value).splitlines() or [""])
        for piece in (textwrap.wrap(raw, room, drop_whitespace=False, replace_whitespace=False) or [""])
    ]
    return [head + wrapped[0]] + [" " * len(head) + piece for piece in wrapped[1:]]


def span_details(node: Node, style: Style, *, cols: int = 120, tool_io: dict | None = None) -> list[str]:
    """Key facts, a tool call's arguments and result, then every label of one span.

    ``tool_io`` is ``{"args": dict | None, "result": object, "note": str | None}``
    for tool spans (see ``cli.App.tool_io``). Long values wrap to ``cols``.
    """
    s = node.span
    seconds = (node.end - node.start).total_seconds()
    facts = [
        ("span", s.get("spanId", "") + (f"  (parent {s['parentSpanId']})" if s.get("parentSpanId") else "")),
        ("kind", node.kind),
        ("start", s.get("startTime", "")),
        ("duration", _fmt_duration(seconds)),
    ]
    lines = [style.bold(s.get("name", "")), ""]
    for k, v in facts:
        lines += _field(k, v, 8, cols)
    if node.error:
        lines += [line.replace(node.error, style.red(node.error), 1) for line in _field("error", node.error, 8, cols)]

    if tool_io is not None:
        lines += ["", style.bold("Arguments")]
        args = tool_io.get("args")
        if args:
            key_width = max(len(k) for k in args) + 1
            for k, v in args.items():
                lines += _field(f"{k}:", repr(v), key_width, cols, sep=" ")
        elif args is not None:
            lines.append(style.dim("  (none)"))
        if tool_io.get("result") is not None or args is not None:
            lines += ["", style.bold("Result")]
            result = tool_io.get("result")
            if isinstance(result, dict) and set(result) == {"result"}:
                result = result["result"]
            text = result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False)
            lines += _field("", text, 0, cols) if result is not None else [style.dim("  (no result recorded)")]
        if tool_io.get("note"):
            lines.append(style.dim(f"  {tool_io['note']}"))

    labels = s.get("labels", {})
    if labels:
        lines += ["", style.bold("Labels")]
        key_width = max(len(k) for k in labels)
        for k, v in sorted(labels.items()):
            lines += _field(k, v, key_width, cols)
    return lines
