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

"""Seam 4: the interactive browser, driven by key names (no curses)."""

from __future__ import annotations

import io

from agent_traces.browse import Browser
from agent_traces.cli import App, build_parser, main
from agent_traces.render import Style
from agent_traces.target import resolve_target
from conftest import FakeTransport, load_fixture
from test_cli import SESSION_RUNTIME, _long_reply_events, _trace


def browser(project, argv, routes) -> Browser:
    args = build_parser().parse_args(argv)
    app = App(resolve_target(project), FakeTransport(routes), io.StringIO(), Style(False), args, interactive=True)
    return Browser(app.root_view(), tabs={"t": app.view_turns, "s": app.view_sessions})


def highlighted(b: Browser, height: int = 40) -> str:
    return next(r.text for r in b.screen(height, 160) if r.highlighted)


def crumbs(b: Browser) -> str:
    return b.screen(40, 160)[0].text.strip()


NEWEST = "1e352072b4b9a1e7b584b6156678d98a"


def test_turns_list_starts_on_the_newest_turn_and_moves_with_arrows(cloud_run_project):
    b = browser(cloud_run_project, ["-i", "--since", "30d"], {"/traces": load_fixture("cloud_run_list.json")})

    assert NEWEST in highlighted(b)
    b.handle("down")
    assert "c4964da89689f55228f54f61947265a6" in highlighted(b)
    b.handle("up")
    b.handle("up")  # already on the first row: stays
    assert NEWEST in highlighted(b)


def test_enter_opens_the_waterfall_then_a_span_and_back_restores_the_cursor(cloud_run_project):
    b = browser(
        cloud_run_project,
        ["-i", "--since", "30d"],
        {f"/traces/{NEWEST}": _trace("cloud_run_list.json", NEWEST), "/traces": load_fixture("cloud_run_list.json")},
    )
    b.handle("down")
    b.handle("up")

    b.handle("enter")
    assert crumbs(b).startswith("Turns › Turn 2026-10-25 15:05:57")
    assert "[turn]" in highlighted(b)  # first span row
    b.handle("down")
    assert "invoke_agent traces_cr_demo" in highlighted(b)

    b.handle("enter")
    screen = "\n".join(r.text for r in b.screen(60, 160))
    assert "gen_ai.agent.name" in screen and "traces_cr_demo" in screen

    b.handle("left")
    assert "invoke_agent traces_cr_demo" in highlighted(b)
    b.handle("esc")
    assert crumbs(b) == "Turns" and NEWEST in highlighted(b)


def test_session_view_selects_message_lines_and_enter_shows_the_whole_message(cloud_run_project):
    sid = "6349499777176043520"
    b = browser(
        cloud_run_project,
        ["session", sid, "-i", "--since", "30d"],
        {"/traces": load_fixture("cloud_run_list.json"), "/reasoningEngines": SESSION_RUNTIME, "/events": _long_reply_events()},
    )

    assert highlighted(b).startswith(" Turn 1")
    b.handle("down")
    assert "user> What's the weather in San Francisco?" in highlighted(b)
    for _ in range(2):  # tool call, then the first line of the reply
        b.handle("down")
    assert "Paragraph 1 of the forecast." in highlighted(b)
    b.handle("down")  # every line of a message is selectable
    assert "Paragraph 2 of the forecast." in highlighted(b)
    screen = "\n".join(r.text for r in b.screen(80, 160))
    assert "(Enter to show all)" in screen and "--full" not in screen

    b.handle("enter")
    full = "\n".join(r.text for r in b.screen(80, 160))
    assert "Paragraph 12 of the forecast." in full
    assert crumbs(b).endswith("› traces_cr_demo message")


def test_tabs_switch_between_turns_and_sessions(cloud_run_project):
    routes = {
        "/traces": [load_fixture("cloud_run_list.json")] * 3,
        "/reasoningEngines": SESSION_RUNTIME,
        "/events": load_fixture("cloud_run_session_events.json"),
    }
    b = browser(cloud_run_project, ["-i", "--since", "30d"], routes)

    b.handle("s")
    assert crumbs(b) == "Sessions"
    assert "4890333497908002816" in highlighted(b)
    b.handle("down")
    b.handle("down")
    b.handle("enter")
    assert crumbs(b) == "Sessions › Session 6349499777176043520"
    b.handle("t")
    assert crumbs(b) == "Turns"


def test_cursor_stays_visible_on_a_short_screen(cloud_run_project):
    sid = "6349499777176043520"
    b = browser(
        cloud_run_project,
        ["session", sid, "-i", "--since", "30d"],
        {"/traces": load_fixture("cloud_run_list.json"), "/reasoningEngines": SESSION_RUNTIME, "/events": load_fixture("cloud_run_session_events.json")},
    )
    for _ in range(15):
        b.handle("down")
        assert any(r.highlighted for r in b.screen(8, 160))
    b.handle("end")
    assert "430/27 tok" in highlighted(b, height=8)  # last span of the last turn


