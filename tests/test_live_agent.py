"""harness/live_agent.py: trajectory saved after every model reply + atomically, only when RSM_LIVE_SAVE=1."""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.live_agent import LiveSaveAgent  # noqa: E402


class StubModel:
    """Minimal mini-swe-agent Model: one reply with one action."""
    def __init__(self):
        self.calls = 0

    def format_message(self, role, content):
        return {"role": role, "content": content}

    def query(self, messages):
        self.calls += 1
        return {"role": "assistant", "content": "THOUGHT: look\n```bash\nls\n```", "extra": {"actions": [{"command": "ls"}], "cost": 0.0}}

    def format_observation_messages(self, message, outputs, template_vars):
        return [{"role": "user", "content": f"<returncode>0</returncode>\n<output>\n{outputs[0]['output']}</output>"}]

    def serialize(self):
        return {"info": {"config": {"model": {"model_name": "stub"}}}}

    def get_template_vars(self):
        return {}


class StubEnv:
    def execute(self, action, cwd=""):
        return {"output": "a.py\n", "returncode": 0}

    def serialize(self):
        return {}

    def get_template_vars(self):
        return {}


def _agent(tmp_path):
    a = LiveSaveAgent(StubModel(), StubEnv(), system_template="sys", instance_template="{{task}}", step_limit=5, output_path=tmp_path / "traj_1_x.json")
    a.extra_template_vars |= {"task": "fix it"}
    a.messages = [a.model.format_message("system", "sys"), a.model.format_message("user", "fix it")]
    return a


def test_live_save_writes_after_the_reply_and_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("RSM_LIVE_SAVE", "1")
    a = _agent(tmp_path)
    a.query()
    f = tmp_path / "traj_1_x.json"
    assert f.exists(), "trajectory must exist right after the model reply, before the command runs"
    t = json.load(open(f))
    assert [m["role"] for m in t["messages"]] == ["system", "user", "assistant"]
    assert not list(tmp_path.glob("*.tmp")), "atomic write leaves no temp file behind"
    a.execute_actions(a.messages[-1]); a.save(a.config.output_path)
    t = json.load(open(f))
    assert [m["role"] for m in t["messages"]] == ["system", "user", "assistant", "user"]
    assert t["info"]["config"]["model"]["model_name"] == "stub"


def test_without_the_switch_it_is_the_plain_agent(tmp_path, monkeypatch):
    monkeypatch.delenv("RSM_LIVE_SAVE", raising=False)
    a = _agent(tmp_path)
    a.query()
    assert not (tmp_path / "traj_1_x.json").exists(), "DefaultAgent saves only after the whole step"
    a.save(a.config.output_path)
    assert (tmp_path / "traj_1_x.json").exists() and not list(tmp_path.glob("*.tmp"))
