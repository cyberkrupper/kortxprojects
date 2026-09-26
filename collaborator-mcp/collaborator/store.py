"""SQLite persistence for missions, tasks, agents, events and usage."""

import json
import os
import sqlite3
import threading
import time
import uuid

from . import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    brief        TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    orchestrator TEXT NOT NULL DEFAULT 'Orchestrator',
    cost_usd     REAL NOT NULL DEFAULT 0,
    meta         TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS goals (
    id          TEXT PRIMARY KEY,
    mission_id  TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'open',
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_goals_mission ON goals(mission_id);

CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,
    mission_id   TEXT NOT NULL,
    goal_id      TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL,
    instructions TEXT NOT NULL DEFAULT '',
    context      TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'queued',
    priority     INTEGER NOT NULL DEFAULT 5,
    created_at   REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL,
    result       TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    cost_usd     REAL NOT NULL DEFAULT 0,
    meta         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_mission ON tasks(mission_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);

CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL DEFAULT '',
    mission_id  TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL,
    provider    TEXT NOT NULL DEFAULT '',
    prompt      TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'running',
    created_at  REAL NOT NULL,
    finished_at REAL,
    in_tokens   INTEGER NOT NULL DEFAULT 0,
    out_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd    REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agents_task ON agents(task_id);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    mission_id TEXT NOT NULL DEFAULT '',
    task_id    TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL,
    actor      TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS usage (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    day        TEXT NOT NULL,
    model      TEXT NOT NULL,
    role       TEXT NOT NULL DEFAULT '',
    mission_id TEXT NOT NULL DEFAULT '',
    task_id    TEXT NOT NULL DEFAULT '',
    in_tokens  INTEGER NOT NULL DEFAULT 0,
    out_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_usage_day ON usage(day);
"""


def new_id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:12])


def _row_to_dict(row):
    d = dict(row)
    for key in ("meta",):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                d[key] = {}
    return d


class Store:
    def __init__(self, path=None):
        self.path = path or os.path.join(config.app_dir(), "collaborator.db")
        self._lock = threading.RLock()
        # Another process (app + MCP server) may hold the write lock briefly.
        self._conn = sqlite3.connect(self.path, check_same_thread=False,
                                     timeout=15)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()

    def _migrate(self):
        """Add columns introduced after a database was first created."""
        existing = {row[1] for row in
                    self._conn.execute("PRAGMA table_info(tasks)")}
        if "goal_id" not in existing:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN goal_id TEXT NOT NULL "
                "DEFAULT ''")
        if "owner_pid" not in existing:
            # Which engine process claimed the task, for crash recovery.
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN owner_pid INTEGER NOT NULL "
                "DEFAULT 0")

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _exec(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur

    def _query(self, sql, args=()):
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [_row_to_dict(r) for r in cur.fetchall()]

    def _one(self, sql, args=()):
        rows = self._query(sql, args)
        return rows[0] if rows else None

    # ---------------- missions ---------------------------------------------
    def create_mission(self, title, brief="", orchestrator="Orchestrator", meta=None):
        mid = new_id("msn")
        now = time.time()
        self._exec(
            "INSERT INTO missions (id,title,brief,status,created_at,updated_at,"
            "orchestrator,cost_usd,meta) VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, title, brief, "open", now, now, orchestrator, 0.0,
             json.dumps(meta or {})))
        return self.get_mission(mid)

    def get_mission(self, mid):
        return self._one("SELECT * FROM missions WHERE id=?", (mid,))

    def list_missions(self, status=None, limit=200):
        if status:
            return self._query(
                "SELECT * FROM missions WHERE status=? ORDER BY created_at DESC "
                "LIMIT ?", (status, limit))
        return self._query(
            "SELECT * FROM missions ORDER BY created_at DESC LIMIT ?", (limit,))

    def update_mission(self, mid, **fields):
        if not fields:
            return self.get_mission(mid)
        fields["updated_at"] = time.time()
        if "meta" in fields and not isinstance(fields["meta"], str):
            fields["meta"] = json.dumps(fields["meta"])
        cols = ", ".join("%s=?" % k for k in fields)
        self._exec("UPDATE missions SET %s WHERE id=?" % cols,
                   tuple(fields.values()) + (mid,))
        return self.get_mission(mid)

    def delete_mission(self, mid):
        self._exec("DELETE FROM agents WHERE mission_id=?", (mid,))
        self._exec("DELETE FROM tasks WHERE mission_id=?", (mid,))
        self._exec("DELETE FROM goals WHERE mission_id=?", (mid,))
        self._exec("DELETE FROM events WHERE mission_id=?", (mid,))
        self._exec("DELETE FROM missions WHERE id=?", (mid,))

    # ---------------- goals -------------------------------------------------
    def create_goal(self, mission_id, title, description="", position=None,
                    meta=None):
        gid = new_id("gol")
        if position is None:
            position = len(self.list_goals(mission_id)) + 1
        self._exec(
            "INSERT INTO goals (id,mission_id,title,description,status,"
            "position,created_at,meta) VALUES (?,?,?,?,?,?,?,?)",
            (gid, mission_id, title, description, "open", position,
             time.time(), json.dumps(meta or {})))
        return self.get_goal(gid)

    def get_goal(self, gid):
        return self._one("SELECT * FROM goals WHERE id=?", (gid,))

    def list_goals(self, mission_id):
        return self._query(
            "SELECT * FROM goals WHERE mission_id=? ORDER BY position, "
            "created_at", (mission_id,))

    def update_goal(self, gid, **fields):
        if not fields:
            return self.get_goal(gid)
        if "meta" in fields and not isinstance(fields["meta"], str):
            fields["meta"] = json.dumps(fields["meta"])
        cols = ", ".join("%s=?" % k for k in fields)
        self._exec("UPDATE goals SET %s WHERE id=?" % cols,
                   tuple(fields.values()) + (gid,))
        return self.get_goal(gid)

    def delete_goal(self, gid):
        self._exec("UPDATE tasks SET goal_id='' WHERE goal_id=?", (gid,))
        self._exec("DELETE FROM goals WHERE id=?", (gid,))

    def refresh_goal_status(self, gid):
        """A goal is done when every task under it has finished."""
        tasks = self._query("SELECT status FROM tasks WHERE goal_id=?", (gid,))
        if not tasks:
            return self.get_goal(gid)
        states = [t["status"] for t in tasks]
        terminal = {"done", "partial", "blocked", "error", "cancelled"}
        if all(s in terminal for s in states):
            status = "done" if all(s == "done" for s in states) else "mixed"
        elif any(s in ("running", "claimed") for s in states):
            status = "running"
        else:
            status = "open"
        return self.update_goal(gid, status=status)

    # ---------------- tasks -------------------------------------------------
    def create_task(self, mission_id, title, instructions="", context="",
                    priority=5, meta=None, goal_id=""):
        tid = new_id("tsk")
        self._exec(
            "INSERT INTO tasks (id,mission_id,goal_id,title,instructions,"
            "context,status,priority,created_at,meta) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, mission_id, goal_id or "", title, instructions, context,
             "queued", priority, time.time(), json.dumps(meta or {})))
        return self.get_task(tid)

    def get_task(self, tid):
        return self._one("SELECT * FROM tasks WHERE id=?", (tid,))

    def list_tasks(self, mission_id=None, status=None, limit=500):
        sql = "SELECT * FROM tasks"
        clauses, args = [], []
        if mission_id:
            clauses.append("mission_id=?")
            args.append(mission_id)
        if status:
            clauses.append("status=?")
            args.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY priority ASC, created_at ASC LIMIT ?"
        args.append(limit)
        return self._query(sql, tuple(args))

    def next_queued_task(self, exclude_missions=()):
        exclude = list(exclude_missions or ())
        skip = ("AND task.mission_id NOT IN (%s) "
                % ",".join("?" * len(exclude))) if exclude else ""
        rows = self._query(
            "SELECT task.* FROM tasks AS task WHERE task.status='queued' "
            + skip +
            "AND NOT EXISTS (SELECT 1 FROM tasks AS active "
            "WHERE active.mission_id=task.mission_id "
            "AND active.status IN ('claimed','running')) "
            "ORDER BY task.priority ASC, task.created_at ASC, task.id ASC "
            "LIMIT 1", tuple(exclude))
        return rows[0] if rows else None

    def cancel_if_queued(self, tid, error=""):
        """Cancel a task only if no worker has claimed it. -> bool"""
        cur = self._exec(
            "UPDATE tasks SET status='cancelled', finished_at=?, error=? "
            "WHERE id=? AND status='queued'", (time.time(), error, tid))
        return cur.rowcount == 1

    def claim_next_task(self, owner_pid, exclude_missions=()):
        """Atomically move the next queued task to 'claimed'.

        Different missions may run in parallel. Within one mission only one
        task may be claimed or running at a time, preserving its task order.
        Recheck that condition in the UPDATE so concurrent processes cannot
        claim a second task after selecting it. ``exclude_missions`` are
        held back by the caller (e.g. awaiting an orchestrator review).
        """
        while True:
            task = self.next_queued_task(exclude_missions)
            if task is None:
                return None
            cur = self._exec(
                "UPDATE tasks SET status='claimed', owner_pid=? "
                "WHERE id=? AND status='queued' "
                "AND NOT EXISTS (SELECT 1 FROM tasks AS active "
                "WHERE active.mission_id=tasks.mission_id "
                "AND active.status IN ('claimed','running'))",
                (owner_pid, task["id"]))
            if cur.rowcount == 1:
                return self.get_task(task["id"])
            # Another process won this one; try the next.

    def recover_orphaned_tasks(self, is_alive):
        """Close out tasks whose engine process died mid-run.

        ``is_alive(pid)`` decides; tasks without a recorded owner predate
        ownership tracking and are treated as orphaned too.
        """
        rows = self._query(
            "SELECT id, goal_id, status, owner_pid FROM tasks "
            "WHERE status IN ('claimed','running')")
        recovered = []
        for row in rows:
            pid = int(row.get("owner_pid") or 0)
            if pid and is_alive(pid):
                continue
            if row["status"] == "claimed":
                # Never started: safe to hand to the queue again.
                self._exec("UPDATE tasks SET status='queued', owner_pid=0 "
                           "WHERE id=? AND status='claimed'", (row["id"],))
            else:
                self._exec(
                    "UPDATE tasks SET status='error', finished_at=?, "
                    "error=? WHERE id=? AND status='running'",
                    (time.time(), "Interrupted: CollaboratorMCP stopped while "
                     "this task was running. Delegate it again to retry.",
                     row["id"]))
            if row.get("goal_id"):
                self.refresh_goal_status(row["goal_id"])
            recovered.append(row["id"])
        return recovered

    def update_task(self, tid, **fields):
        if not fields:
            return self.get_task(tid)
        if "meta" in fields and not isinstance(fields["meta"], str):
            fields["meta"] = json.dumps(fields["meta"])
        cols = ", ".join("%s=?" % k for k in fields)
        self._exec("UPDATE tasks SET %s WHERE id=?" % cols,
                   tuple(fields.values()) + (tid,))
        return self.get_task(tid)

    def add_task_cost(self, tid, usd):
        self._exec("UPDATE tasks SET cost_usd = cost_usd + ? WHERE id=?", (usd, tid))

    def add_mission_cost(self, mid, usd):
        self._exec("UPDATE missions SET cost_usd = cost_usd + ? WHERE id=?",
                   (usd, mid))

    def mission_cost(self, mid):
        row = self._one("SELECT cost_usd FROM missions WHERE id=?", (mid,))
        return float(row["cost_usd"]) if row else 0.0

    # ---------------- agents ------------------------------------------------
    def create_agent(self, model, provider, prompt, name="", task_id="",
                     mission_id=""):
        aid = new_id("agt")
        self._exec(
            "INSERT INTO agents (id,task_id,mission_id,name,model,provider,prompt,"
            "status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, task_id, mission_id, name, model, provider, prompt, "running",
             time.time()))
        return self.get_agent(aid)

    def get_agent(self, aid):
        return self._one("SELECT * FROM agents WHERE id=?", (aid,))

    def finish_agent(self, aid, result, status="done", in_tokens=0,
                     out_tokens=0, cost_usd=0.0):
        self._exec(
            "UPDATE agents SET result=?, status=?, finished_at=?, in_tokens=?,"
            " out_tokens=?, cost_usd=? WHERE id=?",
            (result, status, time.time(), in_tokens, out_tokens, cost_usd, aid))
        return self.get_agent(aid)

    def list_agents(self, task_id=None, mission_id=None, limit=300):
        sql, clauses, args = "SELECT * FROM agents", [], []
        if task_id:
            clauses.append("task_id=?")
            args.append(task_id)
        if mission_id:
            clauses.append("mission_id=?")
            args.append(mission_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return self._query(sql, tuple(args))

    # ---------------- events ------------------------------------------------
    def add_event(self, kind, text="", actor="", mission_id="", task_id="",
                  data=None):
        ts = time.time()
        cur = self._exec(
            "INSERT INTO events (ts,mission_id,task_id,kind,actor,text,data) "
            "VALUES (?,?,?,?,?,?,?)",
            (ts, mission_id or "", task_id or "", kind, actor, text,
             json.dumps(data or {})))
        return {"id": cur.lastrowid, "ts": ts, "mission_id": mission_id or "",
                "task_id": task_id or "", "kind": kind, "actor": actor,
                "text": text, "data": data or {}}

    def list_events(self, mission_id=None, task_id=None, since_id=0, limit=400):
        sql, clauses, args = "SELECT * FROM events", ["id > ?"], [since_id]
        if mission_id:
            clauses.append("mission_id=?")
            args.append(mission_id)
        if task_id:
            clauses.append("task_id=?")
            args.append(task_id)
        sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        rows = self._query(sql, tuple(args))
        rows.reverse()
        for r in rows:
            if isinstance(r.get("data"), str):
                try:
                    r["data"] = json.loads(r["data"])
                except Exception:
                    r["data"] = {}
        return rows

    def clear_events(self):
        self._exec("DELETE FROM events")

    # ---------------- usage -------------------------------------------------
    def record_usage(self, model, in_tokens, out_tokens, cost_usd, role="",
                     mission_id="", task_id=""):
        ts = time.time()
        day = time.strftime("%Y-%m-%d", time.localtime(ts))
        self._exec(
            "INSERT INTO usage (ts,day,model,role,mission_id,task_id,in_tokens,"
            "out_tokens,cost_usd) VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, day, model, role, mission_id, task_id, in_tokens, out_tokens,
             cost_usd))
        if mission_id:
            self.add_mission_cost(mission_id, cost_usd)
        if task_id:
            self.add_task_cost(task_id, cost_usd)

    def today_cost(self):
        day = time.strftime("%Y-%m-%d")
        row = self._one(
            "SELECT COALESCE(SUM(cost_usd),0) AS c FROM usage WHERE day=?", (day,))
        return float(row["c"]) if row else 0.0

    def usage_summary(self, limit=50):
        return self._query(
            "SELECT model, COUNT(*) AS calls, SUM(in_tokens) AS in_tokens, "
            "SUM(out_tokens) AS out_tokens, SUM(cost_usd) AS cost_usd "
            "FROM usage GROUP BY model ORDER BY cost_usd DESC LIMIT ?", (limit,))

    def totals(self):
        row = self._one(
            "SELECT COALESCE(SUM(cost_usd),0) c, COALESCE(SUM(in_tokens),0) i, "
            "COALESCE(SUM(out_tokens),0) o, COUNT(*) n FROM usage")
        return {"cost_usd": float(row["c"]), "in_tokens": int(row["i"]),
                "out_tokens": int(row["o"]), "calls": int(row["n"])}
