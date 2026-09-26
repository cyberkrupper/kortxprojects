"""Tools exposed to the Claude executor.

Every tool is client-executed: the engine receives a ``tool_use`` block and
runs the corresponding handler here. File paths are confined to the configured
workspace directory; shell access is off unless explicitly enabled.
"""

import os
import subprocess
import time

from . import config


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def tool_schemas(settings):
    team = config.helper_roster(settings)
    names = [member["name"] for member in team]
    roster = "; ".join("%s (%s)" % (m["name"], m["model"]) for m in team)

    schemas = [
        {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the workspace. "
                           "Paths are relative to the workspace root.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Workspace-relative file path."},
                },
                "required": ["path"],
            },
        },
        {
            "name": "write_file",
            "description": "Create or overwrite a UTF-8 text file in the "
                           "workspace. Parent directories are created.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "list_files",
            "description": "List files and directories under a workspace path.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "Workspace-relative directory. "
                                            "Defaults to the root."},
                },
                "required": [],
            },
        },
        {
            "name": "spawn_agent",
            "description": (
                "Hand a scoped sub-task to one member of your helper team "
                "(summarising, extraction, classification, drafting, review). "
                "Your team: " + roster + ". A helper has no tools and no "
                "memory: give it everything it needs in 'instructions' and "
                "'context'. Returns the helper's answer."),
            "input_schema": {
                "type": "object",
                "properties": {
                    "helper": {"type": "string", "enum": names,
                               "description": "Which team member does it. "
                                              "Defaults to the first one."},
                    "role": {"type": "string",
                             "description": "Short label for this job, e.g. "
                                            "'summariser'."},
                    "instructions": {"type": "string",
                                     "description": "What the helper must do."},
                    "context": {"type": "string",
                                "description": "Material the helper needs."},
                },
                "required": ["instructions"],
            },
        },
        {
            "name": "spawn_agents",
            "description": (
                "Give several helpers a job each, run them in parallel and "
                "collect all of their answers. Use for fan-out over "
                "independent sub-tasks. The same helper may appear more than "
                "once."),
            "input_schema": {
                "type": "object",
                "properties": {
                    "agents": {
                        "type": "array",
                        "description": "One entry per job.",
                        "maxItems": config.MAX_HELPERS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "helper": {"type": "string", "enum": names},
                                "role": {"type": "string"},
                                "instructions": {"type": "string"},
                                "context": {"type": "string"},
                            },
                            "required": ["instructions"],
                        },
                    },
                },
                "required": ["agents"],
            },
        },
        {
            "name": "note",
            "description": "Post a short progress note to the orchestrator and "
                           "the activity log. Use sparingly for real milestones.",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
        {
            "name": "finish",
            "description": "Complete the task. Provide the deliverable or a "
                           "clear summary of what was produced and where.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "status": {"type": "string",
                               "enum": ["done", "blocked", "partial"],
                               "description": "Report honestly."},
                },
                "required": ["summary"],
            },
        },
    ]

    if settings.get("allow_shell"):
        schemas.insert(3, {
            "name": "run_command",
            "description": (
                "Run a shell command inside the workspace directory and return "
                "its combined output. Only use it when a file tool cannot do "
                "the job."),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                },
                "required": ["command"],
            },
        })
    return schemas


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

class ToolError(Exception):
    pass


def resolve_in_workspace(root, path):
    """Resolve ``path`` inside ``root``; raise if it escapes."""
    root_abs = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_abs, path or ""))
    # commonpath, not a prefix test: a drive root such as C:\ already ends
    # in a separator, and "C:\\" would reject every path inside it.
    try:
        inside = (os.path.normcase(os.path.commonpath([root_abs, candidate]))
                  == os.path.normcase(root_abs))
    except ValueError:                  # different drives
        inside = False
    if not inside:
        raise ToolError("Path escapes the workspace: %r" % path)
    return candidate


MAX_READ_BYTES = 400_000


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def read_file(ctx, args):
    path = resolve_in_workspace(ctx.workspace, args.get("path", ""))
    if not os.path.isfile(path):
        raise ToolError("No such file: %s" % args.get("path"))
    size = os.path.getsize(path)
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        data = fh.read(MAX_READ_BYTES)
    if size > MAX_READ_BYTES:
        data += "\n\n[truncated: %d of %d bytes shown]" % (MAX_READ_BYTES, size)
    return data


