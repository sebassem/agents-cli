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
  agents-cli traces show <trace-id>   one turn's waterfall
  agents-cli traces last              waterfall of the most recent turn

Scoped to the agent this project deployed (Agent Runtime, Cloud Run or GKE).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TextIO

from agent_traces import cloudtrace, model, render, sessions
from agent_traces.render import Style
from agent_traces.target import TURN_FILTER, Target, TargetError, resolve_target
from agent_traces.waterfall import render_waterfall

SINCE_RE = re.compile(r"(\d+)([mhd])")
UNITS = {"m": "minutes", "h": "hours", "d": "days"}
COMMANDS = ("list", "sessions", "session", "show", "last")
NEEDS_ID = {"show": "a trace ID", "session": "a session ID"}
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agents-cli traces",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("command", nargs="?", default="list", choices=COMMANDS)
    p.add_argument("id", nargs="?", help="trace ID (show) or session ID (session)")
    p.add_argument("--since", default="7d", help="time window: 30m, 24h, 7d (default 7d)")
    p.add_argument("--limit", type=int, default=20, help="max rows (default 20)")
    p.add_argument("--errors", action="store_true", help="only turns/sessions with errors")
    p.add_argument("--format", choices=["table", "json"], default="table")
    p.add_argument("--all-spans", action="store_true", help="waterfall: include HTTP/A2A internals")
    p.add_argument(
        "--full",
        action="store_true",
        help=f"session: show whole messages (default: first {MESSAGE_LINES} lines each)",
    )
    return p


def _when(iso: str) -> str:
    return iso[:19].replace("T", " ") if iso else "-"


def _tokens(i: int, o: int) -> str:
    return f"{i:,} / {o:,}"


def _meta(t: Target, since: str) -> list[str]:
    where = {"agent_runtime": "Engine", "cloud_run": "Service", "gke": "Service"}[t.kind]
    return [f"Project: {t.project}", f"Region: {t.region or '?'}", f"{where}: {t.agent_id}", f"Window: {since}"]


