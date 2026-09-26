"""MCP stdio server - the surface the orchestrator drives.

Speaks JSON-RPC 2.0 over stdin/stdout. If the CollaboratorMCP desktop app is
running it proxies to that engine over the loopback hub, so the operator sees
every delegation live; otherwise it starts its own embedded engine.
"""

import json
import os
import sys
import threading
import time

from . import __version__, api
from .config import settings as get_settings


PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "collaborator-mcp"
SERVER_VERSION = __version__


def _str(desc, required=False):
    return {"type": "string", "description": desc}


TOOLS = [
    {
        "name": "collaborator_status",
        "title": "Collaboration status",
        "description": "Queue depth, running tasks, spend so far, which "
                       "executor model is configured, and whether credentials "
                       "are present. Call this first if unsure of the state.",
        "method": "stats",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "create_mission",
        "title": "Create a mission",
        "description": "Open a mission: a named unit of work you will break "
                       "into tasks for the executor. Returns a mission_id.",
        "method": "mission.create",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": _str("Short mission name."),
                "brief": _str("Goal, constraints and definition of done."),
                "workspace": _str("Repository this mission works in. Its "
                                  "tasks are confined to that directory."),
            },
            "required": ["title"],
        },
    },
    {
        "name": "delegate_task",
        "title": "Delegate a task to the executor",
        "description": (
            "Hand one concrete task to the executor model. It works it "
            "with file tools inside the workspace and may deploy smaller "
            "agent models itself. Returns immediately with a task_id - poll "
            "with task_status or block with wait_for_task. If mission_id is "
            "omitted a mission is created for you."),
        "method": "task.delegate",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": _str("Mission to file this task under."),
                "goal_id": _str("Goal this task belongs to, if any."),
                "title": _str("Short task name."),
                "instructions": _str("What the executor must do, and what "
                                     "'done' looks like."),
                "context": _str("Material the executor needs: findings, "
                                "constraints, prior results."),
                "workspace": _str("Repository this work belongs to. Sets the "
                                  "mission's repo and grants access to it."),
                "priority": {"type": "integer",
                             "description": "1 = highest, 9 = lowest. "
                                            "Default 5."},
            },
            "required": ["title", "instructions"],
        },
    },
    {
        "name": "plan_mission",
        "title": "Plan a mission into tasks",
        "description": (
            "Hand a mission brief to the configured orchestrator model and "
            "have it break the mission into goals, and each goal into "
            "executor tasks, all queued ready to run. Use this when you want "
            "the planning done for you; otherwise add goals with add_goal "
            "and tasks with delegate_task yourself."),
        "method": "mission.plan",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": _str("Plan into an existing mission."),
                "title": _str("Mission title when creating a new one."),
                "brief": _str("Goal, constraints and definition of done."),
                "workspace": _str("Repository this mission works in."),
                "max_goals": {"type": "integer",
                              "description": "Upper bound on goals."},
                "max_tasks": {"type": "integer",
                              "description": "Upper bound on total tasks."},
            },
            "required": [],
        },
    },
    {
        "name": "add_goal",
        "title": "Add a goal to a mission",
        "description": (
            "Missions break into goals, and goals into tasks. A goal is a "
            "milestone someone could tick off, not an action. Add one "
            "yourself when the plan needs an outcome it does not yet cover."),
        "method": "goal.create",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": _str("Mission to add the goal to."),
                "title": _str("The outcome, stated as a milestone."),
                "description": _str("What 'done' means for this goal."),
            },
            "required": ["mission_id", "title"],
        },
    },
    {
        "name": "list_goals",
        "title": "List a mission's goals",
        "description": "Goals for one mission, each with its tasks and their "
                       "current status.",
        "method": "goal.list",
        "inputSchema": {
            "type": "object",
            "properties": {"mission_id": _str("Mission identifier.")},
            "required": ["mission_id"],
        },
    },
    {
        "name": "task_status",
        "title": "Check a task",
        "description": "Current status, result and cost of one task, plus the "
                       "agents the executor deployed for it.",
        "method": "task.get",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": _str("Task identifier.")},
            "required": ["task_id"],
        },
    },
    {
        "name": "wait_for_task",
        "title": "Wait for a task",
        "description": "Block until the task reaches a terminal state or the "
                       "timeout elapses, then return it.",
        "method": "task.wait",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": _str("Task identifier."),
                "timeout": {"type": "number",
                            "description": "Seconds to wait. Default 300."},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "list_tasks",
        "title": "List tasks",
        "description": "Tasks, optionally filtered by mission or status "
                       "(queued, running, done, partial, blocked, error, "
                       "cancelled).",
        "method": "task.list",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": _str("Restrict to one mission."),
                "status": _str("Restrict to one status."),
            },
            "required": [],
        },
    },
    {
        "name": "cancel_task",
        "title": "Cancel a task",
        "description": "Stop a queued or running task at its next safe point.",
        "method": "task.cancel",
        "inputSchema": {
            "type": "object",
            "properties": {"task_id": _str("Task identifier.")},
            "required": ["task_id"],
        },
    },
    {
        "name": "list_missions",
        "title": "List missions",
        "description": "All missions, newest first.",
        "method": "mission.list",
        "inputSchema": {
            "type": "object",
            "properties": {"status": _str("Filter: open, on_hold, done, "
                                          "partial or closed.")},
            "required": [],
        },
    },
    {
        "name": "get_mission",
        "title": "Get a mission",
        "description": "One mission with all of its tasks and their results.",
        "method": "mission.get",
        "inputSchema": {
            "type": "object",
            "properties": {"mission_id": _str("Mission identifier.")},
            "required": ["mission_id"],
        },
    },
    {
        "name": "close_mission",
        "title": "Close a mission",
        "description": "Mark a mission finished.",
        "method": "mission.close",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": _str("Mission identifier."),
                "status": _str("closed (default) or abandoned."),
            },
            "required": ["mission_id"],
        },
    },
    {
        "name": "resume_mission",
        "title": "Resume a mission on hold",
        "description": "Release a mission the local orchestrator paused "
                       "for an operator decision (status on_hold), so its "
                       "queued tasks run again.",
        "method": "mission.resume",
        "inputSchema": {
            "type": "object",
            "properties": {"mission_id": _str("Mission identifier.")},
            "required": ["mission_id"],
        },
    },
    {
        "name": "run_agent",
        "title": "Run a helper directly",
        "description": (
            "Give one member of the helper team a scoped sub-task yourself, "
            "without going through the executor - summarising, extraction, "
            "classification, a quick draft. Blocks and returns the answer. "
            "The helper has no tools and no memory: put everything it needs "
            "in instructions and context. list_models shows the team."),
        "method": "agent.run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "helper": _str("Team member's name. Defaults to the first "
                               "helper."),
                "role": _str("Short label for this job, e.g. 'extractor'."),
                "model": _str("A helper's model id, instead of a name."),
                "instructions": _str("What the agent must do."),
                "context": _str("Material the agent needs."),
                "mission_id": _str("Bill this call to a mission."),
            },
            "required": ["instructions"],
        },
    },
    {
        "name": "list_models",
        "title": "List available models",
        "description": (
            "Every model CollaboratorMCP can reach - Anthropic, OpenAI, "
            "Google Gemini, Moonshot (Kimi), Alibaba (Qwen) and local "
            "subscription CLIs - with which provider serves it, whether a "
            "credential is configured, and per-million-token prices. Prices "
            "marked approximate come from secondary sources."),
        "method": "models.list",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": _str("Restrict to one provider: anthropic, "
                                 "openai, google, moonshot, qwen or cli."),
            },
            "required": [],
        },
    },
    {
        "name": "refresh_models",
        "title": "Check providers for new models",
        "description": (
            "Query each configured provider's live model list and add any "
            "models CollaboratorMCP does not know about yet, so newly "
            "released models become selectable without a code change. "
            "Discovered models have no published price, so they start at "
            "zero and are flagged approximate."),
        "method": "models.refresh",
        "inputSchema": {
            "type": "object",
            "properties": {
                "providers": {
                    "type": "array",
                    "description": "Which providers to check. Defaults to "
                                   "every provider with a key configured.",
                    "items": {"type": "string"},
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_agents",
        "title": "List agent runs",
        "description": "Agent runs, optionally scoped to a task or "
                       "mission, with their outputs and costs.",
        "method": "agent.list",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": _str("Restrict to one task."),
                "mission_id": _str("Restrict to one mission."),
            },
            "required": [],
        },
    },
    {
        "name": "activity_log",
        "title": "Read the activity log",
        "description": "Recent events: delegations, tool calls, sub-agent "
                       "deployments, notes, errors and budget stops.",
        "method": "events.list",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mission_id": _str("Restrict to one mission."),
                "task_id": _str("Restrict to one task."),
                "since_id": {"type": "integer",
                             "description": "Only events after this id."},
                "limit": {"type": "integer", "description": "Default 200."},
            },
            "required": [],
        },
    },
    {
        "name": "post_note",
        "title": "Post a note",
        "description": "Write a note into the shared activity log so the "
                       "operator and the executor can see your reasoning.",
        "method": "note",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": _str("The note."),
                "mission_id": _str("Attach to a mission."),
                "task_id": _str("Attach to a task."),
            },
            "required": ["text"],
        },
    },
    {
        "name": "message_executor",
        "title": "Send a live message into a running task",
        "description": (
            "Interject while the executor is mid-task. The message is handed "
            "to it on its next step, so you can redirect, add a constraint, "
            "or answer a question without cancelling and re-delegating. "
            "Defaults to the most recently started running task."),
        "method": "task.steer",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": _str("What to tell the executor."),
                "task_id": _str("Which running task. Defaults to the latest."),
            },
            "required": ["text"],
        },
    },
    {
        "name": "get_workspace",
        "title": "Which directory the agents can reach",
        "description": (
            "The workspace root every agent is confined to, plus whether it "
            "is a git repository and which branch is checked out. All file "
            "paths the executor uses are relative to this. Call it before "
            "delegating work that touches files, so you know what is in "
            "scope."),
        "method": "workspace.get",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _str("Inspect a different directory without "
                             "switching to it."),
            },
            "required": [],
        },
    },
    {
        "name": "set_workspace",
        "title": "Point the agents at a repository",
        "description": (
            "Change the workspace root - the directory the executor and "
            "every sub-agent may read and write. Use this to grant access to "
            "a specific repository or project folder. Tasks already running "
            "keep the old root; new tasks use the new one."),
        "method": "workspace.set",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _str("Absolute directory path."),
                "create": {"type": "boolean",
                           "description": "Create it if missing. "
                                          "Default false."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "browse_workspace",
        "title": "Browse the workspace",
        "description": "List a directory inside the workspace so you can see "
                       "what the executor has to work with before delegating.",
        "method": "workspace.browse",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": _str("Workspace-relative directory. Defaults to the "
                             "root."),
                "show_hidden": {"type": "boolean",
                                "description": "Include dot-files."},
            },
            "required": [],
        },
    },
    {
        "name": "list_approvals",
        "title": "List pending approvals",
        "description": (
            "Actions the executor is currently blocked on. Only populated "
            "when auto-approve is off: gated tool calls (file writes, shell "
            "commands, sub-agent deployments) wait here until someone "
            "decides."),
        "method": "approval.list",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "resolve_approval",
        "title": "Approve or deny a blocked action",
        "description": (
            "Release a pending action. Omit approval_id to apply the same "
            "decision to every pending approval. A denial is reported back to "
            "the executor, which will work around it or stop."),
        "method": "approval.resolve",
        "inputSchema": {
            "type": "object",
            "properties": {
                "approval_id": _str("Which approval. Omit for all pending."),
                "decision": {"type": "boolean",
                             "description": "true to approve, false to deny."},
                "reason": _str("Shown to the executor and logged."),
            },
            "required": ["decision"],
        },
    },
    {
        "name": "set_auto_approve",
        "title": "Turn auto-approve on or off",
        "description": "With auto-approve on, the executor's tool calls run "
                       "immediately. With it off, gated tools wait for a "
                       "decision from you or the operator. You can turn it "
                       "off; only the operator can turn it back on.",
        "method": "approval.mode",
        "inputSchema": {
            "type": "object",
            "properties": {
                "auto_approve": {"type": "boolean"},
            },
            "required": ["auto_approve"],
        },
    },
    {
        "name": "pause_queue",
        "title": "Pause the executor queue",
        "description": "Stop starting new tasks. Running tasks finish.",
        "method": "queue.pause",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "resume_queue",
        "title": "Resume the executor queue",
        "description": "Start taking queued tasks again.",
        "method": "queue.resume",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

METHOD_FOR = {t["name"]: t["method"] for t in TOOLS}


def _public_tools():
    out = []
    for t in TOOLS:
        out.append({"name": t["name"], "title": t["title"],
                    "description": t["description"],
                    "inputSchema": t["inputSchema"]})
    return out


class Backend(object):
    """Either a hub client (shared engine) or an embedded engine."""

    def __init__(self, settings):
        self.settings = settings
        self.client = None
        self.engine = None
        self.mode = "none"
        self.client_name = ""
        self.client_version = ""
        self._fallback_lock = threading.Lock()
        self._probe = (0.0, False)

    def _hub_address(self):
        return (self.settings.get("hub_host") or "127.0.0.1",
                int(self.settings.get("hub_port") or 8787))

    def desktop_is_up(self):
        """Whether a desktop/headless engine owns the hub (cached briefly)."""
        checked, up = self._probe
        if time.time() - checked > 3.0:
            from . import hub
            up = hub.hub_listening(*self._hub_address())
            self._probe = (time.time(), up)
        return up

    def connect(self):
        if os.environ.get("COLLABORATORMCP_PROBE") == "1":
            self.mode = "probe"
            return self
        from . import hub
        host, port = self._hub_address()
        client = hub.try_connect(host, port,
                                 token_dir=os.path.dirname(self.settings.path))
        if client is not None:
            self.client = client
            self.mode = "hub"
            if self.client_name:
                self.identify(self.client_name, self.client_version)
            return self
        # Merely discovering tools must not start queued work. It also gives
        # a desktop opened after Codex a chance to become the shared owner.
        self.mode = "embedded" if self.engine is not None else "standby"
        return self

    def identify(self, name, version=""):
        """Tell a shared engine which orchestrator is driving it."""
        self.client_name = name or ""
        self.client_version = version or ""
        if self.client is None:
            return
        try:
            self.client.call("hello", {"name": name, "version": version})
        except Exception:
            pass

    def call(self, method, params):
        with self._fallback_lock:
            if self.client is None and (self.engine is None
                                        or self.desktop_is_up()):
                # Also retried while embedded, so a desktop opened later
                # takes over instead of running a second, invisible queue.
                self.connect()
            if self.client is None and self.engine is None:
                from .engine import Engine
                self.engine = Engine(self.settings)
                # Stop claiming new work whenever a desktop owns the hub.
                self.engine.defer_claims = self.desktop_is_up
                self.engine.start()
                self.mode = "embedded"
            client, engine = self.client, self.engine
        if client is not None and engine is not None:
            local = self._embedded_call(client, engine, method, params)
            if local is not None:
                return local
        if client is not None:
            try:
                return client.call(method, params)
            except ConnectionError as exc:
                with self._fallback_lock:
                    if self.client is client:
                        client.close()
                        self.client = None
                        self.mode = "standby"
                # The hub may have committed a mutation before losing its
                # reply. Replaying it here can create duplicate tasks.
                raise ConnectionError(
                    "The desktop connection was lost. This request's outcome "
                    "is unknown and it was not automatically repeated. "
                    "Check task status before submitting the work again; "
                    "the next call will reconnect.") from exc
        return api.dispatch(engine, method, params, source="mcp")

    def _embedded_call(self, client, engine, method, params):
        """Route calls about work the embedded engine is still running.

        After a desktop takes over, tasks this process had already started
        finish here; their approvals, cancellation and steering live only in
        this engine's memory. Returns None when the hub should handle it.
        """
        params = params or {}
        if method in ("task.cancel", "task.steer"):
            if params.get("task_id") in engine.active_tasks():
                return api.dispatch(engine, method, params, source="mcp")
            return None
        pending = engine.list_approvals()
        if not pending or method not in ("approval.list", "approval.resolve"):
            return None
        if method == "approval.list":
            return pending + (client.call(method, params) or [])
        approval_id = params.get("approval_id")
        if approval_id:
            if any(a["approval_id"] == approval_id for a in pending):
                return api.dispatch(engine, method, params, source="mcp")
            return None
        local = api.dispatch(engine, method, params, source="mcp")
        remote = client.call(method, params) or {}
        local["resolved"] += int(remote.get("resolved") or 0)
        return local


class MCPServer(object):
    def __init__(self, backend, stdin=None, stdout=None):
        self.backend = backend
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._write_lock = threading.Lock()
        self._inflight = []

    # -- wire ---------------------------------------------------------------
    def _send(self, payload):
        with self._write_lock:
            self.stdout.write(json.dumps(payload) + "\n")
            self.stdout.flush()

    def _result(self, req_id, result):
        self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _error(self, req_id, code, message):
        self._send({"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": code, "message": message}})

    # -- loop ---------------------------------------------------------------
    def serve(self):
        for line in self.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if isinstance(msg, list):
                for item in msg:
                    self._handle(item)
            else:
                self._handle(msg)
            self._inflight = [t for t in self._inflight if t.is_alive()]
        # stdin closed: answer what is still running before exiting.
        for worker in self._inflight:
            worker.join()

    def _handle(self, msg):
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            info = params.get("clientInfo") or {}
            self.backend.identify(info.get("name") or "MCP client",
                                  info.get("version") or "")
            self._result(req_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "CollaboratorMCP pairs you, the orchestrator, with an "
                    "executor model. Open a mission with create_mission, then "
                    "break the work into concrete tasks and hand each to "
                    "delegate_task - or call plan_mission to have the "
                    "orchestrator model draft the task list for you. Poll "
                    "with task_status or block with wait_for_task. For small "
                    "scoped work you can run a cheap agent model yourself "
                    "via run_agent instead of delegating a whole task. "
                    "The executor can deploy its own agents; you do "
                    "not need to micromanage that. If auto-approve is off, "
                    "the executor's file writes, shell commands and "
                    "sub-agent deployments block until released - watch for "
                    "them with list_approvals and release them with "
                    "resolve_approval, or the task will stall."),
            })
            return

        if method in ("notifications/initialized", "initialized", "ping"):
            if req_id is not None:
                self._result(req_id, {})
            return

        if method == "tools/list":
            self._result(req_id, {"tools": _public_tools()})
            return

        if method == "tools/call":
            name = params.get("name") or ""
            args = params.get("arguments") or {}
            api_method = METHOD_FOR.get(name)
            if api_method is None:
                self._error(req_id, -32602, "Unknown tool: %s" % name)
                return
            # Run off the read loop: wait_for_task can block for minutes and
            # must not hold up every other call behind it.
            worker = threading.Thread(target=self._call_tool,
                                      args=(req_id, api_method, args),
                                      name="mcp-call", daemon=True)
            self._inflight.append(worker)
            worker.start()
            return

        if method in ("resources/list", "prompts/list"):
            key = "resources" if method.startswith("resources") else "prompts"
            self._result(req_id, {key: []})
            return

        if req_id is not None:
            self._error(req_id, -32601, "Method not found: %s" % method)

    def _call_tool(self, req_id, api_method, args):
        try:
            result = self.backend.call(api_method, args)
            text = json.dumps(result, indent=2, default=str)
            self._result(req_id, {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            })
        except Exception as exc:
            self._result(req_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })


def main():
    # stdout is the protocol channel; nothing else may write to it.
    # MCP is UTF-8 JSON, but on Windows stdin defaults to the ANSI code page,
    # which garbles any non-ASCII text in task instructions. utf-8-sig also
    # drops a leading byte-order mark, which would otherwise make the first
    # message (initialize) unparseable.
    for stream, enc in ((sys.stdin, "utf-8-sig"), (sys.stdout, "utf-8")):
        try:
            stream.reconfigure(encoding=enc)
        except (AttributeError, ValueError):
            pass
    settings = get_settings()
    backend = Backend(settings).connect()
    sys.stderr.write("[collaborator-mcp] backend=%s\n" % backend.mode)
    sys.stderr.flush()
    try:
        MCPServer(backend).serve()
    finally:
        if backend.client:
            backend.client.close()
        if backend.engine:
            backend.engine.shutdown()
    return 0
