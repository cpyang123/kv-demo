"""Demo server with the harness replaced by a replayer: a 'live run' copies a recorded session into the run dir step
by step (assistant reply first, then its observation), grades it after a pause, then moves to the next task."""
import glob, json, os, pathlib, shutil, sys, threading, time, uuid
ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = pathlib.Path(os.environ.get("KV_UI_RUNS", str(ROOT.parent / "tmp" / "ui_runs"))); RUNS.mkdir(parents=True, exist_ok=True)
if not (RUNS / "v3main100").exists():
    os.symlink(ROOT / "runs" / "v3main100", RUNS / "v3main100")
os.environ.setdefault("RSM_SIF_DIR", str(ROOT / "sifs"))
os.environ.setdefault("RSM_HF_HOME", str(RUNS / "_hf_home"))
os.environ.setdefault("DEMO_CUSTOM_WORK", str(RUNS / "_custom_work"))
os.environ.update({"RSM_RUNS_DIR": str(RUNS), "DEMO_LIVE_DIR": str(RUNS / "_live")})
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "demo"))
import server as S
S._sif_ok = lambda cid: True
STEP = float(os.environ.get("FAKE_STEP_SECS", "0.8"))

def write(path, data):
    tmp = path.with_name(path.name + ".tmp"); tmp.write_text(json.dumps(data)); os.replace(tmp, path)

def fake_start(req):
    cid, kind = req.cid, req.kind; arm = S.ARMS[kind]; run_id = req.run_id or S._new_live_run_id()
    src = ROOT / "runs" / "v3main100" / f"{cid}__s0__{arm}"
    out = RUNS / run_id / f"{cid}__s0__{arm}"; shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True); time.sleep(0.3)
    job = uuid.uuid4().hex[:8]; (RUNS / "_live").mkdir(exist_ok=True); log = RUNS / "_live" / f"{job}.log"; log.write_text("$ fake harness (replaying v3main100)\n")
    S.JOBS[job] = {"id": job, "cid": cid, "kind": kind, "arm": arm, "run_id": run_id, "started": time.time(), "status": "running", "returncode": None,
                   "log": str(log), "api_base": "fake", "provider": req.provider, "model": req.model or "fake-model", "pid": 0}
    def run():
        results = [json.loads(l) for l in open(src / "results.jsonl") if l.strip()]
        time.sleep(2.5)
        with open(log, "a") as lf: lf.write(f"=== {cid} arm={arm} (1/1)\nINFO: apptainer image ready\n")
        for r in results[: int(os.environ.get("FAKE_TASKS", "2"))]:
            k = r["order"]; tf = sorted(glob.glob(f"{src}/traj_{k}_*.json"))[0]; t = json.load(open(tf)); msgs = t["messages"]; dst = out / os.path.basename(tf)
            if kind != "none": time.sleep(1.5)
            i = 2
            while i < len(msgs):
                part = dict(t); part["messages"] = msgs[:i + 1]; part["info"] = dict(t["info"], exit_status="", submission="")
                write(dst, part); time.sleep(STEP * 0.6)
                part["messages"] = msgs[:i + 2]; last = part["messages"][-1]
                if last.get("role") == "exit" or i + 2 >= len(msgs): part["info"] = dict(t["info"])
                write(dst, part); time.sleep(STEP); i += 2
            write(dst, t); time.sleep(2.5)
            with open(out / "results.jsonl", "a") as f: f.write(json.dumps(r) + "\n")
            with open(log, "a") as lf: lf.write(f"  [{cid}|{arm}] pos{k} {r.get('instance')}: resolved={r.get('resolved')} steps={r.get('n_steps')}\n")
        time.sleep(1.0)
        S.JOBS[job].update(status="done", returncode=0, finished=time.time())
    threading.Thread(target=run, daemon=True).start()
    return S.JOBS[job]
S._start_live = fake_start
import uvicorn
uvicorn.run(S.app, host="127.0.0.1", port=int(os.environ.get("FAKE_PORT", "8799")), log_level="warning")
