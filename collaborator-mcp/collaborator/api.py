"""Single dispatch surface shared by the UI, the local hub and the MCP server."""

import json
import time

from . import catalog, config, providers


class ApiError(Exception):
    pass


def _task_view(task):
    if not task:
        return None
    return {
        "task_id": task["id"],
        "mission_id": task["mission_id"],
        "title": task["title"],
        "status": task["status"],
        "priority": task["priority"],
        "model": task["model"],
        "created_at": task["created_at"],
        "started_at": task["started_at"],
        "finished_at": task["finished_at"],
        "result": task["result"],
        "error": task["error"],
        "cost_usd": round(float(task["cost_usd"] or 0), 6),
    }


def _goal_view(goal, store=None):
    if not goal:
        return None
    view = {
        "goal_id": goal["id"],
        "mission_id": goal["mission_id"],
        "title": goal["title"],
        "description": goal["description"],
        "status": goal["status"],
        "position": goal["position"],
    }
    if store is not None:
        view["tasks"] = [_task_view(t)
                         for t in store.list_tasks(goal["mission_id"])
                         if t.get("goal_id") == goal["id"]]
    return view


def _mission_view(mission):
    if not mission:
        return None
    return {
        "mission_id": mission["id"],
        "title": mission["title"],
        "brief": mission["brief"],
        "status": mission["status"],
        "orchestrator": mission["orchestrator"],
        "created_at": mission["created_at"],
        "cost_usd": round(float(mission["cost_usd"] or 0), 6),
    }


def dispatch(engine, method, params=None, source="local"):
    """Execute one API call; orchestrator calls are echoed to its terminal.

    An external orchestrator (an MCP client) otherwise works invisibly: its
    planning happens on its side, and all the operator would see is the
    tasks appearing. Mirroring each call and its outcome into the
    Orchestrator terminal shows what it is doing, as it does it.
    """
    if source != "mcp" or method in ("ping", "hello"):
        return _dispatch(engine, method, params, source)
    engine._console_write("cmd", "%s(%s)" % (method, _brief(params)),
                          channel="orchestrator")
    try:
        result = _dispatch(engine, method, params, source)
    except Exception as exc:
        engine._console_write("err", str(exc), channel="orchestrator")
        raise
    engine._console_write("out", _brief(result, 400), channel="orchestrator")
    return result


def _brief(value, limit=240):
    """Compact one-line rendering of call params or a result."""
    try:
        text = json.dumps(value, default=str, ensure_ascii=False)
    except Exception:
        text = str(value)
    if text in ("{}", "null"):
        return ""
    return text if len(text) <= limit else text[:limit] + "..."


