# CollaboratorMCP 1.3.0 - quick start

Open **Start CollaboratorMCP.bat**. If an older window is running, let its work finish, then close and reopen it to load this update.

1. Choose your working folder at the top. Each mission keeps the folder where it started.
2. On **Start**, use **Orchestrator** (the first tab) for a larger goal, or **Executor** for a concrete request.
3. Read results under **Tasks & results**. Follow progress or send a follow-up in **Activity**.

## Connecting Codex

After connecting Codex, restart it to load the Collaborator tools. Keep CollaboratorMCP open before using those tools so both apps share the same engine and activity view.

There are two independent ways to use an orchestrator:

- **In Codex:** Connect > Connect Codex adds this server to Codex. Ask Codex to call `collaborator_status`, then delegate work through Collaborator.
- **Inside this app:** Connect > Use Codex planner enables local planning with your existing Codex subscription. Open Start > Orchestrator.

**Check connection** tests the MCP handshake and Codex sign-in. It does not run a model task or consume model quota. Manual TOML and JSON configuration is available under the expandable manual configuration option.

Codex registration uses the [official CLI and config format](https://learn.chatgpt.com/docs/extend/mcp?surface=cli). It preserves a timestamped backup of `.codex/config.toml` and leaves other server entries in place.

## Settings

**Settings > Your team** (or **Change team** on Start) holds the whole team, and saves immediately:

- **Orchestrator** - any model, or *External MCP client* to let the Codex app orchestrate over MCP.
- **Task executor** and its reasoning effort.
- **Helper team** - up to 8 named helpers, each on its own model (for example *Reviewer* on Claude Sonnet, *Researcher* on GPT-4.1 mini). The executor hands them sub-tasks by name, several in parallel. The first helper is the default.
- **Supervision** - when the orchestrator is a model here, it decides after every task: accept or send back, add or drop queued tasks, finish the mission, or pause it and tell you why. A paused mission shows *on_hold*; use **Resume mission** on Tasks & results. It can also check in on a running task every few steps and redirect or stop it. Approvals stay with you.

Other settings use **Save settings**. Long settings pages scroll. Subscription models require a signed-in Claude Code or Codex CLI; API models require their provider's API key.

## Repair notes

- Native Windows CLI launching preserves multiline instructions and quoted paths.
- Codex planning uses an enforced output schema. Local Codex calls disable this app's own MCP entry to avoid recursive connections.
- API providers now send tool schemas, parse tool calls, and return tool results to the model.
- Failed/empty CLI replies and incomplete executor protocols no longer report success. Planning failures leave a visible error state.
- Start, results, activity, connections, and settings have separate purposes; model controls no longer crowd the sidebar.
- Tasks in the same mission run in their planned order. Independent missions can still run concurrently.
- Existing missions keep their original workspace when the folder selector changes.
- Only one desktop/headless instance can own the hub port, including on Windows. Duplicate windows do not start competing workers.
- Loading MCP tools alone does not start queued work. A desktop opened after tool discovery is picked up on the first tool call.
- A dropped connection reports an unknown outcome instead of automatically repeating a possibly committed request. Check task status before resubmitting.
- Planning cannot be submitted twice while it is in progress. Background results return through the UI event queue.
- The local hub requires a secret (`hub.token`, beside `settings.json`) on every connection, so web pages cannot drive the engine. Programs running under your Windows account can still read the token.
- Costs are priced by the requested model id, so dated snapshot ids returned by the APIs no longer record as $0 and bypass the budget.
- Shell commands that time out or belong to a cancelled task are stopped together with every process they started.
- A drive root (for example `C:\`) works as a workspace.
- With a local planner, a mission's next task waits for the review of the previous one, so revisions run first. A failed review no longer keeps a mission open.
- The executor effort setting now reaches the Claude Code CLI in subscription mode.
- An engine started by Codex while the desktop was closed stops taking new work once the desktop opens. Tasks it already started finish there, and their approvals stay reachable through Codex.
- **Save settings** writes only the fields you edited, so it no longer undoes changes made meanwhile by Codex (such as turning auto-approve off) or elsewhere in the window. Turning auto-approve on there also releases actions already waiting.
- Tasks & results no longer jumps back to the top while work runs, and keeps collapsed missions collapsed. Retry keeps the task's goal and refuses unfinished tasks.
- Original source and executables are retained in `repair-backup`.

Validation: 38 regression tests; live Codex planning and fixed-response checks through Claude Haiku, Claude Sonnet, and Codex; isolated UI startup, compact/large layouts, settings persistence, and helper panels. API provider execution was tested with simulated responses because no API keys are configured. Native screenshot inspection was unavailable; UI checks used Tk widget layout and behavior.

Developer checks:

```
python -m unittest discover -s tests -v
python tests/ui_smoke.py
python main.py --check-connection
```

`python tests/live_smoke.py` uses your subscriptions for small connection tests. `python build.py` rebuilds both executables.
