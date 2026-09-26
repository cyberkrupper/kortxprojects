"""The collaboration engine.

The orchestrator (a local model, or any MCP client): it creates missions and
delegates tasks. The executor (any model you choose) works each task with tools, and may
deploy smaller Anthropic/OpenAI models as scoped sub-agents. Everything is
recorded in the store and broadcast as events to the UI and to MCP subscribers.
"""

import json
import os
import queue
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

import contextlib

from . import catalog, config, providers, tools
from .config import settings as get_settings
from .store import Store


EXECUTOR_SYSTEM = """\
You are {executor}, the executor in a two-role collaboration.

{orchestrator} is the orchestrator. {orchestrator} decides *what* needs doing \
and delegates discrete tasks to you. You decide *how*, and you do the work.

How you operate:
- You have tools for reading, writing and listing files inside a workspace \
directory. All paths are workspace-relative.
- You can deploy smaller agent models as sub-agents with `spawn_agent` (one) \
or `spawn_agents` (several in parallel). Use them for scoped, self-contained \
work: summarising long material, extracting structured data, classifying, \
drafting boilerplate, or double-checking a result. They are cheap and fast, \
but they have no tools, no workspace access and no memory of this \
conversation - hand them everything they need in `instructions` and `context`, \
and never delegate the judgement calls that are yours to make.
- Fan out to sub-agents when work splits into independent pieces (several \
files to summarise, several drafts, several checks) - use `spawn_agents` to \
run them in parallel. Keep the judgement calls and the edits yourself.
{delegation}
- Call `note` only for real milestones, not routine narration.
- When the task is complete, call `finish` with the deliverable or a precise \
summary of what you produced and where it lives. Report honestly: if something \
is blocked or only partly done, say `blocked` or `partial` and state plainly \
what is missing and why.

Workspace root: {workspace}
Your helper team: {agent_models}
{extra}"""

AGENT_SYSTEM = """\
You are an agent named "{role}", deployed by {executor} for one \
scoped sub-task. Answer only that sub-task. Be direct and complete; no \
preamble, no offers of further help. If the provided context is insufficient, \
say exactly what is missing."""


ORCHESTRATOR_SYSTEM = """\
You are {orchestrator}, the orchestrator. You do not do the work yourself - \
{executor} does. Your job is to turn a mission into a plan on two levels:

1. GOALS - the handful of outcomes the mission breaks into. A goal is a \
milestone, not an action: something a reader could tick off.
2. TASKS - under each goal, the concrete pieces of work {executor} will pick \
up and run one at a time.

Rules:
- At most {max_goals} goals, and at most {max_tasks} tasks in total.
- Order matters at both levels: nothing may depend on something later.
- Each task is a single deliverable with an unambiguous definition of done.
- Put everything the executor needs into `instructions`; it cannot ask you \
questions mid-task.
- Prefer few substantial tasks over many trivial ones.
- The executor has file tools scoped to a workspace directory and a helper \
team it hands sub-tasks to: {helpers}. You may say in a task's instructions \
which helper suits a piece of it, but leave the calls to the executor.

Return only the plan in the requested JSON shape."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "instructions": {"type": "string"},
                                "context": {"type": "string"},
                            },
                            "required": ["title", "instructions", "context"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "description", "tasks"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["goals"],
    "additionalProperties": False,
}


REVIEW_SYSTEM = """\
You are {orchestrator}, the orchestrator. {executor} has just finished a \
task you are responsible for. Judge the result against what the task asked \
for and what the mission needs - not against how you would have done it.

- "accept" when the task's definition of done is met, even if imperfectly.
- "revise" only for a concrete gap: a missed requirement, a wrong result, \
work claimed but not shown. Then write revision instructions {executor} can \
act on without asking you anything: what is wrong, what done looks like.
- A task reported as blocked or partial usually needs "revise" with a way \
around the blocker - unless the blocker needs the human operator, in which \
case "accept" and say so in the assessment.
- Revisions left for this task: {revisions_left}. At 0 you must "accept" \
and put any remaining concern in the assessment.

Return only the JSON verdict."""

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
        "assessment": {"type": "string"},
        "revision_instructions": {"type": "string"},
    },
    "required": ["verdict", "assessment", "revision_instructions"],
    "additionalProperties": False,
}


SUPERVISE_SYSTEM = """\
You are {orchestrator}, the orchestrator and supervisor of this mission. \
{executor} executes the tasks; its helper team is {helpers}. {executor} has \
just finished a task. Judge it, then decide what happens next.

The finished task:
- "accept" when its definition of done is met, even if imperfectly.
- "revise" only for a concrete gap: a missed requirement, a wrong result, \
work claimed but not shown. Then write revision instructions {executor} can \
act on without asking you anything. Revisions left for this task: \
{revisions_left}; at 0 you must "accept".

The rest of the mission:
- new_tasks: work the mission now needs that no queued task covers, at most \
{max_new}. Each is self-contained: {executor} cannot ask you questions. \
Leave empty while the plan still holds.
- cancel_tasks: ids of QUEUED tasks that are no longer needed or that your \
new tasks replace. Leave empty otherwise.
- mission: "continue" normally. "complete" when the mission's goal is met \
and the queued work is unnecessary (it is cancelled). "hold" when progress \
needs a decision only the operator can make: queued work pauses until the \
operator resumes the mission, so explain in note_to_operator.
- note_to_operator: one or two sentences when the operator should know \
something; otherwise "".

Be decisive but steady: change the plan only for a reason you can state.
Return only the JSON decision."""

SUPERVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["accept", "revise"]},
        "assessment": {"type": "string"},
        "revision_instructions": {"type": "string"},
        "new_tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "instructions": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["title", "instructions", "context"],
                "additionalProperties": False,
            },
        },
        "cancel_tasks": {"type": "array", "items": {"type": "string"}},
        "mission": {"type": "string",
                    "enum": ["continue", "complete", "hold"]},
        "note_to_operator": {"type": "string"},
    },
    "required": ["verdict", "assessment", "revision_instructions",
                 "new_tasks", "cancel_tasks", "mission", "note_to_operator"],
    "additionalProperties": False,
}

# New tasks one review may add, and the ceiling on a mission's size as a
# multiple of orchestrator_max_tasks, so supervision cannot run away.
MAX_NEW_TASKS_PER_REVIEW = 3
MISSION_TASK_CEILING = 3

CHECKIN_SYSTEM = """\
You are {orchestrator}, supervising {executor} while it works a task. Below \
are the task and what {executor} has done so far. Decide:
- "continue" when it is on track. This is the usual answer.
- "redirect" when it is drifting, looping, or missing a requirement: write \
a short, concrete message it reads on its next step.
- "stop" only when carrying on is pointless or harmful (wrong target, \
impossible as specified, repeated failures). Give the reason.
Return only the JSON."""

CHECKIN_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string",
                   "enum": ["continue", "redirect", "stop"]},
        "message": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["action", "message", "reason"],
    "additionalProperties": False,
}


TEXT_PROTOCOL_PROMPT = """\
[CollaboratorMCP protocol - read this before anything else]

You are {executor}, the executor. {orchestrator} delegates tasks; you carry \
them out.

Your own tools are DISABLED in this session. Do not try to read or write \
files, and do not run shell commands - every such attempt will fail. That is \
expected and is not a problem: CollaboratorMCP performs all actions for you.

You act by replying with EXACTLY ONE JSON object and nothing else. No prose \
before or after it, no markdown fence, no explanation:

{{"say": "<optional one-line note for the operator>",
  "tool": "<tool name>",
  "args": {{...}}}}

CollaboratorMCP runs that tool inside the workspace and replies with its \
result. Then you send the next JSON object. Repeat until the task is done.

Tools available to you through this protocol:
{tools}

Rules:
- One JSON object per reply. Nothing outside it.
- Paths are relative to the workspace root and must stay inside it.
- Sub-agents have no tools and no memory of this conversation; give them \
everything they need in `instructions` and `context`. Fan out with \
`spawn_agents` when work splits into independent pieces.
{delegation}
- When the task is complete, use the `finish` tool and put the deliverable or \
a precise summary in `summary`. Report honestly: use status "blocked" or \
"partial" if you could not finish, and say what is missing.

Workspace root: {workspace}
{extra}