class App:
    def __init__(self, target: Target, http: cloudtrace.Transport, out: TextIO, style: Style):
        self.t, self.http, self.out, self.st = target, http, out, style

    def turns(self, since: str, want: int) -> list[dict]:
        end = datetime.now(timezone.utc)
        traces = cloudtrace.list_traces(
            self.http, self.t.project, self.t.trace_filter, end - parse_since(since), end, want
        )
        return sorted(
            (model.summarize(t) for t in traces if model.is_turn(t)),
            key=lambda r: r["start"],
            reverse=True,
        )

    def emit_json(self, data: object) -> None:
        print(json.dumps(data, indent=2), file=self.out)

    def cmd_list(self, a: argparse.Namespace) -> int:
        rows = self.turns(a.since, a.limit * 5 if a.errors else a.limit)
        if a.errors:
            rows = [r for r in rows if r["errors"]]
        rows = rows[: a.limit]
        if a.format == "json":
            self.emit_json(rows)
            return 0
        st = self.st
        render.title(self.out, st, "Turns", _meta(self.t, a.since) + [f"Turns: {len(rows)}"])
        if not rows:
            print(st.dim(" No turns in this window. Try --since 30d."), file=self.out)
            return 0
        render.table(
            self.out,
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
        render.hint(self.out, st, "agents-cli traces show <trace-id> | traces session <id> | traces sessions")
        return 0

    def cmd_sessions(self, a: argparse.Namespace) -> int:
        sessions = model.group_sessions(self.turns(a.since, SESSIONS_SCAN))
        if a.errors:
            sessions = [s for s in sessions if s["errors"]]
        sessions = sessions[: a.limit]
        if a.format == "json":
            self.emit_json(sessions)
            return 0
        st = self.st
        render.title(self.out, st, "Sessions", _meta(self.t, a.since) + [f"Sessions: {len(sessions)}"])
        if not sessions:
            print(st.dim(" No sessions with activity in this window. Try --since 30d."), file=self.out)
            return 0
        render.table(
            self.out,
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
                for s in sessions
            ],
            right={2, 4, 5, 6},
        )
        render.hint(self.out, st, "agents-cli traces session <id>")
        return 0


    # --- one session: messages and a waterfall per turn ---------------------
    def cmd_session(self, a: argparse.Namespace) -> int:
        sid = a.id
        end = datetime.now(timezone.utc)
        traces = cloudtrace.list_traces(
            self.http,
            self.t.project,
            f"{TURN_FILTER} +gen_ai.conversation.id:{sid}",
            end - parse_since(a.since),
            end,
            SESSIONS_SCAN,
        )
        turns = sorted(
            ((model.summarize(t), t) for t in traces if model.is_turn(t)),
            key=lambda p: p[0]["start"],
        )
        turns = [(s, t) for s, t in turns if s["session_id"] == sid]

        note = None
        messages: list[dict] = []
        try:
            engine = sessions.find_engine(self.http, self.t)
            messages = sessions.to_messages(sessions.list_events(self.http, engine, sid, self.t.region))
        except sessions.NoTranscript as e:
            note = str(e)
        except cloudtrace.ApiError as e:
            note = f"Couldn't read the transcript: {e}"

        if not turns and not messages:
            raise UsageError(f"No turns or messages for session {sid} in the last {a.since}. Try --since 30d.")

        if a.format == "json":
            self.emit_json(
                {
                    "session_id": sid,
                    "turns": [{**s, "invocation_ids": sorted(model.invocation_ids(t))} for s, t in turns],
                    "messages": messages,
                    "transcript_note": note,
                }
            )
            return 0

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

        st = self.st
        s_tok = (sum(s["input_tokens"] for s, _ in turns), sum(s["output_tokens"] for s, _ in turns))
        render.title(
            self.out,
            st,
            f"Session {sid}",
            _meta(self.t, a.since) + [f"Turns: {len(turns)}", f"Tokens: {_tokens(*s_tok)}"],
        )
        if note:
            print(st.yellow(f" ! {note}"), file=self.out)
            print(file=self.out)
        n = 0
        for start, s, t, msgs in sections:
            if s is None:
                print(st.bold(f" {_when(start)}  (no trace in this window)"), file=self.out)
            else:
                n += 1
                head = f" Turn {n}  {_when(s['start'])}"
                tail = f"trace {s['trace_id']}  {st.duration(s['duration_s'])}  tokens {_tokens(s['input_tokens'], s['output_tokens'])}"
                print(st.bold(head) + "  " + st.dim(tail) + "  " + render.status(st, s["errors"]), file=self.out)
            for speaker, text in sessions.transcript_lines(msgs):
                self._message(speaker, text, a.full)
            if t is not None:
                if msgs:
                    print(file=self.out)
                self.draw(t, a.all_spans)
            print(file=self.out)
        render.hint(self.out, st, "agents-cli traces show <trace-id> | traces session <id> --full | --format json")
        return 0

    def _message(self, speaker: str, text: str, full: bool = False) -> None:
        st = self.st
        color = st.cyan if speaker == "user" else st.dim if speaker == "tool" else st.magenta
        prefix = f"   {speaker}> "
        indent = " " * len(prefix)
        if full:
            lines, hidden = text.splitlines() or [""], 0
        else:
            cols = shutil.get_terminal_size((120, 24)).columns - len(prefix)
            lines, hidden = render.clip(text, MESSAGE_LINES, cols)
        first, *rest = lines
        print(color(prefix) + first, file=self.out)
        for line in rest:
            print(indent + line, file=self.out)
        if hidden:
            more = "line" if hidden == 1 else "lines"
            print(indent + st.dim(f"... +{hidden} more {more} (--full to show all)"), file=self.out)

    # --- waterfalls --------------------------------------------------------
    def waterfall(self, trace_id: str, all_spans: bool) -> None:
        self.draw(cloudtrace.get_trace(self.http, self.t.project, trace_id), all_spans)

    def draw(self, trace: dict, all_spans: bool) -> None:
        bar_width = max(16, min(40, shutil.get_terminal_size((120, 24)).columns - 90))
        lines = render_waterfall(trace, self.st, bar_width=bar_width, all_spans=all_spans)
        if not lines:
            print(self.st.dim(" (no spans)"), file=self.out)
        for line in lines:
            print(" " + line, file=self.out)

    def cmd_show(self, a: argparse.Namespace) -> int:
        if a.format == "json":
            self.emit_json(cloudtrace.get_trace(self.http, self.t.project, a.id))
            return 0
        render.title(self.out, self.st, f"Trace {a.id}", _meta(self.t, a.since)[:3])
        self.waterfall(a.id, a.all_spans)
        return 0

    def cmd_last(self, a: argparse.Namespace) -> int:
        rows = self.turns(a.since, 20)
        if not rows:
            raise UsageError(f"No turns in the last {a.since}. Try --since 30d.")
        r = rows[0]
        if a.format == "json":
            self.emit_json(cloudtrace.get_trace(self.http, self.t.project, r["trace_id"]))
            return 0
        st = self.st
        render.title(
            self.out,
            st,
            f"Latest turn {_when(r['start'])}",
            _meta(self.t, a.since)[:3]
            + [f"Trace: {r['trace_id']}", f"Session: {r['session_id']}",
               f"Tokens: {_tokens(r['input_tokens'], r['output_tokens'])}"],
        )
        self.waterfall(r["trace_id"], a.all_spans)
        return 0


def main(
    argv: list[str] | None = None,
    *,
    cwd: Path | None = None,
    http: cloudtrace.Transport | None = None,
    out: TextIO | None = None,
) -> int:
    out = out or render.default_stream()
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command in NEEDS_ID and not args.id:
        print(f"x `traces {args.command}` needs {NEEDS_ID[args.command]}.", file=sys.stderr)
        return 2
    rich = args.format == "table" and render.detect_rich(out)
    if rich:
        render.ensure_utf8(out)
    try:
        target = resolve_target(cwd or Path.cwd())
        app = App(target, http or cloudtrace.GoogleTransport(quota_project=target.project), out, Style(rich))
        handlers = {
            "list": app.cmd_list,
            "sessions": app.cmd_sessions,
            "session": app.cmd_session,
            "show": app.cmd_show,
            "last": app.cmd_last,
        }
        return handlers[args.command](args)
    except (TargetError, UsageError, cloudtrace.ApiError) as e:
        print(f"x {e}", file=sys.stderr)
        return 1
