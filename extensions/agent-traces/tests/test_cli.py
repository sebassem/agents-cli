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

"""Seam 1: `agents-cli traces …` end to end, against a fake Cloud Trace API."""

from __future__ import annotations

import json

from agent_traces.cli import main
from conftest import FakeTransport, load_fixture


def run(argv, *, cwd, http, capsys) -> tuple[int, str]:
    code = main(argv, cwd=cwd, http=http)
    return code, capsys.readouterr().out


def test_list_json_returns_turns_of_the_deployed_engine_newest_first(agent_runtime_project, capsys):
    http = FakeTransport({"/traces": load_fixture("agent_runtime_list.json")})

    code, out = run(["--since", "30d", "--format", "json"], cwd=agent_runtime_project, http=http, capsys=capsys)

    assert code == 0
    turns = json.loads(out)
    assert [t["trace_id"] for t in turns] == [
        "0d824994cc78412905c9f66a635cc241",  # 12:54:04
        "56eb62cb59ae75988c25d83316ea4a0b",  # 12:53:43
        "381903fe77ee6bd6cde50c23d82f35c2",  # 12:53:25
        "6dda4d5436f3f10e8f4b69701e955579",  # 12:53:03
    ]
    assert {t["session_id"] for t in turns} == {"600771126192242688"}
    # Values cross-checked against AgFleet's waterfall of the same trace.
    newest = turns[0]
    assert newest["duration_s"] == 5.15
    assert newest["errors"] == [{"span": "execute_tool get_recent_filings", "type": "TypeError"}]
    # Scoped to this project's engine (exact match), agent turns only.
    path, query = http.requests[0]
    assert path == "/v1/projects/demo-project/traces"
    assert query["filter"] == "span:invoke_agent +service.name:3324335473429577728"


def test_list_on_cloud_run_matches_the_service_and_skips_http_noise(cloud_run_project, capsys):
    # Cloud Run spans carry no service.name; the service is in faas.name. The
    # fixture also holds non-turn traces (GET /, session listing, A2A queue).
    http = FakeTransport({"/traces": load_fixture("cloud_run_list.json")})

    code, out = run(["--format", "json"], cwd=cloud_run_project, http=http, capsys=capsys)

    assert code == 0
    turns = json.loads(out)
    # 3 ADK turns over 2 sessions, plus 1 A2A turn (session = A2A context ID).
    assert [t["session_id"] for t in turns] == [
        "4890333497908002816",
        "cb934d9b-6e9e-4671-a79c-94748a8f7a5c",
        "6349499777176043520",
        "6349499777176043520",
    ]
    assert all(t["agents"] == ["traces_cr_demo"] for t in turns)
    # Parallel tool calls add an ADK "(merged tools)" span; it isn't a tool.
    assert turns[0]["tools"] == ["get_current_time", "get_weather", "get_current_time"]
    # The A2A root span returns after 0.32s while the reply streams on; the turn
    # lasts from its first span's start to its last span's end.
    assert turns[1]["duration_s"] == 3.33
    path, query = http.requests[0]
    assert path == "/v1/projects/demo-project/traces"
    assert query["filter"] == "span:invoke_agent +faas.name:traces-cr-demo"


def test_list_table_is_plain_ascii_when_not_a_terminal(agent_runtime_project, capsys):
    http = FakeTransport({"/traces": load_fixture("agent_runtime_list.json")})

    code, out = run(["--since", "30d"], cwd=agent_runtime_project, http=http, capsys=capsys)

    assert code == 0
    assert out.isascii() and "\033[" not in out
    lines = out.splitlines()
    header = next(line for line in lines if "TRACE" in line)
    assert header.split() == ["STARTED", "TRACE", "SESSION", "AGENTS", "TOOLS", "TOKENS", "IN/OUT", "DURATION", "STATUS"]
    rows = [line for line in lines if "600771126192242688" in line]
    assert len(rows) == 4
    assert rows[0].split()[2] == "0d824994cc78412905c9f66a635cc241"
    assert rows[0].rstrip().endswith("x TypeError")
    assert all(r.rstrip().endswith("ok") for r in rows[1:])


def test_sessions_json_groups_turns_by_conversation(cloud_run_project, capsys):
    http = FakeTransport({"/traces": load_fixture("cloud_run_list.json")})

    code, out = run(["sessions", "--format", "json"], cwd=cloud_run_project, http=http, capsys=capsys)

    assert code == 0
    sessions = json.loads(out)
    # Most recently active first; turns within a session in time order.
    assert [(s["session_id"], s["turns"]) for s in sessions] == [
        ("4890333497908002816", 1),
        ("cb934d9b-6e9e-4671-a79c-94748a8f7a5c", 1),
        ("6349499777176043520", 2),
    ]
    sf = sessions[2]
    assert sf["trace_ids"] == ["d87529272f2bb8fc4fd5e878cf83dbfd", "e84854f4c5fe54185d4653970bf2668d"]
    # Per-turn totals (474/32 and 751/46, from the live run) add up.
    assert (sf["input_tokens"], sf["output_tokens"]) == (1225, 78)
    assert sf["errors"] == []


