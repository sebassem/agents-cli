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

"""Session transcripts from the Agent Runtime Sessions API (standard library only).

Where the session events live:
- Agent Runtime: on the deployed engine itself.
- Cloud Run / GKE with ``session_type: agent_platform_sessions``: on a
  sessions-only Agent Runtime whose display name is the project name.
- Any other session store (in-memory, Cloud SQL): nowhere we can read.

Events carry the ADK ``invocationId``, which traces also carry
(``gcp.vertex.agent.invocation_id``), so messages can be matched to turns.
"""

from __future__ import annotations

import json
import urllib.parse

from agent_traces.cloudtrace import ApiError, Transport
from agent_traces.target import Target

API = "https://{region}-aiplatform.googleapis.com/v1beta1"
STORED = "agent_platform_sessions"
MAX_PAGES = 50
MAX_RESULT = 200  # characters of a tool result shown in the table view


class NoTranscript(Exception):
    """No transcript can be read; the message says why (shown as a note)."""


def _engines_url(project: str, region: str) -> str:
    return f"{API.format(region=region)}/projects/{project}/locations/{region}/reasoningEngines"


def find_engines(http: Transport, t: Target) -> list[str]:
    """Resource names of the engines that may hold this agent's sessions.

    Usually one. But the Cloud Run / GKE app creates its session store at
    startup when none exists, so replicas starting together can each create
    one with the same display name (seen live on GKE with 2 replicas); a
    session then lives in whichever store its pod created.
    """
    if not t.region:
        raise NoTranscript("No region in the project files, so the session store can't be found.")
    if t.kind == "agent_runtime":
        return [f"projects/{t.project}/locations/{t.region}/reasoningEngines/{t.agent_id}"]
    if t.session_type != STORED:
        raise NoTranscript(
            f"Sessions are stored in {t.session_type or 'an unknown store'}, so there is no transcript; "
            "showing traces only."
        )
    query = urllib.parse.urlencode({"filter": f'display_name="{t.agent_id}"'})
    engines = http.get_json(f"{_engines_url(t.project, t.region)}?{query}").get("reasoningEngines", [])
    if not engines:
        raise NoTranscript(f"No session store named {t.agent_id} in {t.region}, so there is no transcript.")
    return [e["name"] for e in engines]


def read_events(http: Transport, t: Target, session_id: str) -> list[dict]:
    """The session's events from whichever store has it."""
    engines = find_engines(http, t)
    for engine in engines:
        try:
            return list_events(http, engine, session_id, t.region)
        except ApiError as e:
            if e.status != 404:
                raise
    stores = "the session store" if len(engines) == 1 else f"the {len(engines)} session stores named {t.agent_id}"
    raise NoTranscript(f"Session {session_id} was not found in {stores} (it may have expired or been deleted).")


def list_events(http: Transport, engine: str, session_id: str, region: str) -> list[dict]:
    base = f"{API.format(region=region)}/{engine}/sessions/{urllib.parse.quote(session_id)}/events"
    params = {"pageSize": "100"}
    events: list[dict] = []
    for _ in range(MAX_PAGES):
        data = http.get_json(f"{base}?{urllib.parse.urlencode(params)}")
        events += data.get("sessionEvents", [])
        token = data.get("nextPageToken")
        if not token:
            break
        params["pageToken"] = token
    return sorted(events, key=lambda e: e.get("timestamp", ""))


def to_messages(events: list[dict]) -> list[dict]:
    """Flatten events into messages: text, tool_call or tool_result. Thoughts are skipped."""
    out: list[dict] = []
    for e in events:
        base = {
            "invocation_id": e.get("invocationId"),
            "timestamp": e.get("timestamp"),
            "author": e.get("author"),
        }
        for part in (e.get("content") or {}).get("parts", []):
            if part.get("thought"):
                continue
            if part.get("text"):
                out.append({**base, "kind": "text", "text": part["text"]})
            elif "functionCall" in part:
                fc = part["functionCall"]
                out.append(
                    {**base, "kind": "tool_call", "id": fc.get("id"), "tool": fc.get("name"), "args": fc.get("args") or {}}
                )
            elif "functionResponse" in part:
                fr = part["functionResponse"]
                out.append(
                    {**base, "kind": "tool_result", "id": fr.get("id"), "tool": fr.get("name"), "result": fr.get("response")}
                )
    return out


def _result_text(result: object) -> str:
    if isinstance(result, dict) and set(result) == {"result"}:
        result = result["result"]
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return text if len(text) <= MAX_RESULT else text[: MAX_RESULT - 3] + "..."


def transcript_lines(messages: list[dict]) -> list[tuple[str, str]]:
    """(speaker, text) pairs; a tool call and its result share one line."""
    results = {m["id"]: m for m in messages if m["kind"] == "tool_result" and m.get("id")}
    used: set[str] = set()
    lines: list[tuple[str, str]] = []
    for m in messages:
        if m["kind"] == "text":
            lines.append((m["author"] or "?", m["text"]))
        elif m["kind"] == "tool_call":
            args = ", ".join(f"{k}={v!r}" for k, v in m["args"].items())
            text = f"{m['tool']}({args})"
            res = results.get(m.get("id"))
            if res:
                used.add(res["id"])
                text += f" -> {_result_text(res['result'])}"
            lines.append(("tool", text))
        elif m["kind"] == "tool_result" and m.get("id") not in used:
            lines.append(("tool", f"{m['tool']} -> {_result_text(m['result'])}"))
    return lines
