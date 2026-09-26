"""Dark / light palettes and ttk styling for the CollaboratorMCP UI."""

from tkinter import font as tkfont
from tkinter import ttk


DARK = {
    "bg":          "#14161b",
    "panel":       "#1b1e25",
    "panel_alt":   "#21252e",
    "sidebar":     "#101217",
    "border":      "#2b303b",
    "fg":          "#e6e9ef",
    "fg_dim":      "#98a1b2",
    "fg_faint":    "#6b7383",
    "accent":      "#7c8cff",
    "accent_dim":  "#5a68cc",
    "ok":          "#48c78e",
    "warn":        "#f0b429",
    "err":         "#f0616d",
    "info":        "#4fb8e8",
    "agent":       "#c48aff",
    "input":       "#0e1015",
    "select":      "#2c3446",
}

LIGHT = {
    "bg":          "#f4f5f8",
    "panel":       "#ffffff",
    "panel_alt":   "#eef0f5",
    "sidebar":     "#e7e9f0",
    "border":      "#d2d6e0",
    "fg":          "#1c2027",
    "fg_dim":      "#5b6373",
    "fg_faint":    "#868d9c",
    "accent":      "#4a58d6",
    "accent_dim":  "#7c8cff",
    "ok":          "#1d8b5f",
    "warn":        "#9a6c00",
    "err":         "#c2323e",
    "info":        "#1a7aa8",
    "agent":       "#7b3fb8",
    "input":       "#ffffff",
    "select":      "#d5dbf5",
}

# Event kind -> palette key used for colouring the activity log.
KIND_COLOUR = {
    "system":  "info",
    "mission": "accent",
    "orchestrator": "accent",
    "task":    "fg",
    "tool":    "fg_dim",
    "agent":   "agent",
    "note":    "warn",
    "message": "fg",
    "file":    "ok",
    "shell":   "fg_dim",
    "error":    "err",
    "budget":   "warn",
    "approval": "warn",
    "steer":    "accent",
}


def palette(name):
    return dict(DARK if name == "dark" else LIGHT)


class Tooltip(object):
    """A pale popup that appears after the pointer rests on a widget.

    ``text`` may be a callable, so a tooltip can describe whatever is
    currently under the cursor (notebook tabs, tree rows, and so on).
    """

    BG = "#fdfdf7"
    FG = "#1c2027"
    BORDER = "#9aa1b0"

    def __init__(self, widget, text, delay=2000, wraplength=420, fonts=None):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self.fonts = fonts or {}
        self._after = None
        self._window = None
        self._last = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<Motion>", self._on_motion, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    # -- resolve ------------------------------------------------------------
    def _resolve(self, event):
        if callable(self.text):
            try:
                return self.text(event)
            except Exception:
                return ""
        return self.text

    # -- events -------------------------------------------------------------
    def _on_enter(self, event=None):
        self._schedule(event)

    def _on_motion(self, event):
        # Restart the timer only when the pointer moves to a different target,
        # otherwise a resting hand would never trigger the popup.
        current = self._resolve(event)
        if current != self._last:
            self._last = current
            self._hide()
            self._schedule(event)

    def _on_leave(self, _event=None):
        self._cancel()
        self._hide()
        self._last = None

    def _schedule(self, event):
        self._cancel()
        x = self.widget.winfo_pointerx() + 14
        y = self.widget.winfo_pointery() + 20
        text = self._resolve(event)
        if not text:
            return
        self._after = self.widget.after(self.delay,
                                        lambda: self._show(text, x, y))

    def _cancel(self):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    # -- window -------------------------------------------------------------
    def _show(self, text, x, y):
        import tkinter as tk
        self._hide()
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        win.configure(bg=self.BORDER)
        label = tk.Label(win, text=text, justify="left", bg=self.BG,
                         fg=self.FG, wraplength=self.wraplength,
                         font=self.fonts.get("small"), padx=10, pady=7,
                         bd=0)
        label.pack(padx=1, pady=1)
        win.update_idletasks()
        # Keep it on screen.
        width, height = win.winfo_width(), win.winfo_height()
        screen_w = win.winfo_screenwidth()
        screen_h = win.winfo_screenheight()
        x = min(x, screen_w - width - 8)
        y = min(y, screen_h - height - 8)
        win.wm_geometry("+%d+%d" % (max(0, x), max(0, y)))
        try:
            win.wm_attributes("-topmost", True)
        except Exception:
            pass
        self._window = win

    def _hide(self):
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
            self._window = None