def test_sessions_table_lists_one_row_per_session(cloud_run_project, capsys):
    http = FakeTransport({"/traces": load_fixture("cloud_run_list.json")})

    code, out = run(["sessions"], cwd=cloud_run_project, http=http, capsys=capsys)

    assert code == 0
    assert out.isascii()
    for sid in ("4890333497908002816", "cb934d9b-6e9e-4671-a79c-94748a8f7a5c", "6349499777176043520"):
        assert sum(sid in line for line in out.splitlines()) == 1


def _trace(fixture: str, trace_id: str) -> dict:
    return next(t for t in load_fixture(fixture)["traces"] if t["traceId"] == trace_id)


def test_show_draws_the_waterfall_of_one_trace(agent_runtime_project, capsys):
    trace_id = "0d824994cc78412905c9f66a635cc241"
    http = FakeTransport({f"/traces/{trace_id}": _trace("agent_runtime_list.json", trace_id)})

    code, out = run(["show", trace_id], cwd=agent_runtime_project, http=http, capsys=capsys)

    assert code == 0
    assert http.requests[0][0] == f"/v1/projects/demo-project/traces/{trace_id}"
    lines = out.splitlines()
    assert any(trace_id in line for line in lines[:2])
    # Same tree AgFleet draws for this trace: the failing tool sits under the
    # nested agents, with its error type.
    failing = next(line for line in lines if "get_recent_filings" in line)
    assert "[ERROR] execute_tool get_recent_filings" in failing
    assert failing.rstrip().endswith("TypeError")
    assert sum("[agent] invoke_agent" in line for line in lines) == 6


def test_last_draws_the_newest_turn(cloud_run_project, capsys):
    newest = "1e352072b4b9a1e7b584b6156678d98a"
    http = FakeTransport(
        {
            f"/traces/{newest}": _trace("cloud_run_list.json", newest),
            "/traces": load_fixture("cloud_run_list.json"),
        }
    )

    code, out = run(["last"], cwd=cloud_run_project, http=http, capsys=capsys)

    assert code == 0
    assert "4890333497908002816" in out  # its session
    tool_rows = [line for line in out.splitlines() if "[tool]  execute_tool" in line]
    assert sum("get_current_time" in r for r in tool_rows) == 2
    assert sum("get_weather" in r for r in tool_rows) == 1


def test_show_needs_a_trace_id(agent_runtime_project, capsys):
    code = main(["show"], cwd=agent_runtime_project, http=FakeTransport({}))

    assert code == 2
    assert "trace ID" in capsys.readouterr().err


SESSION_RUNTIME = {
    "reasoningEngines": [
        {
            "name": "projects/123456789012/locations/us-central1/reasoningEngines/4119177651829080064",
            "displayName": "traces-cr-demo",
        }
    ]
}


def test_session_shows_each_turns_messages_above_its_waterfall(cloud_run_project, capsys):
    sid = "6349499777176043520"
    http = FakeTransport(
        {
            "/traces": load_fixture("cloud_run_list.json"),
            "/reasoningEngines": SESSION_RUNTIME,
            "/events": load_fixture("cloud_run_session_events.json"),
        }
    )

    code, out = run(["session", sid, "--since", "30d"], cwd=cloud_run_project, http=http, capsys=capsys)

    assert code == 0
    paths = [p for p, _ in http.requests]
    trace_query = next(q for p, q in http.requests if p.endswith("/traces"))
    assert trace_query["filter"] == f"span:invoke_agent +gen_ai.conversation.id:{sid}"
    # Cloud Run keeps sessions in a sessions-only Agent Runtime named after the project.
    engines_path, engines_query = next((p, q) for p, q in http.requests if p.endswith("/reasoningEngines"))
    assert engines_path == "/v1beta1/projects/demo-project/locations/us-central1/reasoningEngines"
    assert engines_query["filter"] == 'display_name="traces-cr-demo"'
    assert any(p.endswith(f"/reasoningEngines/4119177651829080064/sessions/{sid}/events") for p in paths)

    # Only this session's 2 turns, oldest first; each turn's messages precede its waterfall.
    expected_in_order = [
        "Turn 1",
        "user> What's the weather in San Francisco?",
        "tool> get_weather(query='San Francisco') -> It's 60 degrees and foggy.",
        "traces_cr_demo> The weather in San Francisco is currently 60°F and foggy.",
        "[tool]  execute_tool get_weather",
        "Turn 2",
        "user> And what time is it there?",
        "[tool]  execute_tool get_current_time",
    ]
    positions = [out.find(s) for s in expected_in_order]
    assert -1 not in positions, list(zip(expected_in_order, positions))
    assert positions == sorted(positions)
    assert "4890333497908002816" not in out


