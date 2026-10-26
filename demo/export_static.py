"""Export the compare page as one self-contained HTML file (no backend): the recorded sessions of a few
showcase chains are embedded as JSON and a small shim answers the page's api() calls from it. Live runs,
model endpoints and the GoodVision key are not part of the export. Used for a shareable snapshot.

  python demo/export_static.py --server http://127.0.0.1:8765 --out /path/compare_snapshot.html \
      --pick v3main100:pyccel_pyccel_20240307 --pick v2main100:matthewwithanm_python-markdownify_20250302 ...
"""
import argparse, json, pathlib, re, time, urllib.request, urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--server", default="http://127.0.0.1:8765")
ap.add_argument("--out", required=True)
ap.add_argument("--pick", action="append", required=True, help="run:cid")
ap.add_argument("--obs-chars", type=int, default=3000)
a = ap.parse_args()

def get(path):
    with urllib.request.urlopen(a.server + "/" + path, timeout=120) as r:
        return json.loads(r.read())

meta = get("api/meta"); cfgs = get("api/configs")
picks = [p.split(":", 1) for p in a.pick]
runs = sorted({r for r, _ in picks}); cids = sorted({c for _, c in picks})
runchains = {}
for run in runs:
    rc = get(f"api/runchains?run={urllib.parse.quote(run)}")
    rc["chains"] = [c for c in rc["chains"] if c["cid"] in cids and (run, c["cid"]) in [tuple(p) for p in picks]]
    runchains[run] = rc
sessions = {}
for run, cid in picks:
    ch = next((c for c in runchains[run]["chains"] if c["cid"] == cid), None)
    if not ch:
        continue
    for kind, arm in ch["arms"].items():
        for s in arm["sessions"]:
            key = f"{run}|{arm['arm']}|{cid}|{s['order']}"
            sess = get(f"api/session?run={urllib.parse.quote(run)}&arm={arm['arm']}&cid={urllib.parse.quote(cid)}&order={s['order']}")
            sess.pop("system_prompt", None); sess.pop("instructions", None); sess.pop("ledger", None)
            for t in sess["turns"]:
                t.pop("raw", None)
                if t.get("observation") and len(t["observation"]["output"]) > a.obs_chars:
                    t["observation"]["output"] = t["observation"]["output"][:a.obs_chars] + "\n…"; t["observation"]["truncated"] = True
            sessions[key] = sess
# configs: only the recorded ones can be shown without a backend
cfgs["configs"] = [c for c in cfgs["configs"] if c.get("recorded")]
cfgs["default_pair"] = [cfgs["showcase"]["a"], cfgs["showcase"]["b"]] if cfgs.get("showcase") else [c["id"] for c in cfgs["configs"][:2]]
meta["chains"] = [c for c in meta["chains"] if c["cid"] in cids]
meta["providers"] = []; meta["runs"] = runs
data = {"meta": meta, "configs": cfgs, "runchains": runchains, "sessions": sessions, "exported": time.strftime("%Y-%m-%d")}

html = (HERE / "static/compare.html").read_text()
player = (HERE / "static/player.js").read_text()
# the shim replaces player.js's network api()
player = "\n".join(l for l in player.splitlines() if not l.startswith("export async function api("))   # one-line function
player = player.replace("export const ", "const ").replace("export function ", "function ").replace("export class ", "class ")
shim = '''
const __DATA__ = JSON.parse(document.getElementById("snapshot-data").textContent);
async function api(path, opts) {
  if (opts && opts.method === "POST") throw new Error("Live runs are not available in this shared snapshot.");
  const [p, qs] = path.split("?"); const q = new URLSearchParams(qs || "");
  if (p === "api/meta") return __DATA__.meta;
  if (p === "api/configs") return __DATA__.configs;
  if (p === "api/providers") return [];
  if (p === "api/runchains") { const rc = __DATA__.runchains[q.get("run")]; if (!rc) throw new Error("run not in snapshot"); return rc; }
  if (p === "api/session") { const s = __DATA__.sessions[`${q.get("run")}|${q.get("arm")}|${q.get("cid")}|${q.get("order")}`]; if (!s) throw new Error("session not in snapshot"); return s; }
  throw new Error("not available in this snapshot: " + path);
}
'''
html = html.replace('import { SessionPlayer, NAMES, COLOR, esc, usd, tok, fmtClock, api } from "./static/player.js";', shim + player)
# artifact page contract: no document skeleton tags of our own; title + meta at the top; safe-area side gutter
html = re.sub(r"^<!doctype html>\s*<html[^>]*>\s*<head>\s*", "", html, flags=re.I)
html = re.sub(r'<meta charset="utf-8">\s*<meta name="viewport"[^>]*>\s*', "", html)
html = html.replace("</head>\n<body>", "").replace("</body>\n</html>", "")
html = html.replace("<title>Knowledge Vault vs Mem0</title>", '<title>Knowledge Vault vs Mem0</title>\n<meta name="claude:viewport-fit-sides" content="cover">')
html = html.replace("  .app { display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; grid-template-columns: minmax(0, 1fr); height: 100vh; }",
                    "  html, body { height: 100%; }\n  .app { display: grid; grid-template-rows: auto auto minmax(0, 1fr) auto; grid-template-columns: minmax(0, 1fr); height: 100%; min-height: 100vh; padding-left: max(0px, env(safe-area-inset-left, 0px)); padding-right: max(0px, env(safe-area-inset-right, 0px)); }\n  header.top, .controls, footer.sum { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }\n  .live { display: none !important; }")
banner = '<span class="st" style="margin-left:auto;white-space:normal;max-width:100%">Shared snapshot of recorded paper runs (exported ' + data["exported"] + '). Live runs and model endpoints are not part of this copy.</span>'
html = html.replace('<div class="live">', banner + '\n    <div class="live">', 1)
html = html.replace('<span style="white-space:nowrap;font-size:13px"><a href="./">Agent view</a> · <a href="report" target="_blank">Report ↗</a></span>', "")
blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
html = html.replace("<script type=\"module\">", f'<script id="snapshot-data" type="application/json">{blob}</script>\n<script type="module">', 1)
out = pathlib.Path(a.out); out.write_text(html)
print(f"wrote {out} ({out.stat().st_size/1e6:.1f} MB): {len(sessions)} sessions, chains {cids}, runs {runs}")
