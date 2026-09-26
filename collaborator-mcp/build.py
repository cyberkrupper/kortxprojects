"""Build the CollaboratorMCP executables with PyInstaller.

    python build.py

Produces, in dist/:
    CollaboratorMCP.exe    windowed desktop app (UI + MCP bridge)
    collaborator-mcp.exe   console MCP stdio server for MCP clients

Both are single-file builds and share the same settings and database.
"""

import os
import shutil
import subprocess
import sys


ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build")

HIDDEN = [
    "anthropic",
    "requests",
    "collaborator",
    "collaborator.api",
    "collaborator.catalog",
    "collaborator.config",
    "collaborator.engine",
    "collaborator.hub",
    "collaborator.mcp_server",
    "collaborator.providers",
    "collaborator.store",
    "collaborator.tools",
    "collaborator.ui",
    "collaborator.ui.app",
    "collaborator.ui.theme",
]

EXCLUDE = [
    "matplotlib", "numpy", "pandas", "scipy", "PyQt5", "PyQt6", "PySide2",
    "PySide6", "IPython", "notebook", "pytest", "setuptools._distutils",
]


def _check_pyinstaller():
    try:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--version"],
                       check=True, capture_output=True)
    except Exception:
        print("PyInstaller is missing. Install it with:")
        print("    %s -m pip install pyinstaller" % sys.executable)
        return False
    return True


def _run(name, script, windowed):
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--onefile", "--name", name,
            "--distpath", DIST,
            "--workpath", os.path.join(WORK, name),
            "--specpath", WORK,
            "--paths", ROOT]
    args.append("--windowed" if windowed else "--console")
    for mod in HIDDEN:
        args += ["--hidden-import", mod]
    for mod in EXCLUDE:
        args += ["--exclude-module", mod]
    icon = os.path.join(ROOT, "assets", "collaborator.ico")
    if os.path.exists(icon):
        args += ["--icon", icon]
    args.append(os.path.join(ROOT, script))

    print("\n=== building %s ===" % name)
    result = subprocess.run(args, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit("PyInstaller failed for %s (exit %d)"
                         % (name, result.returncode))


def main():
    if not _check_pyinstaller():
        return 1
    os.makedirs(WORK, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)

    _run("CollaboratorMCP", "main.py", windowed=True)
    _run("collaborator-mcp", "mcp_entry.py", windowed=False)

    print("\nBuilt:")
    for name in sorted(os.listdir(DIST)):
        path = os.path.join(DIST, name)
        if os.path.isfile(path):
            print("  %-26s %8.1f MB" % (name, os.path.getsize(path) / 1e6))
    print("\nRun dist/CollaboratorMCP.exe to open the app.")
    print("Point your MCP client at dist/collaborator-mcp.exe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
