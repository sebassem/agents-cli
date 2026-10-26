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

"""Navigation for the interactive view, independent of curses (so it's testable).

A stack of views, each with a cursor and a scroll offset. Keys are names
(``up``, ``enter``, ``back`` ...); ``tui`` maps real keys onto them and draws
what ``screen()`` returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from agent_traces.cloudtrace import ApiError
from agent_traces.view import Line, View

KEYS_HELP = "↑↓ move  Enter open  ← back  e next error  PgUp/PgDn  t turns  s sessions  q quit"


@dataclass
class Frame:
    view: View
    cursor: int = 0  # index into view.lines
    top: int = 0  # first line shown


@dataclass
class Row:
    text: str
    highlighted: bool = False
    chrome: bool = False  # header/footer bar


class Browser:
    def __init__(
        self,
        root: View,
        tabs: dict[str, Callable[[], View]] | None = None,
        loading: Callable[[str], None] | None = None,
    ):
        self.stack = [self._frame(root)]
        self.tabs = tabs or {}
        self.loading = loading or (lambda _msg: None)
        self.status = ""
        self.body_height = 20  # updated by screen()
        self._cache: dict[int, View] = {}

    # --- state -------------------------------------------------------------
    @property
    def frame(self) -> Frame:
        return self.stack[-1]

    @property
    def lines(self) -> list[Line]:
        return self.frame.view.lines

    def _selectable(self) -> list[int]:
        return [i for i, line in enumerate(self.lines) if line.is_selectable]

    def _frame(self, view: View) -> Frame:
        first = next((i for i, line in enumerate(view.lines) if line.is_selectable), 0)
        return Frame(view, cursor=first)

    @property
    def current(self) -> Line | None:
        lines = self.lines
        if lines and lines[self.frame.cursor].is_selectable:
            return lines[self.frame.cursor]
        return None

    # --- keys --------------------------------------------------------------
    def handle(self, key: str) -> bool:
        """Apply one key; False means quit."""
        self.status = ""
        if key in ("q", "quit"):
            return False
        if key in ("up", "k"):
            self._step(-1)
        elif key in ("down", "j"):
            self._step(1)
        elif key == "pgdn":
            self._page(1)
        elif key == "pgup":
            self._page(-1)
        elif key in ("home", "g"):
            self._jump(0)
        elif key in ("end", "G"):
            self._jump(len(self.lines) - 1)
        elif key in ("enter", "right", "l"):
            self._open()
        elif key in ("back", "left", "h", "esc"):
            if len(self.stack) > 1:
                self.stack.pop()
        elif key == "e":
            self._next_error()
        elif key in self.tabs:
            self._tab(key)
        return True

    def _next_error(self) -> None:
        errors = [i for i, line in enumerate(self.lines) if line.error and line.is_selectable]
        if not errors:
            self.status = "No errors in this view."
            return
        f = self.frame
        f.cursor = next((i for i in errors if i > f.cursor), errors[0])  # wrap around
        self._follow()

    def _step(self, delta: int) -> None:
        sel = self._selectable()
        f = self.frame
        ahead = [i for i in sel if (i > f.cursor if delta > 0 else i < f.cursor)]
        if ahead:
            f.cursor = ahead[0] if delta > 0 else ahead[-1]
        else:
            # Nothing to select that way: scroll, so trailing text can be read.
            f.top = max(0, min(f.top + delta, len(self.lines) - self.body_height))
            return
        self._follow()

    def _page(self, direction: int) -> None:
        f, sel = self.frame, self._selectable()
        goal = f.cursor + direction * self.body_height
        if sel:
            f.cursor = min(sel, key=lambda i: abs(i - goal))
        f.top = max(0, min(f.top + direction * self.body_height, len(self.lines) - self.body_height))
        self._follow()

    def _jump(self, goal: int) -> None:
        f, sel = self.frame, self._selectable()
        if sel:
            f.cursor = min(sel, key=lambda i: abs(i - goal))
        f.top = 0 if goal == 0 else max(0, len(self.lines) - self.body_height)
        self._follow()

    def _follow(self) -> None:
        f = self.frame
        if f.cursor < f.top:
            f.top = f.cursor
        elif f.cursor >= f.top + self.body_height:
            f.top = f.cursor - self.body_height + 1

    def _open(self) -> None:
        line = self.current
        if line is None or line.open is None:
            return
        view = self._cache.get(id(line))
        if view is None:
            self.loading("Loading…")
            try:
                view = line.open()
            except ApiError as e:
                self.status = f"x {e}"
                return
            self._cache[id(line)] = view
        self.stack.append(self._frame(view))

    def _tab(self, key: str) -> None:
        self.loading("Loading…")
        try:
            view = self.tabs[key]()
        except ApiError as e:
            self.status = f"x {e}"
            return
        self.stack = [self._frame(view)]

    # --- drawing -----------------------------------------------------------
    def screen(self, height: int, width: int) -> list[Row]:
        """Rows to draw: a breadcrumb bar, the visible lines, a key/status bar."""
        self.body_height = max(1, height - 2)
        self._follow()
        f = self.frame
        crumbs = " › ".join(fr.view.title for fr in self.stack)
        rows = [Row((" " + crumbs)[: width - 1], chrome=True)]
        shown = self.lines[f.top : f.top + self.body_height]
        for i, line in enumerate(shown, start=f.top):
            rows.append(Row(line.text, highlighted=(i == f.cursor and line.is_selectable)))
        rows += [Row("")] * (self.body_height - len(shown))
        rows.append(Row((" " + (self.status or KEYS_HELP))[: width - 1], chrome=True))
        return rows
