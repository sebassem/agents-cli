# agent-traces

An agents-cli extension that adds `agents-cli traces`: the turns, sessions and span waterfalls of the agent the current project deployed (Agent Runtime, Cloud Run or GKE), from Cloud Trace and the agent's session store. Standard library only.

```bash
cd my-agent                         # a project deployed with agents-cli
agents-cli extension add agent-traces
agents-cli traces                   # recent turns
agents-cli traces sessions          # recent sessions
agents-cli traces session <id>      # messages + waterfall per turn
agents-cli traces -i                # interactive browser
agents-cli traces --format json     # for scripts and coding agents
```

Requires the `gcloud` CLI with Application Default Credentials (`gcloud auth application-default login`), plus read access to Cloud Trace in the agent's project. It runs under `uv`, which agents-cli already needs. See [Requirements](https://google.github.io/agents-cli/guide/extensions/traces/#requirements).

Full reference: the [Traces guide](https://google.github.io/agents-cli/guide/extensions/traces/) ([source](../../docs/src/guide/extensions/traces.md)).

## Layout

| Path | What |
|---|---|
| `agents-cli-extension.yaml` | adds the `traces` command and the `agent-traces` skill |
| `scripts/traces.py` | entry point (`uv run --no-project python scripts/traces.py <args>`) |
| `scripts/agent_traces/target.py` | finds the deployed agent from the project files |
| `scripts/agent_traces/cloudtrace.py` | Cloud Trace v1 client (ADC token via `gcloud`) |
| `scripts/agent_traces/sessions.py` | Sessions API client and transcript formatting |
| `scripts/agent_traces/model.py` | traces → turns and sessions |
| `scripts/agent_traces/waterfall.py`, `render.py` | waterfall, tables, colors / plain fallback |
| `scripts/agent_traces/view.py` | the lines every command shows (printed, or browsed) |
| `scripts/agent_traces/browse.py`, `tui.py` | interactive browser: navigation model, curses front end |
| `skills/agent-traces/SKILL.md` | coding-agent skill, installed into `.agents/skills/` |
| `tests/` | offline tests (see Development) |

## Development

```bash
uv run --with pytest pytest extensions/agent-traces/tests -q
```

Tests run offline against trimmed real API responses in `tests/fixtures/`: project renamed to `demo-project`, resource IDs replaced, hosts and message content removed except the demo conversation. `test_extension.py` checks the manifest with agents-cli's own parser, so it needs the repo environment (it's skipped elsewhere). To try local changes in a deployed project:

```bash
agents-cli extension add local@/path/to/agents-cli/extensions/agent-traces
agents-cli extension update agent-traces   # after each change
```