[Task]
{brief}"""

AGENT_REVIEW_RULE = """\
- Required before `finish` on any task where you created or changed files: \
deploy one reviewer with `spawn_agent` (role "reviewer"). Give it the task \
goal and the full content of what you wrote or changed, and ask it for \
concrete defects only - bugs, missed requirements, broken references. Fix \
the real defects it finds, ignore style preferences, and say in your summary \
what the review caught."""


def delegation_rules(settings):
    """Sub-agent obligations for the executor, per the current settings."""
    return AGENT_REVIEW_RULE if settings.get("executor_agent_review") else ""


TEXT_PROTOCOL_REMINDER = (
    "\n\nReply now with exactly one JSON object of the form "
    '{"say": "...", "tool": "...", "args": {...}} and nothing else.')


class Budget(Exception):
    pass


class Denied(Exception):
    """The operator declined a gated action."""


class ExecContext(object):
    """Handle passed to tool handlers."""

    def __init__(self, engine, task, mission):
        self.engine = engine
        self.task = task
        self.mission = mission
        self.settings = engine.settings
        # A mission may pin its own repository; fall back to the global one.
        self.workspace = engine.workspace_for(mission)
        self.executor_name = engine.settings.get("executor_name")
        self.default_agent_model = engine.settings.get("default_agent_model")
        # What happened during the task, for the reviewer-agent gate.
        self.wrote_files = False
        self.agents_deployed = 0
        self.review_nudged = False

    def finish_blocker(self):
        """Why ``finish`` must wait, or "" to allow it.

        Refuses once: a task that wrote files must have had a reviewer agent
        look at it first. The second attempt goes through regardless, so a
        model that cannot comply never gets stuck.
        """
        if (self.settings.get("executor_agent_review") and self.wrote_files
                and not self.agents_deployed and not self.review_nudged):
            self.review_nudged = True
            return ("Not finished yet: you changed files but no reviewer "
                    "agent has checked them. Deploy one with spawn_agent "
                    "(role \"reviewer\"), passing the task goal and the full "
                    "content you changed; fix any real defects it reports; "
                    "then call finish again.")
        return ""

    def should_stop(self):
        """True once the task is cancelled or the engine is shutting down."""
        return (self.engine._is_cancelled(self.task["id"])
                or self.engine._stop.is_set())

    def emit(self, kind, text, actor=None):
        self.engine.emit(kind, text,
                         actor=actor or self.executor_name,
                         mission_id=self.mission["id"] if self.mission else "",
                         task_id=self.task["id"])

    def run_agent(self, role, model, instructions, context=""):
        return self.engine.run_sub_agent(
            role=role, model=model, instructions=instructions, context=context,
            task_id=self.task["id"],
            mission_id=self.mission["id"] if self.mission else "")

    def run_agents_parallel(self, specs):
        return self.engine.run_sub_agents_parallel(
            specs, task_id=self.task["id"],
            mission_id=self.mission["id"] if self.mission else "")

    def approve(self, tool_name, args):
        """Gate a tool call. Returns (allowed, reason)."""
        return self.engine.request_approval(
            tool_name, args,
            task_id=self.task["id"],
            mission_id=self.mission["id"] if self.mission else "")


class Engine(object):
    def __init__(self, settings=None, store=None):
        self.settings = settings or get_settings()
        self.store = store or Store()
        catalog.load_custom(self.settings)
        self._subscribers = []
        self._sub_lock = threading.Lock()
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._cancelled = set()
        self._cancel_lock = threading.Lock()
        self._active = {}          # task_id -> dict
        self._active_lock = threading.Lock()
        self._approvals = {}       # approval_id -> record
        self._approval_lock = threading.Lock()
        self._steer = {}           # task_id -> [str] injected mid-task
        self._steer_lock = threading.Lock()
        self._clients = {}         # connection id -> orchestrator info
        self._client_lock = threading.Lock()
        self._workers = []
        self._agent_pool = ThreadPoolExecutor(
            max_workers=max(1, int(self.settings.get("max_parallel_agents") or 4)),
            thread_name_prefix="agent")
        self._started = False
        self._console = []          # rolling CLI transcript for the UI
        self._console_labels = {"executor": "Executor",
                                "orchestrator": "Orchestrator"}
        self._console_lock = threading.Lock()
        self._chan = threading.local()
        providers.CLIProvider.console_hook = self._console_write
        self._orchestrator_state = {"state": "idle", "detail": "",
                                    "since": time.time()}
        # Finished tasks waiting for the orchestrator's verdict.
        self._reviews = queue.Queue()
        # mission_id -> tasks running or finished but not yet reviewed. With
        # a local orchestrator, a mission's next task waits until this is 0,
        # so a revision runs before work that builds on the rejected result.
        self._review_pending = {}
        self._review_lock = threading.Lock()
        # Optional callable: while it returns True, workers claim nothing.
        # An MCP process's embedded engine uses it to yield to the desktop.
        self.defer_claims = None

    # ------------------------------------------------------------------ bus
    def subscribe(self, callback):
        with self._sub_lock:
            self._subscribers.append(callback)
        return callback

    def unsubscribe(self, callback):
        with self._sub_lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def emit(self, kind, text="", actor="", mission_id="", task_id="",
             data=None):
        event = self.store.add_event(kind, text=text, actor=actor,
                                     mission_id=mission_id, task_id=task_id,
                                     data=data)
        with self._sub_lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(event)
            except Exception:
                pass
        return event

    # --------------------------------------------------------------- control
    def start(self):
        if self._started:
            return
        self._started = True
        orphaned = self.store.recover_orphaned_tasks(_pid_alive)
        if orphaned:
            self.emit("system",
                      "Recovered %d task(s) left unfinished by a previous "
                      "run: unstarted ones were re-queued, interrupted ones "
                      "marked as errors." % len(orphaned),
                      actor="CollaboratorMCP")
        count = max(1, int(self.settings.get("max_parallel_tasks") or 2))
        for i in range(count):
            t = threading.Thread(target=self._worker_loop, name="exec-%d" % i,
                                 daemon=True)
            t.start()
            self._workers.append(t)
        threading.Thread(target=self._orchestrator_loop, name="orchestrator",
                         daemon=True).start()
        self.emit("system", "Engine started with %d executor slot(s)." % count,
                  actor="CollaboratorMCP")

    def shutdown(self, grace_s=3.0):
        self._stop.set()
        self.resolve_all_approvals(False, "shutting down")
        self._agent_pool.shutdown(wait=False)
        # Give running tasks a moment to notice the stop flag, so any CLI
        # subprocess is killed rather than orphaned (still spending quota)
        # when this process exits.
        deadline = time.time() + grace_s
        while self.active_tasks() and time.time() < deadline:
            time.sleep(0.1)

    def pause(self):
        self._paused.set()
        self.emit("system", "Queue paused.", actor="CollaboratorMCP")

    def resume(self):
        self._paused.clear()
        self.emit("system", "Queue resumed.", actor="CollaboratorMCP")

    @property
    def is_paused(self):
        return self._paused.is_set()

    def cancel_task(self, task_id):
        with self._cancel_lock:
            self._cancelled.add(task_id)
        for pending in self.list_approvals():
            if pending.get("task_id") == task_id:
                self.resolve_approval(pending["approval_id"], False,
                                      "task cancelled")
        task = self.store.get_task(task_id)
        if task and self.store.cancel_if_queued(task_id,
                                                "Cancelled before start."):
            # No worker can claim it now, so the in-memory flag is not needed.
            with self._cancel_lock:
                self._cancelled.discard(task_id)
            if task.get("goal_id"):
                self.store.refresh_goal_status(task["goal_id"])
        self.emit("task", "Cancellation requested.", actor="Operator",
                  task_id=task_id,
                  mission_id=task["mission_id"] if task else "")
        return True

    def _is_cancelled(self, task_id):
        with self._cancel_lock:
            return task_id in self._cancelled

    # -------------------------------------------------------------- approvals
    def approval_required(self, tool_name):
        if self.settings.get("auto_approve"):
            return False
        gated = self.settings.get("approval_tools") or []
        return tool_name in gated

    def request_approval(self, tool_name, args, task_id="", mission_id=""):
        """Block until the operator (or orchestrator) decides. -> (bool, str)"""
        if not self.approval_required(tool_name):
            return (True, "auto-approved")

        approval_id = "apv_" + uuid.uuid4().hex[:10]
        record = {
            "approval_id": approval_id,
            "tool": tool_name,
            "summary": "%s(%s)" % (tool_name, _preview_args(args, 160)),
            "arguments": args,
            "task_id": task_id,
            "mission_id": mission_id,
            "created_at": time.time(),
            "status": "pending",
            "reason": "",
            "_event": threading.Event(),
        }
        with self._approval_lock:
            self._approvals[approval_id] = record

        self.emit("approval", "Approval needed: %s" % record["summary"],
                  actor=self.settings.get("executor_name"),
                  mission_id=mission_id, task_id=task_id,
                  data={"approval_id": approval_id, "tool": tool_name,
                        "state": "pending"})

        timeout = float(self.settings.get("approval_timeout_s") or 300)
        granted = record["_event"].wait(timeout=timeout)
        with self._approval_lock:
            record = self._approvals.get(approval_id, record)
            if not granted and record["status"] == "pending":
                allow = self.settings.get("approval_on_timeout") == "allow"
                record["status"] = "approved" if allow else "denied"
                record["reason"] = "no decision within %ds" % int(timeout)
            self._approvals.pop(approval_id, None)

        approved = record["status"] == "approved"
        self.emit("approval",
                  "%s: %s%s" % ("Approved" if approved else "Denied",
                                record["summary"],
                                " (%s)" % record["reason"]
                                if record["reason"] else ""),
                  actor="Operator", mission_id=mission_id, task_id=task_id,
                  data={"approval_id": approval_id, "state": record["status"]})
        return (approved, record["reason"])

    def list_approvals(self):
        with self._approval_lock:
            return [{k: v for k, v in rec.items() if not k.startswith("_")}
                    for rec in self._approvals.values()
                    if rec["status"] == "pending"]

    def resolve_approval(self, approval_id, approve, reason=""):
        with self._approval_lock:
            record = self._approvals.get(approval_id)
            if record is None or record["status"] != "pending":
                return False
            record["status"] = "approved" if approve else "denied"
            record["reason"] = reason or ""
            record["_event"].set()
        return True

    def resolve_all_approvals(self, approve, reason=""):
        count = 0
        for record in self.list_approvals():
            if self.resolve_approval(record["approval_id"], approve, reason):
                count += 1
        return count

    # ---------------------------------------------------------------- console
    def _channel(self):
        """Which in-app terminal the current thread's output belongs to."""
        return getattr(self._chan, "name", "executor")

    def _set_channel(self, name, label=""):
        self._chan.name = name
        if label:
            with self._console_lock:
                self._console_labels[name] = label

    def channel_label(self, channel):
        with self._console_lock:
            return self._console_labels.get(channel, channel)

    def _console_write(self, kind, text, channel=None):
        """Collect CLI activity so it shows in-app, not in a popup window."""
        channel = channel or self._channel()
        entry = {"ts": time.time(), "kind": kind, "channel": channel,
                 "text": str(text)[-20000:]}
        with self._console_lock:
            self._console.append(entry)
            if len(self._console) > 1200:
                del self._console[:-800]
        with self._sub_lock:
            subs = list(self._subscribers)
        event = {"id": 0, "ts": entry["ts"], "kind": "console",
                 "actor": kind, "text": entry["text"], "mission_id": "",
                 "task_id": "", "data": {"stream": kind, "channel": channel,
                                         "label": self.channel_label(channel)}}
        for cb in subs:
            try:
                cb(event)
            except Exception:
                pass

    def console_lines(self, channel=None, limit=400):
        with self._console_lock:
            rows = [e for e in self._console
                    if channel is None or e["channel"] == channel]
            return rows[-limit:]

    def console_channels(self):
        with self._console_lock:
            seen = []
            for entry in self._console:
                if entry["channel"] not in seen:
                    seen.append(entry["channel"])
            return [(c, self._console_labels.get(c, c)) for c in seen]

    def clear_console(self, channel=None):
        with self._console_lock:
            if channel is None:
                self._console = []
            else:
                self._console = [e for e in self._console
                                 if e["channel"] != channel]

    # --------------------------------------------------- orchestrator clients
    def register_client(self, conn_id, name="", version=""):
        """An MCP orchestrator attached to this engine."""
        with self._client_lock:
            first = conn_id not in self._clients
            self._clients[conn_id] = {
                "id": conn_id, "name": name or "unidentified client",
                "version": version, "since": time.time(), "calls": 0,
                "last_call": 0.0, "last_method": ""}
        if first:
            self.emit("system", "Orchestrator connected: %s"
                      % (name or "unidentified MCP client"),
                      actor="CollaboratorMCP")

    def note_client_call(self, conn_id, method):
        with self._client_lock:
            record = self._clients.get(conn_id)
            if record is not None:
                record["calls"] += 1
                record["last_call"] = time.time()
                record["last_method"] = method

    def unregister_client(self, conn_id):
        with self._client_lock:
            record = self._clients.pop(conn_id, None)
        if record:
            self.emit("system", "Orchestrator disconnected: %s (%d call(s))"
                      % (record["name"], record["calls"]),
                      actor="CollaboratorMCP")

    def clients(self):
        with self._client_lock:
            return [dict(v) for v in self._clients.values()]

    # -------------------------------------------------------- live steering
    def steer(self, text, task_id=None, actor="Operator"):
        """Inject a message into a running task, picked up on its next turn."""
        text = (text or "").strip()
        if not text:
            raise ValueError("Nothing to send.")
        if not task_id:
            active = sorted(self.active_tasks().items(),
                            key=lambda kv: kv[1].get("started", 0))
            if not active:
                raise ValueError(
                    "No task is running, so there is nothing to steer. "
                    "Delegate a task first.")
            task_id = active[-1][0]
        task = self.store.get_task(task_id)
        if not task:
            raise ValueError("No such task: %s" % task_id)
        if task["status"] not in ("running", "claimed", "queued"):
            raise ValueError("Task %s is already %s."
                             % (task_id, task["status"]))
        with self._steer_lock:
            self._steer.setdefault(task_id, []).append("%s: %s"
                                                       % (actor, text))
        self.emit("steer", text, actor=actor, task_id=task_id,
                  mission_id=task["mission_id"])
        return {"task_id": task_id, "queued": True, "title": task["title"]}

    def _take_steering(self, task_id):
        with self._steer_lock:
            return self._steer.pop(task_id, [])

    def pending_steering(self, task_id):
        with self._steer_lock:
            return list(self._steer.get(task_id, []))

    # ------------------------------------------------------------- workspace
    def workspace_info(self, path=None):
        """Describe a directory: existence, git branch, entry counts."""
        path = os.path.abspath(os.path.expanduser(
            path or self.settings.get("workspace") or "."))
        info = {"path": path, "exists": os.path.isdir(path),
                "is_git": False, "branch": "", "entries": 0, "files": 0,
                "dirs": 0, "readable": False, "writable": False}
        if not info["exists"]:
            return info
        try:
            names = os.listdir(path)
            info["readable"] = True
            info["entries"] = len(names)
            for name in names:
                if os.path.isdir(os.path.join(path, name)):
                    info["dirs"] += 1
                else:
                    info["files"] += 1
        except OSError:
            return info
        info["writable"] = os.access(path, os.W_OK)
        git_dir = os.path.join(path, ".git")
        if os.path.exists(git_dir):
            info["is_git"] = True
            info["branch"] = _git_branch(path, git_dir)
        return info

    def set_workspace(self, path, create=False):
        """Repoint the workspace every agent is confined to."""
        if not path or not str(path).strip():
            raise ValueError("Give a directory path.")
        path = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))
        if not os.path.isdir(path):
            if not create:
                raise ValueError("No such directory: %s" % path)
            os.makedirs(path, exist_ok=True)
        if not os.access(path, os.R_OK):
            raise ValueError("Cannot read that directory: %s" % path)

        previous = self.settings.workspace_dir()
        if os.path.normcase(os.path.realpath(previous)) != os.path.normcase(os.path.realpath(path)):
            # Preserve the workspace of missions created by older versions,
            # which did not save it at mission creation.
            for mission in self.store.list_missions(status="open"):
                meta = dict(mission.get("meta") or {})
                if not meta.get("workspace"):
                    meta["workspace"] = previous
                    self.store.update_mission(mission["id"], meta=meta)
        self.settings.set("workspace", path)
        recents = [p for p in (self.settings.get("recent_workspaces") or [])
                   if p and os.path.normcase(p) != os.path.normcase(path)]
        recents.insert(0, path)
        self.settings.set("recent_workspaces", recents[:12])
        self.settings.save()

        info = self.workspace_info(path)
        detail = "%s (%d item(s)%s)" % (
            path, info["entries"],
            ", git %s" % info["branch"] if info["is_git"] and info["branch"]
            else ", git repo" if info["is_git"] else "")
        self.emit("system", "Workspace set to %s" % detail,
                  actor="Operator", data={"workspace": path,
                                          "previous": previous})
        running = self.active_tasks()
        if running:
            self.emit("system",
                      "%d task(s) already running keep the previous "
                      "workspace; existing missions stay in their original "
                      "workspace. New missions use the new one." % len(running),
                      actor="CollaboratorMCP")
        return info

    def browse(self, relative="", show_hidden=False):
        """List a directory inside the workspace, for the UI and for the orchestrator."""
        root = self.settings.workspace_dir()
        target = tools.resolve_in_workspace(root, relative or "")
        if not os.path.isdir(target):
            raise ValueError("Not a directory: %s" % (relative or "."))
        rows = []
        try:
            names = sorted(os.listdir(target),
                           key=lambda n: (not os.path.isdir(
                               os.path.join(target, n)), n.lower()))
        except OSError as exc:
            raise ValueError("Cannot read that directory: %s" % exc)
        for name in names:
            if not show_hidden and name.startswith("."):
                continue
            full = os.path.join(target, name)
            is_dir = os.path.isdir(full)
            try:
                stat = os.stat(full)
                size, mtime = stat.st_size, stat.st_mtime
            except OSError:
                size, mtime = 0, 0
            rows.append({
                "name": name,
                "path": os.path.relpath(full, root).replace("\\", "/"),
                "type": "dir" if is_dir else "file",
                "size": 0 if is_dir else size,
                "modified": mtime,
            })
        return {"root": root,
                "relative": os.path.relpath(target, root).replace("\\", "/"),
                "entries": rows}

    # --------------------------------------------------------- model refresh
    def refresh_models(self, providers_wanted=None):
        """Ask each configured provider what it currently serves.

        New model ids are stored so they show up in the dropdowns. Prices are
        not published by the /models endpoints, so discovered entries start at
        zero and are flagged as estimated - you can fill them in under Fleet.
        """
        known = {m.id for m in catalog.SEED}
        stored = {row.get("id"): dict(row)
                  for row in (self.settings.get("custom_models") or [])
                  if isinstance(row, dict) and row.get("id")}

        wanted = providers_wanted or providers.configured_providers(
            self.settings)
        report = {"checked": [], "new": [], "errors": {}, "total_seen": 0}

        for provider_id in wanted:
            info = catalog.PROVIDERS.get(provider_id)
            label = info.label if info else provider_id
            try:
                client = providers.discovery_provider(self.settings,
                                                      provider_id)
                if client is None:
                    continue
                seen = client.list_models()
            except providers.MissingCredentials:
                continue          # no key for this provider: silently skip
            except Exception as exc:
                report["errors"][provider_id] = str(exc)
                self.emit("error", "%s: %s" % (label, exc),
                          actor="CollaboratorMCP")
                continue

            report["checked"].append(provider_id)
            report["total_seen"] += len(seen)
            for model_id in seen:
                if model_id in known or model_id in stored:
                    continue
                if _skip_model(model_id):
                    continue
                stored[model_id] = {
                    "id": model_id,
                    "provider": provider_id,
                    "label": model_id,
                    "input_price": 0.0,
                    "output_price": 0.0,
                    "context": 0,
                    "tier": "mid",
                    "price_estimated": True,
                    "notes": "Discovered from %s." % label,
                    "tags": ["executor", "agent"],
                }
                report["new"].append({"id": model_id, "provider": provider_id})

        self.settings.set("custom_models", list(stored.values()))
        self.settings.set("models_last_refreshed",
                          time.strftime("%Y-%m-%d %H:%M"))
        self.settings.save()
        catalog.load_custom(self.settings)

        if report["new"]:
            self.emit("system", "Model refresh: %d new model(s) from %s."
                      % (len(report["new"]),
                         ", ".join(report["checked"]) or "no provider"),
                      actor="CollaboratorMCP")
        else:
            self.emit("system",
                      "Model refresh: nothing new (%d model(s) seen across %d "
                      "provider(s))."
                      % (report["total_seen"], len(report["checked"])),
                      actor="CollaboratorMCP")
        report["last_refreshed"] = self.settings.get("models_last_refreshed")
        return report

    # ------------------------------------------------------ model orchestrator
    def plan_mission(self, mission_id=None, title="", brief="",
                     max_tasks=None, max_goals=None, workspace=None):
        try:
            return self._plan_mission(mission_id, title, brief, max_tasks,
                                      max_goals, workspace)
        except Exception as exc:
            self.set_orchestrator_state("error", str(exc))
            self.emit("error", "Planning failed: %s" % exc,
                      actor=self.settings.get("orchestrator_name"),
                      mission_id=mission_id or "")
            raise

    def _plan_mission(self, mission_id=None, title="", brief="",
                      max_tasks=None, max_goals=None, workspace=None):
        """Break a mission into goals, and each goal into executor tasks."""
        if mission_id:
            mission = self.store.get_mission(mission_id)
            if not mission:
                raise ValueError("No such mission: %s" % mission_id)
        else:
            mission = self.create_mission(title or brief[:60] or "Mission",
                                          brief, workspace=workspace)
        brief = brief or mission["brief"] or mission["title"]
        limit = int(max_tasks or self.settings.get("orchestrator_max_tasks")
                    or 8)
        goal_limit = int(max_goals or self.settings.get("orchestrator_max_goals")
                         or 4)
        model = self.settings.get("orchestrator_model")
        orchestrator = self.settings.get("orchestrator_name")

        self._set_channel("orchestrator", "Orchestrator")
        self.set_orchestrator_state(
            "planning", "Breaking down '%s'" % mission["title"])
        self.emit("mission", "%s is planning: %s" % (orchestrator,
                                                     mission["title"]),
                  actor=orchestrator, mission_id=mission["id"])
        self._check_budget(mission["id"])

        provider = providers.provider_for(model, self.settings)
        system = ORCHESTRATOR_SYSTEM.format(
            orchestrator=orchestrator,
            executor=self.settings.get("executor_name"),
            max_tasks=limit, max_goals=goal_limit,
            helpers=self.helper_team_text())
        user = ("Mission: %s\n\nBrief:\n%s\n\nProduce at most %d goals and "
                "%d tasks in total." % (mission["title"], brief, goal_limit,
                                        limit))
        result = provider.complete(
            model=model,
            messages=[{"role": "user", "content": user}],
            system=system,
            max_tokens=8000,
            effort=self.settings.get("executor_effort"),
            thinking=True,
            output_format={"type": "json_schema", "schema": PLAN_SCHEMA},
            workspace=self.workspace_for(mission))
        self._record(result, "orchestrator", mission["id"], "")

        if result.refused:
            self.set_orchestrator_state("error", "Planning was declined.")
            self.emit("error", "%s declined to plan this mission." % model,
                      actor="CollaboratorMCP", mission_id=mission["id"])
            return {"mission_id": mission["id"], "tasks": [],
                    "error": "Planning was declined."}

        plan = _parse_plan(result.text)
        if not plan:
            self.set_orchestrator_state("error", "Could not read the plan.")
            self.emit("error",
                      "Could not read a plan out of the response.",
                      actor="CollaboratorMCP", mission_id=mission["id"])
            return {"mission_id": mission["id"], "goals": [],
                    "error": "Unparseable plan.", "raw": result.text}

        goals_out, made = [], 0
        for gi, goal_item in enumerate(plan[:goal_limit]):
            goal = self.store.create_goal(
                mission["id"], goal_item.get("title") or "Goal %d" % (gi + 1),
                goal_item.get("description") or "", position=gi + 1)
            tasks_out = []
            for item in (goal_item.get("tasks") or []):
                if made >= limit:
                    break
                made += 1
                task = self.store.create_task(
                    mission["id"],
                    item.get("title") or "Task %d" % made,
                    item.get("instructions") or "",
                    item.get("context") or "",
                    priority=made, goal_id=goal["id"])
                tasks_out.append({"task_id": task["id"],
                                  "title": task["title"],
                                  "status": task["status"]})
            goals_out.append({"goal_id": goal["id"], "title": goal["title"],
                              "description": goal["description"],
                              "tasks": tasks_out})

        self.set_orchestrator_state(
            "idle", "Planned %d goal(s), %d task(s)" % (len(goals_out), made))
        self.emit("mission", "%s planned %d goal(s) and %d task(s)."
                  % (orchestrator, len(goals_out), made), actor=orchestrator,
                  mission_id=mission["id"])
        return {"mission_id": mission["id"], "title": mission["title"],
                "goals": goals_out, "task_count": made}

    # ---------------------------------------------------- orchestrator review
    def orchestrator_is_local(self):
        """True when a model in this app orchestrates (not an MCP client)."""
        return self.settings.get("orchestrator_mode") == "model"

    def _hold_mission(self, mission_id):
        """Keep a mission's next task queued until this one is reviewed."""
        if not self.orchestrator_is_local():
            return False
        with self._review_lock:
            self._review_pending[mission_id] = \
                self._review_pending.get(mission_id, 0) + 1
        return True

    def _release_mission(self, mission_id):
        with self._review_lock:
            self._review_pending[mission_id] = max(
                0, self._review_pending.get(mission_id, 1) - 1)

    def held_missions(self):
        """Missions whose queued tasks must not start yet: awaiting a
        review here, or put on hold for the operator."""
        with self._review_lock:
            held = [mid for mid, n in self._review_pending.items() if n > 0]
        held += [m["id"] for m in self.store.list_missions(status="on_hold")]
        return held

    def resume_mission(self, mission_id, actor="Operator"):
        """Release a mission the orchestrator put on hold."""
        mission = self.store.get_mission(mission_id)
        if not mission:
            raise ValueError("No such mission: %s" % mission_id)
        if mission["status"] != "on_hold":
            return mission
        mission = self.store.update_mission(mission_id, status="open")
        self.emit("mission", "Mission resumed: %s" % mission["title"],
                  actor=actor, mission_id=mission_id)
        self._maybe_close_mission(mission_id)
        return self.store.get_mission(mission_id)

    def helper_team_text(self):
        return "; ".join("%s (%s)" % (m["name"], m["model"])
                         for m in config.helper_roster(self.settings))

    @contextlib.contextmanager
    def _channel_scope(self, name, label=""):
        """Send this thread's CLI output to another terminal for a while."""
        previous = getattr(self._chan, "name", None)
        self._set_channel(name, label)
        try:
            yield
        finally:
            if previous is None:
                self._chan.__dict__.pop("name", None)
            else:
                self._chan.name = previous

    def _queue_review(self, task_id, held=False):
        """Hand a finished task to the orchestrator, if one runs here.

        ``held`` means run_task already counted this task against its
        mission; the hold passes to the review, or is released here.
        """
        task = self.store.get_task(task_id)
        reviewable = (self.orchestrator_is_local() and not self._stop.is_set()
                      and task is not None
                      and task["status"] in ("done", "partial", "blocked",
                                             "error"))
        if not reviewable:
            # Cancelled, never finished, or no local orchestrator any more.
            if held and task:
                self._release_mission(task["mission_id"])
            return
        if not held:
            self._hold_mission(task["mission_id"])
        self._reviews.put(task_id)

    def _orchestrator_loop(self):
        self._set_channel("orchestrator", "Orchestrator")
        while not self._stop.is_set():
            try:
                task_id = self._reviews.get(timeout=0.5)
            except queue.Empty:
                continue
            task = self.store.get_task(task_id)
            if not task:
                continue
            try:
                self.review_task(task)
            except Exception as exc:
                self.emit("error", "Orchestrator review failed: %s" % exc,
                          actor="CollaboratorMCP",
                          mission_id=task["mission_id"], task_id=task_id)
                # Record the failure as the verdict, or the mission would
                # wait forever for a review that is never coming.
                self._record_failed_review(task_id, exc)
            finally:
                self._release_mission(task["mission_id"])
                self.set_orchestrator_state("idle", "")
                self._maybe_close_mission(task["mission_id"])

    def _record_failed_review(self, task_id, exc):
        try:
            fresh = self.store.get_task(task_id)
            if not fresh:
                return
            meta = fresh.get("meta") if isinstance(fresh.get("meta"),
                                                   dict) else {}
            if "review" not in meta:
                meta["review"] = {"verdict": "failed",
                                  "assessment": "Review failed: %s" % exc}
                self.store.update_task(task_id, meta=meta)
        except Exception:
            pass

    def review_task(self, task):
        """The orchestrator judges one finished task and steers the mission.

        Always: accept, or send the task back (a revision is queued in the
        same goal with the orchestrator's instructions). When supervising it
        also adds or cancels queued tasks, completes the mission, puts it on
        hold for the operator, or leaves the operator a note.
        """
        orchestrator = self.settings.get("orchestrator_name")
        executor = self.settings.get("executor_name")
        model = self.settings.get("orchestrator_model")
        supervise = bool(self.settings.get("orchestrator_supervise"))
        mission = self.store.get_mission(task["mission_id"]) or {}
        meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
        depth = int(meta.get("revision") or 0)
        cap = int(self.settings.get("orchestrator_max_revisions") or 0)
        revisions_left = max(0, cap - depth)

        self._check_budget(task["mission_id"])
        self.set_orchestrator_state("reviewing",
                                    "Reviewing '%s'" % task["title"])

        goal = self.store.get_goal(task["goal_id"]) if task.get("goal_id") \
            else None
        siblings = [t for t in self.store.list_tasks(task["mission_id"])
                    if t["id"] != task["id"]]
        lines = ["Mission: %s" % mission.get("title", "")]
        if mission.get("brief"):
            lines.append("Mission brief: %s" % mission["brief"][:3000])
        if goal:
            lines.append("Goal: %s - %s" % (goal["title"], goal["description"]))
        lines += ["", "Task: %s" % task["title"],
                  "Instructions given:\n%s" % (task["instructions"] or "")[:6000],
                  "", "Reported status: %s" % task["status"],
                  "Result reported by %s:\n%s"
                  % (executor, (task["result"] or "")[-8000:] or "(none)")]
        if task.get("error"):
            lines.append("Error: %s" % task["error"][-2000:])
        if siblings:
            lines.append("\nOther tasks in this mission:")
            lines += ["- [%s] %s  %s" % (t["status"], t["id"], t["title"])
                      for t in siblings[:40]]

        if supervise:
            system = SUPERVISE_SYSTEM.format(
                orchestrator=orchestrator, executor=executor,
                helpers=self.helper_team_text(),
                revisions_left=revisions_left,
                max_new=MAX_NEW_TASKS_PER_REVIEW)
            schema = SUPERVISE_SCHEMA
        else:
            system = REVIEW_SYSTEM.format(orchestrator=orchestrator,
                                          executor=executor,
                                          revisions_left=revisions_left)
            schema = REVIEW_SCHEMA
        provider = providers.provider_for(model, self.settings)
        result = provider.complete(
            model=model,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            system=system, max_tokens=4000, effort="medium", thinking=True,
            output_format={"type": "json_schema", "schema": schema},
            workspace=self.workspace_for(mission))
        self._record(result, "orchestrator", task["mission_id"], task["id"])

        verdict = providers._first_json_object(result.text or "") or {}
        decision = verdict.get("verdict")
        assessment = (verdict.get("assessment") or result.text or "").strip()
        fix = (verdict.get("revision_instructions") or "").strip()
        if decision not in ("accept", "revise"):
            raise providers.ProviderError("Could not read the review verdict: "
                                          + assessment[:500])
        if decision == "revise" and (revisions_left <= 0 or not fix):
            decision = "accept"

        meta["review"] = {"verdict": decision, "assessment": assessment}
        self.store.update_task(task["id"], meta=meta)
        outcome = {"verdict": decision, "assessment": assessment}

        if decision == "accept":
            self.emit("orchestrator", "Accepted: %s - %s"
                      % (task["title"], assessment[:400]), actor=orchestrator,
                      mission_id=task["mission_id"], task_id=task["id"])
        else:
            root = meta.get("revision_of") or task["id"]
            base_title = task["title"].split(": ", 1)[-1] \
                if task["title"].startswith("Revise") else task["title"]
            follow = self.store.create_task(
                task["mission_id"], "Revise %d: %s" % (depth + 1, base_title),
                fix,
                "Original instructions:\n%s\n\nYour previous result:\n%s\n\n"
                "%s's assessment:\n%s"
                % (task["instructions"], (task["result"] or "")[-6000:],
                   orchestrator, assessment),
                priority=task["priority"], goal_id=task.get("goal_id") or "",
                meta={"revision_of": root, "revision": depth + 1})
            if task.get("goal_id"):
                self.store.refresh_goal_status(task["goal_id"])
            self.emit("task", "%s sent back '%s' for revision: %s"
                      % (orchestrator, task["title"], fix[:300]),
                      actor=orchestrator, mission_id=task["mission_id"],
                      task_id=follow["id"])
            outcome["follow_up"] = follow["id"]

        if supervise:
            outcome.update(self._apply_supervision(task, verdict))
        return outcome

    def _apply_supervision(self, task, verdict):
        """Carry out the orchestrator's decisions about the rest of a mission."""
        orchestrator = self.settings.get("orchestrator_name")
        mission_id = task["mission_id"]
        done = {"added": [], "cancelled": [], "mission": "continue"}

        def say(text, kind="orchestrator", task_id=""):
            self.emit(kind, text, actor=orchestrator, mission_id=mission_id,
                      task_id=task_id)

        queued = {t["id"]: t for t in self.store.list_tasks(mission_id)
                  if t["status"] == "queued"}
        for tid in verdict.get("cancel_tasks") or []:
            if not isinstance(tid, str) or tid not in queued:
                continue            # only this mission's queued work
            if self.store.cancel_if_queued(
                    tid, "Cancelled by %s while supervising." % orchestrator):
                done["cancelled"].append(tid)
                if queued[tid].get("goal_id"):
                    self.store.refresh_goal_status(queued[tid]["goal_id"])
                say("Dropped '%s' from the plan." % queued[tid]["title"],
                    "task", tid)

        ceiling = MISSION_TASK_CEILING * int(
            self.settings.get("orchestrator_max_tasks") or 8)
        existing = self.store.list_tasks(mission_id)
        priority = max([t["priority"] for t in existing] + [task["priority"]])
        for item in (verdict.get("new_tasks") or [])[:MAX_NEW_TASKS_PER_REVIEW]:
            if not isinstance(item, dict) or not (item.get("title") or "").strip():
                continue
            if len(existing) + len(done["added"]) >= ceiling:
                say("Not adding '%s': the mission already has %d tasks, the "
                    "most one mission may grow to." % (item["title"], ceiling),
                    "system")
                break
            new = self.store.create_task(
                mission_id, item["title"].strip(),
                item.get("instructions") or "", item.get("context") or "",
                priority=priority, goal_id=task.get("goal_id") or "",
                meta={"added_by": "orchestrator", "after": task["id"]})
            done["added"].append(new["id"])
            say("Added to the plan: %s" % new["title"], "task", new["id"])
        if task.get("goal_id") and done["added"]:
            self.store.refresh_goal_status(task["goal_id"])

        note = (verdict.get("note_to_operator") or "").strip()
        decision = verdict.get("mission")
        if decision == "complete":
            for t in self.store.list_tasks(mission_id):
                if t["status"] == "queued" and self.store.cancel_if_queued(
                        t["id"], "Not needed: %s judged the mission complete."
                        % orchestrator):
                    done["cancelled"].append(t["id"])
                    if t.get("goal_id"):
                        self.store.refresh_goal_status(t["goal_id"])
            done["mission"] = "complete"
            say("Judged the mission complete." + (" " + note if note else ""))
        elif decision == "hold":
            mission = self.store.get_mission(mission_id) or {}
            meta = dict(mission.get("meta") or {})
            meta["hold_reason"] = note or "The orchestrator needs a decision."
            self.store.update_mission(mission_id, status="on_hold", meta=meta)
            done["mission"] = "hold"
            say("Paused this mission for you: %s. Resume it on Tasks & "
                "results when ready." % meta["hold_reason"], "approval")
        elif note:
            say("Note for you: %s" % note)
        return done

    def _supervise_step(self, task, mission, step):
        """Check in on a running task every few steps.

        Returns "" to carry on, or the reason the orchestrator stopped it.
        A redirect arrives as a live message on the executor's next step.
        """
        every = int(self.settings.get("orchestrator_checkin_steps") or 0)
        if (step <= 0 or every <= 0 or step % every
                or not self.orchestrator_is_local()
                or not self.settings.get("orchestrator_supervise")):
            return ""
        orchestrator = self.settings.get("orchestrator_name")
        try:
            decision = self._checkin(task, mission, step)
        except Budget:
            raise
        except Exception as exc:
            self.emit("error", "Orchestrator check-in failed: %s" % exc,
                      actor="CollaboratorMCP", mission_id=task["mission_id"],
                      task_id=task["id"])
            return ""
        action = decision.get("action")
        message = (decision.get("message") or "").strip()
        reason = (decision.get("reason") or "").strip()
        if action == "redirect" and message:
            self.steer(message, task_id=task["id"], actor=orchestrator)
        elif action == "stop":
            text = "Stopped by %s: %s" % (orchestrator,
                                          reason or message or "no reason given")
            self.emit("orchestrator", text, actor=orchestrator,
                      mission_id=task["mission_id"], task_id=task["id"])
            return text
        else:
            self.emit("orchestrator", "Checked in on '%s': on track."
                      % task["title"], actor=orchestrator,
                      mission_id=task["mission_id"], task_id=task["id"])
        return ""

    def _checkin(self, task, mission, step):
        orchestrator = self.settings.get("orchestrator_name")
        executor = self.settings.get("executor_name")
        model = self.settings.get("orchestrator_model")
        self._check_budget(task["mission_id"])
        activity = ["%s %s: %s" % (e["kind"], e["actor"],
                                   (e["text"] or "").replace("\n", " ")[:400])
                    for e in self.store.list_events(task_id=task["id"],
                                                    limit=60)
                    if e["kind"] in ("tool", "message", "error", "note",
                                     "agent", "steer", "file", "shell")]
        lines = ["Mission: %s" % ((mission or {}).get("title") or ""),
                 "Task: %s" % task["title"],
                 "Instructions:\n%s" % (task["instructions"] or "")[:6000],
                 "", "Steps taken so far: %d" % step,
                 "Recent activity, oldest first:"] + (activity[-25:]
                                                      or ["(nothing yet)"])
        with self._channel_scope("orchestrator", "Orchestrator"):
            self.set_orchestrator_state("supervising",
                                        "Checking in on '%s'" % task["title"])
            try:
                provider = providers.provider_for(model, self.settings)
                result = provider.complete(
                    model=model,
                    messages=[{"role": "user", "content": "\n".join(lines)}],
                    system=CHECKIN_SYSTEM.format(orchestrator=orchestrator,
                                                 executor=executor),
                    max_tokens=2000, effort="low", thinking=False,
                    output_format={"type": "json_schema",
                                   "schema": CHECKIN_SCHEMA},
                    workspace=self.workspace_for(mission))
            finally:
                self.set_orchestrator_state("idle", "")
        self._record(result, "orchestrator", task["mission_id"], task["id"])
        return providers._first_json_object(result.text or "") or {}

    def _maybe_close_mission(self, mission_id):
        """Close a mission the orchestrator has finished reviewing."""
        with self._review_lock:
            if self._review_pending.get(mission_id):
                return
        mission = self.store.get_mission(mission_id)
        if not mission or mission["status"] != "open":
            return
        tasks = self.store.list_tasks(mission_id)
        if not tasks or any(t["status"] in ("queued", "claimed", "running")
                            for t in tasks):
            return
        if any(t["status"] != "cancelled"
               and "review" not in (t.get("meta") or {}) for t in tasks):
            return          # a finished task still awaits its verdict
        # Judge each piece of work by its newest attempt: an original task
        # that was revised counts only through its latest revision.
        latest, revisions = {}, 0
        for t in tasks:
            meta = t.get("meta") if isinstance(t.get("meta"), dict) else {}
            root = meta.get("revision_of") or t["id"]
            revisions += 1 if meta.get("revision_of") else 0
            if root not in latest or t["created_at"] > latest[root]["created_at"]:
                latest[root] = t
        def settled(t):
            # A partial result the orchestrator accepted is finished work;
            # blocked or failed work needs the operator even if accepted.
            verdict = ((t.get("meta") or {}).get("review") or {}).get(
                "verdict")
            return t["status"] == "done" or (
                t["status"] == "partial" and verdict == "accept")

        # Work the orchestrator or operator cancelled is not owed.
        ok = all(settled(t) for t in latest.values()
                 if t["status"] != "cancelled")
        status = "done" if ok else "partial"
        orchestrator = self.settings.get("orchestrator_name")
        self.store.update_mission(mission_id, status=status)
        self.emit("mission", "%s closed the mission as %s: %s (%d task(s), "
                  "%d revision(s))."
                  % (orchestrator, "complete" if ok else "needing attention",
                     mission["title"], len(tasks), revisions),
                  actor=orchestrator, mission_id=mission_id)

    # ----------------------------------------------------------- orchestration
    def create_mission(self, title, brief="", orchestrator=None,
                       workspace=None):
        """Create a mission, optionally pinned to its own repository."""
        # Every mission keeps the workspace it started in. Changing the
        # address bar later must not redirect queued work into another repo.
        resolved = os.path.abspath(os.path.expanduser(
            str(workspace or self.settings.workspace_dir())))
        if not os.path.isdir(resolved):
            raise ValueError("No such directory: %s" % resolved)
        meta = {"workspace": resolved}
        mission = self.store.create_mission(
            title, brief,
            orchestrator=orchestrator or self.settings.get("orchestrator_name"),
            meta=meta)
        self.emit("mission", "Mission created: %s" % title,
                  actor=mission["orchestrator"], mission_id=mission["id"])
        if meta.get("workspace"):
            self.emit("system", "Mission repository: %s" % meta["workspace"],
                      actor="CollaboratorMCP", mission_id=mission["id"])
            # Grant access straight away so the address bar and every agent
            # follow the mission that was just created.
            if os.path.normcase(meta["workspace"]) != os.path.normcase(
                    self.settings.get("workspace") or ""):
                self.set_workspace(meta["workspace"])
        return mission

    def workspace_for(self, mission):
        """The repository a mission's tasks run against."""
        meta = (mission or {}).get("meta") or {}
        path = meta.get("workspace") if isinstance(meta, dict) else ""
        if path and os.path.isdir(path):
            return path
        return self.settings.workspace_dir()

    def close_mission(self, mission_id, status="closed"):
        mission = self.store.update_mission(mission_id, status=status)
        if mission:
            self.emit("mission", "Mission %s." % status,
                      actor=mission["orchestrator"], mission_id=mission_id)
        return mission

    def delegate(self, mission_id, title, instructions, context="", priority=5,
                 actor=None, goal_id=""):
        mission = self.store.get_mission(mission_id)
        if not mission:
            raise ValueError("No such mission: %s" % mission_id)
        task = self.store.create_task(mission_id, title, instructions, context,
                                      priority=priority, goal_id=goal_id)
        self.emit("task", "Delegated: %s" % title,
                  actor=actor or mission["orchestrator"],
                  mission_id=mission_id, task_id=task["id"])
        return task

    def quick_task(self, title, instructions, context=""):
        """Delegate into an ad-hoc mission (used by the UI's quick box)."""
        mission = self.create_mission(title[:80] or "Ad-hoc", instructions)
        return self.delegate(mission["id"], title, instructions, context)

    # ---------------------------------------------------------------- budget
    def _check_budget(self, mission_id):
        if not self.settings.get("budget_enabled"):
            return
        daily_cap = float(self.settings.get("budget_usd_daily") or 0)
        if daily_cap > 0 and self.store.today_cost() >= daily_cap:
            raise Budget("Daily budget of %s reached."
                         % catalog.fmt_cost(daily_cap))
        mission_cap = float(self.settings.get("budget_usd_per_mission") or 0)
        if mission_id and mission_cap > 0:
            if self.store.mission_cost(mission_id) >= mission_cap:
                raise Budget("Mission budget of %s reached."
                             % catalog.fmt_cost(mission_cap))

    def _record(self, result, role, mission_id="", task_id=""):
        self.store.record_usage(result.model or "", result.in_tokens,
                                result.out_tokens, result.cost_usd, role=role,
                                mission_id=mission_id, task_id=task_id)
        return result.cost_usd

    # ------------------------------------------------------------ sub-agents
    def set_orchestrator_state(self, state, detail=""):
        """What the orchestrator is doing right now, for the Missions panel."""
        self._orchestrator_state = {"state": state, "detail": detail,
                                    "since": time.time()}
        if state == "idle" and not detail:
            return              # going quiet is not worth a log line
        self.emit("orchestrator", detail or state,
                  actor=self.settings.get("orchestrator_name"),
                  data={"state": state})

    def orchestrator_state(self):
        return dict(self._orchestrator_state)

    def run_sub_agent(self, role, model, instructions, context="",
                      task_id="", mission_id=""):
        # spawn_agent runs on the executor's own thread; without restoring
        # the channel, everything the executor does afterwards would be
        # written into this agent's terminal.
        with self._channel_scope(getattr(self._chan, "name", "executor")):
            return self._run_sub_agent(role, model, instructions, context,
                                       task_id, mission_id)

    def _run_sub_agent(self, role, model, instructions, context="",
                       task_id="", mission_id=""):
        allowed = config.helper_models(self.settings)
        if allowed and model not in allowed:
            fallback = allowed[0]
            self.emit("agent", "%s is not enabled; using %s instead."
                      % (model, fallback), actor="CollaboratorMCP",
                      task_id=task_id, mission_id=mission_id)
            model = fallback

        spec = catalog.get(model)
        record = self.store.create_agent(
            model=model, provider=spec.provider if spec else "",
            prompt=instructions, name=role, task_id=task_id,
            mission_id=mission_id)
        channel = "agent:%s" % record["id"]
        self._set_channel(channel, "%s (%s)" % (role, model))
        self._console_write("cmd", "agent %s on %s\n%s"
                            % (role, model, instructions[:2000]), channel)
        self.emit("agent", "Deployed %s on %s" % (role, model),
                  actor=self.settings.get("executor_name"), task_id=task_id,
                  mission_id=mission_id,
                  data={"agent_id": record["id"], "channel": channel,
                        "label": "%s (%s)" % (role, model)})

        try:
            self._check_budget(mission_id)
            provider = providers.provider_for(model, self.settings)
            user = instructions
            if context:
                user += "\n\n--- context ---\n" + context
            result = provider.complete(
                model=model,
                messages=[{"role": "user", "content": user}],
                system=AGENT_SYSTEM.format(
                    role=role, executor=self.settings.get("executor_name")),
                max_tokens=int(self.settings.get("agent_max_tokens") or 4000),
                effort="low",
                thinking=False,
                timeout=float(self.settings.get("agent_timeout_s") or 180),
                workspace=self.workspace_for(self.store.get_mission(mission_id)
                                             if mission_id else None),
            )
            if result.refused:
                text = ("The agent declined this sub-task"
                        + (" (%s)" % result.refusal_category
                           if result.refusal_category else "") + ".")
                self.store.finish_agent(record["id"], text, status="refused",
                                        in_tokens=result.in_tokens,
                                        out_tokens=result.out_tokens,
                                        cost_usd=result.cost_usd)
            else:
                text = result.text or "(empty response)"
                self.store.finish_agent(record["id"], text, status="done",
                                        in_tokens=result.in_tokens,
                                        out_tokens=result.out_tokens,
                                        cost_usd=result.cost_usd)
            cost = self._record(result, "agent", mission_id, task_id)
            self._console_write("out", text, channel)
            self._console_write("exit", "finished (%s)"
                                % catalog.fmt_cost(cost), channel)
            self.emit("agent", "%s finished (%s, %s)"
                      % (role, model, catalog.fmt_cost(cost)),
                      actor=role, task_id=task_id, mission_id=mission_id,
                      data={"agent_id": record["id"], "channel": channel})
            return text
        except Budget as exc:
            msg = "Sub-agent not run: %s" % exc
            self.store.finish_agent(record["id"], msg, status="blocked")
            self.emit("budget", msg, actor="CollaboratorMCP", task_id=task_id,
                      mission_id=mission_id)
            return msg
        except Exception as exc:
            msg = "Sub-agent %s (%s) failed: %s" % (role, model, exc)
            self.store.finish_agent(record["id"], msg, status="error")
            self._console_write("err", msg, channel)
            self.emit("error", msg, actor="CollaboratorMCP", task_id=task_id,
                      mission_id=mission_id)
            return msg

    def run_sub_agents_parallel(self, specs, task_id="", mission_id=""):
        default = config.helper_models(self.settings)[0]
        futures = []
        for spec in specs:
            futures.append(self._agent_pool.submit(
                self.run_sub_agent,
                spec.get("role", "agent"),
                spec.get("model") or default,
                spec.get("instructions", ""),
                spec.get("context", ""),
                task_id, mission_id))
        out = []
        for fut in futures:
            try:
                out.append(fut.result())
            except Exception as exc:
                out.append("Sub-agent failed: %s" % exc)
        return out

    # --------------------------------------------------------------- executor
    def _executor_call(self, messages, schemas, mission_id, task_id,
                       workspace):
        """One Claude turn, with a client-side fallback on refusal."""
        model = self.settings.get("executor_model")
        provider = providers.provider_for(model, self.settings)
        system = EXECUTOR_SYSTEM.format(
            executor=self.settings.get("executor_name"),
            orchestrator=self.settings.get("orchestrator_name"),
            # The mission's own repository when it pins one - the same root
            # the file tools resolve against.
            workspace=workspace,
            agent_models=self.helper_team_text(),
            delegation=delegation_rules(self.settings),
            extra=(self.settings.get("executor_system_extra") or "").strip())

        result = provider.complete(
            model=model, messages=messages, system=system, tools=schemas,
            max_tokens=int(self.settings.get("executor_max_tokens") or 16000),
            effort=self.settings.get("executor_effort"),
            thinking=bool(self.settings.get("executor_thinking")))
        self._record(result, "executor", mission_id, task_id)

        if result.refused and self.settings.get("enable_refusal_fallback"):
            fallback = self.settings.get("fallback_model")
            if fallback and fallback != model:
                self.emit("system",
                          "%s declined (%s); retrying on %s."
                          % (model, result.refusal_category or "policy",
                             fallback),
                          actor="CollaboratorMCP", mission_id=mission_id,
                          task_id=task_id)
                fb_provider = providers.provider_for(fallback, self.settings)
                result = fb_provider.complete(
                    model=fallback, messages=messages, system=system,
                    tools=schemas,
                    max_tokens=int(self.settings.get("executor_max_tokens")
                                   or 16000),
                    effort=self.settings.get("executor_effort"),
                    thinking=bool(self.settings.get("executor_thinking")))
                self._record(result, "executor-fallback", mission_id, task_id)
        return result

    def run_task(self, task):
        """Drive one task to completion. Runs on a worker thread."""
        task_id = task["id"]
        mission = self.store.get_mission(task["mission_id"])
        ctx = ExecContext(self, task, mission)
        schemas = tools.tool_schemas(self.settings)
        executor_name = self.settings.get("executor_name")

        brief = ["# Task: %s" % task["title"]]
        if mission:
            brief.append("Mission: %s" % mission["title"])
            if mission["brief"]:
                brief.append("Mission brief: %s" % mission["brief"])
        brief.append("")
        brief.append(task["instructions"] or "(no further instructions)")
        if task["context"]:
            brief.append("\n--- context from %s ---\n%s"
                         % (mission["orchestrator"] if mission else "orchestrator",
                            task["context"]))
        brief_text = "\n".join(brief)
        messages = [{"role": "user", "content": brief_text}]

        self._set_channel("executor", self.settings.get("executor_name"))
        self._console_write("cmd", "TASK  %s\n%s"
                            % (task["title"], brief_text[:4000]), "executor")
        self.store.update_task(task_id, status="running",
                               started_at=time.time(),
                               model=self.settings.get("executor_model"))
        if task.get("goal_id"):
            self.store.refresh_goal_status(task["goal_id"])
        self.emit("task", "Started: %s" % task["title"], actor=executor_name,
                  mission_id=task["mission_id"], task_id=task_id)
        with self._active_lock:
            self._active[task_id] = {"title": task["title"],
                                     "started": time.time()}
        held = self._hold_mission(task["mission_id"])

        max_iters = int(self.settings.get("max_tool_iterations") or 40)
        final_status, final_text = "done", ""
        try:
            if catalog.is_subscription(self.settings.get("executor_model")):
                return self._run_task_via_cli(task, mission, brief_text)
            for iteration in range(max_iters):
                if self._is_cancelled(task_id) or self._stop.is_set():
                    final_status, final_text = "cancelled", "Cancelled."
                    break
                self._check_budget(task["mission_id"])
                stopped = self._supervise_step(task, mission, iteration)
                if stopped:
                    final_status, final_text = "blocked", stopped
                    break

                for note in self._take_steering(task_id):
                    messages.append({
                        "role": "user",
                        "content": [{"type": "text",
                                     "text": "[live message] " + note}]})

                result = self._executor_call(messages, schemas,
                                             task["mission_id"], task_id,
                                             ctx.workspace)

                if result.refused:
                    final_status = "blocked"
                    final_text = ("%s declined this task%s."
                                  % (executor_name,
                                     " (%s)" % result.refusal_category
                                     if result.refusal_category else ""))
                    break

                if result.text:
                    self.emit("message", result.text, actor=executor_name,
                              mission_id=task["mission_id"], task_id=task_id)

                if not result.tool_calls:
                    final_text = result.text or "(no output)"
                    final_status = "done"
                    if result.stop_reason == "max_tokens":
                        # Cut off mid-reply: whatever it was about to do
                        # (possibly a tool call) never arrived.
                        final_status = "partial"
                        final_text += ("\n\n[Reply cut off at the %s-token "
                                       "output limit. Raise executor_max_"
                                       "tokens in Settings and retry.]"
                                       % self.settings.get(
                                           "executor_max_tokens"))
                    break

                messages.append({"role": "assistant",
                                 "content": result.raw_content})

                tool_results, finished = [], None
                for call in result.tool_calls:
                    if call["name"] == "finish":
                        blocker = ctx.finish_blocker()
                        if blocker:
                            self._emit_review_gate(task)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": call["id"],
                                "content": blocker, "is_error": True,
                            })
                            continue
                        finished = call
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": call["id"],
                            "content": "Task closed.",
                        })
                        continue
                    self.emit("tool", "%s(%s)" % (
                        call["name"],
                        _preview_args(call["input"])), actor=executor_name,
                        mission_id=task["mission_id"], task_id=task_id)
                    text, is_error = tools.dispatch(ctx, call["name"],
                                                    call["input"])
                    if is_error:
                        self.emit("error", "%s: %s" % (call["name"], text),
                                  actor="CollaboratorMCP",
                                  mission_id=task["mission_id"],
                                  task_id=task_id)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": str(text)[:200000],
                        "is_error": bool(is_error),
                    })

                messages.append({"role": "user", "content": tool_results})

                if finished is not None:
                    final_text = finished["input"].get("summary", "")
                    final_status = finished["input"].get("status") or "done"
                    break
            else:
                final_status = "partial"
                final_text = ("Reached the %d-step limit before finishing."
                              % max_iters)

            final_status = self.normalise_status(final_status)
            self._console_write("exit", "%s: %s"
                                % (final_status, final_text[:2000]),
                                "executor")
            self.store.update_task(
                task_id, status=final_status,
                result=final_text, finished_at=time.time())
            if task.get("goal_id"):
                self.store.refresh_goal_status(task["goal_id"])
            self.emit("task", "%s: %s" % (final_status.capitalize(),
                                          task["title"]),
                      actor=executor_name, mission_id=task["mission_id"],
                      task_id=task_id, data={"result": final_text})

        except Budget as exc:
            self.store.update_task(task_id, status="blocked",
                                   error=str(exc), finished_at=time.time())
            self.emit("budget", "Task halted: %s" % exc,
                      actor="CollaboratorMCP", mission_id=task["mission_id"],
                      task_id=task_id)
        except providers.Cancelled:
            self._finish_task(task, "cancelled", "Cancelled.")
        except providers.ProviderError as exc:
            self.store.update_task(task_id, status="error", error=str(exc),
                                   finished_at=time.time())
            self.emit("error", str(exc), actor="CollaboratorMCP",
                      mission_id=task["mission_id"], task_id=task_id)
        except Exception as exc:
            detail = "%s\n%s" % (exc, traceback.format_exc(limit=4))
            self.store.update_task(task_id, status="error", error=detail,
                                   finished_at=time.time())
            self.emit("error", "Task failed: %s" % exc, actor="CollaboratorMCP",
                      mission_id=task["mission_id"], task_id=task_id)
        finally:
            with self._active_lock:
                self._active.pop(task_id, None)
            with self._cancel_lock:
                self._cancelled.discard(task_id)
            if task.get("goal_id"):
                self.store.refresh_goal_status(task["goal_id"])
            self._queue_review(task_id, held)
        return self.store.get_task(task_id)

    def _run_task_via_cli(self, task, mission, brief_text):
        """Subscription mode: same tool loop, driven over a text protocol.

        A CLI cannot emit native tool_use blocks, so the executor replies with
        one JSON action per turn and CollaboratorMCP runs the tool itself.
        That keeps the approval gate, the workspace confinement and the shell
        toggle identical to API mode - the CLI never touches the filesystem on
        its own.
        """
        task_id = task["id"]
        mission_id = task["mission_id"]
        model = self.settings.get("executor_model")
        spec = catalog.get(model)
        executor_name = self.settings.get("executor_name")
        ctx = ExecContext(self, task, mission)
        schemas = tools.tool_schemas(self.settings)

        self.emit("system",
                  "Running on %s via your subscription - no API charge."
                  % (spec.label if spec else model),
                  actor="CollaboratorMCP", mission_id=mission_id,
                  task_id=task_id)

        # The protocol goes in the user turn, not the system prompt: a CLI
        # harness has its own strong system prompt telling it to use its own
        # tools, and an appended instruction loses that contest.
        system = None
        transcript = [TEXT_PROTOCOL_PROMPT.format(
            executor=executor_name,
            orchestrator=self.settings.get("orchestrator_name"),
            workspace=ctx.workspace,
            tools=_describe_tools(schemas),
            delegation=delegation_rules(self.settings),
            extra=(self.settings.get("executor_system_extra") or "").strip(),
            brief=brief_text)]
        # When the CLI can resume its own session, only what is new since
        # the last turn is sent; the full transcript is kept as a fallback
        # for when a resume fails.
        pending = list(transcript)
        session_id = ""

        def add(text, echo=False):
            """Record a turn. ``echo`` marks the model's own words, which a
            resumed session already has."""
            transcript.append(text)
            if not echo:
                pending.append(text)

        provider = providers.provider_for(model, self.settings)
        resumable = getattr(provider, "supports_resume", False)
        max_iters = int(self.settings.get("max_tool_iterations") or 40)
        quota_total = 0.0
        reasked = False

        def should_stop():
            return self._is_cancelled(task_id) or self._stop.is_set()

        for step in range(max_iters):
            if should_stop():
                return self._finish_task(task, "cancelled", "Cancelled.",
                                         quota_total)
            stopped = self._supervise_step(task, mission, step)
            if stopped:
                return self._finish_task(task, "blocked", stopped, quota_total)

            for note in self._take_steering(task_id):
                add("[live message] " + note)

            resume = session_id if (resumable and session_id) else None
            body = pending if resume else transcript
            prompt = "\n\n".join(body) + TEXT_PROTOCOL_REMINDER
            try:
                result = provider.complete(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    system=system,
                    max_tokens=int(self.settings.get("executor_max_tokens")
                                   or 16000),
                    effort=self.settings.get("executor_effort"),
                    timeout=float(self.settings.get("cli_timeout_s") or 900),
                    workspace=ctx.workspace, should_stop=should_stop,
                    resume=resume)
            except providers.Cancelled:
                raise
            except providers.ProviderError as exc:
                if not resume:
                    raise
                result = None
                failure = str(exc)
            else:
                failure = (result.text if resume and
                           result.stop_reason == "error" else "")
            if resume and (result is None or failure):
                # The session could not be continued (expired, pruned,
                # different CLI version). Start over with the full record.
                session_id = ""
                self.emit("system",
                          "Could not resume the CLI session (%s); resending "
                          "the full transcript." % (failure[:200] or "error"),
                          actor="CollaboratorMCP", mission_id=mission_id,
                          task_id=task_id)
                continue
            if result.session_id:
                session_id = result.session_id
            del pending[:]
            self._record(result, "executor-cli", mission_id, task_id)
            quota_total += result.quota_usd or 0.0

            if result.refused:
                return self._finish_task(task, "blocked",
                                         "%s declined this task."
                                         % executor_name)

            action = _parse_action(result.text)
            if action is None:
                if not reasked:
                    # Nudge once before giving up on the protocol.
                    reasked = True
                    add("Your reply:\n" + (result.text or ""), echo=True)
                    add("That was not a JSON action. Your own tools are "
                        "disabled; the only way to act is the JSON object. "
                        "If the task is finished, send "
                        '{"tool": "finish", "args": {"summary": "...", '
                        '"status": "done"}}.')
                    continue
                return self._finish_task(task, "partial",
                                         (result.text or "(no output)") +
                                         "\n\nThe executor did not complete the tool protocol.",
                                         quota_total)
            reasked = False

            name = action.get("tool") or ""
            args = action.get("args") or {}
            if action.get("say"):
                self.emit("message", str(action["say"]), actor=executor_name,
                          mission_id=mission_id, task_id=task_id)

            if name == "finish":
                blocker = ctx.finish_blocker()
                if blocker:
                    self._emit_review_gate(task)
                    add("Your action:\n" + json.dumps(action), echo=True)
                    add("Result of finish (ERROR):\n" + blocker)
                    continue
                return self._finish_task(
                    task, args.get("status") or "done",
                    args.get("summary") or result.text or "(no summary)",
                    quota_total)

            self.emit("tool", "%s(%s)" % (name, _preview_args(args)),
                      actor=executor_name, mission_id=mission_id,
                      task_id=task_id)
            output, is_error = tools.dispatch(ctx, name, args)
            if is_error:
                self.emit("error", "%s: %s" % (name, output),
                          actor="CollaboratorMCP", mission_id=mission_id,
                          task_id=task_id)

            add("Your action:\n" + json.dumps(action), echo=True)
            add("Result of %s%s:\n%s" % (name, " (ERROR)" if is_error else "",
                                         str(output)[:20000]))

        return self._finish_task(
            task, "partial",
            "Reached the %d-step limit before finishing." % max_iters,
            quota_total)

    def _emit_review_gate(self, task):
        self.emit("system",
                  "Finish held back: files changed with no reviewer agent. "
                  "Asked %s to deploy one first."
                  % self.settings.get("executor_name"),
                  actor="CollaboratorMCP", mission_id=task["mission_id"],
                  task_id=task["id"])

    # A model may report "success"/"complete"/etc; the store only knows these.
    TERMINAL = ("done", "partial", "blocked", "error", "cancelled")
    STATUS_ALIASES = {
        "success": "done", "succeeded": "done", "complete": "done",
        "completed": "done", "ok": "done", "finished": "done",
        "incomplete": "partial", "failed": "error", "failure": "error",
        "stuck": "blocked", "cancelled": "cancelled", "canceled": "cancelled",
    }

    @classmethod
    def normalise_status(cls, status):
        status = (status or "done").strip().lower()
        if status in cls.TERMINAL:
            return status
        return cls.STATUS_ALIASES.get(status, "done")

    def _finish_task(self, task, status, text, quota_usd=0.0):
        status = self.normalise_status(status)
        task_id = task["id"]
        self._console_write("exit", "%s: %s" % (status, text[:2000]),
                            "executor")
        mission_id = task["mission_id"]
        executor_name = self.settings.get("executor_name")
        if quota_usd:
            self.emit("system",
                      "Plan quota used: %s equivalent (not billed)."
                      % catalog.fmt_cost(quota_usd),
                      actor="CollaboratorMCP", mission_id=mission_id,
                      task_id=task_id)
        self.store.update_task(task_id, status=status, result=text,
                               finished_at=time.time(),
                               error=text if status == "error" else "")
        if task.get("goal_id"):
            self.store.refresh_goal_status(task["goal_id"])
        self.emit("task", "%s: %s" % (status.capitalize(), task["title"]),
                  actor=executor_name, mission_id=mission_id, task_id=task_id,
                  data={"result": text})
        return self.store.get_task(task_id)

    # ------------------------------------------------------------ worker loop
    def _worker_loop(self):
        while not self._stop.is_set():
            if self._paused.is_set() or self._claims_deferred():
                time.sleep(0.4)
                continue
            task = self._claim_task()
            if task is None:
                time.sleep(0.5)
                continue
            try:
                self.run_task(task)
            except Exception:
                pass

    def _claims_deferred(self):
        try:
            return bool(self.defer_claims and self.defer_claims())
        except Exception:
            return False

    def _claim_task(self):
        task = self.store.claim_next_task(
            os.getpid(), exclude_missions=self.held_missions())
        if task is None:
            return None
        if self._is_cancelled(task["id"]):
            self.store.update_task(task["id"], status="cancelled",
                                   finished_at=time.time())
            return None
        return task

    # ------------------------------------------------------------------ views
    def active_tasks(self):
        with self._active_lock:
            return dict(self._active)

    def stats(self):
        totals = self.store.totals()
        queued = len(self.store.list_tasks(status="queued"))
        running = len(self.active_tasks())
        return {
            "queued": queued,
            "running": running,
            "paused": self.is_paused,
            "today_usd": self.store.today_cost(),
            "total_usd": totals["cost_usd"],
            "calls": totals["calls"],
            "in_tokens": totals["in_tokens"],
            "out_tokens": totals["out_tokens"],
            "executor_model": self.settings.get("executor_model"),
            "account_mode": self.settings.get("account_mode"),
            "orchestrator_mode": self.settings.get("orchestrator_mode"),
            "orchestrator_model": self.settings.get("orchestrator_model"),
            "auto_approve": bool(self.settings.get("auto_approve")),
            "pending_approvals": len(self.list_approvals()),
            "orchestrators": self.clients(),
            "credentials": providers.credentials_status(self.settings),
        }


