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

"""Command-line entry point for `agents-cli traces`.

  agents-cli traces                   recent turns (one trace per turn)
  agents-cli traces sessions          recent sessions with activity
  agents-cli traces session <id>      one session: turns, messages, waterfalls
  agents-cli traces show <trace-id>   one turn's waterfall (or just: traces <trace-id>)
  agents-cli traces last              waterfall of the most recent turn
  agents-cli traces browse            interactive browser (same as -i)

Add -i to any of them to browse interactively: move with the arrow keys,
Enter opens a turn, session, span or message, Left goes back.

Scoped to the agent this project deployed (Agent Runtime, Cloud Run or GKE).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO

from agent_traces import cloudtrace, model, render, sessions
from agent_traces.render import Style
from agent_traces.target import Target, TargetError, resolve_target
from agent_traces.view import View, print_view
from agent_traces.waterfall import Node, span_details, waterfall_rows

SINCE_RE = re.compile(r"(\d+)([mhd])")
TRACE_ID_RE = re.compile(r"[0-9a-f]{32}")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
COMMANDS = ("list", "sessions", "session", "show", "last", "browse")
NEEDS_ID = {"show": "a trace ID", "session": "a session ID"}
# Default --since: a session is looked up by ID, so look further back.
DEFAULT_SINCE = "7d"
SESSION_SINCE = "30d"
# Sessions need every turn in the window to group them; cap the scan.
SESSIONS_SCAN = 500
# Session view: lines per message before it's clipped (see --full).
MESSAGE_LINES = 6


class UsageError(Exception):
    pass


def parse_since(value: str) -> timedelta:
    m = SINCE_RE.fullmatch(value)
    if not m:
        raise UsageError("--since must look like 30m, 24h or 7d")
    return timedelta(**{UNITS[m.group(2)]: int(m.group(1))})


def _window(since: str) -> tuple[datetime, datetime]:
    """(start, end) of the last ``since``, ending now."""
    end = datetime.now(timezone.utc)
    return end - parse_since(since), end


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agents-cli traces",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("command", nargs="?", default="list", choices=COMMANDS)
    p.add_argument("id", nargs="?", help="trace ID (show) or session ID (session)")
    p.add_argument(
        "--since",
        help=f"time window: 30m, 24h, 7d (default {DEFAULT_SINCE}; {SESSION_SINCE} for `session`)",
    )
    p.add_argument("--limit", type=int, default=20, help="max rows (default 20)")
    p.add_argument("--errors", action="store_true", help="only turns/sessions with errors")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.add_argument("--all-spans", action="store_true", help="waterfall: include HTTP/A2A internals")
    p.add_argument(
        "--service",
        help="Cloud Run / GKE service name when it isn't the project name (Cloud Run --service-name, GKE OTEL_SERVICE_NAME)",
    )
    p.add_argument(
        "--project",
        help="Google Cloud project to read (default: from the project files, .env or gcloud)",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help=f"session: show whole messages (default: first {MESSAGE_LINES} lines each)",
    )
    p.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="browse interactively (arrow keys, Enter, Left); needs a terminal",
    )
    return p


def _when(iso: str) -> str:
    return iso[:19].replace("T", " ") if iso else "-"


def _widen(since: str) -> str:
    """Suggest a wider window, unless it's already 30 days or more."""
    return " Try --since 30d." if parse_since(since) < timedelta(days=30) else ""


def _tokens(input_tokens: int, output_tokens: int) -> str:
    return f"{input_tokens:,} / {output_tokens:,}"


def _meta(target: Target, since: str) -> list[str]:
    where = target.info.label
    return [f"Project: {target.project}", f"Region: {target.region or '?'}", f"{where}: {target.agent_id}", f"Window: {since}"]


def _cols() -> int:
    return shutil.get_terminal_size((120, 24)).columns


