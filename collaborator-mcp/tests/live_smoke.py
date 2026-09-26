"""Live subscription smoke checks in a throwaway database; no real tasks run."""
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collaborator.config import Settings
from collaborator.engine import Engine
from collaborator.store import Store
from collaborator import providers

with tempfile.TemporaryDirectory(prefix='collaborator-smoke-') as tmp:
    settings=Settings(str(Path(tmp)/'settings.json'))
    settings.update({'workspace':tmp,'budget_enabled':False,'executor_model':'claude-cli',
        'orchestrator_model':'codex-cli','orchestrator_mode':'external',
        'allowed_agent_models':['claude-cli-haiku','claude-cli-sonnet','codex-cli'],
        'cli_codex_sandbox':'read-only','cli_timeout_s':120,'agent_timeout_s':120})
    store=Store(str(Path(tmp)/'smoke.db'))
    engine=Engine(settings,store)
    try:
        plan=engine.plan_mission(title='Connection smoke test',brief='Make one goal with exactly one task: reply COLLABORATOR_OK. No files or tools needed.',max_tasks=1,max_goals=1)
        assert plan.get('task_count')==1,plan
        print('PASS Codex planner: structured plan with one task',flush=True)
        for model in ['claude-cli-haiku','claude-cli-sonnet','codex-cli']:
            reply=engine.run_sub_agent('connection-check',model,'This is a connection smoke test. Do not use tools. Reply exactly COLLABORATOR_OK.')
            assert reply.strip()=='COLLABORATOR_OK',(model,reply)
            print('PASS helper '+model,flush=True)
    finally:
        engine.shutdown()
        store.close()
