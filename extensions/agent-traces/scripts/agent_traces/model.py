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

"""Turn raw Cloud Trace traces into turns, using OpenTelemetry GenAI labels.

One trace is one turn (one user message handled by the agent).
"""

from __future__ import annotations

import re
from datetime import datetime

# ADK wraps parallel tool calls in an extra execute_tool span with this name.
MERGED_TOOLS = "(merged tools)"


def parse_ts(s: str) -> datetime:
    """RFC 3339 with nanoseconds (Cloud Trace) to an aware datetime."""
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d*", r"\1", s).replace("Z", "+00:00"))


def span_error(labels: dict) -> str | None:
    return (
        labels.get("error.type")
        or labels.get("/error/name")
        or ("error" if labels.get("otel.status_code") == "ERROR" else None)
    )


def is_turn(trace: dict) -> bool:
    """A trace in which the agent ran (not a health check or HTTP-only request)."""
    return any(s.get("name", "").startswith("invoke_agent") for s in trace.get("spans", []))


def invocation_ids(trace: dict) -> set[str]:
    """ADK invocation IDs in a trace; session events carry the same IDs."""
    return {
        s["labels"]["gcp.vertex.agent.invocation_id"]
        for s in trace.get("spans", [])
        if s.get("labels", {}).get("gcp.vertex.agent.invocation_id")
    }


def summarize(trace: dict) -> dict:
    spans = trace.get("spans", [])
    root = min(spans, key=lambda s: s.get("startTime", "")) if spans else {}
    session = None
    agents: list[str] = []
    tools: list[str] = []
    errors: list[dict] = []
    models: set[str] = set()
    tokens_in = tokens_out = 0
    for s in spans:
        labels, name = s.get("labels", {}), s.get("name", "")
        session = (
            session
            or labels.get("gen_ai.conversation.id")
            or labels.get("gcp.vertex.agent.session_id")
        )
        if name.startswith("invoke_agent"):
            agent = labels.get("gen_ai.agent.name") or name.partition(" ")[2]
            if agent and agent not in agents:
                agents.append(agent)
        elif name.startswith("execute_tool"):
            tool = labels.get("gen_ai.tool.name") or name.partition(" ")[2]
            if tool and tool != MERGED_TOOLS:
                tools.append(tool)
        elif name.startswith("generate_content"):
            tokens_in += int(labels.get("gen_ai.usage.input_tokens") or 0)
            tokens_out += int(labels.get("gen_ai.usage.output_tokens") or 0)
            model = labels.get("gen_ai.request.model") or name.partition(" ")[2]
            if model:
                models.add(model)
        err = span_error(labels)
        if err:
            errors.append({"span": name, "type": err})
    duration = 0.0
    timed = [s for s in spans if s.get("startTime") and s.get("endTime")]
    if timed:
        # Envelope, not root: an A2A root span ends before the streamed reply does.
        first = min(parse_ts(s["startTime"]) for s in timed)
        last = max(parse_ts(s["endTime"]) for s in timed)
        duration = (last - first).total_seconds()
    return {
        "trace_id": trace["traceId"],
        "start": root.get("startTime", ""),
        "duration_s": round(duration, 2),
        "session_id": session,
        "agents": agents,
        "tools": tools,
        "models": sorted(models),
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "errors": errors,
    }


def group_sessions(turns: list[dict]) -> list[dict]:
    """Turns grouped by session, most recently active session first."""
    by_session: dict[str, list[dict]] = {}
    for t in turns:
        by_session.setdefault(t["session_id"] or "(none)", []).append(t)
    sessions = []
    for sid, ts in by_session.items():
        ts.sort(key=lambda t: t["start"])
        agents: list[str] = []
        for t in ts:
            agents += [a for a in t["agents"] if a not in agents]
        sessions.append(
            {
                "session_id": sid,
                "first": ts[0]["start"],
                "last": ts[-1]["start"],
                "turns": len(ts),
                "agents": agents,
                "tools": sum(len(t["tools"]) for t in ts),
                "input_tokens": sum(t["input_tokens"] for t in ts),
                "output_tokens": sum(t["output_tokens"] for t in ts),
                "duration_s": round(sum(t["duration_s"] for t in ts), 2),
                "errors": [e for t in ts for e in t["errors"]],
                "trace_ids": [t["trace_id"] for t in ts],
            }
        )
    return sorted(sessions, key=lambda s: s["last"], reverse=True)
