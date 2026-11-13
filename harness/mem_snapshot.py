"""Per-session snapshot of a memory layer's store, for the demo's memory comparison view.

Opt-in with RSM_MEM_SAVE=1 (demo/server.py sets it for the runs it launches); otherwise a no-op, so the
paper runs are unchanged. After the write at session k the full store is saved as
<chain dir>/mem_store_<k>.json = {"order": k, "nodes": <items this write added>, "items": [[name, text], ...]}.
Recorded runs without these files are reconstructed from the next session's injected block, which cannot
recover what the *last* session wrote; the snapshot can. Never raises: a failed snapshot must not cost a run.
"""
import json
import os


def enabled():
    return os.environ.get("RSM_MEM_SAVE") == "1"


def save(out, order, layer, nodes, force=False):
    if not (force or enabled()) or layer is None:
        return
    try:
        items = [[str(n), str(t)] for n, t in getattr(getattr(layer, "store", None), "items", []) or []]
        p = out / f"mem_store_{order}.json"
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps({"order": order, "nodes": nodes, "items": items}))
        os.replace(tmp, p)
    except Exception as e:                       # noqa: BLE001  diagnostics only
        print(f"  mem_snapshot: could not save store after session {order}: {e}", flush=True)
