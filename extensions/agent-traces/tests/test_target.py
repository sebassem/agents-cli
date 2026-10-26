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

"""Seam 2: which deployed agent a directory belongs to."""

from __future__ import annotations

import json

import pytest

from agent_traces.target import KINDS, Kind, TargetError, resolve_target


def test_a_folder_with_only_deployment_metadata_is_an_agent_runtime_target(tmp_path):
    # Some projects keep just the file `agents-cli deploy` wrote, without the manifest.
    (tmp_path / "deployment_metadata.json").write_text(
        json.dumps(
            {
                "remote_agent_runtime_id": "projects/demo-project/locations/us-central1/reasoningEngines/1111111111111111111",
                "deployment_target": "agent_runtime",
            }
        )
    )
    sub = tmp_path / "app"
    sub.mkdir()

    target = resolve_target(sub)

    assert (target.kind, target.project, target.region, target.agent_id) == (
        "agent_runtime",
        "demo-project",
        "us-central1",
        "1111111111111111111",
    )


def test_a_project_number_in_the_metadata_resolves_to_the_project_id(tmp_path):
    # Cloud Trace lists traces under the project ID, so prefer the configured one.
    (tmp_path / "deployment_metadata.json").write_text(
        json.dumps({"remote_agent_runtime_id": "projects/123456789012/locations/us-central1/reasoningEngines/42"})
    )
    (tmp_path / ".env").write_text("GOOGLE_CLOUD_PROJECT=demo-project\n")

    assert resolve_target(tmp_path).project == "demo-project"


def test_older_metadata_with_agent_runtime_id_still_works(tmp_path):
    (tmp_path / "deployment_metadata.json").write_text(
        json.dumps({"agent_runtime_id": "projects/demo-project/locations/europe-west1/reasoningEngines/42"})
    )

    target = resolve_target(tmp_path)

    assert (target.kind, target.region, target.agent_id) == ("agent_runtime", "europe-west1", "42")


def test_malformed_metadata_is_a_plain_error(tmp_path):
    (tmp_path / "deployment_metadata.json").write_text("{not json")

    with pytest.raises(TargetError, match="isn't valid JSON"):
        resolve_target(tmp_path)


def test_every_deployment_target_has_its_facts_in_one_table():
    assert set(KINDS) == set(Kind)


def test_project_flag_overrides_the_env_file(tmp_path):
    (tmp_path / "agents-cli-manifest.yaml").write_text('name: "svc"\ncreate_params:\n  deployment_target: "cloud_run"\n')
    (tmp_path / ".env").write_text("GOOGLE_CLOUD_PROJECT=from-env\n")

    assert resolve_target(tmp_path, project="from-flag").project == "from-flag"


def test_project_flag_resolves_a_project_number_without_env_or_gcloud(tmp_path):
    (tmp_path / "deployment_metadata.json").write_text(
        json.dumps({"remote_agent_runtime_id": "projects/123456789012/locations/us-central1/reasoningEngines/42"})
    )

    assert resolve_target(tmp_path, project="demo-project").project == "demo-project"
