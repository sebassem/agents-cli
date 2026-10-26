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

"""Terminal rendering: rich (ANSI colors + emoji) on a TTY, plain ASCII otherwise.

Rich output is used only when stdout is a terminal, ``NO_COLOR`` is unset and
``TERM`` isn't ``dumb``; ``FORCE_COLOR`` forces it (useful through pagers).
Emoji are restricted to ones every modern terminal draws two columns wide, so
columns stay aligned; widths are computed with that assumption.
"""

from __future__ import annotations

import os
import re
import sys
import textwrap
import unicodedata
from dataclasses import dataclass
from typing import TextIO

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _enable_windows_ansi() -> bool:
    """Turn on VT escape processing in a Windows console; False if unavailable."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
    except Exception:  # noqa: BLE001 - any failure means "no ANSI"
        return False


def detect_rich(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb" or not getattr(stream, "isatty", lambda: False)():
        return False
    return _enable_windows_ansi()


def ensure_utf8(stream: TextIO) -> None:
    """Avoid UnicodeEncodeError on legacy code pages (e.g. cp1252 on Windows)."""
    reconfigure = getattr(stream, "reconfigure", None)
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    if reconfigure and encoding != "utf8":
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def width(s: str) -> int:
    """Display width: ANSI codes are zero-width, wide/emoji chars take two columns."""
    plain = ANSI_RE.sub("", s)
    return sum(
        0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        for ch in plain
    )


@dataclass(frozen=True)
class Style:
    rich: bool

    def _c(self, code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if self.rich and s else s

    def bold(self, s: str) -> str:
        return self._c("1", s)

    def dim(self, s: str) -> str:
        return self._c("2", s)

    def red(self, s: str) -> str:
        return self._c("31", s)

    def green(self, s: str) -> str:
        return self._c("32", s)

    def yellow(self, s: str) -> str:
        return self._c("33", s)

    def blue(self, s: str) -> str:
        return self._c("34", s)

    def magenta(self, s: str) -> str:
        return self._c("35", s)

    def cyan(self, s: str) -> str:
        return self._c("36", s)

    # --- symbols with ASCII fallbacks -----------------------------------
    @property
    def arrow(self) -> str:
        return " → " if self.rich else " -> "

    @property
    def rule_char(self) -> str:
        return "─" if self.rich else "-"

    def badge(self, text: str) -> str:
        return self._c("1;97;44", f"  {text}  ") if self.rich else f"== {text} =="

    def ok(self) -> str:
        return self.green("✓ ok") if self.rich else "ok"

    def fail(self, text: str) -> str:
        return self.red(f"✗ {text}") if self.rich else f"x {text}"

    def duration(self, seconds: float) -> str:
        # Neutral unless slow; red is reserved for errors.
        text = f"{seconds:.2f}s"
        return self.yellow(text) if seconds >= 5 else text


def status(st: Style, errors: list[dict]) -> str:
    if not errors:
        return st.ok()
    more = st.dim(f" +{len(errors) - 1}") if len(errors) > 1 else ""
    return st.fail(errors[0]["type"]) + more


def chain(st: Style, agents: list[str], n: int = 3) -> str:
    if not agents:
        return "-"
    if len(agents) <= n:
        return st.arrow.join(agents)
    return st.arrow.join(agents[:n]) + st.dim(f"{st.arrow}... (+{len(agents) - n})")


def title(out: TextIO, st: Style, text: str, meta: list[str]) -> None:
    print(st.badge(text), file=out)
    print(st.dim(" | ".join(m for m in meta if m)), file=out)
    print(file=out)


def table(
    out: TextIO,
    st: Style,
    headers: list[str],
    rows: list[list[str]],
    right: set[int] | None = None,
) -> None:
    right = right or set()
    widths = [max([width(h)] + [width(r[i]) for r in rows]) for i, h in enumerate(headers)]

    def fmt(cells: list[str], bold: bool = False) -> str:
        parts = []
        for i, cell in enumerate(cells):
            pad = " " * (widths[i] - width(cell))
            text = pad + cell if i in right else cell + pad
            parts.append(st.bold(text) if bold else text)
        return " " + "  ".join(parts).rstrip()

    print(fmt(headers, bold=True), file=out)
    print(st.dim(st.rule_char * (sum(widths) + 2 * (len(widths) - 1) + 1)), file=out)
    for r in rows:
        print(fmt(r), file=out)


def clip(text: str, max_lines: int, cols: int) -> tuple[list[str], int]:
    """Fit a message into at most ``max_lines`` lines of ``cols`` columns.

    Blank lines are dropped and long lines wrapped, so the cap counts what the
    terminal would actually draw. Returns (lines to show, lines left out).
    """
    lines = [
        wrapped
        for line in text.splitlines()
        if line.strip()
        for wrapped in textwrap.wrap(line, max(20, cols), drop_whitespace=True) or [line]
    ] or [""]
    if len(lines) <= max_lines + 1:  # a "+1 more line" note would save nothing
        return lines, 0
    return lines[:max_lines], len(lines) - max_lines


def hint(out: TextIO, st: Style, text: str) -> None:
    print("\n" + st.dim(("→ " if st.rich else "> ") + text), file=out)


def default_stream() -> TextIO:
    return sys.stdout
