"""CollaboratorMCP entry point.

    main.py                 launch the desktop app (UI + MCP bridge)
    main.py --mcp           run as an MCP stdio server (for any MCP client)
    main.py --headless      run the engine + hub with no window
    main.py --task "..."    delegate one task from the command line and wait
"""

import argparse
import os
import sys


def _ensure_package_importable():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def run_mcp():
    from collaborator.mcp_server import main as mcp_main
    return mcp_main()


def run_headless():
    import time
    from collaborator import hub
    from collaborator.config import settings as get_settings
    from collaborator.engine import Engine

    settings = get_settings()
    engine = Engine(settings)
    server = hub.HubServer(engine, settings.get("hub_host") or "127.0.0.1",
                           int(settings.get("hub_port") or 8787))
    if not server.start():
        engine.shutdown()
        engine.store.close()
        sys.stderr.write("Hub port %s is already in use.\n"
                         % settings.get("hub_port"))
        return 1
    engine.start()
    sys.stdout.write("CollaboratorMCP engine on %s:%s. Ctrl+C to stop.\n"
                     % (settings.get("hub_host"), settings.get("hub_port")))
    sys.stdout.flush()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        engine.shutdown()
    return 0


def run_task(title, instructions, context, timeout):
    from collaborator import api
    from collaborator.config import settings as get_settings
    from collaborator.engine import Engine

    settings = get_settings()
    engine = Engine(settings)
    engine.start()
    engine.subscribe(lambda e: sys.stdout.write(
        "[%s] %s %s\n" % (e["kind"], e.get("actor", ""), e.get("text", ""))))
    task = api.dispatch(engine, "task.delegate",
                        {"title": title, "instructions": instructions,
                         "context": context})
    result = api.dispatch(engine, "task.wait",
                          {"task_id": task["task_id"], "timeout": timeout})
    sys.stdout.write("\n--- %s ---\n%s\n"
                     % (result.get("status"), result.get("result") or
                        result.get("error") or ""))
    engine.shutdown()
    return 0 if result.get("status") in ("done", "partial") else 1


def run_ui():
    try:
        from collaborator.ui.app import main as ui_main
    except ImportError as exc:
        sys.stderr.write(
            "The desktop UI needs tkinter, which is not available: %s\n"
            "Use --mcp or --headless instead.\n" % exc)
        return 2
    return ui_main()


def main(argv=None):
    _ensure_package_importable()
    parser = argparse.ArgumentParser(
        prog="CollaboratorMCP",
        description="Any model can orchestrate, execute, or assist - you choose the roles.")
    parser.add_argument("--mcp", action="store_true",
                        help="run as an MCP stdio server")
    parser.add_argument("--headless", action="store_true",
                        help="run the engine and hub without a window")
    parser.add_argument("--task", metavar="TITLE",
                        help="delegate one task and wait for the result")
    parser.add_argument("--instructions", default="",
                        help="instructions for --task")
    parser.add_argument("--context", default="", help="context for --task")
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds to wait for --task (default 900)")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--connect-codex", action="store_true",
                        help="register this MCP server with Codex")
    parser.add_argument("--check-connection", action="store_true",
                        help="check the MCP handshake and Codex sign-in")
    args = parser.parse_args(argv)

    if args.version:
        from collaborator import __version__
        sys.stdout.write("CollaboratorMCP %s\n" % __version__)
        return 0
    if args.connect_codex or args.check_connection:
        from collaborator import connection
        try:
            print(connection.register_codex() if args.connect_codex
                  else connection.test_connection())
            return 0
        except Exception as exc:
            sys.stderr.write(str(exc) + "\n")
            return 1
    if args.mcp:
        return run_mcp()
    if args.headless:
        return run_headless()
    if args.task:
        return run_task(args.task, args.instructions or args.task,
                        args.context, args.timeout)
    return run_ui()


if __name__ == "__main__":
    sys.exit(main())
