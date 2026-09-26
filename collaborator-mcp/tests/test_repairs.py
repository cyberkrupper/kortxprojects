import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from collaborator import catalog, cli, connection, providers
from collaborator.config import Settings
from collaborator.engine import Engine, PLAN_SCHEMA
from collaborator.store import Store


class Repairs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.settings = Settings(str(self.root / "settings.json"))
        self.settings.update({"workspace": str(self.root), "budget_enabled": False,
                              "orchestrator_mode": "external"})
        self.store = Store(str(self.root / "test.db"))
        self.engine = Engine(self.settings, self.store)

    def tearDown(self):
        self.engine.shutdown()
        self.store.close()
        self.tmp.cleanup()

    def test_mission_tasks_run_in_order_while_other_missions_can_run(self):
        first = self.engine.create_mission("First mission")
        second = self.engine.create_mission("Second mission")
        first_one = self.engine.delegate(first["id"], "First step", "Step one", priority=1)
        first_two = self.engine.delegate(first["id"], "Second step", "Step two", priority=2)
        other = self.engine.delegate(second["id"], "Independent", "Other work", priority=3)
        another_store = Store(str(self.root / "test.db"))
        try:
            claimed_one = self.store.claim_next_task(os.getpid())
            self.assertEqual(claimed_one["id"], first_one["id"])
            claimed_other = another_store.claim_next_task(os.getpid())
            self.assertEqual(claimed_other["id"], other["id"])
            self.assertIsNone(self.store.claim_next_task(os.getpid()))
            self.store.update_task(first_one["id"], status="done")
            claimed_two = another_store.claim_next_task(os.getpid())
            self.assertEqual(claimed_two["id"], first_two["id"])
        finally:
            another_store.close()

    def test_switching_workspace_keeps_existing_missions_in_their_folder(self):
        original = self.root / "original"
        destination = self.root / "destination"
        original.mkdir()
        destination.mkdir()
        self.settings.set("workspace", str(original))
        mission = self.engine.create_mission("Existing work")
        self.assertEqual(self.engine.workspace_for(mission), str(original))
        # Simulate a mission written by the previous version with no pin.
        legacy = self.store.create_mission("Legacy work")
        self.engine.set_workspace(str(destination))
        self.assertEqual(self.engine.workspace_for(self.store.get_mission(mission["id"])), str(original))
        self.assertEqual(self.engine.workspace_for(self.store.get_mission(legacy["id"])), str(original))
        new_mission = self.engine.create_mission("New work")
        self.assertEqual(self.engine.workspace_for(new_mission), str(destination))

    def test_api_executor_runs_tools_and_returns_results(self):
        self.settings.set("executor_model", "gpt-4.1-mini")
        self.settings.set("openai_api_key", "test-only")
        self.settings.set("executor_agent_review", False)
        task = self.engine.quick_task("write", "Write greeting.txt then finish")
        responses = []
        for name, arguments in [("write_file", {"path": "greeting.txt", "content": "hello"}),
                                ("finish", {"summary": "Created greeting.txt", "status": "done"})]:
            responses.append(Mock(status_code=200, json=Mock(return_value={
                "choices": [{"finish_reason": "tool_calls", "message": {
                    "content": None, "tool_calls": [{"id": name, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)}}]}}]})))
        with patch("requests.post", side_effect=responses) as post:
            result = self.engine.run_task(task)
        self.assertEqual(result["status"], "done", result)
        self.assertEqual((self.root / "greeting.txt").read_text(), "hello")
        first = json.loads(post.call_args_list[0].kwargs["data"])
        second = json.loads(post.call_args_list[1].kwargs["data"])
        self.assertTrue(any(t["function"]["name"] == "write_file" for t in first["tools"]))
        self.assertEqual(second["messages"][-1]["role"], "tool")
        self.assertEqual(second["messages"][-1]["tool_call_id"], "write_file")
        self.assertEqual(second["messages"][-2]["tool_calls"][0]["function"]["name"], "write_file")

    def test_claude_error_is_not_success(self):
        with patch("collaborator.providers.find_cli", return_value="claude"):
            provider = providers.CLIProvider(catalog.get("claude-cli"), self.settings)
        with patch.object(provider, "_run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps({"is_error": True, "result": "Not signed in"}), "")):
            with self.assertRaisesRegex(providers.ProviderError, "Not signed in"):
                provider.complete("claude-cli", [{"role": "user", "content": "hello"}])

    def test_codex_nonzero_with_partial_output_is_error(self):
        with patch("collaborator.providers.find_cli", return_value="codex"):
            provider = providers.CLIProvider(catalog.get("codex-cli"), self.settings)
        def failed(argv, *args, **kwargs):
            Path(argv[argv.index("--output-last-message") + 1]).write_text("partial")
            return subprocess.CompletedProcess(argv, 1, "", "authentication failed")
        with patch.object(provider, "_run", side_effect=failed):
            with self.assertRaisesRegex(providers.ProviderError, "authentication failed"):
                provider.complete("codex-cli", [{"role": "user", "content": "hello"}])

    def test_codex_planner_uses_native_output_schema(self):
        with patch("collaborator.providers.find_cli", return_value="codex"):
            provider = providers.CLIProvider(catalog.get("codex-cli"), self.settings)
        paths = []
        def answer(argv, *args, **kwargs):
            path = Path(argv[argv.index("--output-schema") + 1])
            self.assertEqual(json.loads(path.read_text()), PLAN_SCHEMA)
            paths.append(path)
            Path(argv[argv.index("--output-last-message") + 1]).write_text('{"goals":[]}')
            return subprocess.CompletedProcess(argv, 0, "", "")
        with patch.object(provider, "_run", side_effect=answer):
            provider.complete("codex-cli", [{"role": "user", "content": "Plan"}],
                              output_format={"schema": PLAN_SCHEMA})
        self.assertFalse(paths[0].exists())

    def test_codex_empty_answer_is_error(self):
        with patch("collaborator.providers.find_cli", return_value="codex"):
            provider = providers.CLIProvider(catalog.get("codex-cli"), self.settings)
        with patch.object(provider, "_run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            with self.assertRaisesRegex(providers.ProviderError, "no final answer"):
                provider.complete("codex-cli", [{"role": "user", "content": "hello"}])

    def test_planner_failure_resets_state_and_records_error(self):
        with patch("collaborator.providers.provider_for", side_effect=providers.ProviderError("offline")):
            with self.assertRaisesRegex(providers.ProviderError, "offline"):
                self.engine.plan_mission(title="Test", brief="Test")
        self.assertEqual(self.engine.orchestrator_state()["state"], "error")
        self.assertTrue(any("Planning failed" in e["text"] for e in self.store.list_events()))

    def test_planner_uses_mission_workspace(self):
        mission = self.engine.create_mission("Test")
        plan = {"goals": [{"title": "Goal", "description": "", "tasks": [
            {"title": "Task", "instructions": "Reply OK", "context": ""}]}]}
        provider = Mock()
        provider.complete.return_value = providers.LLMResult(text=json.dumps(plan), model="codex-cli")
        with patch("collaborator.providers.provider_for", return_value=provider):
            result = self.engine.plan_mission(mission_id=mission["id"])
        self.assertEqual(result["task_count"], 1)
        self.assertEqual(provider.complete.call_args.kwargs["workspace"], str(self.root))
        self.assertEqual(self.engine.orchestrator_state()["state"], "idle")

    def test_nonprotocol_cli_reply_is_partial(self):
        self.settings.set("executor_model", "codex-cli")
        task = self.engine.quick_task("Test", "Test")
        provider = Mock(supports_resume=False)
        provider.complete.return_value = providers.LLMResult(text="Could not act", model="codex-cli")
        with patch("collaborator.providers.provider_for", return_value=provider):
            result = self.engine.run_task(task)
        self.assertEqual(result["status"], "partial")

    def test_claude_native_resolution(self):
        native = self.root / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        native.parent.mkdir(parents=True)
        native.touch()
        with patch("collaborator.cli.os.name", "nt"):
            self.assertEqual(cli.command(str(self.root / "claude.cmd")), [str(native)])

    def test_codex_registration_preserves_other_config(self):
        config = self.root / "config.toml"
        before = 'model = "gpt-6-astra"\n[mcp_servers.existing]\ncommand = "existing"\n'
        config.write_text(before)
        def register(argv, **kwargs):
            config.write_text(before + '\n[mcp_servers.collaborator]\ncommand = "python"\nargs = []\n')
            return subprocess.CompletedProcess(argv, 0, "Added", "")
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root)}), \
                patch("collaborator.connection.find_cli", return_value="codex"), \
                patch("collaborator.connection.subprocess.run", side_effect=register):
            connection.register_codex()
        self.assertTrue(config.read_text().startswith(before))
        self.assertIn("tool_timeout_sec = 960", config.read_text())
        backups = list(self.root.glob("*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), before)

    def test_mcp_stdio_handshake(self):
        with patch("collaborator.connection.codex_status", return_value="Signed in"):
            self.assertIn("29 tools available", connection.test_connection())

    def test_mcp_calls_share_the_desktop_engine(self):
        from collaborator.hub import HubServer
        server = HubServer(self.engine, port=0)
        self.assertTrue(server.start())
        self.settings.set("hub_port", server._server.server_address[1])
        self.settings.save()
        self.engine.quick_task("Shared task", "Do not execute during this test")
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "clientInfo": {"name": "test-codex"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                "name": "collaborator_status", "arguments": {}}},
        ]
        env = dict(os.environ, COLLABORATORMCP_HOME=str(self.root))
        env.pop("COLLABORATORMCP_PROBE", None)
        try:
            result = subprocess.run(connection.server_command(),
                input="".join(json.dumps(row)+"\n" for row in messages),
                text=True, capture_output=True, timeout=15, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("backend=hub", result.stderr)
            reply = next(json.loads(line) for line in result.stdout.splitlines()
                         if json.loads(line).get("id") == 2)
            self.assertFalse(reply["result"]["isError"])
            stats = json.loads(reply["result"]["content"][0]["text"])
            self.assertEqual(stats["queued"], 1)
            self.assertEqual(stats["orchestrators"][0]["name"], "test-codex")
        finally:
            server.stop()

    def test_hub_port_has_exactly_one_owner(self):
        from collaborator.hub import HubServer
        first = HubServer(self.engine, port=0)
        self.assertTrue(first.start())
        second = HubServer(self.engine, port=first._server.server_address[1])
        try:
            self.assertFalse(second.start(), "A second engine bound the same hub port")
        finally:
            second.stop()
            first.stop()

    def test_mcp_discovery_does_not_start_workers(self):
        from collaborator.mcp_server import Backend
        with patch("collaborator.hub.try_connect", return_value=None), \
                patch("collaborator.engine.Engine") as engine:
            backend = Backend(self.settings).connect()
        self.assertEqual(backend.mode, "standby")
        engine.assert_not_called()

    def test_mcp_lost_reply_never_replays_mutation(self):
        from collaborator.mcp_server import Backend
        backend = Backend(self.settings)
        client = Mock()
        client.call.side_effect = ConnectionError("reply lost after commit")
        backend.client = client
        backend.mode = "hub"
        with patch("collaborator.mcp_server.api.dispatch") as dispatch:
            with self.assertRaisesRegex(ConnectionError, "not automatically repeated"):
                backend.call("task.delegate", {"title": "Only once"})
        dispatch.assert_not_called()
        self.assertIsNone(backend.engine)
        self.assertIsNone(backend.client)
        client.close.assert_called_once()

    def test_mcp_reconnect_restores_client_identity(self):
        from collaborator.mcp_server import Backend
        backend = Backend(self.settings)
        backend.identify("Astra", "test-version")
        client = Mock()
        client.call.side_effect = [{"registered": "test"}, {"queued": 1}]
        with patch("collaborator.hub.try_connect", return_value=client):
            self.assertEqual(backend.call("stats", {}), {"queued": 1})
        self.assertEqual(client.call.call_args_list[0].args,
                         ("hello", {"name": "Astra", "version": "test-version"}))
        self.assertEqual(backend.mode, "hub")

    def test_mcp_lazy_engine_starts_once_under_concurrent_calls(self):
        from concurrent.futures import ThreadPoolExecutor
        from collaborator.mcp_server import Backend
        backend = Backend(self.settings)
        with patch("collaborator.hub.try_connect", return_value=None), \
                patch("collaborator.engine.Engine") as engine, \
                patch("collaborator.mcp_server.api.dispatch", return_value={"ok": True}):
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: backend.call("stats", {}), range(4)))
            engine.assert_called_once_with(self.settings)
            engine.return_value.start.assert_called_once()
        self.assertEqual(len(results), 4)

    def test_google_environment_alias(self):
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test", "GEMINI_API_KEY": ""}):
            self.assertEqual(providers.provider_key(self.settings, catalog.GOOGLE), "test")

    # ---- bug-fix regressions -------------------------------------------------

    def test_hub_rejects_unauthenticated_and_http_requests(self):
        import socket
        import time
        from collaborator.hub import HubServer, HubClient
        server = HubServer(self.engine, port=0)
        self.assertTrue(server.start())
        port = server._server.server_address[1]
        try:
            # A browser-style POST whose body is a valid hub command.
            body = ('\n{"id":1,"method":"settings.set","params":'
                    '{"updates":{"allow_shell":true}}}\n')
            request = ("POST / HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: "
                       "text/plain\r\nContent-Length: %d\r\n\r\n%s"
                       % (len(body), body))
            with socket.create_connection(("127.0.0.1", port)) as sock:
                sock.sendall(request.encode("utf-8"))
                sock.settimeout(3)
                self.assertEqual(sock.recv(100), b"")      # closed, no reply
            wrong = ('{"id":0,"method":"auth","params":{"token":"guess"}}\n'
                     '{"id":1,"method":"settings.set","params":'
                     '{"updates":{"allow_shell":true}}}\n')
            with socket.create_connection(("127.0.0.1", port)) as sock:
                sock.sendall(wrong.encode("utf-8"))
                sock.settimeout(3)
                self.assertEqual(sock.recv(100), b"")
            time.sleep(0.2)
            self.assertFalse(self.settings.get("allow_shell"))
            client = HubClient(port=port, token_dir=str(self.root)).connect()
            try:
                self.assertTrue(client.call("ping")["ok"])
            finally:
                client.close()
        finally:
            server.stop()

    def test_cost_uses_requested_model_not_dated_snapshot(self):
        dated = providers.LLMResult(model="claude-haiku-4-5-20251001",
                                    billing_model="claude-haiku-4-5",
                                    in_tokens=1_000_000, out_tokens=1_000_000)
        self.assertAlmostEqual(dated.cost_usd, 6.0)
        provider = providers.OpenAICompatibleProvider(catalog.OPENAI, "test-only", "")
        reply = Mock(status_code=200, json=Mock(return_value={
            "model": "gpt-4o-mini-2024-07-18",
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000},
            "choices": [{"finish_reason": "stop",
                         "message": {"content": "hi"}}]}))
        with patch("requests.post", return_value=reply):
            result = provider.complete("gpt-4o-mini", [{"role": "user", "content": "hi"}])
        self.assertAlmostEqual(result.cost_usd, 0.75)

    def test_shell_timeout_kills_child_processes(self):
        import sys
        import time
        from types import SimpleNamespace
        from collaborator import tools
        self.settings.update({"allow_shell": True, "shell_timeout_s": 2})
        ctx = SimpleNamespace(settings=self.settings, workspace=str(self.root),
                              emit=lambda *a, **k: None)
        # The shell starts a child that outlives it by far.
        command = ('"%s" -c "import subprocess, sys; subprocess.run([sys.executable, '
                   '\'-c\', \'import time; time.sleep(30)\'])"' % sys.executable)
        started = time.time()
        with self.assertRaisesRegex(tools.ToolError, "timed out"):
            tools.run_command(ctx, {"command": command})
        self.assertLess(time.time() - started, 12)

    def test_shell_stops_when_task_is_cancelled(self):
        import sys
        import time
        from types import SimpleNamespace
        from collaborator import tools
        self.settings.update({"allow_shell": True, "shell_timeout_s": 60})
        started = time.time()
        ctx = SimpleNamespace(settings=self.settings, workspace=str(self.root),
                              emit=lambda *a, **k: None,
                              should_stop=lambda: time.time() - started > 1)
        command = '"%s" -c "import time; time.sleep(30)"' % sys.executable
        with self.assertRaisesRegex(tools.ToolError, "cancelled"):
            tools.run_command(ctx, {"command": command})
        self.assertLess(time.time() - started, 12)

    def test_drive_root_workspace_accepts_paths_inside_it(self):
        from collaborator import tools
        root = os.path.abspath(os.sep)
        self.assertEqual(tools.resolve_in_workspace(root, "inside.txt"),
                         os.path.join(os.path.realpath(root), "inside.txt"))
        with self.assertRaises(tools.ToolError):
            tools.resolve_in_workspace(str(self.root), "../outside.txt")

    def _gpt_executor_finishing(self):
        self.settings.update({"executor_model": "gpt-4.1-mini",
                              "openai_api_key": "test-only",
                              "executor_agent_review": False})
        return Mock(status_code=200, json=Mock(return_value={
            "choices": [{"finish_reason": "tool_calls", "message": {
                "content": None, "tool_calls": [{"id": "f", "type": "function",
                    "function": {"name": "finish", "arguments": json.dumps(
                        {"summary": "done", "status": "done"})}}]}}]}))

    def test_local_review_holds_the_mission_until_reviewed(self):
        self.settings.set("orchestrator_mode", "model")
        mission = self.engine.create_mission("Ordered")
        first = self.engine.delegate(mission["id"], "First", "one", priority=1)
        second = self.engine.delegate(mission["id"], "Second", "two", priority=2)
        claimed = self.engine._claim_task()
        self.assertEqual(claimed["id"], first["id"])
        with patch("requests.post", return_value=self._gpt_executor_finishing()):
            self.assertEqual(self.engine.run_task(claimed)["status"], "done")
        # Finished but not yet reviewed: the second task must wait.
        self.assertIsNone(self.engine._claim_task())
        # The review sends it back; the revision runs before the second task.
        self.engine.review_task = lambda task: self.engine.delegate(
            mission["id"], "Revise 1: First", "fix", priority=1)
        task_id = self.engine._reviews.get_nowait()
        try:
            self.engine.review_task(self.store.get_task(task_id))
        finally:
            self.engine._release_mission(mission["id"])
        self.assertEqual(self.engine._claim_task()["title"], "Revise 1: First")
        self.assertEqual(self.store.get_task(second["id"])["status"], "queued")

    def test_failed_review_still_lets_the_mission_close(self):
        import threading
        import time
        self.settings.set("orchestrator_mode", "model")
        mission = self.engine.create_mission("Closes")
        task = self.engine.delegate(mission["id"], "Only", "one")
        self.store.update_task(task["id"], status="done")
        loop = threading.Thread(target=self.engine._orchestrator_loop, daemon=True)
        with patch.object(self.engine, "review_task",
                          side_effect=providers.ProviderError("bad verdict")):
            loop.start()
            self.engine._queue_review(task["id"])
            deadline = time.time() + 5
            while (self.store.get_mission(mission["id"])["status"] == "open"
                   and time.time() < deadline):
                time.sleep(0.05)
        meta = self.store.get_task(task["id"])["meta"]
        self.assertEqual(meta["review"]["verdict"], "failed")
        self.assertEqual(self.store.get_mission(mission["id"])["status"], "done")
        self.assertEqual(self.engine.held_missions(), [])

    def test_cli_executor_receives_effort(self):
        self.settings.update({"executor_model": "codex-cli", "executor_effort": "low"})
        task = self.engine.quick_task("Test", "Test")
        provider = Mock(supports_resume=False)
        provider.complete.return_value = providers.LLMResult(
            text='{"tool": "finish", "args": {"summary": "ok"}}', model="codex-cli")
        with patch("collaborator.providers.provider_for", return_value=provider):
            self.engine.run_task(task)
        self.assertEqual(provider.complete.call_args.kwargs["effort"], "low")

    def test_cancel_does_not_overwrite_a_claimed_task(self):
        mission = self.engine.create_mission("Race")
        task = self.engine.delegate(mission["id"], "Claimed", "x")
        self.store.claim_next_task(os.getpid())
        self.engine.cancel_task(task["id"])
        self.assertEqual(self.store.get_task(task["id"])["status"], "claimed")
        queued = self.engine.delegate(mission["id"], "Queued", "y")
        self.engine.cancel_task(queued["id"])
        self.assertEqual(self.store.get_task(queued["id"])["status"], "cancelled")

    def test_embedded_engine_yields_to_a_desktop(self):
        from collaborator.mcp_server import Backend
        backend = Backend(self.settings)
        with patch("collaborator.hub.try_connect", return_value=None), \
                patch("collaborator.engine.Engine") as engine_cls, \
                patch("collaborator.mcp_server.api.dispatch", return_value={}):
            backend.call("stats", {})
        embedded = engine_cls.return_value
        self.assertEqual(embedded.defer_claims, backend.desktop_is_up)
        self.assertEqual(backend.mode, "embedded")
        # The desktop appears: calls move to it, except for work the embedded
        # engine is still running.
        embedded.active_tasks.return_value = {"tsk_local": {}}
        embedded.list_approvals.return_value = [{"approval_id": "apv_local"}]
        client = Mock()
        client.call.return_value = [{"approval_id": "apv_desktop"}]
        backend._probe = (0.0, False)
        with patch("collaborator.hub.hub_listening", return_value=True), \
                patch("collaborator.hub.try_connect", return_value=client), \
                patch("collaborator.mcp_server.api.dispatch",
                      return_value={"cancelled": "here"}) as dispatch:
            self.assertEqual(backend.call("task.cancel", {"task_id": "tsk_local"}),
                             {"cancelled": "here"})
            self.assertEqual(dispatch.call_args.args[0], embedded)
            ids = [a["approval_id"] for a in backend.call("approval.list", {})]
            self.assertEqual(ids, ["apv_local", "apv_desktop"])
        self.assertEqual(backend.mode, "hub")

    # ---- team: helpers and a supervising orchestrator ----------------------

    def test_helper_team_roster_rules(self):
        from collaborator import config
        # Not set up yet: derived from the old checklist, default first.
        self.settings.update({"helpers": [], "default_agent_model": "claude-sonnet-5",
                              "allowed_agent_models": ["claude-haiku-4-5", "claude-sonnet-5"]})
        self.assertEqual([m["model"] for m in config.helper_roster(self.settings)],
                         ["claude-sonnet-5", "claude-haiku-4-5"])
        team = config.set_helpers(self.settings, [
            {"name": "Reviewer", "model": "claude-sonnet-5"},
            {"name": "reviewer", "model": "gpt-4.1-mini"},
            {"name": "", "model": "claude-haiku-4-5"},
            {"name": "No model", "model": ""}] +
            [{"name": "Extra", "model": "claude-haiku-4-5"}] * 10)
        self.assertEqual(len(team), config.MAX_HELPERS)
        self.assertEqual([m["name"] for m in team[:3]], ["Reviewer", "reviewer 2", "Helper 3"])
        self.assertEqual(self.settings.get("default_agent_model"), "claude-sonnet-5")
        self.assertEqual(self.settings.get("allowed_agent_models"),
                         ["claude-sonnet-5", "gpt-4.1-mini", "claude-haiku-4-5"])
        with self.assertRaises(ValueError):
            config.set_helpers(self.settings, [])
        # Switching account mode keeps names, moves models that cannot run there.
        config.apply_account_mode(self.settings, "subscription")
        team = config.helper_roster(self.settings)
        self.assertEqual(team[0], {"name": "Reviewer", "model": "claude-cli-haiku"})

    def test_executor_calls_helpers_by_name(self):
        from types import SimpleNamespace
        from collaborator import config, tools
        config.set_helpers(self.settings, [{"name": "Reviewer", "model": "claude-sonnet-5"},
                                           {"name": "Researcher", "model": "gpt-4.1-mini"}])
        schemas = {s["name"]: s for s in tools.tool_schemas(self.settings)}
        self.assertEqual(schemas["spawn_agent"]["input_schema"]["properties"]["helper"]["enum"],
                         ["Reviewer", "Researcher"])
        calls = []
        ctx = SimpleNamespace(settings=self.settings, default_agent_model="",
                              run_agent=lambda **kw: calls.append(kw) or "ok",
                              run_agents_parallel=lambda specs: calls.extend(specs) or ["a"] * len(specs))
        tools.spawn_agent(ctx, {"helper": "researcher", "role": "sources", "instructions": "x"})
        self.assertEqual((calls[0]["role"], calls[0]["model"]), ("Researcher - sources", "gpt-4.1-mini"))
        tools.spawn_agent(ctx, {"instructions": "x"})            # default helper
        self.assertEqual(calls[1]["model"], "claude-sonnet-5")
        with self.assertRaisesRegex(tools.ToolError, "No helper called"):
            tools.spawn_agent(ctx, {"helper": "Nobody", "instructions": "x"})
        with self.assertRaisesRegex(tools.ToolError, "At most 8"):
            tools.spawn_agents(ctx, {"agents": [{"instructions": "x"}] * 9})
        tools.spawn_agents(ctx, {"agents": [{"helper": "Reviewer", "instructions": "a"},
                                            {"helper": "Researcher", "instructions": "b"}]})
        self.assertEqual([c["model"] for c in calls[2:]], ["claude-sonnet-5", "gpt-4.1-mini"])

    def _supervise(self, decision, finished_status="done"):
        self.settings.update({"orchestrator_mode": "model", "orchestrator_supervise": True})
        mission = self.engine.create_mission("Supervised")
        done = self.engine.delegate(mission["id"], "Built it", "build", priority=1)
        later = self.engine.delegate(mission["id"], "Polish", "polish", priority=2)
        self.store.update_task(done["id"], status=finished_status, result="built")
        provider = Mock()
        base = {"verdict": "accept", "assessment": "fine", "revision_instructions": "",
                "new_tasks": [], "cancel_tasks": [], "mission": "continue", "note_to_operator": ""}
        base.update(decision)
        provider.complete.return_value = providers.LLMResult(text=json.dumps(base), model="codex-cli")
        with patch("collaborator.providers.provider_for", return_value=provider):
            outcome = self.engine.review_task(self.store.get_task(done["id"]))
        return mission, done, later, outcome, provider

    def test_supervisor_adds_tasks(self):
        mission, done, later, outcome, provider = self._supervise({
            "new_tasks": [{"title": "Write tests", "instructions": "test it", "context": ""}]})
        self.assertIn("new_tasks", provider.complete.call_args.kwargs["output_format"]["schema"]["properties"])
        added = self.store.get_task(outcome["added"][0])
        self.assertEqual((added["title"], added["status"]), ("Write tests", "queued"))
        # Added work runs after the plan's existing queued tasks.
        self.assertGreaterEqual(added["priority"], later["priority"])

    def test_supervisor_cancels_only_this_missions_queued_tasks(self):
        other = self.engine.create_mission("Unrelated")
        foreign = self.engine.delegate(other["id"], "Not yours", "x")
        mission, done, later, outcome, _ = self._supervise({"cancel_tasks": []})
        provider = Mock()
        provider.complete.return_value = providers.LLMResult(text=json.dumps({
            "verdict": "accept", "assessment": "", "revision_instructions": "", "new_tasks": [],
            "cancel_tasks": [later["id"], done["id"], foreign["id"], None],
            "mission": "continue", "note_to_operator": ""}), model="codex-cli")
        with patch("collaborator.providers.provider_for", return_value=provider):
            outcome = self.engine.review_task(self.store.get_task(done["id"]))
        self.assertEqual(outcome["cancelled"], [later["id"]])
        self.assertEqual(self.store.get_task(later["id"])["status"], "cancelled")
        self.assertEqual(self.store.get_task(done["id"])["status"], "done")
        self.assertEqual(self.store.get_task(foreign["id"])["status"], "queued")

    def test_supervisor_can_complete_or_hold_a_mission(self):
        mission, done, later, outcome, _ = self._supervise({"mission": "complete"})
        self.assertEqual(self.store.get_task(later["id"])["status"], "cancelled")
        self.engine._maybe_close_mission(mission["id"])
        self.assertEqual(self.store.get_mission(mission["id"])["status"], "done")

        mission, done, later, outcome, _ = self._supervise(
            {"mission": "hold", "note_to_operator": "Which database should it use?"})
        self.assertEqual(self.store.get_mission(mission["id"])["status"], "on_hold")
        self.assertIn(mission["id"], self.engine.held_missions())
        self.assertIsNone(self.store.claim_next_task(os.getpid(), self.engine.held_missions()))
        self.engine.resume_mission(mission["id"])
        self.assertEqual(self.store.get_mission(mission["id"])["status"], "open")
        self.assertEqual(self.engine._claim_task()["id"], later["id"])

    def test_supervisor_checks_in_redirects_and_stops(self):
        self.settings.update({"orchestrator_mode": "model", "orchestrator_supervise": True,
                              "orchestrator_checkin_steps": 1})
        executor_reply = self._gpt_executor_finishing()
        executor_reply.json.return_value = {"choices": [{"finish_reason": "tool_calls", "message": {
            "content": None, "tool_calls": [{"id": "n", "type": "function", "function": {
                "name": "note", "arguments": json.dumps({"text": "working"})}}]}}]}
        task = self.engine.quick_task("Long job", "keep going")
        decisions = iter([{"action": "redirect", "message": "Focus on the API first.", "reason": ""},
                          {"action": "stop", "message": "", "reason": "Wrong repository."}])
        with patch.object(self.engine, "_checkin", side_effect=lambda *a: next(decisions)), \
                patch("requests.post", return_value=executor_reply) as post:
            result = self.engine.run_task(task)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("Wrong repository", result["result"])
        second = json.loads(post.call_args_list[1].kwargs["data"])
        self.assertTrue(any("Focus on the API first." in json.dumps(m) for m in second["messages"]))
        self.assertEqual(post.call_count, 2)

    def test_checkins_only_for_a_local_supervising_orchestrator(self):
        task = {"id": "t", "mission_id": "m", "title": "x", "instructions": ""}
        self.settings.update({"orchestrator_checkin_steps": 1, "orchestrator_supervise": True,
                              "orchestrator_mode": "external"})
        with patch.object(self.engine, "_checkin") as checkin:
            self.assertEqual(self.engine._supervise_step(task, None, 3), "")
            self.settings.update({"orchestrator_mode": "model", "orchestrator_checkin_steps": 0})
            self.assertEqual(self.engine._supervise_step(task, None, 3), "")
            checkin.assert_not_called()

    def test_engine_defers_claims_when_asked(self):
        self.engine.defer_claims = lambda: True
        self.assertTrue(self.engine._claims_deferred())
        self.engine.defer_claims = lambda: 1 / 0
        self.assertFalse(self.engine._claims_deferred())


if __name__ == "__main__":
    unittest.main()
