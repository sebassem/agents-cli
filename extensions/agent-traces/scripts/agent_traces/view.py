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

"""What a command shows, as lines; printed as-is, or browsed interactively.

A line the cursor can rest on is *selectable*; if it has ``open``, Enter opens
the view that function builds (a turn's waterfall, a span's labels, ...).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, TextIO

from agent_traces import render
from agent_traces.render import Style


@dataclass
class Line:
    text: str
    open: Callable[[], "View"] | None = None
    selectable: bool | None = None  # default: selectable when it opens something
    error: bool = False  # a failed turn/span; `e` in the browser jumps between these

    @property
    def is_selectable(self) -> bool:
        return self.open is not None if self.selectable is None else self.selectable


@dataclass
class View:
    title: str  # short, for the breadcrumb
    lines: list[Line] = field(default_factory=list)
    hint: str | None = None  # printed under static output only

    def add(
        self,
        *texts: str,
        open: Callable[[], "View"] | None = None,
        selectable: bool | None = None,
        error: bool = False,
    ) -> None:
        self.lines += [Line(t, open, selectable, error) for t in texts]


def print_view(out: TextIO, st: Style, view: View) -> None:
    for line in view.lines:
        print(line.text, file=out)
    if view.hint:
        for text in render.hint_lines(st, view.hint):
            print(text, file=out)
