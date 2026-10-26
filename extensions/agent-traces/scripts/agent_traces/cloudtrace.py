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

"""Cloud Trace v1 REST client (standard library only).

Gotchas handled here:
- A list page can be empty yet carry a ``nextPageToken`` while the API scans
  the time window, so keep paging until the token runs out or we have enough.
- Filters must be ``key:value``; a ``+`` prefix makes the match exact.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Protocol

TRACE_API = "https://cloudtrace.googleapis.com/v1/projects/{project}/traces"
MAX_PAGES = 100


class ApiError(Exception):
    """A Google API call failed; the message is safe to show the user."""


class Transport(Protocol):
    def get_json(self, url: str) -> dict: ...


class GoogleTransport:
    """Authenticated GETs using the gcloud Application Default Credentials."""

    def __init__(self, quota_project: str | None = None):
        self.quota_project = quota_project
        self._token: str | None = None

    def _access_token(self) -> str:
        if self._token:
            return self._token
        for cmd in (
            ["gcloud", "auth", "application-default", "print-access-token"],
            ["gcloud", "auth", "print-access-token"],
        ):
            try:
                out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if out.returncode == 0 and out.stdout.strip():
                self._token = out.stdout.strip()
                return self._token
        raise ApiError("No credentials. Run `gcloud auth application-default login`.")

    def get_json(self, url: str) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        if self.quota_project:
            headers["x-goog-user-project"] = self.quota_project
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            raise ApiError(f"Google API error {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise ApiError(f"Network error: {e.reason}") from e


def _rfc3339(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def list_traces(
    http: Transport, project: str, flt: str, start: datetime, end: datetime, want: int
) -> list[dict]:
    params = {
        "startTime": _rfc3339(start),
        "endTime": _rfc3339(end),
        "pageSize": "100",
        "view": "COMPLETE",
        "filter": flt,
    }
    out: list[dict] = []
    for _ in range(MAX_PAGES):
        url = TRACE_API.format(project=project) + "?" + urllib.parse.urlencode(params)
        data = http.get_json(url)
        out += data.get("traces", [])
        token = data.get("nextPageToken")
        if len(out) >= want or not token:
            break
        params["pageToken"] = token
    return out


def get_trace(http: Transport, project: str, trace_id: str) -> dict:
    url = TRACE_API.format(project=project) + "/" + urllib.parse.quote(trace_id)
    return http.get_json(url)