class App:
    def __init__(
        self,
        target: Target,
        http: cloudtrace.Transport,
        out: TextIO,
        style: Style,
        args: argparse.Namespace,
        interactive: bool = False,
    ):
        self.target, self.http, self.out, self.style, self.args = target, http, out, style, args
        self.interactive = interactive
        self._messages: dict[str, tuple[list[dict], str | None]] = {}

    # --- data ----------------------------------------------------------------
    def turn_traces(self, since: str, want: int, *terms: str) -> list[tuple[dict, dict]]:
        """(summary, trace) of this agent's turns in the window, newest first.

        Tries each of the target's filters (narrowed by ``terms``); the first
        that finds turns wins.
        """
        start, end = _window(since)
        for flt in self.target.filters(*terms):
            traces = cloudtrace.list_traces(self.http, self.target.project, flt, start, end, want)
            pairs = [(model.summarize(t), t) for t in traces if model.is_turn(t)]
            if pairs:
                return sorted(pairs, key=lambda p: p[0]["start"], reverse=True)
        return []

    def turns(self, since: str, want: int) -> list[dict]:
        return [summary for summary, _ in self.turn_traces(since, want)]

    def list_rows(self) -> list[dict]:
        args = self.args
        # --errors filters after fetching, so scan the whole window, not one page.
        rows = self.turns(args.since, SESSIONS_SCAN if args.errors else args.limit)
        if args.errors:
            rows = [r for r in rows if r["errors"]]
        return rows[: args.limit]

    def session_rows(self) -> list[dict]:
        args = self.args
        found = model.group_sessions(self.turns(args.since, SESSIONS_SCAN))
        if args.errors:
            found = [s for s in found if s["errors"]]
        return found[: args.limit]

    def session_data(self, sid: str) -> tuple[list[tuple[dict, dict]], list[dict], str | None]:
        """(summary, trace) per turn oldest first, transcript messages, and a note if there's no transcript."""
        pairs = self.turn_traces(self.args.since, SESSIONS_SCAN, f"+gen_ai.conversation.id:{sid}")
        turns = [(s, t) for s, t in reversed(pairs) if s["session_id"] == sid]

        messages, note = self.session_messages(sid)
        if not turns and not messages:
            raise UsageError(f"No turns or messages for session {sid} in the last {self.args.since}.{_widen(self.args.since)}")
        return turns, messages, note

    def session_messages(self, sid: str | None) -> tuple[list[dict], str | None]:
        """Transcript messages of one session (fetched once), and a note if there are none to read."""
        if not sid:
            return [], "This turn has no session ID."
        if sid not in self._messages:
            note = None
            messages: list[dict] = []
            try:
                messages = sessions.to_messages(sessions.read_events(self.http, self.target, sid))
            except sessions.NoTranscript as e:
                note = str(e)
            except cloudtrace.ApiError as e:
                note = f"Couldn't read the transcript: {e}"
            self._messages[sid] = (messages, note)
        return self._messages[sid]

    def tool_io(self, node: Node, trace: dict) -> dict:
        """Arguments and result of the tool call a tool span ran, from the session transcript.

        Matched on the call ID (``gen_ai.tool.call.id`` = the event's
        ``functionCall.id``); failing that (ADK records ``<not specified>`` when
        a tool raises), on the tool's name and position within the turn.
        """
        labels = node.span.get("labels", {})
        tool = labels.get("gen_ai.tool.name") or node.span.get("name", "").partition(" ")[2]
        messages, note = self.session_messages(model.summarize(trace)["session_id"])
        if note:
            return {"args": None, "result": None, "note": f"Arguments and result unavailable: {note}"}
        calls = [m for m in messages if m["kind"] == "tool_call"]
        results = {m["id"]: m for m in messages if m["kind"] == "tool_result" and m.get("id")}
        call_id = labels.get("gen_ai.tool.call.id")
        call = next((c for c in calls if call_id and c.get("id") == call_id), None)
        if call is None:
            invocations = model.invocation_ids(trace)
            same = [c for c in calls if c["tool"] == tool and c["invocation_id"] in invocations]
            spans = sorted(
                (s for s in trace.get("spans", []) if s.get("name") == node.span.get("name")),
                key=lambda s: s.get("startTime", ""),
            )
            index = next((i for i, s in enumerate(spans) if s.get("spanId") == node.span.get("spanId")), 0)
            call = same[index] if index < len(same) else None
        if call is None:
            return {"args": None, "result": None, "note": "No matching tool call in the session transcript."}
        result = results.get(call.get("id"))
        return {"args": call["args"], "result": result["result"] if result else None, "note": None}

    def no_turns_hint(self, what: str) -> str:
        msg = f" No {what} in this window.{_widen(self.args.since)}"
        how = self.target.info.rename_hint
        if how:
            msg += f" If the service isn't named {self.target.agent_id} ({how}), pass --service <name>."
        return msg

    def emit_json(self, data: object) -> None:
        print(json.dumps(data, indent=2), file=self.out)

    # --- views ---------------------------------------------------------------
    def view_turns(self) -> View:
        rows, st = self.list_rows(), self.style
        v = View("Turns", hint="agents-cli traces show <trace-id> | traces session <id> | traces sessions")
        v.add(*render.title_lines(st, "Turns", _meta(self.target, self.args.since) + [f"Turns: {len(rows)}"]))
        if not rows:
            v.add(st.dim(self.no_turns_hint("turns")))
            v.hint = None
            return v
        header, rule, *lines = render.table_lines(
            st,
            ["STARTED", "TRACE", "SESSION", "AGENTS", "TOOLS", "TOKENS IN/OUT", "DURATION", "STATUS"],
            [
                [
                    _when(r["start"]),
                    r["trace_id"],
                    str(r["session_id"] or "-"),
                    render.chain(st, r["agents"], 2),
                    str(len(r["tools"])),
                    _tokens(r["input_tokens"], r["output_tokens"]),
                    st.duration(r["duration_s"]),
                    render.status(st, r["errors"]),
                ]
                for r in rows
            ],
            right={4, 5, 6},
        )
        v.add(header, rule)
        for r, line in zip(rows, lines):
            v.add(
                line,
                open=lambda r=r: self.view_trace_id(r["trace_id"], f"Turn {_when(r['start'])}"),
                error=bool(r["errors"]),
            )
        return v

    def view_sessions(self) -> View:
        found, st = self.session_rows(), self.style
        v = View("Sessions", hint="agents-cli traces session <id>")
        v.add(*render.title_lines(st, "Sessions", _meta(self.target, self.args.since) + [f"Sessions: {len(found)}"]))
        if not found:
            v.add(st.dim(self.no_turns_hint("sessions with activity")))
            v.hint = None
            return v
        header, rule, *lines = render.table_lines(
            st,
            ["LAST ACTIVE", "SESSION", "TURNS", "AGENTS", "TOOLS", "TOKENS IN/OUT", "TOTAL TIME", "STATUS"],
            [
                [
                    _when(s["last"]),
                    s["session_id"],
                    str(s["turns"]),
                    render.chain(st, s["agents"], 2),
                    str(s["tools"]),
                    _tokens(s["input_tokens"], s["output_tokens"]),
                    st.duration(s["duration_s"]),
                    render.status(st, s["errors"]),
                ]
                for s in found
            ],
            right={2, 4, 5, 6},
        )
        v.add(header, rule)
        for s, line in zip(found, lines):
            v.add(line, open=lambda s=s: self.view_session(s["session_id"]), error=bool(s["errors"]))
        return v

    def view_session(self, sid: str) -> View:
        turns, messages, note = self.session_data(sid)
        # Group messages by invocation; each traced turn claims its invocations,
        # leftovers (turns whose trace is outside the window) are shown on their own.
        by_inv: dict[str, list[dict]] = {}
        for m in messages:
            by_inv.setdefault(m["invocation_id"] or "", []).append(m)
        sections: list[tuple[str, dict | None, dict | None, list[dict]]] = []
        for s, t in turns:
            msgs = [m for inv in sorted(model.invocation_ids(t)) for m in by_inv.pop(inv, [])]
            sections.append((s["start"], s, t, sorted(msgs, key=lambda m: m["timestamp"] or "")))
        for msgs in by_inv.values():
            sections.append((msgs[0]["timestamp"] or "", None, None, msgs))
        sections.sort(key=lambda x: x[0])

        st = self.style
        hint = "agents-cli traces show <trace-id> | traces session <id> --full | --format json"
        v = View(f"Session {sid}", hint=hint)
        s_tok = (sum(s["input_tokens"] for s, _ in turns), sum(s["output_tokens"] for s, _ in turns))
        v.add(
            *render.title_lines(
                st, f"Session {sid}", _meta(self.target, self.args.since) + [f"Turns: {len(turns)}", f"Tokens: {_tokens(*s_tok)}"]
            )
        )
        if note:
            v.add(st.yellow(f" ! {note}"), "")
        n = 0
        for start, s, t, msgs in sections:
            if s is None:
                v.add(st.bold(f" {_when(start)}  (no trace in this window)"))
            else:
                n += 1
                head = f" Turn {n}  {_when(s['start'])}"
                tail = f"trace {s['trace_id']}  {st.duration(s['duration_s'])}  tokens {_tokens(s['input_tokens'], s['output_tokens'])}"
                v.add(
                    st.bold(head) + "  " + st.dim(tail) + "  " + render.status(st, s["errors"]),
                    open=lambda t=t, n=n: self.view_trace(t, f"Turn {n}"),
                    error=bool(s["errors"]),
                )
            for speaker, text in sessions.transcript_lines(msgs):
                self._add_message(v, speaker, text)
            if t is not None:
                if msgs:
                    v.add("")
                self._add_waterfall(v, t)
            v.add("")
        return v

    def _add_message(self, v: View, speaker: str, text: str) -> None:
        st = self.style
        color = st.cyan if speaker == "user" else st.dim if speaker == "tool" else st.magenta
        prefix = f"   {speaker}> "
        indent = " " * len(prefix)
        if self.args.full:
            lines, hidden = text.splitlines() or [""], 0
        else:
            lines, hidden = render.clip(text, MESSAGE_LINES, _cols() - len(prefix))
        # In the browser every line of a message is selectable and opens it whole.
        opener = (lambda: self.view_text(speaker, text)) if self.interactive else None
        first, *rest = lines
        v.add(color(prefix) + first, open=opener)
        v.add(*(indent + line for line in rest), open=opener)
        if hidden:
            more = "line" if hidden == 1 else "lines"
            how = "Enter" if self.interactive else "--full"
            v.add(indent + st.dim(f"... +{hidden} more {more} ({how} to show all)"), open=opener)

    def view_text(self, speaker: str, text: str) -> View:
        v = View(f"{speaker} message")
        v.add(self.style.bold(f" {speaker}>"), "", selectable=False)
        room = max(20, _cols() - 2)
        v.add(
            *(" " + piece for raw in text.splitlines() for piece in (textwrap.wrap(raw, room) or [""])),
            selectable=False,
        )
        return v

    def _add_waterfall(self, v: View, trace: dict) -> None:
        bar_width = max(16, min(40, _cols() - 90))
        rows = waterfall_rows(trace, self.style, bar_width=bar_width, all_spans=self.args.all_spans)
        if not rows:
            v.add(self.style.dim(" (no spans)"))
        for line, node in rows:
            v.add(" " + line, open=lambda node=node: self.view_span(node, trace), error=node.error is not None)

    def view_trace(self, trace: dict, title: str, meta: list[str] | None = None) -> View:
        v = View(title)
        v.add(*render.title_lines(self.style, title, meta or (_meta(self.target, self.args.since)[:3] + [f"Trace: {trace.get('traceId', '')}"])))
        self._add_waterfall(v, trace)
        return v

    def view_trace_id(self, trace_id: str, title: str, meta: list[str] | None = None) -> View:
        return self.view_trace(cloudtrace.get_trace(self.http, self.target.project, trace_id), title, meta)

    def view_span(self, node: Node, trace: dict) -> View:
        v = View(node.span.get("name", "span"))
        io = self.tool_io(node, trace) if node.kind == "tool" else None
        lines = span_details(node, self.style, cols=_cols() - 1, tool_io=io)
        v.add(*(" " + line for line in lines), selectable=False)
        return v

    def view_last(self) -> View:
        rows = self.turns(self.args.since, 20)
        if not rows:
            raise UsageError(f"No turns in the last {self.args.since}.{_widen(self.args.since)}")
        r = rows[0]
        meta = _meta(self.target, self.args.since)[:3] + [
            f"Trace: {r['trace_id']}",
            f"Session: {r['session_id']}",
            f"Tokens: {_tokens(r['input_tokens'], r['output_tokens'])}",
        ]
        return self.view_trace_id(r["trace_id"], f"Latest turn {_when(r['start'])}", meta)

    def root_view(self) -> View:
        args = self.args
        if args.command == "sessions":
            return self.view_sessions()
        if args.command == "session":
            return self.view_session(args.id)
        if args.command == "show":
            return self.view_trace_id(args.id, f"Trace {args.id}", _meta(self.target, args.since)[:3])
        if args.command == "last":
            return self.view_last()
        return self.view_turns()

    # --- commands ------------------------------------------------------------
    def run(self) -> int:
        args = self.args
        if args.format == "json":
            return self.run_json()
        if self.interactive:
            from agent_traces import tui

            return tui.run(self.root_view(), tabs={"t": self.view_turns, "s": self.view_sessions})
        print_view(self.out, self.style, self.root_view())
        return 0

    def run_json(self) -> int:
        args = self.args
        if args.command in ("list", "browse"):
            self.emit_json(self.list_rows())
        elif args.command == "sessions":
            self.emit_json(self.session_rows())
        elif args.command == "session":
            turns, messages, note = self.session_data(args.id)
            self.emit_json(
                {
                    "session_id": args.id,
                    "turns": [{**s, "invocation_ids": sorted(model.invocation_ids(t))} for s, t in turns],
                    "messages": messages,
                    "transcript_note": note,
                }
            )
        elif args.command == "show":
            self.emit_json(cloudtrace.get_trace(self.http, self.target.project, args.id))
        elif args.command == "last":
            rows = self.turns(args.since, 20)
            if not rows:
                raise UsageError(f"No turns in the last {args.since}.{_widen(args.since)}")
            self.emit_json(cloudtrace.get_trace(self.http, self.target.project, rows[0]["trace_id"]))
        return 0


