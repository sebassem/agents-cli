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

"""Seam 3: rendering one trace as a span waterfall."""

from __future__ import annotations

import re

from agent_traces.render import ANSI_RE, Style, width
from agent_traces.waterfall import render_waterfall

T0 = "2026-10-25T10:00:0{}.000000000Z"


def span(sid, parent, name, start, end, **labels):
    s = {"spanId": sid, "name": name, "startTime": T0.format(start), "endTime": T0.format(end), "labels": labels}
    if parent:
        s["parentSpanId"] = parent
    return s


# A 4-second turn: the agent calls the model (1s), then a tool that fails (1s).
TRACE = {
    "traceId": "t1",
    "spans": [
        span("1", None, "invocation", 0, 4),
        span("2", "1", "invoke_agent weather_agent", 0, 4, **{"gen_ai.agent.name": "weather_agent"}),
        span("3", "2", "call_llm", 0, 1),
        span(
            "4", "3", "generate_content gemini-x", 0, 1,
            **{"gen_ai.usage.input_tokens": "100", "gen_ai.usage.output_tokens": "10"},
        ),
        span("5", "2", "execute_tool get_weather", 1, 2, **{"error.type": "TypeError"}),
        # Transport internals are hidden by default.
        span("6", "2", "a2a.server.events.event_queue.dequeue_event", 1, 2),
    ],
}


def test_plain_waterfall_is_an_ascii_tree_with_proportional_bars():
    lines = render_waterfall(TRACE, Style(rich=False), bar_width=8)

    assert lines == [
        "[turn]  invocation                          ########   4.00s",
        "`- [agent] invoke_agent weather_agent       ########   4.00s",
        "   |- [llm]   call_llm                      ##......   1.00s",
        "   |  `- [model] generate_content gemini-x  ##......   1.00s  100/10 tok",
        "   `- [ERROR] execute_tool get_weather      ..##....   1.00s  TypeError",
    ]


def test_all_spans_shows_transport_internals():
    lines = render_waterfall(TRACE, Style(rich=False), bar_width=8, all_spans=True)

    assert any("a2a.server.events.event_queue.dequeue_event" in line for line in lines)
    assert len(lines) == 6


def test_rich_waterfall_uses_color_and_emoji_and_keeps_bars_aligned():
    lines = render_waterfall(TRACE, Style(rich=True), bar_width=8)

    assert len(lines) == 5
    assert all("\033[" in line for line in lines)
    joined = "".join(lines)
    for icon in ("🤖", "🧠", "❌"):
        assert icon in joined
    # The bar starts in the same display column on every line.
    bar_cols = set()
    for line in lines:
        plain = ANSI_RE.sub("", line)
        idx = min(i for i, ch in enumerate(plain) if ch in "█·")
        bar_cols.add(width(plain[:idx]))
    assert len(bar_cols) == 1


def test_rich_waterfall_keeps_color_for_bars_and_errors_only():
    root, agent, llm, model, tool = render_waterfall(TRACE, Style(rich=True), bar_width=8)

    # Names read as normal text; agents are bold; only the failing span is red.
    assert "🧠 generate_content gemini-x" in model
    assert "💬 call_llm" in llm
    assert "🤖 \033[1minvoke_agent weather_agent" in agent
    assert "❌ \033[31mexecute_tool get_weather" in tool
    # Empty timeline is faint dots, not shaded blocks.
    assert "░" not in "".join([root, agent, llm, model, tool])
    assert "\033[2m······\033[0m" in model
    # Durations under the slow threshold carry no color.
    assert re.search(r"\033\[(?!0m)[0-9;]*m\s*[0-9.]+s", root) is None
    assert re.search(r"\033\[(?!0m)[0-9;]*m\s*[0-9.]+s", llm) is None