def _pid_alive(pid):
    """Whether a process id belongs to a running process.

    Deliberately avoids os.kill(pid, 0): on Windows that terminates the
    target instead of probing it.
    """
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259                           # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _git_branch(path, git_dir):
    """Current branch name, read from .git without shelling out."""
    try:
        head_path = os.path.join(git_dir, "HEAD")
        if os.path.isfile(git_dir):          # worktree: .git is a file
            with open(git_dir, "r", encoding="utf-8", errors="replace") as fh:
                line = fh.read().strip()
            if line.startswith("gitdir:"):
                head_path = os.path.join(line.split(":", 1)[1].strip(), "HEAD")
        with open(head_path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read().strip()
        if head.startswith("ref:"):
            return head.split("/")[-1]
        return head[:8]                       # detached HEAD
    except Exception:
        return ""


# Model families that are not chat completions and would only clutter the
# dropdowns if a /models call returned them.
_SKIP_SUBSTRINGS = (
    "embedding", "embed", "tts", "whisper", "transcribe", "moderation",
    "image", "vision-preview", "veo", "lyria", "imagen", "dall-e", "sora",
    "rerank", "audio", "realtime", "search-index", "guard",
)


def _skip_model(model_id):
    lowered = model_id.lower()
    return any(token in lowered for token in _SKIP_SUBSTRINGS)


def _describe_tools(schemas):
    """Render tool schemas as a compact list for a text-protocol prompt."""
    lines = []
    for schema in schemas:
        props = (schema.get("input_schema") or {}).get("properties") or {}
        required = set((schema.get("input_schema") or {}).get("required") or [])
        args = []
        for key, meta in props.items():
            mark = "" if key in required else "?"
            args.append("%s%s" % (key, mark))
        lines.append("- %s(%s): %s" % (schema["name"], ", ".join(args),
                                       schema.get("description", "").strip()))
    return "\n".join(lines)


def _parse_action(text):
    """Pull one {"tool": ..., "args": {...}} action out of a text reply."""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped[:4].lower() == "json":
            stripped = stripped[4:]
        stripped = stripped.strip()
    candidates = [stripped]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for chunk in candidates:
        try:
            data = json.loads(chunk)
        except Exception:
            continue
        if isinstance(data, dict) and data.get("tool"):
            if not isinstance(data.get("args"), dict):
                data["args"] = {}
            return data
    return None


def _parse_plan(text):
    """Pull a goal list out of a model response, tolerating stray prose.

    Accepts the two-level {"goals": [{title, tasks: [...]}]} shape, and also
    a bare task list from an older-style reply, which is wrapped in a single
    goal so nothing is lost.
    """
    if not text:
        return []
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    start, end = text.find("["), text.rfind("]")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for chunk in candidates:
        try:
            data = json.loads(chunk)
        except Exception:
            continue
        if isinstance(data, dict):
            if isinstance(data.get("goals"), list):
                goals = [g for g in data["goals"]
                         if isinstance(g, dict) and g.get("title")]
                if goals:
                    return goals
            if isinstance(data.get("tasks"), list):
                data = data["tasks"]
        if isinstance(data, list):
            tasks = [item for item in data if isinstance(item, dict)
                     and item.get("title")]
            if tasks:
                if all("tasks" in t for t in tasks):
                    return tasks          # already goal-shaped
                return [{"title": "Deliver the mission", "description": "",
                         "tasks": tasks}]
    return []


def _preview_args(args, limit=90):
    try:
        parts = []
        for key, value in (args or {}).items():
            text = str(value).replace("\n", " ")
            if len(text) > 40:
                text = text[:40] + "..."
            parts.append("%s=%s" % (key, text))
        joined = ", ".join(parts)
        return joined[:limit] + ("..." if len(joined) > limit else "")
    except Exception:
        return ""
