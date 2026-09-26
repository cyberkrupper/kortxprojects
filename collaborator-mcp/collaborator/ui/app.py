"""CollaboratorMCP desktop UI.

Navigation stays separate from daily work. Start, results, and activity
have dedicated pages; team and advanced controls live in Settings.
"""

import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .. import api, catalog, config, connection, hub, providers
from ..config import settings as get_settings
from ..engine import Engine
from . import theme


PAGES = [
    ("command",   "Start",     "Choose a folder, describe the work, then start"),
    ("missions",  "Tasks & results", "See what is running, review results, and retry failed tasks"),
    ("terminals", "Activity",  "Follow progress and send a message while work runs"),
    ("files",     "Files",     "Browse the workspace the agents can reach"),
    ("fleet",     "Helpers",   "Choose helper models and read their responses"),
    ("usage",     "Costs",     "API spending, subscription usage, and limits"),
    ("connect",   "Connect",   "Wire your orchestrator up to this MCP server"),
    ("settings",  "Settings",  "Your team, accounts, and advanced options"),
]

EFFORTS = ["low", "medium", "high", "xhigh", "max"]


class AlreadyRunning(RuntimeError):
    """Another process owns the shared hub; do not start competing workers."""


class App(tk.Tk):
    def __init__(self):
        tk.Tk.__init__(self)
        self.settings = get_settings()
        self.title("CollaboratorMCP")
        self.geometry("%dx%d" % (min(1360, self.winfo_screenwidth() - 60),
                                     min(900, self.winfo_screenheight() - 80)))
        self.minsize(1060, 660)

        self.colours = theme.palette(self.settings.get("theme"))
        self.fonts = theme.fonts(self)
        theme.apply(self, self.colours, self.fonts)
        self._set_icon()

        self.engine = Engine(self.settings)
        self.events = queue.Queue()
        self.engine.subscribe(self.events.put)

        self.hub_server = hub.HubServer(
            self.engine,
            self.settings.get("hub_host") or "127.0.0.1",
            int(self.settings.get("hub_port") or 8787))
        self.hub_ok = self.hub_server.start()
        if not self.hub_ok:
            self.engine.shutdown()
            self.engine.store.close()
            messagebox.showinfo("Collaborator is already running",
                "Another instance is using the connection port. Use the existing "
                "Collaborator window, or close it before opening this one. "
                "No second worker queue was started.", parent=self)
            self.update_idletasks()
            self.destroy()
            raise AlreadyRunning("Hub port is already in use")
        self.engine.start()

        self._nav_buttons = {}
        self._pages = {}
        self._current = None
        self._log_filters = {}

        self._build()
        self._sync_orchestrator_ui()
        self.show_page("command")
        self._pump_events()
        self._refresh_status()
        self._poll_approvals()
        self._update_say_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if not self.hub_ok:
            self._log_line(
                "Another CollaboratorMCP instance owns port %s; the MCP "
                "bridge in this window is inactive."
                % self.settings.get("hub_port"), "warn")

    def tip(self, widget, text, delay=2000):
        """Attach a hover tooltip. ``text`` may be a callable(event)."""
        return theme.Tooltip(widget, text, delay=delay, fonts=self.fonts)

    # ==================================================================== chrome
    def _set_icon(self):
        try:
            img = tk.PhotoImage(width=64, height=64)
            accent = self.colours["accent"]
            bg = self.colours["sidebar"]
            img.put(bg, to=(0, 0, 64, 64))
            for x in range(10, 30):
                img.put(accent, to=(x, 12, x + 1, 52))
            for x in range(36, 56):
                img.put(accent, to=(x, 12, x + 1, 52))
            for y in range(30, 36):
                img.put(accent, to=(20, y, 46, y + 1))
            self.iconphoto(True, img)
            self._icon = img
        except Exception:
            pass

    def _build(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_sidebar()

        right = ttk.Frame(self, style="TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        self._build_header(right)
        self._build_address_bar(right)

        self.body = ttk.Frame(right, style="TFrame", padding=(18, 10, 18, 14))
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)

        for key, _, _ in PAGES:
            frame = ttk.Frame(self.body, style="TFrame")
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            self._pages[key] = frame
        self._build_settings(self._pages["settings"])
        self._build_command(self._pages["command"])
        self._build_activity(self._pages["terminals"])
        self._build_files(self._pages["files"])
        self._build_missions(self._pages["missions"])
        self._build_fleet(self._pages["fleet"])
        self._build_usage(self._pages["usage"])
        self._build_connect(self._pages["connect"])

    # -------------------------------------------------------------- sidebar
    def _build_sidebar(self):
        side = ttk.Frame(self, style="Sidebar.TFrame", width=210)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(2, weight=1)
        brand = ttk.Frame(side, style="Sidebar.TFrame", padding=(16, 22, 12, 20))
        brand.grid(row=0, column=0, sticky="ew")
        ttk.Label(brand, text="Collaborator", style="Brand.TLabel").pack(anchor="w")
        ttk.Label(brand, text="by KORT-X Laboratories",
                  style="BrandBy.TLabel").pack(anchor="e")
        nav =ttk.Frame(side, style="Sidebar.TFrame", padding=(10, 0))
        nav.grid(row=1, column=0, sticky="ew")
        nav.columnconfigure(0, weight=1)
        for i, (key, label, _) in enumerate(PAGES):
            btn = ttk.Button(nav, text="  " + label, style="Nav.TButton",
                             command=lambda k=key: self.show_page(k))
            btn.grid(row=i, column=0, sticky="ew", pady=3)
            self._nav_buttons[key] = btn
        foot = ttk.Frame(side, style="Sidebar.TFrame", padding=(14, 10, 14, 18))
        foot.grid(row=3, column=0, sticky="ew")
        self.btn_pause = ttk.Button(foot, text="Pause queue", command=self._toggle_pause)
        self.btn_pause.pack(fill="x", pady=(0, 12))
        self.lbl_budget = ttk.Label(foot, text="", style="Sidebar.TLabel", wraplength=178)
        self.lbl_budget.pack(anchor="w")
        self.lbl_hub = ttk.Label(foot, text="", style="Sidebar.TLabel", wraplength=178)
        self.lbl_hub.pack(anchor="w", pady=(10, 0))
        self.var_theme = tk.BooleanVar(value=self.settings.get("theme") == "dark")
        ttk.Checkbutton(foot, text="Dark appearance", variable=self.var_theme,
                        style="Sidebar.TCheckbutton", command=self._toggle_theme).pack(anchor="w", pady=(12, 0))

    def _sidebar_section(self, parent, text, row):
        ttk.Label(parent, text=text.upper(), style="SidebarHead.TLabel").grid(
            row=row, column=0, sticky="w", pady=(8, 3))

    # -- orchestrator selector helpers --------------------------------------
    def _orchestrator_choices(self):
        """(external label, all labels). Also rebuilds label -> model id.

        Any model can orchestrate. Models that can run right now come first;
        the rest are marked, so picking one without a key or CLI is visible.
        """
        external = "External MCP client (waits for %s to connect)" \
            % self.settings.get("orchestrator_name")
        ready, not_ready = [], []
        self._orch_ids = {}
        for m in _all_models():
            ok, _ = providers.check_model_ready(m.id, self.settings)
            label = "%s  ·  %s" % (m.label, m.id)
            if not ok:
                label += "  ·  not set up"
            self._orch_ids[label] = m.id
            (ready if ok else not_ready).append(label)
        return external, [external] + ready + not_ready

    def _orchestrator_label(self):
        external, _ = self._orchestrator_choices()
        if self.settings.get("orchestrator_mode") != "model":
            return external
        wanted = self.settings.get("orchestrator_model")
        for label, model_id in self._orch_ids.items():
            if model_id == wanted:
                return label
        return wanted

    def _on_orchestrator_change(self, _event=None):
        label = self.var_orchestrator.get()
        external, _ = self._orchestrator_choices()
        if label == external:
            self.settings.set("orchestrator_mode", "external")
        else:
            model_id = self._orch_ids.get(label) or label.split("  ·  ")[0]
            self.settings.set("orchestrator_mode", "model")
            # Saves, and says straight away if the model cannot run yet.
            self._pick_model("orchestrator_model", model_id, "orchestrator")
        self.settings.save()
        self._sync_orchestrator_ui()
        self._refresh_status()

    def _sync_orchestrator_ui(self):
        is_model = self.settings.get("orchestrator_mode") == "model"
        state = "normal" if is_model and not getattr(self, "_planning", False) else "disabled"
        if hasattr(self, "btn_plan"):
            self.btn_plan.configure(state=state)
        if hasattr(self, "lbl_plan_hint"):
            self.lbl_plan_hint.configure(
                text="The orchestrator will create a plan and queue its tasks for the executor." if is_model
                else "Planning is currently handled by an external MCP client such as the Codex app. "
                     "To plan here, choose an orchestrator model in Settings > Your team.")

    # -- account mode -------------------------------------------------------
    ACCOUNT_LABELS = {
        "api": "API keys  ·  pay per token",
        "subscription": "Pro / Plus plans  ·  no API key",
    }

    def _on_account_change(self, _event=None):
        label = self.var_account.get()
        mode = next((k for k, v in self.ACCOUNT_LABELS.items() if v == label),
                    "api")
        config.apply_account_mode(self.settings, mode)
        if mode == "subscription":
            missing = [name for name, ok in (
                ("Claude Code (claude)",
                 providers.credentials_status(self.settings)["claude_cli"]),
            ) if not ok]
            if missing:
                messagebox.showwarning(
                    "Subscription mode",
                    "Subscription mode drives locally installed agent CLIs.\n\n"
                    "Not found on PATH: %s\n\n"
                    "Install it and sign in with your plan, then reopen "
                    "CollaboratorMCP." % ", ".join(missing), parent=self)
        self._sync_model_widgets()
        self._refresh_status()

    def _sync_model_widgets(self):
        """Repoint the model dropdowns after an account-mode switch."""
        self.cmb_executor.configure(
            values=[m.id for m in catalog.executor_models()])
        self.var_executor.set(self.settings.get("executor_model"))
        _, choices = self._orchestrator_choices()
        self.cmb_orchestrator.configure(values=choices)
        self.var_orchestrator.set(self._orchestrator_label())
        self._render_helpers()
        if hasattr(self, "fleet_inner"):
            self._render_agent_models()
        for key in ("fallback_model",):
            if key in self._s_vars:
                self._s_vars[key][0].set(self.settings.get(key))
        self._sync_orchestrator_ui()

    def _build_team_settings(self, p):
        ttk.Label(p, text="Set up your team", style="PanelH2.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(p, text="These choices save immediately. Subscription mode uses your signed-in apps.",
                  style="PanelDim.TLabel", wraplength=580).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 20))
        def choice(row, label, variable, values, callback, width=46):
            ttk.Label(p, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 16), pady=12)
            widget = ttk.Combobox(p, textvariable=variable, values=values, state="readonly", width=width)
            widget.grid(row=row, column=1, sticky="ew" if width > 20 else "w", pady=12)
            widget.bind("<<ComboboxSelected>>", callback)
            return widget
        self.var_account = tk.StringVar(value=self.ACCOUNT_LABELS.get(self.settings.get("account_mode"), self.ACCOUNT_LABELS["api"]))
        choice(2, "How you pay", self.var_account, list(self.ACCOUNT_LABELS.values()), self._on_account_change)
        _, choices = self._orchestrator_choices()
        self.var_orchestrator = tk.StringVar(value=self._orchestrator_label())
        self.cmb_orchestrator = choice(3, "Orchestrator", self.var_orchestrator, choices, self._on_orchestrator_change)
        self.var_executor = tk.StringVar(value=self.settings.get("executor_model"))
        self.cmb_executor = choice(4, "Task executor", self.var_executor, [m.id for m in catalog.executor_models()],
                                  lambda e: self._pick_model("executor_model", self.var_executor.get(), "executor"))
        self.var_effort = tk.StringVar(value=self.settings.get("executor_effort"))
        choice(5, "Reasoning effort", self.var_effort, EFFORTS,
               lambda e: self._quick_set("executor_effort", self.var_effort.get()), width=12)

        # How closely a local orchestrator supervises.
        self.var_supervise = tk.BooleanVar(value=bool(self.settings.get("orchestrator_supervise")))
        ttk.Checkbutton(p, text="Orchestrator supervises: after every task it decides what happens next - "
                                "accept or send back, add or drop tasks, finish the mission, or pause it for you",
                        variable=self.var_supervise, style="Panel.TCheckbutton",
                        command=lambda: self._quick_set("orchestrator_supervise", bool(self.var_supervise.get()))
                        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 4))
        self.var_checkin = tk.StringVar(value=_checkin_label(self.settings.get("orchestrator_checkin_steps")))
        choice(7, "Check in on running tasks", self.var_checkin, list(CHECKIN_CHOICES),
               lambda e: self._quick_set("orchestrator_checkin_steps", CHECKIN_CHOICES[self.var_checkin.get()]),
               width=24)
        ttk.Label(p, text="Supervision and check-ins apply when the orchestrator is a model here, not an external "
                         "MCP client. Each check-in is one short orchestrator call.",
                  style="PanelDim.TLabel", wraplength=580).grid(row=8, column=0, columnspan=2, sticky="w")

        # The helper team: up to eight named members, each on its own model.
        head = ttk.Frame(p, style="Panel.TFrame")
        head.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(24, 4))
        head.columnconfigure(0, weight=1)
        self.lbl_helpers = ttk.Label(head, text="Helper team", style="PanelH2.TLabel")
        self.lbl_helpers.grid(row=0, column=0, sticky="w")
        self.btn_add_helper = ttk.Button(head, text="Add helper", command=self._add_helper)
        self.btn_add_helper.grid(row=0, column=1, sticky="e")
        ttk.Label(p, text="The executor hands sub-tasks to these by name, in parallel when work splits. "
                         "The first helper is the default. Any model can be a helper.",
                  style="PanelDim.TLabel", wraplength=580).grid(row=10, column=0, columnspan=2, sticky="w")
        self.helpers_box = ttk.Frame(p, style="Panel.TFrame")
        self.helpers_box.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.helpers_box.columnconfigure(1, weight=1)
        self.lbl_helpers_warn = ttk.Label(p, text="", style="PanelDim.TLabel", wraplength=580, justify="left")
        self.lbl_helpers_warn.grid(row=12, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._helper_rows = []
        self._render_helpers()

        ttk.Label(p, text="Need the orchestrator in the Codex app? Open Connect and choose Connect Codex, "
                         "then set the orchestrator to External MCP client.",
                  style="PanelDim.TLabel", wraplength=580).grid(row=13, column=0, columnspan=2, sticky="w", pady=(20, 0))

    # -- helper team ----------------------------------------------------------
    def _render_helpers(self):
        """Draw one editable row per helper from the saved team."""
        if not hasattr(self, "helpers_box"):
            return
        for child in self.helpers_box.winfo_children():
            child.destroy()
        self._helper_rows = []
        models = [m.id for m in _all_models()]
        for index, member in enumerate(config.helper_roster(self.settings)):
            name = tk.StringVar(value=member["name"])
            model = tk.StringVar(value=member["model"])
            ttk.Label(self.helpers_box, text="%d." % (index + 1), style="PanelDim.TLabel").grid(
                row=index, column=0, sticky="w", padx=(0, 8), pady=3)
            entry = ttk.Entry(self.helpers_box, textvariable=name, width=20)
            entry.grid(row=index, column=1, sticky="ew", pady=3)
            entry.bind("<FocusOut>", lambda e: self._save_helpers())
            entry.bind("<Return>", lambda e: self._save_helpers())
            box = ttk.Combobox(self.helpers_box, textvariable=model, values=models,
                               state="readonly", width=30)
            box.grid(row=index, column=2, sticky="ew", padx=(8, 8), pady=3)
            box.bind("<<ComboboxSelected>>", lambda e: self._save_helpers())
            ttk.Button(self.helpers_box, text="Remove",
                       command=lambda i=index: self._remove_helper(i)).grid(row=index, column=3, pady=3)
            self._helper_rows.append((name, model))
        self._refresh_helper_state()

    def _refresh_helper_state(self):
        team = config.helper_roster(self.settings)
        self.lbl_helpers.configure(text="Helper team  (%d of %d)" % (len(team), config.MAX_HELPERS))
        self.btn_add_helper.configure(state="normal" if len(team) < config.MAX_HELPERS else "disabled")
        problems = []
        for member in team:
            ok, why = providers.check_model_ready(member["model"], self.settings)
            if not ok:
                problems.append("%s (%s): %s" % (member["name"], member["model"], why))
        self.lbl_helpers_warn.configure(
            text=("Not able to run yet - " + "; ".join(problems)) if problems else "")

    def _save_helpers(self, rows=None):
        if rows is None:
            rows = [{"name": name.get(), "model": model.get()} for name, model in self._helper_rows]
        try:
            team = config.set_helpers(self.settings, rows)
        except ValueError as exc:
            messagebox.showwarning("Helper team", str(exc), parent=self)
            self._render_helpers()
            return
        if len(team) == len(self._helper_rows):
            # Show de-duplicated names without rebuilding under the cursor.
            for (name, _), member in zip(self._helper_rows, team):
                if name.get() != member["name"]:
                    name.set(member["name"])
        else:
            self._render_helpers()
        self._refresh_helper_state()
        if hasattr(self, "fleet_inner"):
            self._render_agent_models()
        self._refresh_status()

    def _add_helper(self):
        rows = [{"name": name.get(), "model": model.get()} for name, model in self._helper_rows]
        if len(rows) >= config.MAX_HELPERS:
            return
        rows.append({"name": "Helper %d" % (len(rows) + 1),
                     "model": rows[0]["model"] if rows else self.settings.get("default_agent_model")})
        self._save_helpers(rows)

    def _remove_helper(self, index):
        rows = [{"name": name.get(), "model": model.get()} for name, model in self._helper_rows]
        if len(rows) <= 1:
            messagebox.showinfo("Helper team", "Keep at least one helper.", parent=self)
            return
        del rows[index]
        self._save_helpers(rows)

    # --------------------------------------------------------------- header
    def _build_header(self, parent):
        head = ttk.Frame(parent, padding=(22, 20, 22, 12))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.lbl_title = ttk.Label(head, text="Start", style="H1.TLabel")
        self.lbl_title.grid(row=0, column=0, sticky="w")
        self.lbl_sub = ttk.Label(head, text="", style="Dim.TLabel", wraplength=570)
        self.lbl_sub.grid(row=1, column=0, sticky="w", pady=(4, 0))
        chips = ttk.Frame(head)
        chips.grid(row=2, column=0, sticky="w", pady=(12, 0))
        self.chip_state = ttk.Label(chips, text="", style="Chip.TLabel")
        self.chip_state.pack(side="left", padx=(0, 8))
        self.chip_queue = ttk.Label(chips, text="", style="Chip.TLabel")
        self.chip_queue.pack(side="left", padx=(0, 8))
        self.chip_orchestrator = ttk.Label(chips, text="", style="Chip.TLabel")
        self.chip_orchestrator.pack(side="left")
        # Details belong on Start, rather than overflowing every page's header.
        self.chip_spend = ttk.Label(head)
        self.chip_keys = ttk.Label(head)
        self.chip_creds = ttk.Label(head)

    # ---------------------------------------------------------- address bar
    def _build_address_bar(self, parent):
        c = self.colours
        bar = ttk.Frame(parent, style="Card.TFrame", padding=(12, 8))
        bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(2, 0))
        bar.columnconfigure(1, weight=1)

        ttk.Label(bar, text="Workspace", style="PanelDim.TLabel").grid(
            row=0, column=0, padx=(0, 10))

        self.var_workspace = tk.StringVar(value=self.settings.get("workspace"))
        self.cmb_workspace = ttk.Combobox(
            bar, textvariable=self.var_workspace,
            values=self.settings.get("recent_workspaces") or [],
            font=self.fonts["mono"])
        self.cmb_workspace.grid(row=0, column=1, sticky="ew")
        self.cmb_workspace.bind("<Return>", lambda e: self._apply_workspace())
        self.cmb_workspace.bind("<<ComboboxSelected>>",
                                lambda e: self._apply_workspace())

        btns = ttk.Frame(bar, style="Panel.TFrame")
        btns.grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Button(btns, text="Choose folder...",
                   command=self._browse_workspace).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Use folder", style="Accent.TButton",
                   command=self._apply_workspace).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="Show files",
                   command=self._open_workspace).pack(side="left")

        self.lbl_workspace_info = ttk.Label(bar, text="",
                                            style="PanelDim.TLabel")
        self.lbl_workspace_info.grid(row=1, column=1, columnspan=2, sticky="w",
                                     pady=(4, 0))
        self._refresh_workspace_info()

    def _refresh_workspace_info(self):
        info = self.engine.workspace_info()
        if not info["exists"]:
            text = "Directory does not exist - choose a folder or enter a path, then Use folder."
        else:
            bits = ["%d item(s)" % info["entries"]]
            if info["is_git"]:
                bits.append("git repo" + (" on %s" % info["branch"]
                                          if info["branch"] else ""))
            if not info["writable"]:
                bits.append("READ-ONLY - the executor cannot write here")
            bits.append("new missions use this folder; existing missions keep theirs")
            text = "  ·  ".join(bits)
        self.lbl_workspace_info.configure(text=text)
        self.cmb_workspace.configure(
            values=self.settings.get("recent_workspaces") or [])

    def _browse_workspace(self):
        start = self.settings.get("workspace")
        if not os.path.isdir(start):
            start = os.path.expanduser("~")
        chosen = filedialog.askdirectory(
            parent=self, initialdir=start,
            title="Choose the repository or folder the agents may use")
        if chosen:
            self.var_workspace.set(os.path.normpath(chosen))
            self._apply_workspace()

    def _apply_workspace(self):
        path = self.var_workspace.get().strip().strip('"')
        if not path:
            return
        if os.path.normcase(os.path.abspath(os.path.expanduser(path))) == \
                os.path.normcase(self.settings.get("workspace")):
            self._refresh_workspace_info()
            return
        create = False
        if not os.path.isdir(os.path.expanduser(path)):
            if not messagebox.askyesno(
                    "Workspace",
                    "%s does not exist.\n\nCreate it?" % path, parent=self):
                self.var_workspace.set(self.settings.get("workspace"))
                return
            create = True
        active = self.engine.active_tasks()
        if active and not messagebox.askyesno(
                "Workspace",
                "%d task(s) are running. Existing missions keep their "
                "original folder; new missions use the folder you choose.\n\nSwitch anyway?"
                % len(active), parent=self):
            self.var_workspace.set(self.settings.get("workspace"))
            return
        try:
            self.engine.set_workspace(path, create=create)
        except Exception as exc:
            messagebox.showerror("Workspace", str(exc), parent=self)
            self.var_workspace.set(self.settings.get("workspace"))
            return
        self.var_workspace.set(self.settings.get("workspace"))
        self._sync_workspace_field()
        self._refresh_workspace_info()
        if hasattr(self, "files_tree"):
            self._files_go("")

    # ================================================================= pages
    def show_page(self, key):
        for name, btn in self._nav_buttons.items():
            btn.configure(style="NavActive.TButton" if name == key
                          else "Nav.TButton")
        self._pages[key].tkraise()
        self._current = key
        for k, label, sub in PAGES:
            if k == key:
                self.lbl_title.configure(text=label)
                self.lbl_sub.configure(text=sub)
        if key == "missions":
            self.refresh_missions()
        elif key == "fleet":
            self.refresh_fleet()
        elif key == "usage":
            self.refresh_usage()
        elif key == "files":
            self._files_go(getattr(self, "_files_rel", ""))
        elif key == "terminals":
            self._refresh_orchestrator_panel()

    # ---- Command -----------------------------------------------------------
    def _build_command(self, p):
        p.rowconfigure(1, weight=1)
        summary = ttk.Frame(p, style="Card.TFrame", padding=16)
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        summary.columnconfigure(0, weight=1)
        self.lbl_team_summary = ttk.Label(summary, text="", style="Panel.TLabel", wraplength=650, justify="left")
        self.lbl_team_summary.grid(row=0, column=0, sticky="w")
        ttk.Button(summary, text="Change team", command=self._open_team).grid(row=0, column=1, padx=(14, 0))
        nb = ttk.Notebook(p)
        nb.grid(row=1, column=0, sticky="nsew")
        plan = ttk.Frame(nb, style="Card.TFrame", padding=18)
        plan.columnconfigure(1, weight=1)
        nb.add(plan, text="  Orchestrator  ")
        card = ttk.Frame(nb, style="Card.TFrame", padding=18)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(3, weight=1)
        nb.add(card, text="  Executor  ")
        self._build_planner(plan)
        self._build_approvals(p)
        self.approvals_card.grid_configure(row=2)
        self.approvals_card.grid_remove()
        ttk.Label(card, text="What would you like done?", style="PanelH2.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, text="Describe the result you want. The executor will work in the folder above.",
                  style="PanelDim.TLabel", wraplength=680).grid(row=1, column=0, sticky="w", pady=(4, 12))
        self.var_task_title = tk.StringVar()
        title = ttk.Frame(card, style="Panel.TFrame")
        title.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        title.columnconfigure(1, weight=1)
        ttk.Label(title, text="Short title (optional)", style="PanelDim.TLabel").grid(row=0, column=0, padx=(0, 12))
        ttk.Entry(title, textvariable=self.var_task_title).grid(row=0, column=1, sticky="ew")
        self.txt_instructions = self._text(card, height=7)
        self.txt_instructions.grid(row=3, column=0, sticky="nsew")
        extra = ttk.Frame(card, style="Panel.TFrame")
        extra.columnconfigure(1, weight=1)
        self.var_mission = tk.StringVar(value="(new mission)")
        ttk.Label(extra, text="Add to project", style="PanelDim.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self.cmb_mission = ttk.Combobox(extra, textvariable=self.var_mission, state="readonly", values=["(new mission)"])
        self.cmb_mission.grid(row=0, column=1, sticky="ew")
        ttk.Label(extra, text="Extra context", style="PanelDim.TLabel").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self.txt_context = self._text(extra, height=3)
        self.txt_context.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        self.var_task_details = tk.BooleanVar(value=False)
        def toggle_details():
            if self.var_task_details.get():
                extra.grid(row=5, column=0, sticky="ew", pady=(8, 0))
            else:
                extra.grid_remove()
        ttk.Checkbutton(card, text="Add context or choose an existing project", variable=self.var_task_details,
                        style="Panel.TCheckbutton", command=toggle_details).grid(row=4, column=0, sticky="w", pady=(10, 0))
        actions = ttk.Frame(card, style="Panel.TFrame")
        actions.grid(row=6, column=0, sticky="ew", pady=(16, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Clear", command=self._clear_composer).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(actions, text="Start task", style="Accent.TButton", command=self._delegate_clicked).grid(row=0, column=2)
        self._reload_mission_choices()

    def _build_activity(self, parent):
        c = self.colours
        parent.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(parent)
        notebook.grid(row=0, column=0, sticky="nsew")
        p = ttk.Frame(notebook)
        p.columnconfigure(0, weight=1)
        p.rowconfigure(0, weight=1)
        notebook.add(p, text="  Progress & messages  ")
        details = ttk.Frame(notebook)
        details.columnconfigure(0, weight=1)
        notebook.add(details, text="  Technical output  ")
        self._build_terminals(details)
        log_card = ttk.Frame(p, style="Card.TFrame", padding=(14, 12))
        log_card.grid(row=0, column=0, sticky="nsew")
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(2, weight=1)

        header = ttk.Frame(log_card, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Activity", style="PanelH2.TLabel").grid(
            row=0, column=0, sticky="w")
        right = ttk.Frame(header, style="Panel.TFrame")
        right.grid(row=0, column=2, sticky="e")
        self.var_autoscroll = tk.BooleanVar(
            value=bool(self.settings.get("autoscroll")))
        ttk.Checkbutton(right, text="Auto-scroll", variable=self.var_autoscroll,
                        style="Panel.TCheckbutton",
                        command=lambda: self._quick_set(
                            "autoscroll", self.var_autoscroll.get())).pack(
            side="left", padx=(0, 10))
        ttk.Button(right, text="Clear", command=self._clear_log).pack(side="left")

        filters = ttk.Frame(log_card, style="Panel.TFrame")
        filters.grid(row=1, column=0, sticky="w", pady=(6, 6))
        for index, kind in enumerate(("orchestrator", "message", "tool", "agent", "approval",
                                      "steer", "note", "task", "error")):
            var = tk.BooleanVar(value=True)
            self._log_filters[kind] = var
            ttk.Checkbutton(filters, text=kind, variable=var,
                            style="Panel.TCheckbutton").grid(row=index // 5, column=index % 5, sticky="w", padx=(0, 14), pady=2)

        wrap = ttk.Frame(log_card, style="Panel.TFrame")
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.log = tk.Text(wrap, width=1, wrap="word", bd=0, highlightthickness=0,
                           bg=c["panel"], fg=c["fg"], font=self.fonts["mono"],
                           padx=8, pady=6, state="disabled",
                           insertbackground=c["fg"])
        self.log.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=bar.set)
        for kind, key in theme.KIND_COLOUR.items():
            self.log.tag_configure(kind, foreground=c[key])
        self.log.tag_configure("ts", foreground=c["fg_faint"])
        self.log.tag_configure("actor", foreground=c["accent"])
        self.log.tag_configure("warn", foreground=c["warn"])

        # Talk to the executor while it is working.
        say = ttk.Frame(log_card, style="Panel.TFrame")
        say.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        say.columnconfigure(1, weight=1)
        ttk.Label(say, text="Message the executor", style="PanelDim.TLabel").grid(
            row=0, column=0, padx=(0, 10))
        self.var_say = tk.StringVar()
        entry = ttk.Entry(say, textvariable=self.var_say)
        entry.grid(row=0, column=1, sticky="ew")
        entry.bind("<Return>", lambda e: self._say_clicked())
        self.btn_say = ttk.Button(say, text="Send", command=self._say_clicked)
        self.btn_say.grid(row=0, column=2, padx=(8, 0))
        self.lbl_say = ttk.Label(say, text="", style="PanelDim.TLabel")
        self.lbl_say.grid(row=1, column=1, sticky="w", pady=(3, 0))

        self._load_recent_events()

    def _build_planner(self, p):
        ttk.Label(p, text="Goal", style="PanelDim.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=3)
        self.var_plan_title = tk.StringVar()
        ttk.Entry(p, textvariable=self.var_plan_title).grid(
            row=1, column=1, sticky="ew", pady=3)

        ttk.Label(p, text="Details", style="PanelDim.TLabel").grid(
            row=2, column=0, sticky="nw", padx=(0, 10), pady=3)
        self.txt_plan_brief = self._text(p, height=7)
        self.txt_plan_brief.grid(row=2, column=1, sticky="ew", pady=3)

        ttk.Label(p, text="Max tasks", style="PanelDim.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10), pady=3)
        self.var_plan_max = tk.StringVar(
            value=str(self.settings.get("orchestrator_max_tasks")))
        ttk.Entry(p, textvariable=self.var_plan_max, width=6).grid(
            row=3, column=1, sticky="w", pady=3)

        self.lbl_plan_hint = ttk.Label(p, text="", style="PanelDim.TLabel",
                                       wraplength=900, justify="left")
        self.lbl_plan_hint.grid(row=4, column=1, sticky="w", pady=(6, 0))

        row = ttk.Frame(p, style="Panel.TFrame")
        row.grid(row=5, column=1, sticky="e", pady=(10, 0))
        self.btn_plan = ttk.Button(row, text="Plan and start work",
                                   style="Accent.TButton",
                                   command=self._plan_clicked)
        self.btn_plan.pack(side="left")

    def _build_approvals(self, p):
        c = self.colours
        self.approvals_card = ttk.Frame(p, style="Card.TFrame",
                                        padding=(14, 10))
        self.approvals_card.columnconfigure(0, weight=1)
        head = ttk.Frame(self.approvals_card, style="Panel.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.lbl_approvals = ttk.Label(head, text="Waiting for approval",
                                       style="PanelH2.TLabel")
        self.lbl_approvals.grid(row=0, column=0, sticky="w")
        btns = ttk.Frame(head, style="Panel.TFrame")
        btns.grid(row=0, column=1, sticky="e")
        ttk.Button(btns, text="Deny all", style="Danger.TButton",
                   command=lambda: self._resolve_all(False)).pack(
            side="left", padx=(0, 8))
        ttk.Button(btns, text="Approve all", style="Accent.TButton",
                   command=lambda: self._resolve_all(True)).pack(side="left")
        self.approvals_body = ttk.Frame(self.approvals_card,
                                        style="Panel.TFrame")
        self.approvals_body.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.approvals_body.columnconfigure(0, weight=1)
        self.approvals_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.approvals_card.grid_remove()
        self._approval_rows = {}

    def _refresh_approvals(self):
        pending = self.engine.list_approvals()
        current = {p["approval_id"] for p in pending}
        for aid, widget in list(self._approval_rows.items()):
            if aid not in current:
                widget.destroy()
                self._approval_rows.pop(aid, None)
        for i, item in enumerate(pending):
            aid = item["approval_id"]
            if aid in self._approval_rows:
                continue
            row = ttk.Frame(self.approvals_body, style="Panel.TFrame")
            row.grid(row=i, column=0, sticky="ew", pady=2)
            row.columnconfigure(0, weight=1)
            ttk.Label(row, text=item["summary"], style="Panel.TLabel",
                      wraplength=680, justify="left").grid(
                row=0, column=0, sticky="w")
            ttk.Button(row, text="Deny", style="Danger.TButton",
                       command=lambda a=aid: self._resolve_one(a, False)).grid(
                row=0, column=1, padx=(8, 4))
            ttk.Button(row, text="Approve", style="Accent.TButton",
                       command=lambda a=aid: self._resolve_one(a, True)).grid(
                row=0, column=2)
            self._approval_rows[aid] = row
        if pending:
            self.lbl_approvals.configure(
                text="Waiting for approval (%d)" % len(pending))
            self.approvals_card.grid()
        else:
            self.approvals_card.grid_remove()

    def _resolve_one(self, approval_id, approve):
        self.engine.resolve_approval(approval_id, approve,
                                     "" if approve else "denied by operator")
        self._refresh_approvals()

    def _resolve_all(self, approve):
        self.engine.resolve_all_approvals(
            approve, "" if approve else "denied by operator")
        self._refresh_approvals()

    def _sync_workspace_field(self):
        """Show the current workspace in Settings without marking it edited."""
        if "workspace" in self._s_vars:
            var = self._s_vars["workspace"][0]
            var.set(self.settings.get("workspace"))
            self._s_base["workspace"] = var.get()

    def _plan_clicked(self):
        if getattr(self, "_planning", False):
            return
        if self.settings.get("orchestrator_mode") != "model":
            messagebox.showinfo(
                "Orchestrator",
                "Local planning is off. Choose an orchestrator model in Settings > Your team.", parent=self)
            return
        title = self.var_plan_title.get().strip()
        brief = self.txt_plan_brief.get("1.0", "end").strip()
        if not brief:
            messagebox.showwarning("Plan", "Write a brief to plan from.",
                                   parent=self)
            return
        if not self._model_ready(self.settings.get("orchestrator_model")):
            return
        try:
            max_tasks = int(self.var_plan_max.get() or 8)
        except ValueError:
            max_tasks = 8
        self._planning = True
        self.btn_plan.configure(state="disabled", text="Orchestrator is planning...")

        def work():
            try:
                # The workspace in the address bar is the one repository.
                result = self.engine.plan_mission(
                    title=title or brief[:60], brief=brief,
                    max_tasks=max_tasks)
                self.events.put({"kind": "plan_result", "result": result, "error": None})
            except Exception as exc:
                self.events.put({"kind": "plan_result", "result": None, "error": exc})

        threading.Thread(target=work, name="planner", daemon=True).start()

    def _plan_done(self, result, error):
        self._planning = False
        self.btn_plan.configure(state="normal", text="Plan and start work")
        self._sync_orchestrator_ui()
        if error is not None:
            messagebox.showerror("Plan", str(error), parent=self)
            return
        if result.get("error"):
            messagebox.showwarning("Plan", result["error"], parent=self)
            return
        self.var_plan_title.set("")
        self.txt_plan_brief.delete("1.0", "end")
        self._reload_mission_choices()
        self.refresh_missions()
        self.var_workspace.set(self.settings.get("workspace"))
        self._sync_workspace_field()
        self._refresh_workspace_info()
        goals = result.get("goals") or []
        lines = ["Planned %d goal(s), %d task(s):"
                 % (len(goals), result.get("task_count", 0))]
        for goal in goals:
            lines.append("  %s" % goal["title"])
            for task in goal.get("tasks") or []:
                lines.append("      %s" % task["title"])
        lines.append("")
        lines.append("See the Missions page to add goals or tasks of your "
                     "own.")
        messagebox.showinfo("Plan", "\n".join(lines), parent=self)
        self.show_page("missions")

    def _text(self, parent, height=4):
        c = self.colours
        return tk.Text(parent, height=height, width=1, wrap="word", bd=0,
                       highlightthickness=1,
                       highlightbackground=c["border"],
                       highlightcolor=c["accent"],
                       bg=c["input"], fg=c["fg"], insertbackground=c["fg"],
                       font=self.fonts["base"], padx=8, pady=6)

    # ---- Terminals ---------------------------------------------------------
    TERM_COLOURS = {"cmd": "accent", "stdin": "fg_faint", "out": "fg",
                    "err": "err", "exit": "ok"}

    def _build_terminals(self, p):
        p.columnconfigure(0, weight=1)
        p.rowconfigure(1, weight=3)      # executor terminal
        p.rowconfigure(2, weight=2)      # agent terminals

        bar = ttk.Frame(p, style="TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar,
                  text="Every CLI call runs here instead of in a popup "
                       "window. Full screen expands one terminal over the "
                       "others.",
                  style="Dim.TLabel", wraplength=560).grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Clear all",
                   command=self._terminals_clear).grid(row=0, column=1,
                                                       sticky="e")

        self.term_panes = {}
        self._term_full = None

        self.exec_host = ttk.Frame(p, style="TFrame")
        self.exec_host.grid(row=1, column=0, sticky="nsew")
        self.exec_host.columnconfigure(0, weight=1)
        self.exec_host.rowconfigure(0, weight=1)
        self._make_terminal("executor", self.settings.get("executor_name"),
                            self.exec_host, 0, 0, big=True)
        self._make_terminal("orchestrator",
                            self.settings.get("orchestrator_name"),
                            self.exec_host, 0, 1, big=True)
        self.exec_host.columnconfigure(1, weight=1)

        self.agent_host = ttk.Frame(p, style="TFrame")
        self.agent_host.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self.agent_host.rowconfigure(0, weight=1)
        self.lbl_no_agents = ttk.Label(
            self.agent_host,
            text="Agent terminals appear here when the executor deploys one.",
            style="Dim.TLabel")
        self.lbl_no_agents.grid(row=0, column=0, sticky="w")

        for entry in self.engine.console_lines(limit=400):
            self._term_write(entry["channel"], entry["kind"], entry["text"],
                             entry["ts"])

    def _make_terminal(self, channel, label, host, row, column, big=False):
        c = self.colours
        frame = ttk.Frame(host, style="Card.TFrame", padding=(8, 6))
        frame.grid(row=row, column=column, sticky="nsew",
                   padx=(0, 6) if column == 0 else (6, 0))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        head = ttk.Frame(frame, style="Panel.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        title = ttk.Label(head, text=label, wraplength=180 if big else 100, style="PanelH2.TLabel"
                          if big else "Panel.TLabel")
        title.grid(row=0, column=0, sticky="w")
        ttk.Button(head, text="Expand",
                   command=lambda ch=channel: self._term_fullscreen(ch)).grid(
            row=0, column=1, sticky="e")

        text = tk.Text(frame, width=1, wrap="word", bd=0, highlightthickness=0,
                       bg=c["input"], fg=c["fg"],
                       font=self.fonts["mono"], padx=8, pady=6,
                       state="disabled", height=14 if big else 8)
        text.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        bar.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        text.configure(yscrollcommand=bar.set)
        for kind, key in self.TERM_COLOURS.items():
            text.tag_configure(kind, foreground=c[key])
        text.tag_configure("ts", foreground=c["fg_faint"])

        self.term_panes[channel] = {"frame": frame, "text": text,
                                    "title": title, "host": host,
                                    "row": row, "column": column,
                                    "label": label}
        return self.term_panes[channel]

    def _ensure_agent_terminal(self, channel, label):
        if channel in self.term_panes:
            return self.term_panes[channel]
        self.lbl_no_agents.grid_remove()
        # Keep at most four agent terminals side by side; recycle the oldest.
        agents = [ch for ch in self.term_panes if ch.startswith("agent:")]
        if len(agents) >= 4:
            oldest = agents[0]
            self.term_panes[oldest]["frame"].destroy()
            del self.term_panes[oldest]
            agents = agents[1:]
        for column, existing in enumerate(agents):
            pane = self.term_panes[existing]
            pane["column"] = column
            pane["frame"].grid_configure(column=column)
        column = len(agents)
        self.agent_host.columnconfigure(column, weight=1)
        return self._make_terminal(channel, label, self.agent_host, 0, column)

    def _term_write(self, channel, kind, text, ts=None):
        if not text:
            return
        pane = self.term_panes.get(channel)
        if pane is None:
            if not channel.startswith("agent:"):
                return
            pane = self._ensure_agent_terminal(
                channel, self.engine.channel_label(channel))
        widget = pane["text"]
        widget.configure(state="normal")
        stamp = time.strftime("%H:%M:%S", time.localtime(ts or time.time()))
        widget.insert("end", stamp + "  ", ("ts",))
        widget.insert("end", str(text).rstrip() + "\n", (kind,))
        # Keep each terminal bounded so a long run cannot grow without limit.
        if int(widget.index("end-1c").split(".")[0]) > 1200:
            widget.delete("1.0", "400.0")
        widget.configure(state="disabled")
        widget.see("end")

    def _term_fullscreen(self, channel):
        if self._term_full == channel:
            # Restore the normal layout.
            for ch, pane in self.term_panes.items():
                pane["frame"].grid(row=pane["row"], column=pane["column"],
                                   sticky="nsew",
                                   padx=(0, 6) if pane["column"] == 0
                                   else (6, 0))
            self.agent_host.grid()
            self.exec_host.grid()
            self._term_full = None
            return
        for ch, pane in self.term_panes.items():
            if ch != channel:
                pane["frame"].grid_remove()
        pane = self.term_panes[channel]
        if pane["host"] is self.agent_host:
            self.exec_host.grid_remove()
        else:
            self.agent_host.grid_remove()
        pane["frame"].grid(row=0, column=0, sticky="nsew", padx=0)
        pane["host"].grid()
        self._term_full = channel

    def _terminals_clear(self):
        self.engine.clear_console()
        for pane in self.term_panes.values():
            pane["text"].configure(state="normal")
            pane["text"].delete("1.0", "end")
            pane["text"].configure(state="disabled")

    # ---- Files -------------------------------------------------------------
    def _build_files(self, p):
        c = self.colours
        p.rowconfigure(0, weight=1)
        p.columnconfigure(0, weight=3)
        p.columnconfigure(1, weight=4)

        left = ttk.Frame(p, style="Card.TFrame", padding=(12, 10))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        nav = ttk.Frame(left, style="Panel.TFrame")
        nav.grid(row=0, column=0, columnspan=2, sticky="ew")
        nav.columnconfigure(1, weight=1)
        ttk.Button(nav, text="Up", command=self._files_up).grid(
            row=0, column=0, padx=(0, 8))
        self.lbl_files_path = ttk.Label(nav, text="/", style="Panel.TLabel",
                                        font=self.fonts["mono"])
        self.lbl_files_path.grid(row=0, column=1, sticky="w")
        ttk.Button(nav, text="Refresh",
                   command=lambda: self._files_go(self._files_rel)).grid(
            row=0, column=2)

        self.var_hidden = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Show hidden files",
                        variable=self.var_hidden,
                        style="Panel.TCheckbutton",
                        command=lambda: self._files_go(self._files_rel)).grid(
            row=1, column=0, sticky="w", pady=(6, 6))

        cols = ("size", "modified")
        self.files_tree = ttk.Treeview(left, columns=cols,
                                       show="tree headings", selectmode="browse")
        self.files_tree.heading("#0", text="Name")
        self.files_tree.heading("size", text="Size")
        self.files_tree.heading("modified", text="Modified")
        self.files_tree.column("#0", width=260, anchor="w")
        self.files_tree.column("size", width=90, anchor="e")
        self.files_tree.column("modified", width=130, anchor="w")
        self.files_tree.grid(row=2, column=0, sticky="nsew")
        fbar = ttk.Scrollbar(left, orient="vertical",
                             command=self.files_tree.yview)
        fbar.grid(row=2, column=1, sticky="ns")
        self.files_tree.configure(yscrollcommand=fbar.set)
        self.files_tree.bind("<Double-1>", self._files_open)
        self.files_tree.bind("<<TreeviewSelect>>", self._files_preview)
        self.files_tree.tag_configure("dir", foreground=c["accent"])

        right = ttk.Frame(p, style="Card.TFrame", padding=(14, 12))
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)
        self.lbl_preview = ttk.Label(right, text="Select a file",
                                     style="PanelH2.TLabel")
        self.lbl_preview.grid(row=0, column=0, sticky="w", pady=(0, 6))
        wrap = ttk.Frame(right, style="Panel.TFrame")
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.preview = tk.Text(wrap, wrap="none", bd=0, highlightthickness=0,
                               bg=c["panel"], fg=c["fg"],
                               font=self.fonts["mono"], padx=8, pady=6,
                               state="disabled")
        self.preview.grid(row=0, column=0, sticky="nsew")
        pbar = ttk.Scrollbar(wrap, orient="vertical",
                             command=self.preview.yview)
        pbar.grid(row=0, column=1, sticky="ns")
        self.preview.configure(yscrollcommand=pbar.set)

        acts = ttk.Frame(right, style="Panel.TFrame")
        acts.grid(row=2, column=0, sticky="e", pady=(8, 0))
        ttk.Button(acts, text="Use as workspace",
                   command=self._files_use_as_workspace).pack(side="left",
                                                              padx=(0, 8))
        ttk.Button(acts, text="Copy path",
                   command=self._files_copy_path).pack(side="left")

        self._files_rel = ""
        self._files_go("")

    def _files_go(self, relative):
        try:
            data = self.engine.browse(relative, self.var_hidden.get())
        except Exception as exc:
            self.files_tree.delete(*self.files_tree.get_children())
            self.lbl_files_path.configure(text=str(exc))
            return
        self._files_rel = "" if data["relative"] == "." else data["relative"]
        self.lbl_files_path.configure(text="/" + self._files_rel)
        self.files_tree.delete(*self.files_tree.get_children())
        for entry in data["entries"]:
            is_dir = entry["type"] == "dir"
            self.files_tree.insert(
                "", "end", iid=entry["path"],
                text=("[  ] " if is_dir else "     ") + entry["name"],
                values=("" if is_dir else _human_size(entry["size"]),
                        time.strftime("%Y-%m-%d %H:%M",
                                      time.localtime(entry["modified"]))
                        if entry["modified"] else ""),
                tags=("dir",) if is_dir else ())

    def _files_up(self):
        if not self._files_rel:
            return
        parent = os.path.dirname(self._files_rel.replace("/", os.sep))
        self._files_go(parent.replace(os.sep, "/"))

    def _files_open(self, _event=None):
        sel = self.files_tree.selection()
        if not sel:
            return
        full = os.path.join(self.settings.workspace_dir(),
                            sel[0].replace("/", os.sep))
        if os.path.isdir(full):
            self._files_go(sel[0])

    def _files_preview(self, _event=None):
        sel = self.files_tree.selection()
        if not sel:
            return
        rel = sel[0]
        full = os.path.join(self.settings.workspace_dir(),
                            rel.replace("/", os.sep))
        self.lbl_preview.configure(text=rel)
        if os.path.isdir(full):
            self._set_preview("(directory - double-click to open)")
            return
        try:
            size = os.path.getsize(full)
            if size > 400_000:
                self._set_preview("(%s - too large to preview)"
                                  % _human_size(size))
                return
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                self._set_preview(fh.read())
        except Exception as exc:
            self._set_preview("Cannot read this file: %s" % exc)

    def _set_preview(self, text):
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")

    def _files_copy_path(self):
        sel = self.files_tree.selection()
        if not sel:
            return
        full = os.path.join(self.settings.workspace_dir(),
                            sel[0].replace("/", os.sep))
        self.clipboard_clear()
        self.clipboard_append(full)

    def _files_use_as_workspace(self):
        """Descend into the selected folder as the new workspace root."""
        sel = self.files_tree.selection()
        if not sel:
            return
        full = os.path.join(self.settings.workspace_dir(),
                            sel[0].replace("/", os.sep))
        if not os.path.isdir(full):
            messagebox.showinfo("Workspace", "Select a folder first.",
                                parent=self)
            return
        self.var_workspace.set(full)
        self._apply_workspace()

    # ---- Missions ----------------------------------------------------------
    def _build_missions(self, p):
        c = self.colours
        p.rowconfigure(0, weight=1)
        p.columnconfigure(0, weight=3)
        p.columnconfigure(1, weight=4)

        left = ttk.Frame(p, style="Card.TFrame", padding=(10, 10))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        bar = ttk.Frame(left, style="Panel.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, text="Missions, goals & tasks",
                  style="PanelH2.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(bar, text="Refresh", command=self.refresh_missions).grid(
            row=0, column=1, sticky="e")

        # What the orchestrator is doing right now, next to the plan it owns.
        self.lbl_orch = ttk.Label(left, text="", style="PanelDim.TLabel",
                                  wraplength=420, justify="left")
        self.lbl_orch.grid(row=1, column=0, sticky="w", pady=(0, 8))

        add = ttk.Frame(left, style="Panel.TFrame")
        add.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(add, text="Add goal",
                   command=self._add_goal_clicked).pack(side="left",
                                                        padx=(0, 6))
        ttk.Button(add, text="Add task",
                   command=self._add_task_clicked).pack(side="left",
                                                        padx=(0, 6))
        ttk.Button(add, text="New mission",
                   command=self._add_mission_clicked).pack(side="left")

        self.tree = ttk.Treeview(left, columns=("status", "cost"),
                                 show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Mission / goal / task")
        self.tree.heading("status", text="Status")
        self.tree.heading("cost", text="Cost")
        self.tree.column("#0", width=300, anchor="w")
        self.tree.column("status", width=90, anchor="w")
        self.tree.column("cost", width=80, anchor="e")
        self.tree.grid(row=2, column=0, sticky="nsew")
        tbar = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        tbar.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tbar.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        for name, key in (("done", "ok"), ("error", "err"), ("running", "info"),
                          ("queued", "fg_dim"), ("blocked", "warn"),
                          ("partial", "warn"), ("cancelled", "fg_faint"),
                          ("goal", "accent"), ("mixed", "warn"),
                          ("on_hold", "warn")):
            self.tree.tag_configure(name, foreground=c[key])

        right = ttk.Frame(p, style="Card.TFrame", padding=(14, 12))
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        self.lbl_detail_title = ttk.Label(right, text="Nothing selected",
                                          style="PanelH2.TLabel")
        self.lbl_detail_title.grid(row=0, column=0, sticky="w")
        self.lbl_detail_meta = ttk.Label(right, text="", style="PanelDim.TLabel",
                                         justify="left")
        self.lbl_detail_meta.grid(row=1, column=0, sticky="w", pady=(2, 8))

        wrap = ttk.Frame(right, style="Panel.TFrame")
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        self.detail = tk.Text(wrap, width=1, wrap="word", bd=0, highlightthickness=0,
                              bg=c["panel"], fg=c["fg"],
                              font=self.fonts["mono"], padx=8, pady=6,
                              state="disabled")
        self.detail.grid(row=0, column=0, sticky="nsew")
        dbar = ttk.Scrollbar(wrap, orient="vertical", command=self.detail.yview)
        dbar.grid(row=0, column=1, sticky="ns")
        self.detail.configure(yscrollcommand=dbar.set)

        acts = ttk.Frame(right, style="Panel.TFrame")
        acts.grid(row=3, column=0, sticky="e", pady=(10, 0))
        ttk.Button(acts, text="Copy result", command=self._copy_detail).pack(
            side="left", padx=(0, 8))
        ttk.Button(acts, text="Retry task", command=self._retry_task).pack(
            side="left", padx=(0, 8))
        ttk.Button(acts, text="Resume mission", command=self._resume_selected).pack(
            side="left", padx=(0, 8))
        ttk.Button(acts, text="Cancel task", style="Danger.TButton",
                   command=self._cancel_selected).pack(side="left")

    # ---- Fleet -------------------------------------------------------------
    def _build_fleet(self, p):
        c = self.colours
        p.rowconfigure(1, weight=1)
        p.columnconfigure(0, weight=1)

        card = ttk.Frame(p, style="Card.TFrame", padding=(14, 12))
        card.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(3, weight=1)

        head = ttk.Frame(card, style="Panel.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="Agents the executor may deploy",
                  style="PanelH2.TLabel").grid(row=0, column=0, sticky="w")
        self.btn_refresh_models = ttk.Button(
            head, text="Check providers for new models",
            style="Accent.TButton", command=self._refresh_models_clicked)
        self.btn_refresh_models.grid(row=0, column=1, sticky="e")

        self.lbl_fleet_hint = ttk.Label(
            card,
            text="",
            style="PanelDim.TLabel", wraplength=900, justify="left")
        self.lbl_fleet_hint.grid(row=1, column=0, sticky="w", pady=(2, 8))

        # Scrollable, because the list grows as providers are added.
        holder = ttk.Frame(card, style="Panel.TFrame")
        holder.grid(row=3, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.fleet_canvas = tk.Canvas(holder, bd=0, highlightthickness=0,
                                      bg=c["panel"], height=260)
        self.fleet_canvas.grid(row=0, column=0, sticky="nsew")
        fbar = ttk.Scrollbar(holder, orient="vertical",
                             command=self.fleet_canvas.yview)
        fbar.grid(row=0, column=1, sticky="ns")
        self.fleet_canvas.configure(yscrollcommand=fbar.set)
        self.fleet_inner = ttk.Frame(self.fleet_canvas, style="Panel.TFrame")
        self._fleet_window = self.fleet_canvas.create_window(
            (0, 0), window=self.fleet_inner, anchor="nw")
        self.fleet_inner.bind(
            "<Configure>",
            lambda e: self.fleet_canvas.configure(
                scrollregion=self.fleet_canvas.bbox("all")))
        self.fleet_canvas.bind(
            "<Configure>",
            lambda e: self.fleet_canvas.itemconfigure(self._fleet_window,
                                                      width=e.width))
        self._render_agent_models()

        listcard = ttk.Frame(p, style="Card.TFrame", padding=(14, 12))
        listcard.grid(row=1, column=0, sticky="nsew")
        listcard.rowconfigure(1, weight=1)
        listcard.columnconfigure(0, weight=1)
        bar = ttk.Frame(listcard, style="Panel.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, text="Agent runs", style="PanelH2.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Button(bar, text="Refresh", command=self.refresh_fleet).grid(
            row=0, column=1, sticky="e")

        cols = ("role", "model", "status", "cost", "tokens", "when")
        self.agent_tree = ttk.Treeview(listcard, columns=cols, show="headings")
        for col, label, width, anchor in (
                ("role", "Role", 150, "w"), ("model", "Model", 150, "w"),
                ("status", "Status", 90, "w"), ("cost", "Cost", 80, "e"),
                ("tokens", "Tokens in/out", 120, "e"),
                ("when", "Started", 140, "w")):
            self.agent_tree.heading(col, text=label)
            self.agent_tree.column(col, width=width, anchor=anchor)
        self.agent_tree.grid(row=1, column=0, sticky="nsew")
        abar = ttk.Scrollbar(listcard, orient="vertical",
                             command=self.agent_tree.yview)
        abar.grid(row=1, column=1, sticky="ns")
        self.agent_tree.configure(yscrollcommand=abar.set)
        self.agent_tree.bind("<Double-1>", self._show_agent_output)

    # ---- Usage -------------------------------------------------------------
    def _build_usage(self, p):
        c = self.colours
        p.rowconfigure(1, weight=1)
        p.columnconfigure(0, weight=1)

        top = ttk.Frame(p, style="Card.TFrame", padding=(14, 12))
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.columnconfigure(3, weight=1)
        self.usage_tiles = {}
        for i, (key, label) in enumerate((("today", "Spend today"),
                                          ("total", "Spend all time"),
                                          ("calls", "Model calls"),
                                          ("tokens", "Tokens in / out"))):
            cell = ttk.Frame(top, style="Panel.TFrame")
            cell.grid(row=0, column=i, sticky="w", padx=(0, 28))
            ttk.Label(cell, text=label, style="PanelDim.TLabel").pack(anchor="w")
            val = ttk.Label(cell, text="-", style="PanelH2.TLabel")
            val.pack(anchor="w")
            self.usage_tiles[key] = val

        card = ttk.Frame(p, style="Card.TFrame", padding=(14, 12))
        card.grid(row=1, column=0, sticky="nsew")
        card.rowconfigure(1, weight=1)
        card.columnconfigure(0, weight=1)
        bar = ttk.Frame(card, style="Panel.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        bar.columnconfigure(0, weight=1)
        ttk.Label(bar, text="By model", style="PanelH2.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Button(bar, text="Refresh", command=self.refresh_usage).grid(
            row=0, column=1, sticky="e")

        cols = ("model", "calls", "in", "out", "cost")
        self.usage_tree = ttk.Treeview(card, columns=cols, show="headings")
        for col, label, width, anchor in (
                ("model", "Model", 220, "w"), ("calls", "Calls", 80, "e"),
                ("in", "Input tokens", 130, "e"),
                ("out", "Output tokens", 130, "e"),
                ("cost", "Cost", 110, "e")):
            self.usage_tree.heading(col, text=label)
            self.usage_tree.column(col, width=width, anchor=anchor)
        self.usage_tree.grid(row=1, column=0, sticky="nsew")

        caps = ttk.Frame(card, style="Panel.TFrame")
        caps.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(caps, text="Per-mission cap  $", style="PanelDim.TLabel").pack(
            side="left")
        self.var_cap_mission = tk.StringVar(
            value=str(self.settings.get("budget_usd_per_mission")))
        ttk.Entry(caps, textvariable=self.var_cap_mission, width=8).pack(
            side="left", padx=(4, 16))
        ttk.Label(caps, text="Daily cap  $", style="PanelDim.TLabel").pack(
            side="left")
        self.var_cap_daily = tk.StringVar(
            value=str(self.settings.get("budget_usd_daily")))
        ttk.Entry(caps, textvariable=self.var_cap_daily, width=8).pack(
            side="left", padx=(4, 16))
        ttk.Button(caps, text="Apply caps", command=self._apply_caps).pack(
            side="left")

    # ---- Connect -----------------------------------------------------------
    def _build_connect(self, p):
        c = self.colours
        p.columnconfigure(0, weight=1)
        card = ttk.Frame(p, style="Card.TFrame", padding=20)
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text="Where do you want to work with the orchestrator?", style="PanelH2.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 18))
        ttk.Label(card, text="In the Codex app", style="Panel.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(card, text="Add this app's tools to Codex. Restart Codex after connecting.\nKeep Collaborator open to see progress here.",
                  style="PanelDim.TLabel", wraplength=500).grid(row=2, column=0, sticky="w", pady=(4, 18))
        ttk.Button(card, text="Connect Codex", style="Accent.TButton", command=lambda:
                   self._connection_action(connection.register_codex)).grid(row=1, column=1, rowspan=2, padx=(18, 0), sticky="n")
        ttk.Separator(card).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        ttk.Label(card, text="Inside Collaborator", style="Panel.TLabel").grid(row=4, column=0, sticky="w")
        ttk.Label(card, text="Use your Codex subscription to turn a goal into tasks here.\nThen open Start and choose Orchestrator.",
                  style="PanelDim.TLabel", wraplength=500).grid(row=5, column=0, sticky="w", pady=(4, 14))
        ttk.Button(card, text="Use Codex planner", command=self._use_codex_planner).grid(row=4, column=1, rowspan=2, padx=(18, 0), sticky="n")
        self.var_connection_status = tk.StringVar(value="Already signed in to Codex? No API key is needed for either option.")
        status = ttk.Frame(p, style="Card.TFrame", padding=16)
        status.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        status.columnconfigure(0, weight=1)
        ttk.Label(status, textvariable=self.var_connection_status, style="PanelDim.TLabel", wraplength=540).grid(row=0, column=0, sticky="w")
        ttk.Button(status, text="Check connection", command=lambda:
                   self._connection_action(connection.test_connection)).grid(row=0, column=1, padx=(12, 0))
        wrap = ttk.Frame(p, style="Card.TFrame", padding=14)
        wrap.rowconfigure(1, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.var_manual_config = tk.BooleanVar(value=False)
        def toggle_manual():
            if self.var_manual_config.get():
                wrap.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
                p.rowconfigure(3, weight=1)
            else:
                wrap.grid_remove()
                p.rowconfigure(3, weight=0)
        ttk.Checkbutton(p, text="Manual configuration / other MCP clients", variable=self.var_manual_config,
                        command=toggle_manual).grid(row=2, column=0, sticky="w", pady=(14, 0))
        bar = ttk.Frame(wrap, style="Panel.TFrame")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(bar, text="Copy Codex TOML", command=self._copy_config).pack(side="left")
        ttk.Button(bar, text="Copy JSON for other clients", command=self._copy_json_config).pack(side="left", padx=(8, 0))
        self.txt_config = tk.Text(wrap, width=1, height=8, wrap="word", bd=0, highlightthickness=0,
                                  bg=c["input"], fg=c["fg"], font=self.fonts["mono"], padx=10, pady=8)
        self.txt_config.grid(row=1, column=0, sticky="nsew")
        self.txt_config.insert("1.0", self._config_snippet())
        self.txt_config.configure(state="disabled")

    def _config_snippet(self):
        return connection.config_snippet()

    def _copy_json_config(self):
        self.clipboard_clear()
        self.clipboard_append(connection.config_snippet("json"))
        self.var_connection_status.set("JSON configuration copied for other MCP clients.")

    def _connection_action(self, action):
        self.var_connection_status.set("Checking connection..." if action == connection.test_connection
                                       else "Connecting Codex...")
        def work():
            try:
                result = action()
            except Exception as exc:
                result = str(exc)
            self.events.put({"kind": "connection_result", "text": result})
        threading.Thread(target=work, name="connection", daemon=True).start()

    def _use_codex_planner(self):
        if not self._model_ready("codex-cli"):
            return
        self.settings.update({"orchestrator_mode": "model", "orchestrator_model": "codex-cli"})
        self.settings.save()
        self._sync_orchestrator_ui()
        self.var_orchestrator.set(self._orchestrator_label())
        self._refresh_status()
        self.var_connection_status.set("Codex is the local orchestrator. Open Start > Orchestrator.")

    # ---- Settings ----------------------------------------------------------
    def _build_settings(self, p):
        p.rowconfigure(0, weight=1)
        p.columnconfigure(0, weight=1)
        nb = ttk.Frame(p)
        nb.grid(row=0, column=0, sticky="nsew")
        nb.rowconfigure(0, weight=1)
        nb.columnconfigure(1, weight=1)
        self._settings_pages = []
        self.settings_sections = tk.Listbox(nb, width=19, exportselection=False, activestyle="none",
            bg=self.colours["panel"], fg=self.colours["fg"], selectbackground=self.colours["select"],
            selectforeground=self.colours["fg"], bd=0, highlightthickness=0, font=self.fonts["base"])
        self.settings_sections.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        def select_section(event=None):
            selected = self.settings_sections.curselection()
            if selected:
                self._settings_pages[selected[0]].tkraise()
        self.settings_sections.bind("<<ListboxSelect>>", select_section)
        def scroll_settings(event):
            if self._current != "settings" or isinstance(event.widget, (tk.Text, tk.Listbox, ttk.Combobox)):
                return
            widget = event.widget
            while widget is not None:
                if isinstance(widget, tk.Canvas):
                    widget.yview_scroll(int(-event.delta / 120), "units")
                    return "break"
                widget = getattr(widget, "master", None)
        self.bind("<MouseWheel>", scroll_settings, add="+")

        self._s_vars = {}
        self._s_base = {}           # form values as last filled, per key
        self._persona_base = ""
        team = self._settings_tab(nb, "Your team")
        self._build_team_settings(team)
        cred = self._settings_tab(nb, "Account")
        row = 1
        for pid in catalog.API_PROVIDERS:
            info = catalog.PROVIDERS[pid]
            self._field(cred, row, "%s API key" % info.label,
                        info.key_setting, secret=True,
                        hint="Falls back to %s.  %s" % (info.env_var,
                                                        info.signup))
            row += 1
        self._field(cred, row, "Orchestrator name", "orchestrator_name",
                    hint="Who is directing the work. Shown in the log.")
        self._field(cred, row + 1, "Executor name", "executor_name")
        self.lbl_cli_status = ttk.Label(cred, text="",
                                        style="PanelDim.TLabel",
                                        wraplength=620, justify="left")
        self.lbl_cli_status.grid(row=(row + 2) * 2, column=1, sticky="w",
                                 pady=(12, 0))

        ends = self._settings_tab(nb, "Endpoints")
        ttk.Label(ends, text="Leave blank to use each provider's default. "
                             "Mainland-China Qwen accounts use "
                             "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  style="PanelDim.TLabel", wraplength=620,
                  justify="left").grid(row=0, column=0, columnspan=2,
                                       sticky="w", pady=(0, 6))
        erow = 1
        for pid in catalog.API_PROVIDERS:
            info = catalog.PROVIDERS[pid]
            if not info.base_url_setting:
                continue
            self._field(ends, erow, "%s base URL" % info.label,
                        info.base_url_setting, hint=info.base_url)
            erow += 1

        sub = self._settings_tab(nb, "Subscription CLIs")
        self._field(sub, 0, "CLI timeout (s)", "cli_timeout_s", kind=int,
                    hint="A delegated CLI turn can run for minutes; give it "
                         "room.")
        self._field(sub, 1, "Claude Code extra args", "cli_claude_args",
                    hint="Passed to 'claude -p'. Claude Code's own tools "
                         "stay disabled unless you pass --disallowed-tools "
                         "here; CollaboratorMCP runs file and shell actions "
                         "itself, behind Auto-approve.")
        self._combo(sub, 2, "Codex sandbox", "cli_codex_sandbox",
                    ["read-only", "workspace-write", "danger-full-access"],
                    hint="How much the Codex CLI may touch. workspace-write "
                         "keeps it inside the workspace directory.")
        self._field(sub, 3, "Codex extra args", "cli_codex_args",
                    hint="Passed to 'codex exec'.")

        mdl = self._settings_tab(nb, "Models")
        self._field(mdl, 2, "Max tasks per plan", "orchestrator_max_tasks",
                    kind=int)
        self._check(mdl, 5, "Adaptive thinking", "executor_thinking",
                    hint="Opus 5.5 and Fable 5.1 think adaptively at all "
                         "times; for those this toggle has no effect.")
        self._field(mdl, 6, "Executor max tokens", "executor_max_tokens",
                    kind=int)
        self._combo(mdl, 7, "Refusal fallback model", "fallback_model",
                    [m.id for m in catalog.executor_models()],
                    hint="Retried on this model if the executor declines.")
        self._check(mdl, 8, "Retry on refusal", "enable_refusal_fallback")
        self._field(mdl, 10, "Agent max tokens", "agent_max_tokens", kind=int)

        lim = self._settings_tab(nb, "Limits")
        self._field(lim, 0, "Parallel tasks", "max_parallel_tasks", kind=int,
                    hint="Takes effect on restart.")
        self._field(lim, 1, "Parallel sub-agents", "max_parallel_agents",
                    kind=int, hint="Takes effect on restart.")
        self._field(lim, 2, "Max tool steps per task", "max_tool_iterations",
                    kind=int)
        self._field(lim, 3, "Agent timeout (s)", "agent_timeout_s", kind=int)
        self._check(lim, 4, "Enforce budget caps", "budget_enabled")
        self._field(lim, 5, "Per-mission cap (USD)", "budget_usd_per_mission",
                    kind=float)
        self._field(lim, 6, "Daily cap (USD)", "budget_usd_daily", kind=float)

        saf = self._settings_tab(nb, "Workspace & safety")
        self._path_field(saf, 0, "Workspace directory", "workspace")
        self._check(saf, 1, "Allow shell commands", "allow_shell",
                    hint="Off by default. The executor runs commands inside "
                         "the workspace only.")
        self._field(saf, 2, "Shell timeout (s)", "shell_timeout_s", kind=int)
        self._check(saf, 3, "Auto-approve executor actions", "auto_approve",
                    hint="Off means every gated tool call waits for an "
                         "explicit Approve or Deny.")
        self._field(saf, 4, "Gated tools", "approval_tools", kind=list,
                    hint="Comma-separated tool names that need approval when "
                         "auto-approve is off.")
        self._field(saf, 5, "Approval timeout (s)", "approval_timeout_s",
                    kind=int)
        self._combo(saf, 6, "On approval timeout", "approval_on_timeout",
                    ["deny", "allow"])
        self._check(saf, 8, "Ask a helper to review file changes", "executor_agent_review")
        self._field(saf, 7, "Hub port", "hub_port", kind=int,
                    hint="Loopback port shared with the MCP process. "
                         "Takes effect on restart.")

        per = self._settings_tab(nb, "Persona")
        ttk.Label(per, text="Extra executor instructions",
                  style="PanelDim.TLabel").grid(row=0, column=0, sticky="w",
                                                pady=(0, 4))
        self.txt_persona = self._text(per, height=12)
        self.txt_persona.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.txt_persona.insert("1.0",
                                self.settings.get("executor_system_extra") or "")
        per.rowconfigure(1, weight=1)
        per.columnconfigure(0, weight=1)
        self._build_about(self._settings_tab(nb, "About"))
        self._fill_settings_form()

        self.var_shell = self._s_vars["allow_shell"][0]
        self.var_budget_on = self._s_vars["budget_enabled"][0]
        self.var_auto_approve = self._s_vars["auto_approve"][0]
        self.settings_sections.selection_set(0)
        self._settings_pages[0].tkraise()
        foot = ttk.Frame(p, style="TFrame")
        foot.grid(row=1, column=0, sticky="e", pady=(10, 0))
        ttk.Label(foot, text="", style="Dim.TLabel").pack(side="left")
        self.lbl_saved = ttk.Label(foot, text="", style="Dim.TLabel")
        self.lbl_saved.pack(side="left", padx=(0, 12))
        ttk.Button(foot, text="Reload", command=self._reload_settings).pack(
            side="left", padx=(0, 8))
        ttk.Button(foot, text="Save settings", style="Accent.TButton",
                   command=self._save_settings).pack(side="left")

    ABOUT_SECTIONS = [
        ("What it is",
         "CollaboratorMCP lets AI models work together as a team. One model "
         "orchestrates: it breaks a goal into tasks. Another model executes "
         "each task with file tools inside your workspace, and can hand small "
         "jobs to helper models. Any model can take any role - you choose "
         "who does what. Everything is recorded and shown live in this window, "
         "and any MCP client (such as the Codex app) can drive it too."),
        ("How to use it",
         "1. Pick your working folder in the bar at the top.\n"
         "2. Under Settings > Your team, choose the orchestrator, the "
         "executor, and up to 8 named helpers.\n"
         "3. On Start, describe a larger goal (Orchestrator tab) or a "
         "concrete request (Executor tab), then start.\n"
         "4. Follow progress and send messages in Activity; review results "
         "and retry failed tasks in Tasks & results.\n"
         "5. To drive it from another app, open Connect and register this "
         "MCP server with your client. Keep this window open while it works."),
        ("Requirements",
         "- Windows 10/11 (Python 3 with tkinter if you run from source).\n"
         "- For each model you use, either an API key (Settings > Account) or "
         "a signed-in Claude Code CLI (Pro/Max plan) or Codex CLI (ChatGPT "
         "Plus/Pro plan) - subscriptions need no API key.\n"
         "- Optional: an MCP client such as Codex to orchestrate externally.\n"
         "- Only one Collaborator window can run at a time."),
    ]

    def _build_about(self, f):
        from .. import __version__
        ttk.Label(f, text="CollaboratorMCP", style="PanelH2.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(f, text="Version %s  -  by KORT-X Laboratories" % __version__,
                  style="PanelDim.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        link = ttk.Label(f, text="kort-x.com", style="BrandLink.TLabel",
                         cursor="hand2")
        link.grid(row=2, column=0, columnspan=2, sticky="w", pady=(2, 10))
        link.bind("<Button-1>", lambda _e: webbrowser.open("https://kort-x.com"))
        row = 3
        for title, body in self.ABOUT_SECTIONS:
            ttk.Label(f, text=title, style="PanelH2.TLabel").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(14, 4))
            ttk.Label(f, text=body, style="Panel.TLabel", wraplength=620,
                      justify="left").grid(row=row + 1, column=0, columnspan=2,
                                           sticky="w")
            row += 2

    def _settings_tab(self, nb, label):
        outer = ttk.Frame(nb, style="Card.TFrame")
        outer.grid(row=0, column=1, sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        canvas = tk.Canvas(outer, bg=self.colours["panel"], highlightthickness=0, width=1)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)
        frame = ttk.Frame(canvas, style="Card.TFrame", padding=16)
        frame.columnconfigure(1, weight=1)
        item = canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        def resize(event):
            canvas.itemconfigure(item, width=event.width)
            for child in frame.winfo_children():
                if isinstance(child, ttk.Label) and int(child.cget("wraplength") or 0):
                    column = int(child.grid_info().get("column", 0))
                    child.configure(wraplength=max(220, event.width - (240 if column == 1 else 40)))
        canvas.bind("<Configure>", resize)
        def wheel(event):
            canvas.yview_scroll(int(-event.delta / 120), "units")
            return "break"
        canvas.bind("<MouseWheel>", wheel)
        frame.bind("<MouseWheel>", wheel)
        self._settings_pages.append(outer)
        self.settings_sections.insert("end", "  " + label)
        return frame

    @staticmethod
    def _to_entry_text(value, kind):
        if kind is list:
            return ", ".join(value or [])
        return str(value if value is not None else "")

    def _field(self, parent, row, label, key, kind=str, secret=False, hint=""):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(
            row=row * 2, column=0, sticky="w", pady=(8, 2), padx=(0, 14))
        var = tk.StringVar(
            value=self._to_entry_text(self.settings.get(key), kind))
        entry = ttk.Entry(parent, textvariable=var,
                          show="*" if secret else "")
        entry.grid(row=row * 2, column=1, sticky="ew", pady=(8, 2))
        self._s_vars[key] = (var, kind)
        if hint:
            ttk.Label(parent, text=hint, style="PanelDim.TLabel",
                      wraplength=620, justify="left").grid(
                row=row * 2 + 1, column=1, sticky="w")

    def _path_field(self, parent, row, label, key):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(
            row=row * 2, column=0, sticky="w", pady=(8, 2), padx=(0, 14))
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.grid(row=row * 2, column=1, sticky="ew", pady=(8, 2))
        box.columnconfigure(0, weight=1)
        var = tk.StringVar(value=str(self.settings.get(key) or ""))
        ttk.Entry(box, textvariable=var).grid(row=0, column=0, sticky="ew")
        ttk.Button(box, text="Browse",
                   command=lambda: self._browse_into(var)).grid(
            row=0, column=1, padx=(8, 0))
        self._s_vars[key] = (var, str)

    def _combo(self, parent, row, label, key, values, hint=""):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(
            row=row * 2, column=0, sticky="w", pady=(8, 2), padx=(0, 14))
        var = tk.StringVar(value=str(self.settings.get(key) or ""))
        ttk.Combobox(parent, textvariable=var, values=values,
                     state="readonly").grid(row=row * 2, column=1, sticky="ew",
                                            pady=(8, 2))
        self._s_vars[key] = (var, str)
        if hint:
            ttk.Label(parent, text=hint, style="PanelDim.TLabel", wraplength=440).grid(
                row=row * 2 + 1, column=1, sticky="w")

    def _check(self, parent, row, label, key, hint=""):
        var = tk.BooleanVar(value=bool(self.settings.get(key)))
        ttk.Checkbutton(parent, text=label, variable=var,
                        style="Panel.TCheckbutton").grid(
            row=row * 2, column=1, sticky="w", pady=(8, 2))
        self._s_vars[key] = (var, bool)
        if hint:
            ttk.Label(parent, text=hint, style="PanelDim.TLabel",
                      wraplength=620, justify="left").grid(
                row=row * 2 + 1, column=1, sticky="w")

    # ============================================================== behaviour
    def _quick_set(self, key, value):
        self.settings.set(key, value)
        self.settings.save()
        self._refresh_status()

    def _pick_model(self, key, value, role):
        """Set a model slot and say straight away if it cannot run."""
        self._quick_set(key, value)
        ready, why = providers.check_model_ready(value, self.settings)
        if ready:
            return
        if catalog.is_subscription(value):
            detail = ("%s needs the %s CLI installed and signed in."
                      % (value, (catalog.get(value) or
                                 catalog.ModelSpec("", "", "", 0, 0, 0, "")
                                 ).cli_bin or "matching"))
        else:
            detail = ("%s is an API model and bills per token. %s."
                      % (value, why))
            if self.settings.get("account_mode") == "subscription":
                detail += ("\n\nYou are in Pro / Plus mode, which has no API "
                           "key. Either add one under Settings -> Account, or "
                           "pick a 'claude-cli' / 'codex-cli' model instead.")
        messagebox.showwarning(
            "%s will not run yet" % role.capitalize(), detail, parent=self)

    def _toggle_pause(self):
        if self.engine.is_paused:
            self.engine.resume()
        else:
            self.engine.pause()
        self._refresh_status()

    def _toggle_theme(self):
        name = "dark" if self.var_theme.get() else "light"
        self.settings.set("theme", name)
        self.settings.save()
        messagebox.showinfo(
            "Theme", "Theme set to %s. Restart CollaboratorMCP to apply it."
            % name, parent=self)

    def _open_team(self):
        self.show_page("settings")
        self.settings_sections.selection_clear(0, "end")
        self.settings_sections.selection_set(0)
        self._settings_pages[0].tkraise()

    def _open_workspace(self):
        path = self.settings.workspace_dir()
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            messagebox.showerror("Workspace", str(exc), parent=self)

    def _browse_into(self, var):
        chosen = filedialog.askdirectory(parent=self,
                                         initialdir=var.get() or os.getcwd())
        if chosen:
            var.set(chosen)

    def _clear_composer(self):
        self.var_task_title.set("")
        self.txt_instructions.delete("1.0", "end")
        self.txt_context.delete("1.0", "end")

    def _reload_mission_choices(self):
        missions = self.engine.store.list_missions(limit=60)
        self._mission_choices = {"(new mission)": None}
        labels = ["(new mission)"]
        for m in missions:
            if m["status"] != "open":
                continue
            label = "%s  ·  %s" % (m["title"][:48], m["id"][-6:])
            self._mission_choices[label] = m["id"]
            labels.append(label)
        self.cmb_mission.configure(values=labels)
        if self.var_mission.get() not in labels:
            self.var_mission.set("(new mission)")

    def _delegate_clicked(self):
        title = self.var_task_title.get().strip()
        instructions = self.txt_instructions.get("1.0", "end").strip()
        context = self.txt_context.get("1.0", "end").strip()
        if not title:
            title = instructions.split("\n", 1)[0][:80]
        if not instructions:
            messagebox.showwarning("Delegate",
                                   "Describe what the executor should do.",
                                   parent=self)
            return
        if not self._executor_ready():
            return
        mission_id = self._mission_choices.get(self.var_mission.get())
        try:
            api.dispatch(self.engine, "task.delegate", {
                "mission_id": mission_id, "title": title,
                "instructions": instructions, "context": context},
                "local")
        except Exception as exc:
            messagebox.showerror("Delegate", str(exc), parent=self)
            return
        self._clear_composer()
        self._reload_mission_choices()
        self._refresh_status()
        self.show_page("missions")

    def _executor_ready(self):
        return self._model_ready(self.settings.get("executor_model"))

    def _model_ready(self, model_id):
        ready, why = providers.check_model_ready(model_id, self.settings)
        if ready:
            return True
        if catalog.is_subscription(model_id):
            messagebox.showerror(
                "Subscription CLI not available",
                "%s\n\n%s runs through a locally installed, signed-in CLI. "
                "Install it and log in with your plan, or switch Account back "
                "to API keys in Settings > Your team." % (why, model_id),
                parent=self)
        else:
            messagebox.showerror(
                "No credentials",
                "%s.\n\nAdd a key in Settings > Account, or switch "
                "Account to your Pro / Plus plan in Settings > Your team." % why,
                parent=self)
            self.show_page("settings")
        return False

    def _say_clicked(self):
        text = self.var_say.get().strip()
        if not text:
            return
        try:
            result = self.engine.steer(text, actor="Operator")
        except Exception as exc:
            self.lbl_say.configure(text=str(exc))
            return
        self.var_say.set("")
        self.lbl_say.configure(
            text="Queued for '%s' - the executor reads it on its next step."
                 % result["title"])

    def _update_say_state(self):
        active = self.engine.active_tasks()
        if active:
            newest = sorted(active.items(),
                            key=lambda kv: kv[1].get("started", 0))[-1]
            self.btn_say.configure(state="normal")
            if not self.var_say.get():
                self.lbl_say.configure(text="Goes to: %s"
                                            % newest[1].get("title", ""))
        else:
            self.btn_say.configure(state="disabled")
            if not self.var_say.get():
                self.lbl_say.configure(
                    text="Nothing running - delegate a task to steer it live.")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _load_recent_events(self):
        for event in self.engine.store.list_events(limit=120):
            self._render_event(event)

    # ---- event pump --------------------------------------------------------
    def _pump_events(self):
        drained = 0
        try:
            while drained < 200:
                event = self.events.get_nowait()
                self._render_event(event)
                drained += 1
        except queue.Empty:
            pass
        if drained:
            self._refresh_status()
            self._refresh_approvals()
            self._update_say_state()
            if self._current in ("missions", "fleet"):
                self._schedule_page_refresh()
        self.after(120, self._pump_events)

    def _schedule_page_refresh(self):
        """Rebuild the visible list at most once a second while work runs.

        Rebuilding on every event batch (every 120 ms) made the task list
        and the result pane jump back to the top while you were reading.
        """
        if getattr(self, "_page_refresh_pending", False):
            return
        self._page_refresh_pending = True

        def run():
            self._page_refresh_pending = False
            if self._current == "missions":
                self.refresh_missions()
            elif self._current == "fleet":
                self.refresh_fleet()
        self.after(1000, run)

    def _poll_approvals(self):
        # Backstop for approvals that time out without producing an event.
        self._refresh_approvals()
        self.after(2000, self._poll_approvals)

    def _render_event(self, event):
        kind = event.get("kind", "system")
        if kind == "plan_result":
            self._plan_done(event.get("result"), event.get("error"))
            return
        if kind == "models_result":
            self._refresh_models_done(event.get("result"), event.get("error"))
            return
        if kind == "connection_result":
            self.var_connection_status.set(event.get("text", ""))
            return
        if kind == "console":
            data = event.get("data") or {}
            channel = data.get("channel") or "executor"
            if channel.startswith("agent:") and data.get("label"):
                self._ensure_agent_terminal(channel, data["label"])
            self._term_write(channel, data.get("stream") or "out",
                             event.get("text", ""), event.get("ts"))
            return
        flt = self._log_filters.get(kind)
        if flt is not None and not flt.get():
            return
        stamp = time.strftime("%H:%M:%S", time.localtime(event.get("ts", 0)))
        actor = event.get("actor") or ""
        text = event.get("text") or ""
        self.log.configure(state="normal")
        self.log.insert("end", stamp + "  ", ("ts",))
        if actor:
            self.log.insert("end", actor + "  ", ("actor",))
        self.log.insert("end", text + "\n", (kind,))
        if int(self.log.index("end-1c").split(".")[0]) > 5000:
            self.log.delete("1.0", "1001.0")
        self.log.configure(state="disabled")
        if self.var_autoscroll.get():
            self.log.see("end")

    def _log_line(self, text, tag="system"):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M:%S") + "  ", ("ts",))
        self.log.insert("end", text + "\n", (tag,))
        self.log.configure(state="disabled")
        self.log.see("end")

    # ---- status ------------------------------------------------------------
    def _refresh_status(self):
        s = self.engine.stats()
        state = "Paused" if s["paused"] else ("Working" if s["running"] else "Ready")
        if s.get("pending_approvals"):
            state += "  ·  %d awaiting approval" % s["pending_approvals"]
        elif not s.get("auto_approve"):
            state += "  ·  manual approval"
        self.chip_state.configure(text=state)
        self.chip_queue.configure(
            text="Queue %d  ·  Active %d" % (s["queued"], s["running"]))
        self.chip_spend.configure(
            text="Today %s" % catalog.fmt_cost(s["today_usd"]))
        creds = s["credentials"]
        # The chip that matters most: can the *selected* executor actually run?
        executor = self.settings.get("executor_model")
        ready, why = providers.check_model_ready(executor, self.settings)
        if ready:
            self.chip_keys.configure(text="%s ready" % executor)
        else:
            self.chip_keys.configure(text="%s: %s" % (executor, why))
        if self.settings.get("account_mode") == "subscription":
            marks = ["Claude Code " + ("ready" if creds.get("claude_cli")
                                       else "not installed"),
                     "Codex " + ("ready" if creds.get("codex_cli") else "off")]
        else:
            marks = ["Anthropic " + ("ok" if creds["anthropic"] else "missing"),
                     "OpenAI " + ("ok" if creds["openai"] else "off")]
        self.chip_creds.configure(text="  ·  ".join(marks))
        self.btn_pause.configure(text="Resume queue" if s["paused"] else "Pause queue")
        cap_m = float(self.settings.get("budget_usd_per_mission") or 0)
        cap_d = float(self.settings.get("budget_usd_daily") or 0)
        self.lbl_budget.configure(
            text="Spend today %s of %s\nAll time %s"
                 % (catalog.fmt_cost(s["today_usd"]), catalog.fmt_cost(cap_d),
                    catalog.fmt_cost(s["total_usd"])))
        # Say plainly whether an orchestrator is actually attached, rather
        # than implying one is just because a name is configured.
        clients = s.get("orchestrators") or []
        if not self.hub_ok:
            hub_text = "MCP bridge inactive (port in use)"
        elif clients:
            hub_text = "%s connected  ·  %d call(s)" % (
                clients[0]["name"], sum(c["calls"] for c in clients))
        else:
            hub_text = "No orchestrator connected  ·  %s:%s" % (
                self.settings.get("hub_host"), self.settings.get("hub_port"))
        self.lbl_hub.configure(text=hub_text)
        if self.settings.get("orchestrator_mode") == "model":
            orch = self.engine.orchestrator_state()
            text = "Orchestrator: %s" % orch["state"]
        elif clients:
            text = "%s live" % clients[0]["name"]
        else:
            text = "%s: connect on Connect page" % self.settings.get("orchestrator_name")
        self.chip_orchestrator.configure(text=text)
        spec = catalog.get(executor)
        executor_label = spec.label if spec else executor
        if self.settings.get("orchestrator_mode") == "model":
            planner = "Orchestrator: %s, %s." % (
                self.settings.get("orchestrator_model"),
                "plans and supervises" if self.settings.get("orchestrator_supervise") else "plans and reviews")
            if not clients and self.hub_ok:
                self.lbl_hub.configure(text="Local orchestrator selected. Codex app connection is optional.")
        else:
            planner = ("Orchestrator: %s over MCP." % clients[0]["name"] if clients
                       else "Orchestrator: external MCP client, not connected.")
        team = config.helper_roster(self.settings)
        self.lbl_team_summary.configure(text="%s\nExecutor: %s%s\nHelpers (%d): %s" % (
            planner, executor_label, "" if ready else " - " + why, len(team),
            ", ".join("%s (%s)" % (m["name"], m["model"]) for m in team)))
        if hasattr(self, "lbl_cli_status"):
            lines = ["Detected on this machine:"]
            for label, key, hint in (
                    ("Claude Code (claude)", "claude_cli",
                     "signs in with a Claude Pro or Max plan"),
                    ("Codex CLI (codex)", "codex_cli",
                     "signs in with a ChatGPT Plus or Pro plan")):
                mark = "found" if creds.get(key) else "not on PATH"
                lines.append("   %s - %s  (%s)" % (label, mark, hint))
            lines.append("Subscription mode needs no API key; work draws on "
                         "the plan's quota instead of per-token credit.")
            self.lbl_cli_status.configure(text="\n".join(lines))

    # ---- missions page -----------------------------------------------------
    def refresh_missions(self):
        selected = self.tree.selection()
        keep = selected[0] if selected else None
        top = self.tree.yview()[0]
        closed = set()
        for mission_node in self.tree.get_children():
            for node in (mission_node,) + self.tree.get_children(mission_node):
                if not self.tree.item(node, "open"):
                    closed.add(node)
        self.tree.delete(*self.tree.get_children())
        store = self.engine.store
        for mission in store.list_missions(limit=80):
            node = self.tree.insert(
                "", "end", iid=mission["id"], text=mission["title"],
                values=(mission["status"],
                        catalog.fmt_cost(mission["cost_usd"] or 0)),
                open=True, tags=(mission["status"],))
            tasks = store.list_tasks(mission["id"])
            by_goal = {}
            for task in tasks:
                by_goal.setdefault(task.get("goal_id") or "", []).append(task)

            for goal in store.list_goals(mission["id"]):
                gnode = self.tree.insert(
                    node, "end", iid=goal["id"], text="  " + goal["title"],
                    values=(goal["status"], ""), open=True,
                    tags=("goal",))
                for task in by_goal.get(goal["id"], []):
                    self.tree.insert(
                        gnode, "end", iid=task["id"],
                        text="    " + task["title"],
                        values=(_task_status_label(task),
                                catalog.fmt_cost(task["cost_usd"] or 0)),
                        tags=(task["status"],))
            # Tasks added straight to the mission, with no goal.
            for task in by_goal.get("", []):
                self.tree.insert(
                    node, "end", iid=task["id"], text="  " + task["title"],
                    values=(_task_status_label(task),
                            catalog.fmt_cost(task["cost_usd"] or 0)),
                    tags=(task["status"],))
        for node in closed:
            if self.tree.exists(node):
                self.tree.item(node, open=False)
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        self.tree.update_idletasks()
        self.tree.yview_moveto(top)
        self._refresh_orchestrator_panel()

    def _refresh_orchestrator_panel(self):
        state = self.engine.orchestrator_state()
        clients = self.engine.clients()
        who = self.settings.get("orchestrator_name")
        if self.settings.get("orchestrator_mode") == "model":
            who = self.settings.get("orchestrator_model")
            presence = "planning locally"
        else:
            presence = ("connected" if clients else "not connected")
        detail = state.get("detail") or ""
        text = "Orchestrator: %s  ·  %s  ·  %s" % (
            who, presence, state.get("state", "idle"))
        if detail:
            text += "\n%s" % detail
        if hasattr(self, "lbl_orch"):
            self.lbl_orch.configure(text=text)

    # -- manual plan editing -------------------------------------------------
    def _selected_mission_id(self):
        sel = self.tree.selection()
        if not sel:
            return None
        ident = sel[0]
        for getter in (self.engine.store.get_task,
                       self.engine.store.get_goal):
            row = getter(ident)
            if row:
                return row["mission_id"]
        mission = self.engine.store.get_mission(ident)
        return mission["id"] if mission else None

    def _selected_goal_id(self):
        sel = self.tree.selection()
        if not sel:
            return ""
        ident = sel[0]
        if self.engine.store.get_goal(ident):
            return ident
        task = self.engine.store.get_task(ident)
        return task.get("goal_id", "") if task else ""

    def _add_mission_clicked(self):
        title = simpledialog.askstring("New mission", "Mission title:",
                                       parent=self)
        if not title:
            return
        brief = simpledialog.askstring(
            "New mission", "What is this mission for? (optional)",
            parent=self) or ""
        api.dispatch(self.engine, "mission.create",
                     {"title": title, "brief": brief}, "local")
        self.refresh_missions()
        self._reload_mission_choices()

    def _add_goal_clicked(self):
        mission_id = self._selected_mission_id()
        if not mission_id:
            messagebox.showinfo("Add goal",
                                "Select a mission first.", parent=self)
            return
        title = simpledialog.askstring(
            "Add goal", "Goal (a milestone, not an action):", parent=self)
        if not title:
            return
        description = simpledialog.askstring(
            "Add goal", "What does 'done' mean? (optional)",
            parent=self) or ""
        api.dispatch(self.engine, "goal.create",
                     {"mission_id": mission_id, "title": title,
                      "description": description}, "local")
        self.refresh_missions()

    def _add_task_clicked(self):
        mission_id = self._selected_mission_id()
        if not mission_id:
            messagebox.showinfo(
                "Add task", "Select a mission or a goal first.", parent=self)
            return
        goal_id = self._selected_goal_id()
        title = simpledialog.askstring("Add task", "Task title:", parent=self)
        if not title:
            return
        instructions = simpledialog.askstring(
            "Add task", "What must the executor do?", parent=self)
        if not instructions:
            return
        api.dispatch(self.engine, "task.delegate",
                     {"mission_id": mission_id, "goal_id": goal_id,
                      "title": title, "instructions": instructions}, "local")
        self.refresh_missions()

    def _on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        ident = sel[0]
        task = self.engine.store.get_task(ident)
        if task:
            mission = self.engine.store.get_mission(task["mission_id"])
            self.lbl_detail_title.configure(text=task["title"])
            self.lbl_detail_meta.configure(
                text="%s  ·  %s  ·  %s  ·  mission: %s"
                     % (task["status"], task["model"] or "-",
                        catalog.fmt_cost(task["cost_usd"] or 0),
                        mission["title"] if mission else "-"))
            body = []
            body.append("INSTRUCTIONS\n" + (task["instructions"] or "-"))
            if task["context"]:
                body.append("\n\nCONTEXT\n" + task["context"])
            if task["result"]:
                body.append("\n\nRESULT\n" + task["result"])
            if task["error"]:
                body.append("\n\nERROR\n" + task["error"])
            agents = self.engine.store.list_agents(task_id=task["id"])
            if agents:
                lines = ["\n\nSUB-AGENTS"]
                for a in agents:
                    lines.append("  %-14s %-18s %-8s %s"
                                 % (a["name"], a["model"], a["status"],
                                    catalog.fmt_cost(a["cost_usd"] or 0)))
                body.append("\n".join(lines))
            self._set_detail("".join(body), ident)
            return
        goal = self.engine.store.get_goal(ident)
        if goal:
            tasks = [t for t in self.engine.store.list_tasks(goal["mission_id"])
                     if t.get("goal_id") == goal["id"]]
            self.lbl_detail_title.configure(text=goal["title"])
            self.lbl_detail_meta.configure(
                text="goal  ·  %s  ·  %d task(s)" % (goal["status"],
                                                     len(tasks)))
            body = ["DEFINITION OF DONE\n" + (goal["description"] or "-"),
                    "\n\nTASKS"]
            for t in tasks:
                body.append("\n  [%s] %s" % (t["status"], t["title"]))
            self._set_detail("".join(body), ident)
            return

        mission = self.engine.store.get_mission(ident)
        if mission:
            self.lbl_detail_title.configure(text=mission["title"])
            tasks = self.engine.store.list_tasks(mission["id"])
            goals = self.engine.store.list_goals(mission["id"])
            repo = self.engine.workspace_for(mission)
            hold = (mission.get("meta") or {}).get("hold_reason") \
                if mission["status"] == "on_hold" else ""
            self.lbl_detail_meta.configure(
                text="%s  ·  %d goal(s), %d task(s)  ·  %s  ·  by %s"
                     % (mission["status"], len(goals), len(tasks),
                        catalog.fmt_cost(mission["cost_usd"] or 0),
                        mission["orchestrator"]))
            body = (["ON HOLD - the orchestrator needs you\n%s\nUse Resume mission when "
                     "ready.\n\n" % hold] if hold else []) + ["REPOSITORY\n" + repo,
                    "\n\nBRIEF\n" + (mission["brief"] or "-"), "\n\nPLAN"]
            for g in goals:
                body.append("\n  [%s] %s" % (g["status"], g["title"]))
                for t in tasks:
                    if t.get("goal_id") == g["id"]:
                        body.append("\n      [%s] %s" % (t["status"],
                                                         t["title"]))
            loose = [t for t in tasks if not t.get("goal_id")]
            if loose:
                body.append("\n  (no goal)")
                for t in loose:
                    body.append("\n      [%s] %s" % (t["status"], t["title"]))
            self._set_detail("".join(body), ident)

    def _set_detail(self, text, ident=None):
        same = ident is not None and ident == getattr(self, "_detail_ident",
                                                      None)
        top = self.detail.yview()[0] if same else 0.0
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")
        self.detail.yview_moveto(top)
        self._detail_ident = ident

    def _copy_detail(self):
        text = self.detail.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)

    def _cancel_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        task = self.engine.store.get_task(sel[0])
        if not task:
            return
        self.engine.cancel_task(task["id"])
        self.refresh_missions()

    def _resume_selected(self):
        mission_id = self._selected_mission_id()
        mission = self.engine.store.get_mission(mission_id) if mission_id else None
        if not mission or mission["status"] != "on_hold":
            messagebox.showinfo("Resume mission",
                                "Select a mission the orchestrator paused (status on_hold).",
                                parent=self)
            return
        self.engine.resume_mission(mission_id)
        self.refresh_missions()

    def _retry_task(self):
        sel = self.tree.selection()
        if not sel:
            return
        task = self.engine.store.get_task(sel[0])
        if not task:
            return
        if task["status"] in ("queued", "claimed", "running"):
            messagebox.showinfo("Retry task",
                                "This task has not finished yet.", parent=self)
            return
        self.engine.delegate(task["mission_id"], task["title"] + " (retry)",
                             task["instructions"], task["context"],
                             task["priority"],
                             goal_id=task.get("goal_id") or "")
        self.refresh_missions()

    # ---- fleet page --------------------------------------------------------
    def _render_agent_models(self):
        """(Re)draw the model list, grouped by provider, with who uses each."""
        for child in self.fleet_inner.winfo_children():
            child.destroy()
        used_by = {}
        for member in config.helper_roster(self.settings):
            used_by.setdefault(member["model"], []).append(member["name"])
        creds = providers.credentials_status(self.settings)
        row = 0
        for pid in list(catalog.API_PROVIDERS) + [catalog.CLI]:
            specs = [m for m in _all_models() if m.provider == pid]
            if not specs:
                continue
            info = catalog.PROVIDERS.get(pid)
            label = info.label if info else pid
            if pid == catalog.CLI:
                state = "local, uses your plan"
            else:
                state = ("key set" if creds.get(pid) else "no key configured")
            ttk.Label(self.fleet_inner,
                      text="%s  -  %s" % (label.upper(), state),
                      style="PanelDim.TLabel").grid(
                row=row, column=0, sticky="w", pady=(8, 2))
            row += 1
            for spec in specs:
                line = ttk.Frame(self.fleet_inner, style="Panel.TFrame")
                line.grid(row=row, column=0, sticky="ew", pady=1)
                row += 1
                team = used_by.get(spec.id)
                ttk.Label(line, text="%-26s" % spec.id,
                          style="Panel.TLabel" if team else "PanelDim.TLabel",
                          font=self.fonts["mono"]).pack(side="left")
                if team:
                    ttk.Label(line, text="  helper: %s" % ", ".join(team),
                              style="Panel.TLabel").pack(side="left")
                detail = catalog.price_label(spec)
                if spec.subscription and not catalog.cli_available(spec.id):
                    detail += "  ·  %s NOT on PATH" % spec.cli_bin
                if spec.discovered:
                    detail += "  ·  discovered"
                ttk.Label(line, text="  %s  ·  %s" % (detail, spec.notes),
                          style="PanelDim.TLabel").pack(side="left")
        last = self.settings.get("models_last_refreshed")
        self.lbl_fleet_hint.configure(
            text="Choose which models your helpers use in Settings > Your "
                 "team. Prices marked (approx) come from secondary sources - "
                 "check the provider before relying on them."
                 + ("   Last refreshed: %s" % last if last else ""))

    def _refresh_models_clicked(self):
        configured = providers.configured_providers(self.settings)
        if not configured:
            messagebox.showinfo(
                "Check for new models",
                "No provider API keys are configured, so there is nothing to "
                "query.\n\nAdd a key under Settings -> Account for Anthropic, "
                "OpenAI, Google, Moonshot or Alibaba, then try again.\n\n"
                "Subscription CLI models do not need this - they always "
                "track whatever your local CLI supports.",
                parent=self)
            return
        self.btn_refresh_models.configure(state="disabled",
                                          text="Checking...")

        def work():
            try:
                report = self.engine.refresh_models()
                self.events.put({"kind": "models_result", "result": report, "error": None})
            except Exception as exc:
                self.events.put({"kind": "models_result", "result": None, "error": exc})

        threading.Thread(target=work, name="model-refresh",
                         daemon=True).start()

    def _refresh_models_done(self, report, error):
        self.btn_refresh_models.configure(
            state="normal", text="Check providers for new models")
        if error is not None:
            messagebox.showerror("Check for new models", str(error),
                                 parent=self)
            return
        self._render_agent_models()
        self._sync_model_widgets()
        lines = []
        if report["new"]:
            lines.append("Added %d model(s):" % len(report["new"]))
            for item in report["new"][:25]:
                lines.append("   %s  (%s)" % (item["id"], item["provider"]))
            if len(report["new"]) > 25:
                lines.append("   ... and %d more" % (len(report["new"]) - 25))
            lines.append("")
            lines.append("New models have no published price, so they show as "
                         "$0.00 until you set one. Give one to a helper in "
                         "Settings > Your team to let the executor use it.")
        else:
            lines.append("Nothing new. Checked %d provider(s), %d model(s)."
                         % (len(report["checked"]), report["total_seen"]))
        if report["errors"]:
            lines.append("")
            lines.append("Problems:")
            for pid, err in report["errors"].items():
                lines.append("   %s: %s" % (pid, err[:120]))
        messagebox.showinfo("Check for new models", "\n".join(lines),
                            parent=self)

    def refresh_fleet(self):
        self.agent_tree.delete(*self.agent_tree.get_children())
        for a in self.engine.store.list_agents(limit=200):
            self.agent_tree.insert(
                "", "end", iid=a["id"],
                values=(a["name"], a["model"], a["status"],
                        catalog.fmt_cost(a["cost_usd"] or 0),
                        "%d / %d" % (a["in_tokens"], a["out_tokens"]),
                        time.strftime("%H:%M:%S",
                                      time.localtime(a["created_at"]))))

    def _show_agent_output(self, _event=None):
        sel = self.agent_tree.selection()
        if not sel:
            return
        agent = self.engine.store.get_agent(sel[0])
        if not agent:
            return
        self._popup("%s · %s" % (agent["name"], agent["model"]),
                    "PROMPT\n%s\n\nOUTPUT\n%s"
                    % (agent["prompt"], agent["result"]))

    def _popup(self, title, body):
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=self.colours["panel"])
        win.geometry("760x520")
        txt = tk.Text(win, wrap="word", bd=0, highlightthickness=0,
                      bg=self.colours["panel"], fg=self.colours["fg"],
                      font=self.fonts["mono"], padx=12, pady=10)
        txt.pack(fill="both", expand=True, side="left")
        bar = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        bar.pack(fill="y", side="right")
        txt.configure(yscrollcommand=bar.set)
        txt.insert("1.0", body)
        txt.configure(state="disabled")

    # ---- usage page --------------------------------------------------------
    def refresh_usage(self):
        stats = self.engine.stats()
        self.usage_tiles["today"].configure(
            text=catalog.fmt_cost(stats["today_usd"]))
        self.usage_tiles["total"].configure(
            text=catalog.fmt_cost(stats["total_usd"]))
        self.usage_tiles["calls"].configure(text=str(stats["calls"]))
        self.usage_tiles["tokens"].configure(
            text="%s / %s" % (_thousands(stats["in_tokens"]),
                              _thousands(stats["out_tokens"])))
        self.usage_tree.delete(*self.usage_tree.get_children())
        for row in self.engine.store.usage_summary():
            self.usage_tree.insert(
                "", "end",
                values=(row["model"], row["calls"],
                        _thousands(row["in_tokens"] or 0),
                        _thousands(row["out_tokens"] or 0),
                        catalog.fmt_cost(row["cost_usd"] or 0)))

    def _apply_caps(self):
        try:
            self.settings.set("budget_usd_per_mission",
                              float(self.var_cap_mission.get()))
            self.settings.set("budget_usd_daily",
                              float(self.var_cap_daily.get()))
        except ValueError:
            messagebox.showerror("Budget", "Caps must be numbers.", parent=self)
            return
        self.settings.save()
        self._refresh_status()

    def _copy_config(self):
        self.clipboard_clear()
        self.clipboard_append(self.txt_config.get("1.0", "end").strip())
        messagebox.showinfo("Copied", "MCP configuration copied to clipboard.",
                            parent=self)

    # ---- settings page -----------------------------------------------------
    def _save_settings(self):
        # Write only what the operator edited. The form holds values from
        # when it was last filled; saving all of them would silently undo
        # changes made meanwhile by Codex (auto-approve off, a new
        # workspace) or by the quick controls elsewhere in this window.
        updates = {}
        for key, (var, kind) in self._s_vars.items():
            raw = var.get()
            if raw == self._s_base.get(key):
                continue
            try:
                if kind is bool:
                    updates[key] = bool(raw)
                elif kind is list:
                    updates[key] = [p.strip() for p in str(raw).split(",")
                                    if p.strip()]
                elif kind is int:
                    updates[key] = int(str(raw).strip() or 0)
                elif kind is float:
                    updates[key] = float(str(raw).strip() or 0)
                else:
                    updates[key] = str(raw)
            except ValueError:
                messagebox.showerror("Settings",
                                     "'%s' must be a number." % key,
                                     parent=self)
                return
        persona = self.txt_persona.get("1.0", "end").strip()
        if persona != self._persona_base:
            updates["executor_system_extra"] = persona
        workspace = updates.pop("workspace", None)
        if workspace is not None and os.path.normcase(
                os.path.abspath(os.path.expanduser(workspace.strip()))) != \
                os.path.normcase(self.settings.get("workspace") or ""):
            # Through the engine, so existing missions keep their folder.
            try:
                self.engine.set_workspace(workspace)
            except Exception as exc:
                messagebox.showerror("Workspace", str(exc), parent=self)
                return
        releasing = updates.get("auto_approve") is True and \
            not self.settings.get("auto_approve")
        self.settings.update(updates)
        self.settings.save()
        if releasing:
            # Release anything already blocked; otherwise it waits for the
            # approval timeout, which denies by default.
            self.engine.resolve_all_approvals(True, "auto-approve enabled")
            self._refresh_approvals()
        self._fill_settings_form()
        self.var_workspace.set(self.settings.get("workspace"))
        self._refresh_workspace_info()
        self.var_executor.set(self.settings.get("executor_model"))
        self.var_effort.set(self.settings.get("executor_effort"))
        self._render_helpers()
        self.var_shell.set(bool(self.settings.get("allow_shell")))
        self.var_budget_on.set(bool(self.settings.get("budget_enabled")))
        self.var_auto_approve.set(bool(self.settings.get("auto_approve")))
        self.var_account.set(self.ACCOUNT_LABELS.get(
            self.settings.get("account_mode"), self.ACCOUNT_LABELS["api"]))
        self.var_orchestrator.set(self._orchestrator_label())
        self.lbl_saved.configure(text="Saved at " + time.strftime("%H:%M:%S"))
        self._sync_orchestrator_ui()
        self._refresh_status()

    def _fill_settings_form(self):
        """Show current settings in the form and remember them as unedited."""
        for key, (var, kind) in self._s_vars.items():
            value = self.settings.get(key)
            if kind is bool:
                var.set(bool(value))
            else:
                var.set(self._to_entry_text(value, kind))
            self._s_base[key] = var.get()
        self.txt_persona.delete("1.0", "end")
        self.txt_persona.insert("1.0",
                                self.settings.get("executor_system_extra") or "")
        self._persona_base = self.txt_persona.get("1.0", "end").strip()

    def _reload_settings(self):
        self.settings.load()
        self._fill_settings_form()
        self._sync_model_widgets()
        self._sync_orchestrator_ui()
        self._refresh_status()
        self.lbl_saved.configure(text="Reloaded")

    # ---- shutdown ----------------------------------------------------------
    def _on_close(self):
        active = self.engine.active_tasks()
        if active:
            if not messagebox.askyesno(
                    "Quit",
                    "%d task(s) are still running. Quit anyway?" % len(active),
                    parent=self):
                return
        try:
            self.hub_server.stop()
            self.engine.shutdown()
        except Exception:
            pass
        self.destroy()


CHECKIN_CHOICES = {"Off": 0, "Every 3 steps": 3, "Every 6 steps": 6,
                   "Every 10 steps": 10, "Every 15 steps": 15}


def _checkin_label(steps):
    for label, value in CHECKIN_CHOICES.items():
        if value == int(steps or 0):
            return label
    return "Every %d steps" % int(steps)


def _all_models():
    """Every known model, executors and orchestrators first."""
    first = [m for m in catalog.CATALOG
             if "executor" in m.tags or "orchestrator" in m.tags]
    return first + [m for m in catalog.CATALOG if m not in first]


def _human_size(size):
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%d %s" % (size, unit) if unit == "B" \
                else "%.1f %s" % (size, unit)
        size /= 1024.0
    return "%d B" % size


def _thousands(n):
    try:
        return "{:,}".format(int(n))
    except Exception:
        return str(n)


def _task_status_label(task):
    """Task status plus the orchestrator's verdict, once it has reviewed."""
    meta = task.get("meta") if isinstance(task.get("meta"), dict) else {}
    verdict = (meta.get("review") or {}).get("verdict")
    if verdict == "accept":
        return "%s · accepted" % task["status"]
    if verdict == "revise":
        return "%s · sent back" % task["status"]
    return task["status"]


def _q(text):
    return '"%s"' % str(text).replace("\\", "\\\\").replace('"', '\\"')


def main():
    try:
        app = App()
    except AlreadyRunning:
        return 0
    app.mainloop()
    return 0
