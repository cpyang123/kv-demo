# Handoff: deploy the Knowledge Vault demo on this Azure VM

You are a Claude Code instance running **on the Azure VM `ChronosenseFinance`** (resource group `CHRONOSENSE`,
subscription "Microsoft Azure Sponsorship", East US 2; Ubuntu 24.04, 24 vCPU, one NVIDIA A100 80 GB; **no public
IP, no DNS name**). The human you work with is Peter (`cpyang123` on GitHub). This repository is his copy of the
team's `knowledge-vault-project`; the branch content you see is what was pushed to `main`. A previous Claude
session on the Yale cluster built everything below; this file is its handoff to you.

## Goal

Get the demo (`demo/`) running on this VM so that people can open it in a browser, **without adding a public IP
or opening NSG ports**, and without disturbing whatever else runs on this shared machine. Then wire the
GitHub Actions deployment so pushes redeploy automatically.

## What the demo is (30-second version)

A FastAPI server over the ChainSWE harness that replays recorded coding-agent sessions and compares memory
systems: **Knowledge Vault** (the team's method, arm `mem_fusednote`) vs **Mem0** (`mem_factsall`) vs no
memory (`seq`), with token/cost ledgers, a side-by-side player, sandbox projects, and live runs through the
**GoodVision SmartRouter** (an OpenAI-compatible gateway). Read `demo/README.md` (features), `demo/deploy/README.md`
(deployment paths) and `demo/goodvision.md` (everything known about the router API, incl. current outages).

## Ground rules on this machine

- **Never** create a public IP, change NSGs, or open ports. Ingress is outbound-only (tunnel) — see below.
- This VM belongs to another project. Before changing anything, survey what is there and leave it alone:
  `docker ps -a`, `systemctl list-units --type=service --state=running | head -50`, `ss -ltnp`, `nvidia-smi`,
  `df -h`. Do not stop, rebuild or reconfigure containers/services you did not create. Our footprint is
  `/opt/kv` plus containers named `kv-*` and the `kvrunner` user.
- Secrets live only in `/opt/kv/.env` (`chmod 600`), never in the repo. The GoodVision key comes from Peter;
  `demo/.env.example` shows the variable names. The server scrubs API keys from run artifacts automatically
  (`demo/scrub.py`) — mini-swe-agent writes the model config, including `api_key`, into every `traj_*.json`.
- Ask before spending router credit: a live chain run costs $1–5 of GoodVision credit; sandbox tasks ~$0.2–0.5.

## Plan (do these in order, verify each)

1. **Survey** (above). Note whether Docker, the NVIDIA container toolkit and Apptainer are installed.
2. **Get the code**: this checkout. `git log --oneline -3` should show the demo-deploy commits.
3. **Run it locally first** (no GitHub, no registry):
   ```bash
   sudo mkdir -p /opt/kv && sudo chown "$USER" /opt/kv
   cp docker-compose.yml demo/deploy/Caddyfile /opt/kv/
   printf 'GOODVISION_API_KEY=%s\nDEMO_PROVIDER=goodvision-test\nDEMO_DISABLE_LIVE=0\n' '<key from Peter>' > /opt/kv/.env && chmod 600 /opt/kv/.env
   docker build -t kv-demo:local .                 # ~10 min first time; Apptainer + venv + repo incl. recorded runs
   cd /opt/kv && IMAGE=kv-demo:local docker compose up -d demo     # only the demo service, no ingress yet
   docker logs -f kv-demo                           # entrypoint seeds runs/ and pulls task images in the background
   curl -s http://127.0.0.1:8765/api/meta | python3 -m json.tool | head -30
   curl -s -m 120 http://127.0.0.1:8765/api/providers | python3 -c "import json,sys; [print(p['name'], p['ok'], p.get('message')) for p in json.load(sys.stdin)]"
   ```
   Expected: `runs` lists v2main100/v3main100/demo_live/gv_sonnet5/cfg__*/custom; `goodvision-test` ok=True with
   19 models and a warning that the Anthropic family is blocked upstream (known, see below).
   The container runs **privileged** because the harness executes agent commands with Apptainer inside it. If
   Apptainer fails there (`apptainer exec` errors in `docker logs`), set `DEMO_DISABLE_LIVE=1` in `.env` and
   restart: replay, compare and chat still work; only live/sandbox runs are off.
4. **Ingress without inbound ports** — ask Peter which he has set up:
   - Cloudflare Tunnel: he gives you a tunnel token → `CLOUDFLARE_TUNNEL_TOKEN=…` in `/opt/kv/.env`, then
     `docker compose --profile tunnel up -d`. The tunnel's public hostname must map to HTTP `demo:8765`; a
     Cloudflare Access policy is the login wall. Test: `docker logs kv-tunnel` shows "Registered tunnel connection".
   - Azure Dev Tunnels (no domain): `devtunnel user login`, `devtunnel create kv-demo`, `devtunnel port create -p 8765`,
     `devtunnel host kv-demo` (run under systemd or tmux). Tenant-restricted access is possible.
   - Tailscale (team-only): `tailscale up`, then the site is `http://<vm-tailscale-name>:8765` for tailnet members.
5. **CI/CD**: install the GitHub Actions runner so `.github/workflows/deploy-runner.yml` can deploy on push.
   Peter creates the registration token (repo → Settings → Actions → Runners → New self-hosted runner) — it
   expires in an hour. Then, as root:
   ```bash
   id kvrunner >/dev/null 2>&1 || useradd -m -s /bin/bash kvrunner
   echo 'kvrunner ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/kvrunner && chmod 440 /etc/sudoers.d/kvrunner
   sudo -u kvrunner bash -c 'mkdir -p ~/actions-runner && cd ~/actions-runner && curl -fsSL -o r.tgz https://github.com/actions/runner/releases/download/v2.338.2/actions-runner-linux-x64-2.338.2.tar.gz && tar xzf r.tgz && rm r.tgz && ./config.sh --unattended --url https://github.com/cpyang123/kv-demo --token <TOKEN> --name chronosense-finance --labels kv-demo --replace'
   cd /home/kvrunner/actions-runner && ./svc.sh install kvrunner && ./svc.sh start && ./svc.sh status | tail -3
   ```
   The workflow then writes `/opt/kv/.env` from GitHub secrets (`GOODVISION_API_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`),
   pulls `ghcr.io/cpyang123/kv-demo:<sha>` and runs `docker compose --profile ${DEPLOY_PROFILE:-tunnel} up -d`.
   Peter must create the `production` environment and those secrets in the GitHub repo. Note the workflow's
   `.env` **replaces** the hand-written one from step 3 — keep the key in the GitHub secret.
6. **Optional, this VM has an A100**: serve the paper's actor locally so the "local" provider works here:
   `Qwen/Qwen3.8-27B-FP8` on vLLM (≥0.30), served as `coder-next` on port 8850 with `--max-model-len 98304`
   (`demo/slurm/serve_actor.sbatch` has the exact flags). Easiest as a `vllm/vllm-openai` compose service with
   `deploy.resources.reservations.devices` (needs the NVIDIA container toolkit) and `RSM_API_BASE=http://vllm:8000/v1`
   on the demo service. Check GPU memory is free first (`nvidia-smi`) — the other project may be using it.

## Verification checklist

- `curl localhost:8765/api/meta` → 200, `live_enabled` as intended, `default_cid` = `pyccel_pyccel_20240307`.
- `curl localhost:8765/api/summary?run=v3main100` → `common_chains: 100`, KV cheaper than Mem0.
- `python3 demo/deploy/check_goodvision.py` with the key → exit 0 (all good) or 2 (reachable but degraded; today
  this is expected: Anthropic models blocked upstream, tool calls refused). Exit 1 = key/market problem.
- Browser (through the tunnel): `/` opens the showcase chain; `/compare` plays KV vs Mem0 vs none; `/report`.
- `pytest -q tests/test_demo.py` passes (15 tests, offline) if you change server code.

## Known state of the GoodVision router (2026-10-26)

Key works; 19 models; GPT models and `auto`-on-chat fine. **Every Anthropic model fails upstream** (Bedrock:
"Access to Anthropic models is not allowed for this account") since Oct 22, and the default routing policy sends
coding prompts to `claude-opus-5`, so `model: auto` fails for agent runs — use a fixed GPT id (e.g. `gpt-5.4-mini`
for cheap tests, `gpt-5.5` for the baseline configuration). Requests declaring tools are refused (`no_candidate`),
which rules out Codex/function-calling harnesses on this key. Peter has these points open with GoodVision.
The demo's endpoint status shows the current state; the daily `goodvision-check` workflow tracks it.

## Repository map (what matters for deployment)

| path | role |
|---|---|
| `demo/server.py` | FastAPI app; `demo/providers.py` endpoints; `demo/custom_runner.py` sandbox tasks; `demo/scrub.py` |
| `demo/static/` | `index.html` agent view, `compare.html` + `player.js` side-by-side player, `report.html` dashboard |
| `demo/configs.json`, `demo/model_prices.json` | comparison configurations; per-model list prices |
| `Dockerfile`, `docker-compose.yml`, `demo/deploy/*` | image, services (profiles `public` / `tunnel`), entrypoint, images to pull, Caddyfile, router check |
| `.github/workflows/` | `ci.yml` tests+build, `deploy-runner.yml` (default), `deploy.yml` (SSH path, manual), `infra.yml` (new VM via Bicep, not needed here), `goodvision-check.yml` |
| `runs/` | recorded paper runs (v2main100, v3main100) + router runs; served from the `/data` volume after first start |
| `harness/`, `memory/`, `data/` | the team's ChainSWE harness; env-gated patches for OpenAI-style gateways (`RSM_API_KEY`, `RSM_OPENAI_STRICT`) |

When something is unclear, prefer reading `demo/deploy/README.md` and `demo/goodvision.md` over guessing, keep
changes on a branch, and run the unit tests before pushing.
