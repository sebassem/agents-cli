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

"""Find the agent this project deployed, the way agents-cli finds a project.

The project root is the nearest ancestor holding ``agents-cli-manifest.yaml``
(or, for Agent Runtime, just the ``deployment_metadata.json`` deploy wrote).

- Agent Runtime: ``deployment_metadata.json`` in the root names the engine;
  ADK spans carry ``service.name=<engine id>``.
- Cloud Run: no metadata file is written. The service is named after the
  project, and Cloud Run spans carry ``faas.name=<service>`` (``service.name``
  is only set when the service was deployed through the scaffolded Terraform).
- GKE: deployed through Terraform, which sets ``OTEL_SERVICE_NAME`` to the
  project name.

Only spans of agent turns are wanted, so every filter adds
``span:invoke_agent``, which drops HTTP-only traces (health checks, session
listing, A2A queue housekeeping).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

MANIFEST = "agents-cli-manifest.yaml"
METADATA = "deployment_metadata.json"
ENGINE_RE = re.compile(r"projects/([^/]+)/locations/([^/]+)/reasoningEngines/(\d+)")
TURN_FILTER = "span:invoke_agent"


class TargetError(Exception):
    """The deployed agent could not be determined; the message says how to fix it."""


@dataclass(frozen=True)
class Target:
    kind: str  # "agent_runtime" | "cloud_run" | "gke"
    project: str
    region: str | None
    agent_id: str  # engine ID (Agent Runtime) or service name (Cloud Run / GKE)
    source: str  # where the target came from, for display
    session_type: str | None = None

    @property
    def trace_filters(self) -> list[str]:
        """Filters to try in order; the first that finds turns wins.

        Cloud Run spans always carry ``faas.name`` (the resource detector sets
        it), but ``service.name`` only when ``OTEL_SERVICE_NAME`` is set, as
        Terraform deploys do. GKE and Agent Runtime use ``service.name``.
        """
        keys = ["faas.name", "service.name"] if self.kind == "cloud_run" else ["service.name"]
        # Order matters: Cloud Trace returns nothing for
        # `+service.name:X span:invoke_agent` on Agent Runtime traces, but the
        # same terms with `span:` first match (verified live on both targets).
        return [f"{TURN_FILTER} +{key}:{self.agent_id}" for key in keys]


def find_project_root(cwd: Path) -> Path | None:
    """Nearest ancestor with the manifest, or with just deployment_metadata.json."""
    for d in [cwd, *cwd.parents]:
        if (d / MANIFEST).exists() or (d / METADATA).exists():
            return d
    return None


def _unquote(value: str) -> str:
    value = value.split(" #", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def read_manifest(path: Path) -> dict:
    """The few manifest keys we need, without a YAML dependency.

    Returns top-level scalars plus ``create_params`` scalars, e.g.
    ``{"name": ..., "region": ..., "create_params": {"deployment_target": ...}}``.
    """
    out: dict = {"create_params": {}}
    section = None
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        indent = len(raw) - len(raw.lstrip())
        key, _, value = raw.strip().partition(":")
        if indent == 0:
            section = key if not value.strip() else None
            if value.strip():
                out[key] = _unquote(value)
        elif section == "create_params" and indent == 2 and value.strip():
            out["create_params"][key] = _unquote(value)
    return out


def _dotenv_project(root: Path) -> str | None:
    env = root / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        key, _, value = line.strip().partition("=")
        if key == "GOOGLE_CLOUD_PROJECT" and value:
            return _unquote(value)
    return None


def _gcloud_project() -> str | None:
    try:
        out = subprocess.run(
            ["gcloud", "config", "get-value", "project"], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def resolve_target(cwd: Path, service: str | None = None) -> Target:
    """The deployed agent for ``cwd``; ``service`` overrides the Cloud Run / GKE service name."""
    target = _resolve(cwd)
    if service is None:
        return target
    if target.kind == "agent_runtime":
        raise TargetError("--service applies to Cloud Run and GKE; this project deploys to Agent Runtime.")
    return replace(target, agent_id=service, source="--service")


def _resolve(cwd: Path) -> Target:
    root = find_project_root(cwd)
    if root is None:
        raise TargetError(
            f"Not inside an agents-cli project (no {MANIFEST} or {METADATA} found)."
        )
    manifest_path = root / MANIFEST
    manifest = read_manifest(manifest_path) if manifest_path.exists() else {"create_params": {}}
    params = manifest["create_params"]
    deployment_target = params.get("deployment_target")

    meta_path = root / METADATA
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        m = ENGINE_RE.search(meta.get("remote_agent_runtime_id") or "")
        if m:
            return Target("agent_runtime", m.group(1), m.group(2), m.group(3), METADATA)

    if deployment_target in ("cloud_run", "gke"):
        name = manifest.get("name")
        if not name:
            raise TargetError(f"{MANIFEST} has no `name`; can't tell which service to read.")
        project = _dotenv_project(root) or _gcloud_project()
        if not project:
            raise TargetError("No Google Cloud project. Set GOOGLE_CLOUD_PROJECT in .env.")
        return Target(
            deployment_target,
            project,
            manifest.get("region"),
            name,
            MANIFEST,
            session_type=params.get("session_type"),
        )
    raise TargetError("No deployed agent for this project. Run `agents-cli deploy` first.")