def test_session_without_stored_sessions_says_so_and_still_shows_turns(cloud_run_project, capsys):
    manifest = cloud_run_project / "agents-cli-manifest.yaml"
    manifest.write_text(manifest.read_text().replace("agent_platform_sessions", "in_memory"))
    http = FakeTransport({"/traces": load_fixture("cloud_run_list.json")})

    code, out = run(["session", "6349499777176043520"], cwd=cloud_run_project, http=http, capsys=capsys)

    assert code == 0
    assert "in_memory" in out and "no transcript" in out.lower()
    assert all(not p.endswith(("/reasoningEngines", "/events")) for p, _ in http.requests)
    assert out.count("Turn ") == 2


def test_session_on_agent_runtime_reads_events_from_the_deployed_engine(agent_runtime_project, capsys):
    sid = "600771126192242688"
    http = FakeTransport(
        {"/traces": load_fixture("agent_runtime_list.json"), "/events": {"sessionEvents": []}}
    )

    code, _ = run(["session", sid], cwd=agent_runtime_project, http=http, capsys=capsys)

    assert code == 0
    assert any(
        p == f"/v1beta1/projects/demo-project/locations/us-central1/reasoningEngines/3324335473429577728/sessions/{sid}/events"
        for p, _ in http.requests
    )


def test_session_json_has_turns_and_messages(cloud_run_project, capsys):
    http = FakeTransport(
        {
            "/traces": load_fixture("cloud_run_list.json"),
            "/reasoningEngines": SESSION_RUNTIME,
            "/events": load_fixture("cloud_run_session_events.json"),
        }
    )

    code, out = run(["session", "6349499777176043520", "--format", "json"], cwd=cloud_run_project, http=http, capsys=capsys)

    assert code == 0
    data = json.loads(out)
    assert [t["trace_id"] for t in data["turns"]] == [
        "d87529272f2bb8fc4fd5e878cf83dbfd",
        "e84854f4c5fe54185d4653970bf2668d",
    ]
    assert data["messages"][0] == {
        "invocation_id": "e-d285358b-af8c-4940-97d6-b4295e17a314",
        "timestamp": "2026-10-25T15:05:29.284124Z",
        "author": "user",
        "kind": "text",
        "text": "What's the weather in San Francisco?",
    }
    assert len(data["messages"]) == 8


def _long_reply_events() -> dict:
    """The session fixture, with the first agent reply grown to 12 paragraphs."""
    events = load_fixture("cloud_run_session_events.json")
    reply = next(e for e in events["sessionEvents"] if e["author"] != "user" and "text" in e["content"]["parts"][0])
    reply["content"]["parts"][0]["text"] = "\n\n".join(f"Paragraph {i} of the forecast." for i in range(1, 13))
    return events


def _session_http(events: dict) -> FakeTransport:
    return FakeTransport(
        {"/traces": load_fixture("cloud_run_list.json"), "/reasoningEngines": SESSION_RUNTIME, "/events": events}
    )


def test_session_caps_long_messages_and_points_to_full(cloud_run_project, capsys):
    code, out = run(
        ["session", "6349499777176043520", "--since", "30d"],
        cwd=cloud_run_project, http=_session_http(_long_reply_events()), capsys=capsys,
    )

    assert code == 0
    assert "Paragraph 1 of the forecast." in out
    assert "Paragraph 12 of the forecast." not in out
    # Blank lines between paragraphs don't eat into the cap.
    shown = [n for n in range(1, 13) if f"Paragraph {n} of" in out]
    assert shown == list(range(1, len(shown) + 1)) and 2 <= len(shown) < 12
    assert f"+{12 - len(shown)} more lines" in out and "--full" in out
    # The rest of the session still renders.
    assert "user> And what time is it there?" in out


def test_session_full_shows_every_line(cloud_run_project, capsys):
    code, out = run(
        ["session", "6349499777176043520", "--since", "30d", "--full"],
        cwd=cloud_run_project, http=_session_http(_long_reply_events()), capsys=capsys,
    )

    assert code == 0
    assert all(f"Paragraph {n} of the forecast." in out for n in range(1, 13))
    assert "more lines" not in out
