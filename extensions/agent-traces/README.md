# agent-traces

An agents-cli extension that adds `agents-cli traces`: the turns, sessions and span waterfalls of the agent the current project deployed (Agent Runtime, Cloud Run or GKE), from Cloud Trace and the agent's session store. Standard library only.

```bash
cd my-agent                                   # a project deployed with agents-cli
agents-cli extension add agent-traces --yes   # or from a fork: <org>/agents-cli#extensions/agent-traces --ref <branch>
agents-cli traces                             # recent turns
agents-cli traces sessions                    # recent sessions
agents-cli traces session <id>                # messages + waterfall per turn
agents-cli traces -i                          # interactive browser
agents-cli traces --format json               # for scripts and coding agents
```

Full reference: [docs/src/guide/extensions/traces.md](../../docs/src/guide/extensions/traces.md).

## Layout

| Path | What |
|---|---|
| `agents-cli-extension.yaml` | adds the `traces` command and the `agent-traces` skill |
| `scripts/traces.py` | entry point (`python3 scripts/traces.py <args>`) |
| `scripts/agent_traces/target.py` | finds the deployed agent from the project files |
| `scripts/agent_traces/cloudtrace.py` | Cloud Trace v1 client (ADC token via `gcloud`) |
| `scripts/agent_traces/sessions.py` | Sessions API client and transcript formatting |
| `scripts/agent_traces/model.py` | traces → turns and sessions |
| `scripts/agent_traces/waterfall.py`, `render.py` | waterfall, tables, colors / plain fallback |
| `scripts/agent_traces/view.py` | the lines every command shows (printed, or browsed) |
| `scripts/agent_traces/browse.py`, `tui.py` | interactive browser: navigation model, curses front end |
| `skills/agent-traces/SKILL.md` | coding-agent skill, installed into `.agents/skills/` |

## Development

```bash
uv run --with pytest pytest extensions/agent-traces/tests -q
```

Tests run offline against trimmed real API responses in `tests/fixtures/` (project renamed to `demo-project`, hosts and message content removed except the demo conversation). To try local changes in a deployed project:

```bash
agents-cli extension add local@/path/to/agents-cli/extensions/agent-traces --yes
agents-cli extension update agent-traces --yes   # after each change
```
