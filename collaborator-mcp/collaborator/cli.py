"""Find local CLIs and bypass Windows npm command-shell wrappers."""

import os
import shutil


def find_cli(name):
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt":
        for path in (
            os.path.join(os.environ.get("APPDATA", ""), "npm", name + ".cmd"),
            os.path.join(os.path.expanduser("~"), ".local", "bin", name + ".exe"),
        ):
            if os.path.isfile(path):
                return path
    return None


def command(binary):
    """Use native argv for multiline prompts, quotes, and long arguments."""
    if os.name != "nt" or not binary.lower().endswith((".cmd", ".bat")):
        return [binary]
    root = os.path.dirname(binary)
    name = os.path.splitext(os.path.basename(binary))[0].lower()
    if name == "claude":
        native = os.path.join(root, "node_modules", "@anthropic-ai",
                              "claude-code", "bin", "claude.exe")
        if os.path.isfile(native):
            return [native]
        script = os.path.join(root, "node_modules", "@anthropic-ai",
                              "claude-code", "cli.js")
    elif name == "codex":
        script = os.path.join(root, "node_modules", "@openai", "codex",
                              "bin", "codex.js")
    else:
        return [binary]
    node = os.path.join(root, "node.exe")
    if not os.path.isfile(node):
        node = shutil.which("node")
    if node and os.path.isfile(script):
        return [node, script]
    return [binary]