def test_q_quits():
    from agent_traces.view import View

    assert Browser(View("x")).handle("q") is False


def test_interactive_needs_a_terminal(cloud_run_project, capsys):
    code = main(["-i"], cwd=cloud_run_project, http=FakeTransport({}))

    assert code == 1
    assert "needs a terminal" in capsys.readouterr().err


# --- tool I/O, error jumps, wrapping ------------------------------------------
TURN1 = "d87529272f2bb8fc4fd5e878cf83dbfd"  # session 6349499777176043520, calls get_weather


def screen_text(b: Browser, height: int = 80, width: int = 160) -> str:
    return "\n".join(r.text for r in b.screen(height, width))


def open_line(b: Browser, needle: str) -> None:
    """Move the cursor to the first selectable line containing ``needle`` and press Enter."""
    for _ in range(200):
        if needle in highlighted(b, 200):
            b.handle("enter")
            return
        b.handle("down")
    raise AssertionError(f"no selectable line with {needle!r}")


def test_tool_span_shows_its_arguments_and_result_from_the_session(cloud_run_project):
    b = browser(
        cloud_run_project,
        ["-i", "--since", "30d"],
        {
            f"/traces/{TURN1}": _trace("cloud_run_list.json", TURN1),
            "/traces": load_fixture("cloud_run_list.json"),
            "/reasoningEngines": SESSION_RUNTIME,
            "/events": load_fixture("cloud_run_session_events.json"),
        },
    )
    open_line(b, TURN1)
    open_line(b, "execute_tool get_weather")

    text = screen_text(b)
    assert "Arguments" in text and "query: 'San Francisco'" in text
    assert "Result" in text and "It's 60 degrees and foggy." in text
    # Arguments come before the raw labels.
    assert text.index("Arguments") < text.index("Labels")


def test_tool_span_in_a_session_reuses_the_loaded_transcript(cloud_run_project):
    sid = "6349499777176043520"
    args = build_parser().parse_args(["session", sid, "-i", "--since", "30d"])
    http = FakeTransport(
        {"/traces": load_fixture("cloud_run_list.json"), "/reasoningEngines": SESSION_RUNTIME,
         "/events": load_fixture("cloud_run_session_events.json")}
    )
    app = App(resolve_target(cloud_run_project), http, io.StringIO(), Style(False), args, interactive=True)
    b = Browser(app.root_view())
    calls = len(http.requests)

    open_line(b, "execute_tool get_weather")

    assert "It's 60 degrees and foggy." in screen_text(b)
    assert len(http.requests) == calls  # no second trip to the Sessions API


def test_tool_span_without_a_transcript_says_why(cloud_run_project):
    manifest = cloud_run_project / "agents-cli-manifest.yaml"
    manifest.write_text(manifest.read_text().replace("agent_platform_sessions", "in_memory"))
    b = browser(
        cloud_run_project,
        ["-i", "--since", "30d"],
        {f"/traces/{TURN1}": _trace("cloud_run_list.json", TURN1), "/traces": load_fixture("cloud_run_list.json")},
    )
    open_line(b, TURN1)
    open_line(b, "execute_tool get_weather")

    text = screen_text(b)
    assert "Arguments" in text and "in_memory" in text


def test_e_jumps_to_the_next_error_and_wraps_around(agent_runtime_project):
    failing = "0d824994cc78412905c9f66a635cc241"
    b = browser(
        agent_runtime_project,
        ["-i", "--since", "30d"],
        {f"/traces/{failing}": _trace("agent_runtime_list.json", failing), "/traces": load_fixture("agent_runtime_list.json")},
    )
    b.handle("end")
    b.handle("e")  # wraps to the only failing turn
    assert failing in highlighted(b)

    b.handle("enter")
    b.handle("e")
    assert "get_recent_filings" in highlighted(b)
    b.handle("e")  # only one failing span: stays put
    assert "get_recent_filings" in highlighted(b)


def test_e_says_so_when_there_are_no_errors(cloud_run_project):
    b = browser(cloud_run_project, ["-i", "--since", "30d"], {"/traces": load_fixture("cloud_run_list.json")})

    b.handle("e")

    assert "No errors" in b.screen(40, 160)[-1].text
    assert NEWEST in highlighted(b)


def test_span_details_wrap_long_values_instead_of_cutting_them(agent_runtime_project, monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    failing = "0d824994cc78412905c9f66a635cc241"
    b = browser(
        agent_runtime_project,
        ["show", failing, "-i"],
        {f"/traces/{failing}": _trace("agent_runtime_list.json", failing), "/events": {"sessionEvents": []}},
    )
    open_line(b, "get_recent_filings")

    lines = [r.text for r in b.screen(200, 80)]
    assert all(len(line) <= 80 for line in lines)
    text = " ".join("\n".join(lines).split())
    # The whole tool description survives, wrapped onto continuation lines.
    assert "exact document URL if found." in text