def _dispatch(engine, method, params=None, source="local"):
    """Execute one API call against the engine. Returns a JSON-safe value.

    ``source`` is "mcp" when the call arrived from a connected orchestrator
    (an MCP client) and "local" when it came from the desktop UI. It decides who gets
    credited in the activity log - crediting the orchestrator for work the
    operator did by hand would misrepresent what actually happened.
    """
    params = params or {}
    store = engine.store
    actor = (engine.settings.get("orchestrator_name") if source == "mcp"
             else "Operator")

    # ---- health / info ----------------------------------------------------
    if method == "ping":
        return {"ok": True, "ts": time.time()}

    if method == "stats":
        data = engine.stats()
        data["orchestrator_state"] = engine.orchestrator_state()
        return data

    if method == "console.list":
        return {"channels": [{"id": c, "label": l}
                             for c, l in engine.console_channels()],
                "lines": engine.console_lines(params.get("channel"),
                                              int(params.get("limit") or 300))}

    if method == "models.list":
        def view(m):
            row = {"id": m.id, "label": m.label, "provider": m.provider,
                   "provider_label": m.provider_label, "notes": m.notes,
                   "subscription": m.subscription,
                   "billing": catalog.price_label(m),
                   "discovered": m.discovered}
            if m.subscription:
                row["available"] = catalog.cli_available(m.id)
            else:
                row["input_price"] = m.input_price
                row["output_price"] = m.output_price
                row["price_estimated"] = m.price_estimated
            return row

        wanted = params.get("provider")
        pool = (catalog.by_provider(wanted) if wanted else catalog.CATALOG)
        return {
            "executors": [view(m) for m in pool if "executor" in m.tags],
            "agents": [view(m) for m in pool if "agent" in m.tags],
            "providers": [
                {"id": pid, "label": catalog.PROVIDERS[pid].label,
                 "configured": bool(providers.provider_key(engine.settings,
                                                           pid)),
                 "models": len(catalog.by_provider(pid))}
                for pid in catalog.API_PROVIDERS],
            "account_mode": engine.settings.get("account_mode"),
            "enabled_agents": config.helper_models(engine.settings),
            "helpers": config.helper_roster(engine.settings),
            "executor_model": engine.settings.get("executor_model"),
            "orchestrator_mode": engine.settings.get("orchestrator_mode"),
            "orchestrator_model": engine.settings.get("orchestrator_model"),
            "last_refreshed": engine.settings.get("models_last_refreshed"),
        }

    if method == "models.refresh":
        wanted = params.get("providers")
        if isinstance(wanted, str):
            wanted = [wanted]
        return engine.refresh_models(wanted)

    if method == "models.forget":
        model_id = params.get("model_id") or ""
        rows = [r for r in (engine.settings.get("custom_models") or [])
                if r.get("id") != model_id]
        engine.settings.set("custom_models", rows)
        engine.settings.save()
        catalog.load_custom(engine.settings)
        return {"removed": model_id}

    if method == "models.price":
        model_id = params.get("model_id") or ""
        rows = engine.settings.get("custom_models") or []
        for row in rows:
            if row.get("id") == model_id:
                if "input_price" in params:
                    row["input_price"] = float(params["input_price"])
                if "output_price" in params:
                    row["output_price"] = float(params["output_price"])
                if "context" in params:
                    row["context"] = int(params["context"])
                row["price_estimated"] = False
                engine.settings.set("custom_models", rows)
                engine.settings.save()
                catalog.load_custom(engine.settings)
                return {"updated": model_id}
        raise ApiError("No discovered model with that id.")

    # ---- missions ---------------------------------------------------------
    if method == "mission.create":
        title = (params.get("title") or "").strip()
        if not title:
            raise ApiError("'title' is required.")
        mission = engine.create_mission(title, params.get("brief") or "",
                                        params.get("orchestrator") or actor,
                                        workspace=params.get("workspace"))
        return _mission_view(mission)

    if method == "mission.list":
        return [_mission_view(m)
                for m in store.list_missions(params.get("status"),
                                             int(params.get("limit") or 100))]

    if method == "mission.get":
        mission = store.get_mission(params.get("mission_id") or "")
        if not mission:
            raise ApiError("No such mission.")
        view = _mission_view(mission)
        view["goals"] = [_goal_view(g, store)
                         for g in store.list_goals(mission["id"])]
        view["tasks"] = [_task_view(t)
                         for t in store.list_tasks(mission["id"])]
        view["unassigned_tasks"] = [_task_view(t)
                                    for t in store.list_tasks(mission["id"])
                                    if not t.get("goal_id")]
        return view

    if method == "mission.close":
        mission = engine.close_mission(params.get("mission_id") or "",
                                       params.get("status") or "closed")
        if not mission:
            raise ApiError("No such mission.")
        return _mission_view(mission)

    if method == "mission.resume":
        return _mission_view(engine.resume_mission(
            params.get("mission_id") or "", actor=actor))

    if method == "mission.delete":
        store.delete_mission(params.get("mission_id") or "")
        return {"deleted": True}

    if method == "mission.plan":
        return engine.plan_mission(
            mission_id=params.get("mission_id"),
            title=params.get("title") or "",
            brief=params.get("brief") or "",
            max_tasks=params.get("max_tasks"),
            max_goals=params.get("max_goals"),
            workspace=params.get("workspace"))

    # ---- goals ------------------------------------------------------------
    if method == "goal.create":
        mission_id = params.get("mission_id") or ""
        if not store.get_mission(mission_id):
            raise ApiError("No such mission.")
        title = (params.get("title") or "").strip()
        if not title:
            raise ApiError("'title' is required.")
        goal = store.create_goal(mission_id, title,
                                 params.get("description") or "")
        engine.emit("mission", "Goal added: %s" % title, actor=actor,
                    mission_id=mission_id)
        return _goal_view(goal, store)

    if method == "goal.list":
        mission_id = params.get("mission_id") or ""
        return [_goal_view(g, store) for g in store.list_goals(mission_id)]

    if method == "goal.update":
        goal = store.update_goal(params.get("goal_id") or "",
                                 **{k: v for k, v in params.items()
                                    if k in ("title", "description", "status",
                                             "position")})
        if not goal:
            raise ApiError("No such goal.")
        return _goal_view(goal, store)

    if method == "goal.delete":
        store.delete_goal(params.get("goal_id") or "")
        return {"deleted": params.get("goal_id")}

    # ---- tasks ------------------------------------------------------------
    if method == "task.delegate":
        mission_id = params.get("mission_id")
        title = (params.get("title") or "").strip()
        instructions = params.get("instructions") or ""
        if not title:
            raise ApiError("'title' is required.")
        if not mission_id:
            mission = engine.create_mission(title, instructions,
                                            orchestrator=actor,
                                            workspace=params.get("workspace"))
            mission_id = mission["id"]
        task = engine.delegate(mission_id, title, instructions,
                               params.get("context") or "",
                               int(params.get("priority") or 5),
                               actor=actor,
                               goal_id=params.get("goal_id") or "")
        return _task_view(task)

    if method == "task.get":
        task = store.get_task(params.get("task_id") or "")
        if not task:
            raise ApiError("No such task.")
        view = _task_view(task)
        view["agents"] = [{"agent_id": a["id"], "role": a["name"],
                           "model": a["model"], "status": a["status"],
                           "cost_usd": round(float(a["cost_usd"] or 0), 6)}
                          for a in store.list_agents(task_id=task["id"])]
        return view

    if method == "task.list":
        return [_task_view(t)
                for t in store.list_tasks(params.get("mission_id"),
                                          params.get("status"),
                                          int(params.get("limit") or 200))]

    if method == "task.cancel":
        engine.cancel_task(params.get("task_id") or "")
        return _task_view(store.get_task(params.get("task_id") or ""))

    if method == "task.wait":
        task_id = params.get("task_id") or ""
        deadline = time.time() + float(params.get("timeout") or 300)
        terminal = {"done", "partial", "blocked", "error", "cancelled"}
        while time.time() < deadline:
            task = store.get_task(task_id)
            if not task:
                raise ApiError("No such task.")
            if task["status"] in terminal:
                return _task_view(task)
            time.sleep(1.0)
        task = store.get_task(task_id)
        view = _task_view(task)
        if view:
            view["timed_out"] = True
        return view

    # ---- direct sub-agent use (orchestrator side) -------------------------
    if method == "agent.run":
        from .tools import ToolError, resolve_helper
        try:
            role, model = resolve_helper(engine.settings, params)
        except ToolError as exc:
            raise ApiError(str(exc))
        text = engine.run_sub_agent(
            role=role,
            model=model,
            instructions=params.get("instructions") or "",
            context=params.get("context") or "",
            mission_id=params.get("mission_id") or "")
        return {"model": model, "output": text}

    if method == "agent.list":
        return [{"agent_id": a["id"], "role": a["name"], "model": a["model"],
                 "status": a["status"], "result": a["result"],
                 "cost_usd": round(float(a["cost_usd"] or 0), 6)}
                for a in store.list_agents(params.get("task_id"),
                                           params.get("mission_id"))]

    # ---- events / notes ---------------------------------------------------
    if method == "events.list":
        return store.list_events(params.get("mission_id"),
                                 params.get("task_id"),
                                 int(params.get("since_id") or 0),
                                 int(params.get("limit") or 200))

    if method == "note":
        event = engine.emit("note", params.get("text") or "",
                            actor=params.get("actor") or actor,
                            mission_id=params.get("mission_id") or "",
                            task_id=params.get("task_id") or "")
        return {"event_id": event["id"]}

    if method == "task.steer":
        return engine.steer(params.get("text") or "",
                            params.get("task_id"),
                            params.get("actor") or actor)

    # ---- workspace --------------------------------------------------------
    if method == "workspace.get":
        info = engine.workspace_info(params.get("path"))
        info["recent"] = engine.settings.get("recent_workspaces") or []
        return info

    if method == "workspace.set":
        return engine.set_workspace(params.get("path") or "",
                                    bool(params.get("create")))

    if method == "workspace.browse":
        return engine.browse(params.get("path") or "",
                             bool(params.get("show_hidden")))

    # ---- approvals --------------------------------------------------------
    if method == "approval.list":
        return engine.list_approvals()

    if method == "approval.resolve":
        approval_id = params.get("approval_id") or ""
        decision = params.get("decision")
        if decision is None:
            decision = params.get("approve")
        approve = str(decision).lower() in ("true", "1", "allow", "approve",
                                            "approved", "yes")
        if not approval_id:
            count = engine.resolve_all_approvals(approve,
                                                 params.get("reason") or "")
            return {"resolved": count, "approved": approve}
        ok = engine.resolve_approval(approval_id, approve,
                                     params.get("reason") or "")
        if not ok:
            raise ApiError("No pending approval with that id.")
        return {"approval_id": approval_id, "approved": approve}

    if method == "approval.mode":
        if "auto_approve" in params:
            enable = str(params["auto_approve"]).lower() in (
                "true", "1", "yes", "on")
            if enable and source == "mcp":
                # The gate exists so a person confirms what the agents do;
                # the orchestrator may tighten it but not switch it off.
                raise ApiError("Only the operator can turn auto-approve on, "
                               "from the CollaboratorMCP window. Release "
                               "individual actions with resolve_approval.")
            engine.settings.set("auto_approve", enable)
            engine.settings.save()
        return {"auto_approve": bool(engine.settings.get("auto_approve")),
                "gated_tools": engine.settings.get("approval_tools")}

    # ---- queue ------------------------------------------------------------
    if method == "queue.pause":
        engine.pause()
        return {"paused": True}

    if method == "queue.resume":
        engine.resume()
        return {"paused": False}

    # ---- settings ---------------------------------------------------------
    if method == "settings.get":
        data = engine.settings.as_dict()
        for key in list(data):
            if key.endswith("_api_key"):
                data[key] = "set" if data.get(key) else ""
        return data

    if method == "settings.set":
        updates = params.get("updates") or {}
        engine.settings.update(updates)
        engine.settings.save()
        return {"updated": sorted(updates.keys())}

    raise ApiError("Unknown method: %s" % method)