def main(
    argv: list[str] | None = None,
    *,
    cwd: Path | None = None,
    http: cloudtrace.Transport | None = None,
    out: TextIO | None = None,
) -> int:
    out = out or render.default_stream()
    argv = list(sys.argv[1:] if argv is None else argv)
    # `traces <trace-id>` is short for `traces show <trace-id>`.
    if argv and TRACE_ID_RE.fullmatch(argv[0]):
        argv.insert(0, "show")
    args = build_parser().parse_args(argv)
    if args.since is None:
        args.since = SESSION_SINCE if args.command == "session" else DEFAULT_SINCE
    if args.command in NEEDS_ID and not args.id:
        print(f"x `traces {args.command}` needs {NEEDS_ID[args.command]}.", file=sys.stderr)
        return 1
    interactive = args.format == "table" and (args.interactive or args.command == "browse")
    rich = args.format == "table" and (interactive or render.detect_rich(out))
    if rich:
        render.ensure_utf8(out)
    try:
        if interactive:
            from agent_traces import tui

            try:
                tui.check_available(out)
            except tui.Unavailable as e:
                raise UsageError(str(e)) from e
        target = resolve_target(cwd or Path.cwd(), args.service, args.project)
        transport = http or cloudtrace.GoogleTransport(quota_project=target.project)
        return App(target, transport, out, Style(rich), args, interactive).run()
    except (TargetError, UsageError, cloudtrace.ApiError) as e:
        print(f"x {e}", file=sys.stderr)
        return 1