def write_file(ctx, args):
    rel = args.get("path", "")
    content = args.get("content", "")
    path = resolve_in_workspace(ctx.workspace, rel)
    os.makedirs(os.path.dirname(path) or ctx.workspace, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    ctx.wrote_files = True
    ctx.emit("file", "wrote %s (%d bytes)" % (rel, len(content.encode("utf-8"))))
    return "Wrote %d bytes to %s" % (len(content.encode("utf-8")), rel)


def list_files(ctx, args):
    path = resolve_in_workspace(ctx.workspace, args.get("path", "") or "")
    if not os.path.isdir(path):
        raise ToolError("Not a directory: %s" % args.get("path"))
    entries = []
    for name in sorted(os.listdir(path)):
        full = os.path.join(path, name)
        if os.path.isdir(full):
            entries.append("%s/" % name)
        else:
            entries.append("%s  (%d bytes)" % (name, os.path.getsize(full)))
    return "\n".join(entries) if entries else "(empty directory)"


def run_command(ctx, args):
    if not ctx.settings.get("allow_shell"):
        raise ToolError("Shell access is disabled. Enable it in Settings.")
    command = args.get("command", "").strip()
    if not command:
        raise ToolError("Empty command.")
    ctx.emit("shell", "$ " + command)
    timeout = int(ctx.settings.get("shell_timeout_s") or 120)
    from .providers import CLIProvider, _kill_tree, no_window_kwargs
    hook = CLIProvider.console_hook
    if hook:
        hook("cmd", "%s  (cwd %s)" % (command, ctx.workspace))
    kwargs = no_window_kwargs()
    if os.name != "nt":
        kwargs["start_new_session"] = True     # so the group can be killed
    proc = subprocess.Popen(command, shell=True, cwd=ctx.workspace,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            errors="replace", **kwargs)
    # subprocess.run(timeout=) only kills the shell; a child it started keeps
    # the pipes open and the call blocks until that child exits on its own.
    # Kill the whole tree instead, and stop promptly on cancellation.
    should_stop = getattr(ctx, "should_stop", None)
    deadline = time.time() + timeout
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=1.0)
            break
        except subprocess.TimeoutExpired:
            if should_stop is not None and should_stop():
                _kill_tree(proc)
                raise ToolError("Command stopped: task cancelled.")
            if time.time() >= deadline:
                _kill_tree(proc)
                raise ToolError("Command timed out after %ss." % timeout)
    out = (stdout or "") + (stderr or "")
    out = out[-40000:]
    if hook:
        hook("out", out)
        hook("exit", "exit %d" % proc.returncode)
    return "exit=%d\n%s" % (proc.returncode, out or "(no output)")


def note(ctx, args):
    text = args.get("text", "").strip()
    ctx.emit("note", text, actor=ctx.executor_name)
    return "Noted."


def resolve_helper(settings, spec):
    """(role, model) for a spawn request naming a helper or a model."""
    team = config.helper_roster(settings)
    member = None
    if spec.get("helper"):
        member = config.find_helper(settings, spec["helper"])
        if member is None:
            raise ToolError("No helper called %r. Your team: %s."
                            % (spec["helper"],
                               ", ".join(m["name"] for m in team)))
    elif spec.get("model"):
        # Older callers name a model; use the member who runs it, if any.
        member = next((m for m in team if m["model"] == spec["model"]),
                      {"name": "", "model": spec["model"]})
    member = member or team[0]
    role = str(spec.get("role") or "").strip()
    if member["name"]:
        role = "%s - %s" % (member["name"], role) if role else member["name"]
    return (role or "agent", member["model"])


def spawn_agent(ctx, args):
    role, model = resolve_helper(ctx.settings, args)
    ctx.agents_deployed = getattr(ctx, "agents_deployed", 0) + 1
    return ctx.run_agent(role=role, model=model,
                         instructions=args.get("instructions", ""),
                         context=args.get("context", ""))


def spawn_agents(ctx, args):
    specs = args.get("agents") or []
    if not isinstance(specs, list) or not specs:
        raise ToolError("'agents' must be a non-empty list.")
    if len(specs) > config.MAX_HELPERS:
        raise ToolError("At most %d jobs at once." % config.MAX_HELPERS)
    resolved = []
    for spec in specs:
        if not isinstance(spec, dict):
            raise ToolError("Each entry in 'agents' must be an object.")
        role, model = resolve_helper(ctx.settings, spec)
        resolved.append({"role": role, "model": model,
                         "instructions": spec.get("instructions", ""),
                         "context": spec.get("context", "")})
    ctx.agents_deployed = getattr(ctx, "agents_deployed", 0) + len(specs)
    results = ctx.run_agents_parallel(resolved)
    return "\n\n".join("### %s (%s)\n%s" % (spec["role"], spec["model"], text)
                       for spec, text in zip(resolved, results))


HANDLERS = {
    "read_file": read_file,
    "write_file": write_file,
    "list_files": list_files,
    "run_command": run_command,
    "note": note,
    "spawn_agent": spawn_agent,
    "spawn_agents": spawn_agents,
}


def dispatch(ctx, name, args):
    """Run a tool, subject to the approval gate. Returns (text, is_error)."""
    handler = HANDLERS.get(name)
    if handler is None:
        return ("Unknown tool: %s" % name, True)
    approve = getattr(ctx, "approve", None)
    if approve is not None:
        allowed, reason = approve(name, args or {})
        if not allowed:
            return ("The operator denied this action%s. Do not retry it; "
                    "either work around it or call finish and report that you "
                    "are blocked." % (" (%s)" % reason if reason else ""), True)
    try:
        return (handler(ctx, args or {}), False)
    except ToolError as exc:
        return (str(exc), True)
    except Exception as exc:                                  # defensive
        return ("%s failed: %s" % (name, exc), True)

