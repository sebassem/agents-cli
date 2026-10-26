# Traces (`agent-traces`)

`agent-traces` adds `agents-cli traces`: the turns, sessions and span waterfalls of the agent **this project deployed**, read from Cloud Trace and the agent's session store. It works on Agent Runtime, Cloud Run and GKE, needs only Python's standard library, and scopes itself to your agent from the project files — no IDs to look up.

It answers "what did my deployed agent just do?": which turns failed, what the user asked, which tools ran with which arguments, where the time and tokens went.

!!! note "Not a fleet view"
    `traces` looks at one agent, the one in the current project. For health, latency and evaluations across many deployed agents, use AgFleet (`agents-cli fleet`).

## Install

```bash
cd my-agent                                  # a project you've deployed with agents-cli
agents-cli extension add agent-traces --yes  # project scope; add --global for every project
agents-cli traces
```

The extension also installs a coding-agent skill (`.agents/skills/agent-traces`) that tells your coding agent to call `traces --format json`.

## Commands

| Command | Shows |
|---|---|
| `agents-cli traces` | Recent turns, newest first: agents, tool calls, tokens, duration, status. One trace is one turn. |
| `agents-cli traces sessions` | Recent sessions with activity, one row per conversation. |
| `agents-cli traces session <id>` | One conversation: each turn's messages (user, tool call → result, agent reply) above that turn's waterfall. |
| `agents-cli traces show <trace-id>` | One turn's span waterfall. |
| `agents-cli traces last` | The waterfall of the most recent turn. |
| `agents-cli traces browse` | The interactive browser (same as `-i`). |

| Flag | Effect |
|---|---|
| `--since 30m\|24h\|7d` | Time window (default `7d`). |
| `--limit N` | Rows in `traces` / `traces sessions` (default 20). |
| `--errors` | Only turns or sessions with an error. |
| `--format json` | Machine-readable output, for scripts and coding agents. Never clipped. |
| `--full` | `session`: show whole messages (by default each is cut to 6 lines). |
| `--all-spans` | Waterfalls: include HTTP and A2A transport spans, hidden by default. |
| `--service <name>` | Cloud Run / GKE: the service name, if you deployed with `--service-name`. |
| `-i`, `--interactive` | Browse interactively (see below). |

### The waterfall

```text
 [turn]  invocation                                  ##############################   3.42s
 `- [agent] invoke_agent traces_cr_demo              .#############################   3.28s
    |- [llm]   call_llm                              ..#################...........   1.96s
    |  `- [model] generate_content gemini-3.8-flash  ..###############.............   1.73s  188/17 tok
    |- [tool]  execute_tool get_weather              ..................#...........   9.2ms
    `- [llm]   call_llm                              ...................###########   1.20s
       `- [model] generate_content gemini-3.8-flash  ...................##########.   1.09s  286/15 tok
```

In a terminal, span types get emoji and colored bars, and slow spans (5 s or more) are highlighted; piped output, `NO_COLOR` and `--format json` stay plain ASCII.

## Interactive browser

`agents-cli traces -i` (or `traces session <id> -i`, `traces browse`) opens the same views in a full-screen browser. The highlighted line follows the cursor; Enter opens what it points at:

| On… | Enter opens |
|---|---|
| a turn (in Turns) | its waterfall |
| a session (in Sessions) | the session: messages and waterfalls |
| a span | span details: timing, error, and every label — plus, for a tool span, the call's **arguments and result** |
| a message line | the whole message, wrapped |

| Key | Action |
|---|---|
| ↑ ↓ / `j` `k` | Move |
| Enter / → | Open |
| ← / Esc / Backspace | Back |
| `e` | Next error (wraps around) |
| PgUp / PgDn, Home / End | Page, jump |
| `t` / `s` | Turns / Sessions |
| `q` | Quit |

The browser needs a terminal and Python's `curses` module. Windows Python doesn't include `curses`: there, use the static output, or run under WSL.

## How it finds your agent

`traces` walks up from the current directory to the project root (`agents-cli-manifest.yaml`, or just `deployment_metadata.json`) and reads where the agent runs:

| Target | Identified by | Spans matched on |
|---|---|---|
| Agent Runtime | `deployment_metadata.json` (`remote_agent_runtime_id`) | `service.name` = engine ID |
| Cloud Run | manifest `name`, project from `.env` | `faas.name` = service; falls back to `service.name` (set by Terraform deploys) |
| GKE | manifest `name`, project from `.env` | `service.name` = project name (`OTEL_SERVICE_NAME`) |

Only agent turns are listed (`span:invoke_agent`): health checks, session listing and A2A housekeeping are left out.

## Session transcripts

`traces session` reads messages from the Agent Runtime Sessions API:

| Session store | Transcript |
|---|---|
| Agent Runtime (built in) | from the deployed engine |
| Cloud Run / GKE with `session_type: agent_platform_sessions` | from the sessions-only Agent Runtime named after the project |
| `in_memory`, `cloud_sql` | none — a note says so, and turns and waterfalls still show |

Messages are matched to turns by ADK invocation ID, and a tool call to its `execute_tool` span by `gen_ai.tool.call.id`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No turns in this window` | Traces take about a minute to arrive. Widen `--since`; on Cloud Run / GKE pass `--service <name>` if the service isn't named after the project. |
| `No credentials` | `gcloud auth application-default login`. |
| `No deployed agent for this project` | Run `agents-cli deploy` first, or `cd` into the deployed project. |
| `Session … was not found` | The session expired or was deleted, or the agent keeps sessions elsewhere. |
| `-i` says it needs a terminal | You're piping or scripting: drop `-i`, or use `--format json`. |
