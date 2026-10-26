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

"""Curses front end for ``browse.Browser`` (standard library only).

Views are rendered with the same ANSI styling as the static output; this
module turns those SGR codes into curses attributes, so both look alike.
Python on Windows ships without ``curses``; there ``-i`` explains that and the
static output keeps working.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Callable, TextIO

from agent_traces.browse import Browser, Row
from agent_traces.render import width
from agent_traces.view import View

SGR_RE = re.compile(r"\033\[([0-9;]*)m")


class Unavailable(Exception):
    """Interactive mode can't run here; the message says why."""


def check_available(out: TextIO) -> None:
    try:
        import curses  # noqa: F401
    except ImportError as e:
        raise Unavailable(
            "Interactive mode needs Python's curses module, which Windows Python doesn't include. "
            "Use the static output (drop -i), or run under WSL."
        ) from e
    if not (sys.stdin.isatty() and getattr(out, "isatty", lambda: False)()):
        raise Unavailable("Interactive mode needs a terminal; drop -i when piping or scripting.")


def parse_sgr(text: str) -> list[tuple[str, tuple[bool, bool, int | None, int | None]]]:
    """Split ANSI-styled text into (chunk, (bold, dim, fg, bg)) with fg/bg as 0-15 or None."""
    out = []
    bold = dim = False
    fg = bg = None
    pos = 0
    for m in SGR_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos : m.start()], (bold, dim, fg, bg)))
        for code in [int(c) for c in m.group(1).split(";") if c] or [0]:
            if code == 0:
                bold = dim = False
                fg = bg = None
            elif code == 1:
                bold = True
            elif code == 2:
                dim = True
            elif 30 <= code <= 37:
                fg = code - 30
            elif 90 <= code <= 97:
                fg = code - 90 + 8
            elif 40 <= code <= 47:
                bg = code - 40
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], (bold, dim, fg, bg)))
    return out


KEYMAP_CHARS = {"\n": "enter", "\r": "enter", "\x1b": "esc", "\x7f": "back", "\x08": "back"}


def run(root: View, tabs: dict[str, Callable[[], View]]) -> int:
    import curses
    import locale

    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")  # snappy Esc
    return curses.wrapper(_loop, root, tabs)


def _loop(scr, root: View, tabs: dict[str, Callable[[], View]]) -> int:
    import curses

    curses.curs_set(0)
    colors = curses.has_colors() and not os.environ.get("NO_COLOR")
    pairs: dict[tuple[int, int], int] = {}
    if colors:
        curses.start_color()
        try:
            curses.use_default_colors()
        except curses.error:
            pass

    def color(fg: int | None, bg: int | None) -> int:
        if not colors or (fg is None and bg is None):
            return 0
        if fg is not None and fg >= 8 and curses.COLORS < 16:
            fg -= 8
        key = (-1 if fg is None else fg, -1 if bg is None else bg)
        if key not in pairs:
            if len(pairs) + 1 >= curses.COLOR_PAIRS:
                return 0
            curses.init_pair(len(pairs) + 1, *key)
            pairs[key] = len(pairs) + 1
        return curses.color_pair(pairs[key])

    keys = {
        curses.KEY_UP: "up",
        curses.KEY_DOWN: "down",
        curses.KEY_NPAGE: "pgdn",
        curses.KEY_PPAGE: "pgup",
        curses.KEY_HOME: "home",
        curses.KEY_END: "end",
        curses.KEY_ENTER: "enter",
        curses.KEY_LEFT: "left",
        curses.KEY_RIGHT: "right",
        curses.KEY_BACKSPACE: "back",
    }

    def draw_row(y: int, row: Row, cols: int) -> None:
        base = curses.A_REVERSE if row.highlighted else 0
        if row.chrome:
            base = (color(15, 4) | curses.A_BOLD) if colors else curses.A_REVERSE
        x = 0
        for chunk, (bold, dim, fg, bg) in parse_sgr(row.text):
            attr = base | (curses.A_BOLD if bold else 0) | (curses.A_DIM if dim and not row.highlighted else 0)
            if not row.chrome:
                attr |= color(fg, bg)
            for ch in chunk:
                w = width(ch)
                if x + w > cols - 1:
                    break
                try:
                    scr.addstr(y, x, ch, attr)
                except curses.error:
                    pass
                x += w
        if base and x < cols - 1:
            try:
                scr.addstr(y, x, " " * (cols - 1 - x), base)
            except curses.error:
                pass

    def draw() -> None:
        scr.erase()
        rows_n, cols = scr.getmaxyx()
        for y, row in enumerate(browser.screen(rows_n, cols)):
            draw_row(y, row, cols)
        scr.refresh()

    def loading(msg: str) -> None:
        rows_n, cols = scr.getmaxyx()
        draw_row(rows_n - 1, Row(" " + msg, chrome=True), cols)
        scr.refresh()

    browser = Browser(root, tabs=tabs, loading=loading)
    while True:
        draw()
        ch = scr.get_wch()
        if ch == curses.KEY_RESIZE:
            continue
        key = keys.get(ch) if isinstance(ch, int) else KEYMAP_CHARS.get(ch, ch)
        if key and not browser.handle(key):
            return 0
