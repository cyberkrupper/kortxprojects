"""Persistent settings for CollaboratorMCP.

Settings live in %APPDATA%\\CollaboratorMCP\\settings.json (or ~/.collaboratormcp
on non-Windows). API keys fall back to environment variables when not stored.
"""

import json
import os
import threading


APP_DIR_NAME = "CollaboratorMCP"


def app_dir():
    base = os.environ.get("COLLABORATORMCP_HOME")
    if base:
        d = base
    elif os.name == "nt":
        d = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                         APP_DIR_NAME)
    else:
        d = os.path.join(os.path.expanduser("~"), ".collaboratormcp")
    os.makedirs(d, exist_ok=True)
    return d


SETTINGS_PATH = os.path.join(app_dir(), "settings.json")

DEFAULTS = {
    # --- identity / roles ---------------------------------------------------
    "orchestrator_name": "Orchestrator",
    "executor_name": "Executor",
    # "external" = any MCP client drives via the MCP server.
    # "model"    = a local model plans missions into tasks itself.
    "orchestrator_mode": "external",
    "orchestrator_model": "claude-opus-5-5",
    "orchestrator_max_tasks": 8,
    "orchestrator_max_goals": 4,
    # With a local orchestrator model, it reviews every finished task and
    # may queue a revision; this caps revisions per original task.
    "orchestrator_max_revisions": 1,
    # A local orchestrator supervises as well as plans: after every task it
    # decides what happens next (accept, send back, add or drop tasks,
    # finish the mission, or hand it to the operator).
    "orchestrator_supervise": True,
    # While a task runs, check in every N executor steps and redirect or
    # stop it if it has gone off course. 0 turns check-ins off.
    "orchestrator_checkin_steps": 6,

    # Before finishing a task that changed files, the executor must deploy a
    # reviewer agent (a small model) and fix what it finds.
    "executor_agent_review": True,

    # --- credentials --------------------------------------------------------
    # "api"          - pay-per-token API keys.
    # "subscription" - drive the locally installed Claude Code / Codex CLIs,
    #                  which are already signed in to your Pro/Max or Plus/Pro
    #                  plan. No API key, no extra charge.
    "account_mode": "api",
    "anthropic_api_key": "",
    "openai_api_key": "",
    "google_api_key": "",
    "moonshot_api_key": "",
    "qwen_api_key": "",

    # --- provider endpoints (blank = the built-in default) ------------------
    "openai_base_url": "",
    "google_base_url": "",
    "moonshot_base_url": "",
    # Mainland China accounts use https://dashscope.aliyuncs.com/compatible-mode/v1
    "qwen_base_url": "",

    # --- models discovered from provider /models endpoints ------------------
    # [{id, provider, label, input_price, output_price, context, tags}, ...]
    "custom_models": [],
    "models_last_refreshed": "",

    # --- subscription CLIs --------------------------------------------------
    "cli_timeout_s": 900,
    # Extra arguments for 'claude -p'. Claude Code's built-in tools are
    # disabled unless these include --disallowed-tools; CollaboratorMCP
    # performs file and shell actions itself, behind the approval gate.
    "cli_claude_args": "",
    "cli_codex_args": "",
    # CollaboratorMCP runs the tools itself, so the CLI needs no write access.
    "cli_codex_sandbox": "read-only",

    # --- models -------------------------------------------------------------
    "executor_model": "claude-opus-5-5",
    "executor_effort": "high",           # low|medium|high|xhigh|max
    "executor_thinking": True,
    "executor_max_tokens": 16000,
    "fallback_model": "claude-opus-5",   # used on refusal
    "enable_refusal_fallback": True,
    "default_agent_model": "claude-haiku-4-5",
    "allowed_agent_models": [
        "claude-haiku-4-5", "claude-sonnet-5",
        "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o-mini", "o4-mini",
    ],
    # The helper team: up to MAX_HELPERS named members, each on its own
    # model, e.g. [{"name": "Reviewer", "model": "claude-sonnet-5"}].
    # Empty means "not set up yet": derived from the two keys above.
    "helpers": [],

    # --- execution limits ---------------------------------------------------
    "max_parallel_tasks": 2,
    "max_parallel_agents": 8,
    "max_tool_iterations": 40,
    "agent_max_tokens": 4000,
    "agent_timeout_s": 180,

    # --- budget guard -------------------------------------------------------
    "budget_enabled": True,
    "budget_usd_per_mission": 5.00,
    "budget_usd_daily": 25.00,

    # --- workspace / safety -------------------------------------------------
    "workspace": os.path.join(os.path.expanduser("~"), "CollaboratorMCP-Workspace"),
    # Most-recent-first list shown in the workspace address bar.
    "recent_workspaces": [],
    "allow_shell": False,
    "shell_timeout_s": 120,

    # --- approval gate ------------------------------------------------------
    # When auto_approve is off, the tools listed below pause and wait for an
    # explicit Approve/Deny before they run.
    "auto_approve": True,
    "approval_tools": ["write_file", "run_command", "spawn_agent",
                       "spawn_agents"],
    "approval_timeout_s": 300,
    "approval_on_timeout": "deny",   # deny | allow

    # --- hub ----------------------------------------------------------------
    "hub_host": "127.0.0.1",
    "hub_port": 8787,

    # --- executor persona ---------------------------------------------------
    "executor_system_extra": "",

    # --- ui -----------------------------------------------------------------
    "theme": "dark",
    "autoscroll": True,
}


