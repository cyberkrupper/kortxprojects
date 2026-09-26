"""Codex registration and a real, read-only MCP handshake check."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from .cli import find_cli, command
from .providers import no_window_kwargs


def server_command():
    executable = Path(sys.executable)
    if getattr(sys, "frozen", False):
        server = executable.with_name("collaborator-mcp.exe")
        if not server.is_file():
            raise RuntimeError("The console server collaborator-mcp.exe must be next to the app.")
        return [str(server)]
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    return [str(executable), str(Path(__file__).resolve().parent.parent / "main.py"), "--mcp"]


def config_snippet(client="codex"):
    argv = server_command()
    if client == "codex":
        return ("[mcp_servers.collaborator]\ncommand = %s\nargs = %s\n"
                "startup_timeout_sec = 30\ntool_timeout_sec = 960\n" %
                (json.dumps(argv[0]), json.dumps(argv[1:])))
    return json.dumps({"mcpServers": {"collaborator": {
        "command": argv[0], "args": argv[1:]}}}, indent=2) + "\n"


def codex_status():
    binary = find_cli("codex")
    if not binary:
        return "Codex CLI not found. Install Codex and sign in first."
    result = subprocess.run(command(binary) + ["login", "status"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=20, **no_window_kwargs())
    return (result.stdout + result.stderr).strip() or "Codex sign-in status unavailable."


def register_codex():
    binary = find_cli("codex")
    if not binary:
        raise RuntimeError("Codex CLI not found. Install Codex, then try again.")
    # Let Codex merge its own config. Keep a recoverable copy first.
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    config = home / "config.toml"
    if config.exists():
        shutil.copy2(config, home / ("config.toml.collaborator-%s.bak" % time.time_ns()))
    result = subprocess.run(command(binary) + ["mcp", "add", "collaborator", "--"]
                            + server_command(), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30,
                            **no_window_kwargs())
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    # Long task waits need more than Codex's default tool timeout.
    text = config.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines)
                 if line.strip() == "[mcp_servers.collaborator]")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].lstrip().startswith("[")), len(lines))
    section = [line for line in lines[start + 1:end]
               if not line.strip().startswith(("startup_timeout_sec", "tool_timeout_sec"))]
    lines[start + 1:end] = ["startup_timeout_sec = 30\n", "tool_timeout_sec = 960\n"] + section
    temporary = config.with_name("config.toml.collaborator.tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    os.replace(temporary, config)
    return "Registered CollaboratorMCP in Codex. Restart Codex to load the new tools."


def test_connection():
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "Collaborator connection check", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    env = dict(os.environ, COLLABORATORMCP_PROBE="1")
    result = subprocess.run(server_command(), input="".join(json.dumps(m) + "\n" for m in messages),
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=20, env=env, **no_window_kwargs())
    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    by_id = {reply.get("id"): reply for reply in replies}
    if result.returncode or "result" not in by_id.get(1, {}) or "result" not in by_id.get(2, {}):
        raise RuntimeError("MCP handshake failed: " + (result.stderr or result.stdout)[-1500:])
    count = len(by_id[2]["result"]["tools"])
    return "MCP handshake passed; %d tools available.\n%s" % (count, codex_status())
