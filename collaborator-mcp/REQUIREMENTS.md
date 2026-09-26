# Requirements

- **OS:** Windows 10/11 (the launcher and packaged builds target Windows).
- **Python:** 3.10 or newer with `tkinter` (bundled with CPython on Windows) to run from source.
- **Packages:** `pip install -r requirements.txt` (`anthropic`, `requests`; `pyinstaller` only to build the `.exe`).
- **Models:** for every model you assign to a role, either
  - an API key for its provider (Settings > Account), or
  - a signed-in [Claude Code](https://claude.com/claude-code) CLI (Pro/Max plan) or Codex CLI (ChatGPT Plus/Pro plan). Subscription models need no API key.
- **Optional:** an MCP client such as Codex, to orchestrate from outside the app.
- Only one Collaborator window can run at a time.

## Run

- From source: double-click `Start CollaboratorMCP.bat`, or run `python main.py`.
- MCP stdio server for a client: `python main.py --mcp`.
- Build executables: `python build.py` (outputs to `dist/`).