class Settings:
    """Thread-safe settings object backed by a JSON file."""

    def __init__(self, path=None):
        self.path = path or SETTINGS_PATH
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self._listeners = []
        self.load()

    # -- persistence ---------------------------------------------------------
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                with self._lock:
                    for k, v in stored.items():
                        if k in DEFAULTS:
                            self._data[k] = v
                    # Older builds hard-coded model names as role names.
                    for k, old in (("orchestrator_name", "Astra"),
                                   ("executor_name", "Claude")):
                        if self._data.get(k) == old:
                            self._data[k] = DEFAULTS[k]
        except FileNotFoundError:
            pass
        except Exception:
            # Corrupt settings must never block startup.
            pass
        return self

    def save(self):
        with self._lock:
            snapshot = dict(self._data)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)
        for cb in list(self._listeners):
            try:
                cb(snapshot)
            except Exception:
                pass

    # -- access --------------------------------------------------------------
    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key, value):
        with self._lock:
            self._data[key] = value

    def update(self, mapping):
        with self._lock:
            for k, v in mapping.items():
                self._data[k] = v

    def as_dict(self):
        with self._lock:
            return dict(self._data)

    def on_change(self, callback):
        self._listeners.append(callback)

    # -- derived -------------------------------------------------------------
    def anthropic_key(self):
        return (self.get("anthropic_api_key")
                or os.environ.get("ANTHROPIC_API_KEY") or "").strip()

    def openai_key(self):
        return (self.get("openai_api_key")
                or os.environ.get("OPENAI_API_KEY") or "").strip()

    def google_key(self):
        return (self.get("google_api_key")
                or os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY") or "").strip()

    def moonshot_key(self):
        return (self.get("moonshot_api_key")
                or os.environ.get("MOONSHOT_API_KEY") or "").strip()

    def qwen_key(self):
        return (self.get("qwen_api_key")
                or os.environ.get("DASHSCOPE_API_KEY") or "").strip()

    def workspace_dir(self):
        d = self.get("workspace")
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            d = os.path.join(app_dir(), "workspace")
            os.makedirs(d, exist_ok=True)
        return d


# Model line-up for each account mode. Switching modes rewrites these keys so
# one control flips the whole app between paid API and subscription CLIs.
ACCOUNT_PRESETS = {
    "api": {
        "executor_model": "claude-opus-5-5",
        "orchestrator_model": "claude-opus-5-5",
        "fallback_model": "claude-opus-5",
        "default_agent_model": "claude-haiku-4-5",
        "allowed_agent_models": ["claude-haiku-4-5", "claude-sonnet-5",
                                 "gpt-4.1-mini", "gpt-4.1-nano",
                                 "gpt-4o-mini", "o4-mini"],
    },
    "subscription": {
        "executor_model": "claude-cli",
        "orchestrator_model": "claude-cli",
        "fallback_model": "claude-cli-sonnet",
        "default_agent_model": "claude-cli-haiku",
        "allowed_agent_models": ["claude-cli-haiku", "claude-cli-sonnet",
                                 "claude-cli", "codex-cli"],
    },
}


def apply_account_mode(settings_obj, mode):
    """Point every model slot at the chosen account mode. Returns the mode."""
    mode = mode if mode in ACCOUNT_PRESETS else "api"
    preset = ACCOUNT_PRESETS[mode]
    # Keep the helper team's names and roles; move members whose model
    # belongs to the other account mode onto this mode's default helper.
    team = [{"name": h["name"],
             "model": h["model"] if h["model"] in preset["allowed_agent_models"]
             else preset["default_agent_model"]}
            for h in helper_roster(settings_obj)]
    updates = dict(preset)
    updates["account_mode"] = mode
    settings_obj.update(updates)
    set_helpers(settings_obj, team, save=False)
    settings_obj.save()
    return mode


# --- helper team ------------------------------------------------------------

MAX_HELPERS = 8


def _normalise_helpers(rows):
    """Valid, uniquely named members, at most MAX_HELPERS of them."""
    out, seen = [], set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        model = str(row.get("model") or "").strip()
        if not model:
            continue
        name = " ".join(str(row.get("name") or "").split()) or \
            "Helper %d" % (len(out) + 1)
        base, n = name, 2
        while name.lower() in seen:
            name = "%s %d" % (base, n)
            n += 1
        seen.add(name.lower())
        out.append({"name": name, "model": model})
        if len(out) >= MAX_HELPERS:
            break
    return out


def helper_roster(settings_obj):
    """The helper team as [{"name", "model"}]; never empty."""
    team = _normalise_helpers(settings_obj.get("helpers"))
    if team:
        return team
    # Not set up yet: one member per model the old checklist enabled,
    # starting with the default helper.
    default = settings_obj.get("default_agent_model") or "claude-haiku-4-5"
    models = [default] + [m for m in (settings_obj.get("allowed_agent_models")
                                      or []) if m != default]
    return _normalise_helpers([{"name": "Helper %d" % (i + 1), "model": m}
                               for i, m in enumerate(models)])


def helper_models(settings_obj):
    """Distinct models the helper team uses, default helper first."""
    out = []
    for member in helper_roster(settings_obj):
        if member["model"] not in out:
            out.append(member["model"])
    return out


def find_helper(settings_obj, name):
    """The team member called ``name`` (case-insensitive), or None."""
    wanted = " ".join(str(name or "").split()).lower()
    for member in helper_roster(settings_obj):
        if member["name"].lower() == wanted:
            return member
    return None


def set_helpers(settings_obj, rows, save=True):
    """Store the helper team and keep the derived model keys in step."""
    team = _normalise_helpers(rows)
    if not team:
        raise ValueError("Keep at least one helper.")
    settings_obj.set("helpers", team)
    settings_obj.set("default_agent_model", team[0]["model"])
    settings_obj.set("allowed_agent_models", helper_models(settings_obj))
    if save:
        settings_obj.save()
    return team


_singleton = None


def settings():
    global _singleton
    if _singleton is None:
        _singleton = Settings()
    return _singleton
