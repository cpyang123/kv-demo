"""DefaultAgent that keeps its trajectory file fresh for a live viewer.

Opt-in with RSM_LIVE_SAVE=1 (demo/server.py sets it for the runs it launches). Otherwise this class behaves
byte-for-byte like minisweagent's DefaultAgent: same calls, same messages, same output file.

With the switch on:
  * the trajectory is also saved right after each model reply, before the command runs, so a viewer polling the
    file sees the thought + command while the command is still executing;
  * every save goes through a temp file + os.replace, so a concurrent reader never sees a half-written JSON.
"""
from __future__ import annotations

import json
import os
import pathlib

from minisweagent.agents.default import DefaultAgent


def live_save_on() -> bool:
    return os.environ.get("RSM_LIVE_SAVE") == "1"


class LiveSaveAgent(DefaultAgent):
    def query(self) -> dict:
        message = super().query()
        if live_save_on():
            try:
                self.save(self.config.output_path)
            except Exception:
                pass                                   # a failed intermediate save must never break the run
        return message

    def save(self, path, *extra_dicts) -> dict:
        if not live_save_on() or not path:
            return super().save(path, *extra_dicts)
        data = self.serialize(*extra_dicts)
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, path)
        return data
