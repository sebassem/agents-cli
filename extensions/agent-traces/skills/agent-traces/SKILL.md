---
name: agent-traces
description: Inspect what a deployed agent actually did, from Cloud Trace and its stored sessions, with `agents-cli traces`. Use when debugging a deployed agent's failure, wrong answer, tool call, latency or token use, or when reading a session's conversation.
---

`agents-cli traces` reads the traces and sessions of the agent **this project deployed** (Agent Runtime, Cloud Run or GKE), found from the project files. Run it from inside the project. Add `--format json` to every command: the JSON is complete (no clipping, no colors) and stable to parse.

## Workflow

1. **Find the turns.** `agents-cli traces --format json --since 24h` (add `--errors` for failing turns only). One trace is one turn: one user message the agent handled. Done when you hold the `trace_id` (and `session_id`) of the turn in question.
2. **Read the conversation.** `agents-cli traces session <session_id> --format json`. Messages are grouped to turns by `invocation_id`. Done when you can say what the user asked and what the agent and its tools answered.
3. **Read the spans.** `agents-cli traces show <trace_id> --format json` returns the raw Cloud Trace trace. Look at spans named `invoke_agent …`, `generate_content …` and `execute_tool …`; failures carry `error.type` in `labels`. Done when you've pinned the failing or slow span and its error.

## Commands

| Command | Returns (`--format json`) |
|---|---|
| `traces` (alias `list`) | turns, newest first |
| `traces sessions` | sessions with activity, newest first |
| `traces session <id>` | `{session_id, turns, messages, transcript_note}` |
| `traces show <trace-id>` | the raw trace: `{traceId, spans: [{name, spanId, parentSpanId, startTime, endTime, labels}]}` |
| `traces last` | the raw trace of the newest turn |

Shared flags: `--since 30m|24h|7d` (default `7d`; `30d` for `session`), `--limit N` (default 20), `--errors`, `--service <name>` (Cloud Run / GKE service not named after the project), `--project <id>` (read another Google Cloud project).

## Fields

- **Turn** (list item, and each of a session's `turns`): `trace_id`, `start`, `duration_s`, `session_id`, `agents` (in order of first appearance), `tools` (one entry per call), `models`, `input_tokens`, `output_tokens`, `errors` (`[{span, type}]`). Session turns add `invocation_ids`.
- **Session** (`traces sessions` item): `session_id`, `first`, `last`, `turns`, `agents`, `tools`, `input_tokens`, `output_tokens`, `duration_s`, `errors`, `trace_ids`.
- **Message**: `invocation_id`, `timestamp`, `author` (`user` or the agent's name), `kind`, then by kind: `text` → `text`; `tool_call` → `id`, `tool`, `args`; `tool_result` → `id`, `tool`, `result`. A call and its result share `id`, which equals the `gen_ai.tool.call.id` label on the `execute_tool` span.

## When results are empty or partial

- **No turns:** traces take about a minute to arrive; widen `--since` (e.g. `30d`). On Cloud Run / GKE, a service deployed under another name needs `--service <name>`.
- **Session not found:** it's older than the window; pass a wider `--since` to `traces session`.
- **`transcript_note` is set, `messages` is empty:** the project stores sessions in memory or Cloud SQL, or the session expired. Turns and spans still work; tool arguments are then unavailable (span labels like `gcp.vertex.agent.tool_call_args` hold `{}` unless the agent captures content).
- **Exit code 1** with `x …` on stderr: not inside an agents-cli project, not deployed yet, nothing found for `last` / `session`, an API error (e.g. 403: missing IAM role), or credentials missing (`gcloud auth application-default login`). **Exit code 2**: invalid arguments. An empty list (`[]`) exits 0.