def fonts(root):
    family = "Segoe UI"
    mono = "Consolas"
    available = set(tkfont.families(root))
    if family not in available:
        family = "Helvetica"
    if mono not in available:
        mono = "Courier"
    return {
        "base":    (family, 10),
        "small":   (family, 9),
        "tiny":    (family, 8),
        "bold":    (family, 10, "bold"),
        "h1":      (family, 16, "bold"),
        "h2":      (family, 12, "bold"),
        "nav":     (family, 11),
        "mono":    (mono, 9),
        "mono_b":  (mono, 9, "bold"),
    }


def apply(root, colours, fnt):
    """Configure ttk styles for the given palette."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    c = colours
    root.configure(bg=c["bg"])

    style.configure(".", background=c["bg"], foreground=c["fg"],
                    fieldbackground=c["input"], bordercolor=c["border"],
                    font=fnt["base"])

    style.configure("TFrame", background=c["bg"])
    style.configure("Panel.TFrame", background=c["panel"])
    style.configure("Sidebar.TFrame", background=c["sidebar"])
    style.configure("Card.TFrame", background=c["panel"], relief="flat")

    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure("Panel.TLabel", background=c["panel"], foreground=c["fg"])
    style.configure("Sidebar.TLabel", background=c["sidebar"],
                    foreground=c["fg_dim"], font=fnt["small"])
    style.configure("SidebarHead.TLabel", background=c["sidebar"],
                    foreground=c["fg_faint"], font=fnt["tiny"])
    style.configure("Dim.TLabel", background=c["bg"], foreground=c["fg_dim"],
                    font=fnt["small"])
    style.configure("PanelDim.TLabel", background=c["panel"],
                    foreground=c["fg_dim"], font=fnt["small"])
    style.configure("H1.TLabel", background=c["bg"], foreground=c["fg"],
                    font=fnt["h1"])
    style.configure("H2.TLabel", background=c["bg"], foreground=c["fg"],
                    font=fnt["h2"])
    style.configure("PanelH2.TLabel", background=c["panel"], foreground=c["fg"],
                    font=fnt["h2"])
    style.configure("Brand.TLabel", background=c["sidebar"],
                    foreground=c["fg"], font=fnt["h2"])
    light_blue = "#8ec5ff" if c["sidebar"] == "#101217" else "#4d94dd"
    style.configure("BrandBy.TLabel", background=c["sidebar"],
                    foreground=light_blue, font=(fnt["tiny"][0], 7))
    style.configure("BrandLink.TLabel", background=c["panel"],
                    foreground=c["accent"], font=fnt["tiny"])
    style.configure("Chip.TLabel", background=c["panel_alt"],
                    foreground=c["fg_dim"], font=fnt["small"],
                    padding=(8, 3))

    style.configure("TButton", background=c["panel_alt"], foreground=c["fg"],
                    borderwidth=0, focuscolor=c["panel_alt"], padding=(12, 6))
    style.map("TButton",
              background=[("active", c["select"]), ("disabled", c["panel"])],
              foreground=[("disabled", c["fg_faint"])])

    style.configure("Accent.TButton", background=c["accent"],
                    foreground="#ffffff", borderwidth=0, padding=(14, 7),
                    font=fnt["bold"])
    style.map("Accent.TButton",
              background=[("active", c["accent_dim"]),
                          ("disabled", c["panel_alt"])],
              foreground=[("disabled", c["fg_faint"])])

    style.configure("Danger.TButton", background=c["panel_alt"],
                    foreground=c["err"], borderwidth=0, padding=(12, 6))
    style.map("Danger.TButton", background=[("active", c["select"])])

    style.configure("Nav.TButton", background=c["sidebar"], foreground=c["fg_dim"],
                    borderwidth=0, anchor="w", padding=(14, 7),
                    font=fnt["nav"])
    style.map("Nav.TButton",
              background=[("active", c["panel_alt"])],
              foreground=[("active", c["fg"])])

    style.configure("NavActive.TButton", background=c["panel_alt"],
                    foreground=c["accent"], borderwidth=0, anchor="w",
                    padding=(14, 7), font=fnt["nav"])
    style.map("NavActive.TButton", background=[("active", c["panel_alt"])])

    style.configure("TEntry", fieldbackground=c["input"], foreground=c["fg"],
                    insertcolor=c["fg"], bordercolor=c["border"],
                    lightcolor=c["border"], darkcolor=c["border"],
                    padding=6)
    style.configure("TCombobox", fieldbackground=c["input"], foreground=c["fg"],
                    background=c["panel_alt"], arrowcolor=c["fg_dim"],
                    bordercolor=c["border"], padding=5)
    style.map("TCombobox",
              fieldbackground=[("readonly", c["input"])],
              foreground=[("readonly", c["fg"])])

    # Checkbutton indicators: clam draws a filled square, so the contrast
    # between "off" and "on" has to come from the indicator colours.
    # clam's indicator element exposes indicatorbackground / indicatorforeground
    # (not indicatorcolor, which it silently ignores).
    check_map = {
        "indicatorbackground": [("selected", c["accent"]),
                                ("active", "!selected", c["select"]),
                                ("!selected", c["input"])],
        "indicatorforeground": [("selected", "#ffffff")],
    }
    for name, bg, fg in (("TCheckbutton", c["bg"], c["fg"]),
                         ("Sidebar.TCheckbutton", c["sidebar"], c["fg_dim"]),
                         ("Panel.TCheckbutton", c["panel"], c["fg"])):
        style.configure(name, background=bg, foreground=fg, focuscolor=bg,
                        indicatormargin=(0, 0, 6, 0), indicatorsize=11,
                        borderwidth=0, upperbordercolor=c["border"],
                        lowerbordercolor=c["border"],
                        indicatorbackground=c["input"],
                        indicatorforeground="#ffffff",
                        font=fnt["small"] if "Sidebar" in name else fnt["base"])
        style.map(name, background=[("active", bg)],
                  foreground=[("active", c["fg"])], **check_map)

    style.configure("TNotebook", background=c["bg"], borderwidth=0)
    style.configure("TNotebook.Tab", background=c["panel_alt"],
                    foreground=c["fg_dim"], padding=(14, 7), borderwidth=0)
    style.map("TNotebook.Tab",
              background=[("selected", c["panel"])],
              foreground=[("selected", c["fg"])])

    style.configure("Treeview", background=c["panel"], fieldbackground=c["panel"],
                    foreground=c["fg"], borderwidth=0, rowheight=24)
    style.configure("Treeview.Heading", background=c["panel_alt"],
                    foreground=c["fg_dim"], borderwidth=0, font=fnt["small"],
                    padding=(6, 5))
    style.map("Treeview", background=[("selected", c["select"])],
              foreground=[("selected", c["fg"])])
    style.map("Treeview.Heading", background=[("active", c["select"])])

    style.configure("TSeparator", background=c["border"])
    style.configure("Vertical.TScrollbar", background=c["panel_alt"],
                    troughcolor=c["bg"], bordercolor=c["bg"],
                    arrowcolor=c["fg_faint"], darkcolor=c["panel_alt"],
                    lightcolor=c["panel_alt"])
    style.map("Vertical.TScrollbar", background=[("active", c["select"])])
    style.configure("Horizontal.TScrollbar", background=c["panel_alt"],
                    troughcolor=c["bg"], bordercolor=c["bg"],
                    arrowcolor=c["fg_faint"])

    style.configure("TScale", background=c["bg"], troughcolor=c["panel_alt"])
    style.configure("Horizontal.TProgressbar", background=c["accent"],
                    troughcolor=c["panel_alt"], borderwidth=0)
    return style
