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

"""Shared fixtures: a fake Cloud Trace transport and scaffolded project dirs."""

from __future__ import annotations

import json
import sys
import urllib.parse
from pathlib import Path

import pytest

EXT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EXT_ROOT / "scripts"))

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeTransport:
    """Answers GETs from canned responses; records every request.

    ``routes`` maps a URL path suffix to either a response dict or a list of
    response dicts (served in order, for pagination). A response that is an
    exception is raised instead (e.g. ``ApiError(..., status=404)``).
    """

    def __init__(self, routes: dict[str, object]):
        self.routes = {k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()}
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get_json(self, url: str) -> dict:
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        self.requests.append((parsed.path, query))
        for suffix, resp in self.routes.items():
            if parsed.path.endswith(suffix):
                if isinstance(resp, list):
                    resp = resp.pop(0) if resp else {}
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected request: {url}")


@pytest.fixture
def agent_runtime_project(tmp_path: Path) -> Path:
    """A project as `agents-cli deploy` leaves it for Agent Runtime."""
    (tmp_path / "agents-cli-manifest.yaml").write_text(
        'name: "kyc-demo"\nregion: "us-central1"\n'
        "create_params:\n  deployment_target: \"agent_runtime\"\n"
    )
    (tmp_path / "deployment_metadata.json").write_text(
        json.dumps(
            {
                "remote_agent_runtime_id": "projects/demo-project/locations/us-central1/reasoningEngines/1111111111111111111",
                "deployment_target": "agent_runtime",
            }
        )
    )
    return tmp_path


@pytest.fixture
def cloud_run_project(tmp_path: Path) -> Path:
    """A project as `agents-cli create -d cloud_run` + `agents-cli deploy` leaves it.

    Cloud Run deploys write no deployment_metadata.json; the service is named
    after the project, and `.env` carries the Google Cloud project.
    """
    (tmp_path / "agents-cli-manifest.yaml").write_text(
        'name: "traces-cr-demo"\nregion: \'us-central1\'\n'
        "create_params:\n"
        '  root_agent_name: "traces_cr_demo"\n'
        '  deployment_target: "cloud_run"\n'
        '  session_type: "agent_platform_sessions"\n'
    )
    (tmp_path / ".env").write_text(
        "GOOGLE_GENAI_USE_ENTERPRISE=true\nGOOGLE_CLOUD_PROJECT=demo-project\nGOOGLE_CLOUD_LOCATION=global\n"
    )
    return tmp_path
