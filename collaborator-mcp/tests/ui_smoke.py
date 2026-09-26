"""Exercise UI construction and connection state without touching real settings."""
import os,sys,tempfile,socket
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
with tempfile.TemporaryDirectory(prefix='collaborator-ui-') as tmp:
    os.environ['COLLABORATORMCP_HOME']=tmp
    from collaborator.config import settings
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        test_port=sock.getsockname()[1]
    settings().update({'hub_port':test_port,'workspace':tmp,'orchestrator_mode':'model','orchestrator_model':'codex-cli'})
    from collaborator.ui.app import App, AlreadyRunning
    app=App()
    app.attributes("-alpha",0)
    app.update()
    try:
        app.show_page('connect')
        app.update()
        assert '[mcp_servers.collaborator]' in app.txt_config.get('1.0','end')
        assert 'planner: idle' in app.chip_orchestrator.cget('text')
        app._render_event({'kind':'connection_result','text':'MCP handshake passed'})
        assert app.var_connection_status.get()=='MCP handshake passed'
        for size in ["1060x660", "1060x760", "1360x900"]:
            app.geometry(size)
            app.update()
            for page in ["command","terminals","connect","settings"]:
                app.show_page(page)
                app.update()
                widget = app._pages[page]
                print(size,page,"root",app.winfo_width(),app.winfo_height(),"page",widget.winfo_width(),widget.winfo_height(),"requested",widget.winfo_reqwidth(),widget.winfo_reqheight())
        app.show_page("command")
        app.txt_instructions.insert("1.0", "A task with no separate title")
        with patch.object(app, "_executor_ready", return_value=True), patch("collaborator.ui.app.api.dispatch") as delegate:
            app._delegate_clicked()
            assert delegate.call_args.args[2]["title"]=="A task with no separate title"
            assert app._current=="missions"
        for index in range(5):
            app._ensure_agent_terminal("agent:test"+str(index),"helper "+str(index))
        panes=[pane for key,pane in app.term_panes.items() if key.startswith("agent:")]
        assert len(panes)==4 and len({pane["column"] for pane in panes})==4
        app._term_fullscreen("agent:test4")
        app._term_fullscreen("agent:test4")
        assert app.exec_host.winfo_manager()=="grid"
        app._use_codex_planner()
        assert app.settings.get("orchestrator_model")=="codex-cli"
        app._save_settings()
        app.txt_plan_brief.insert("1.0", "A test plan")
        with patch.object(app, "_model_ready", return_value=True), patch("collaborator.ui.app.threading.Thread") as worker:
            app._plan_clicked()
            app._sync_orchestrator_ui()
            assert str(app.btn_plan.cget("state")) == "disabled"
            app._plan_clicked()
            worker.assert_called_once()
        with patch("collaborator.ui.app.messagebox.showerror"):
            app._render_event({"kind":"plan_result","result":None,"error":RuntimeError("test failure")})
        assert not app._planning and str(app.btn_plan.cget("state")) == "normal"
        with patch("collaborator.ui.app.Engine.start") as start, patch("collaborator.ui.app.messagebox.showinfo"):
            try:
                App()
            except AlreadyRunning:
                pass
            else:
                raise AssertionError("Duplicate window was not rejected")
            start.assert_not_called()
        # Save settings writes only what was edited in the form: changes made
        # elsewhere (Codex turning auto-approve off, a new workspace) survive.
        import threading, time
        other=os.path.join(tmp,"other-repo"); os.makedirs(other)
        app.settings.set("auto_approve",False)
        app.engine.set_workspace(other)
        app._s_vars["shell_timeout_s"][0].set("77")
        app._save_settings()
        assert app.settings.get("shell_timeout_s")==77
        assert app.settings.get("auto_approve") is False, "Save re-enabled auto-approve"
        assert os.path.normcase(app.settings.get("workspace"))==os.path.normcase(other), "Save reverted the workspace"
        # Turning auto-approve on from the form releases actions already waiting.
        decision={}
        waiter=threading.Thread(target=lambda: decision.update(result=app.engine.request_approval("write_file",{"path":"x"})))
        waiter.start()
        deadline=time.time()+5
        while not app.engine.list_approvals() and time.time()<deadline:
            time.sleep(0.05)
        app._s_vars["auto_approve"][0].set(True)
        app._save_settings()
        waiter.join(5)
        assert decision.get("result",(False,))[0] is True, decision
        # Any model can be the orchestrator, even one not set up yet (warned, kept).
        label=next(l for l,m in app._orch_ids.items() if m=="gpt-4.1-mini")
        app.var_orchestrator.set(label)
        with patch("collaborator.ui.app.messagebox.showwarning") as warned:
            app._on_orchestrator_change()
        assert app.settings.get("orchestrator_model")=="gpt-4.1-mini" and app.settings.get("orchestrator_mode")=="model"
        # Helper team: add up to eight, rename, change model, remove.
        from collaborator import config as cfg
        while len(app._helper_rows)<cfg.MAX_HELPERS:
            app._add_helper()
        assert len(cfg.helper_roster(app.settings))==8 and str(app.btn_add_helper.cget("state"))=="disabled"
        app._helper_rows[0][0].set("Reviewer"); app._helper_rows[0][1].set("claude-sonnet-5")
        app._save_helpers()
        assert cfg.helper_roster(app.settings)[0]=={"name":"Reviewer","model":"claude-sonnet-5"}
        assert app.settings.get("default_agent_model")=="claude-sonnet-5"
        app._remove_helper(7)
        assert len(cfg.helper_roster(app.settings))==7 and str(app.btn_add_helper.cget("state"))=="normal"
        assert "Helpers (7)" in app.lbl_team_summary.cget("text")
        # A mission the orchestrator put on hold can be resumed from Tasks & results.
        held=app.engine.create_mission("Held")
        app.engine.store.update_mission(held["id"],status="on_hold",meta={"hold_reason":"Pick a DB"})
        app.show_page("missions"); app.tree.selection_set(held["id"]); app.update()
        app._resume_selected()
        assert app.engine.store.get_mission(held["id"])["status"]=="open"
        for page in app._pages:
            app.show_page(page)
            app.update()
        print('PASS all UI pages; Codex config, local planner, team editor')
    finally:
        app.hub_server.stop()
        app.engine.shutdown()
        app.engine.store.close()
        app.destroy()
