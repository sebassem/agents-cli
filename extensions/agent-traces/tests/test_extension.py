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

"""The manifest agents-cli reads: it parses, and what it declares exists.

Needs the agents-cli package (run from the repo with `uv run`); skipped when
the extension's tests run on their own.
"""

from __future__ import annotations

import pytest

from conftest import EXT_ROOT

yaml = pytest.importorskip("yaml")
_spec = pytest.importorskip("google.agents.cli.extension._spec")


def _parse():
    data = yaml.safe_load((EXT_ROOT / "agents-cli-extension.yaml").read_text())
    return _spec.parse_extension_spec(data, root=EXT_ROOT, default_name="agent-traces")


def test_adds_the_traces_command_and_its_script_exists():
    spec = _parse()

    assert spec.name == "agent-traces"
    [command] = spec.commands
    assert (command.name, command.kind) == ("traces", "add")
    script = next(part for part in command.run if part.endswith(".py"))
    assert (EXT_ROOT / script).is_file()


def test_ships_the_agent_traces_skill():
    spec = _parse()

    [skill] = spec.skills
    assert skill.name == "agent-traces"
    assert (EXT_ROOT / skill.path / "SKILL.md").is_file()
