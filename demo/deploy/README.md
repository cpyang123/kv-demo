# Hosting the demo on Azure

## Automated path (option C): GitHub Actions → GHCR image → Azure VM (Docker Compose)

What is in the repo:

| file | role |
|---|---|
| `Dockerfile`, `.dockerignore` | demo image: Ubuntu 24.04 + Apptainer 1.5.4 + Python venv + this repo (recorded runs included) |
| `docker-compose.yml`, `demo/deploy/Caddyfile` | `demo` (privileged, data volume) + `caddy` (automatic HTTPS, basic auth) |
| `demo/deploy/entrypoint.sh`, `demo/deploy/images.txt` | seeds `runs/` into the volume, pulls task images in the background, starts the server |
| `infra/main.bicep`, `infra/cloud-init.yaml` | the VM (Ubuntu 24.04, D4s_v5, public IP with DNS label, NSG 22/80/443, Docker via cloud-init) |
| `.github/workflows/ci.yml` | every push: unit tests (`tests/test_demo.py`) + Docker build |
| `.github/workflows/infra.yml` | manual: provisions the VM with Bicep (Azure login via GitHub OIDC) |
| `.github/workflows/deploy.yml` | push to `demo-deploy`/`main`: tests → build & push `ghcr.io/<owner>/kv-demo` → SSH `docker compose up` on the VM → smoke test → GoodVision check |
| `.github/workflows/goodvision-check.yml` | daily probe of the router with the demo key (`demo/deploy/check_goodvision.py`) |

### What you need to do (once)

1. **Push this branch** to GitHub (`git push -u origin demo-deploy`). GHCR images are private by default
   and tied to the repository owner — fine.
2. **Azure identity for GitHub (OIDC, no stored password).** In the subscription you may use:
   ```bash
   az ad app create --display-name kv-demo-github            # note appId -> AZURE_CLIENT_ID
   az ad sp create --id <appId>
   az role assignment create --assignee <appId> --role Contributor --scope /subscriptions/<sub-id>/resourceGroups/kv-demo-rg   # after `az group create -n kv-demo-rg -l eastus`
   az ad app federated-credential create --id <appId> --parameters '{"name":"gh-prod","issuer":"https://token.actions.githubusercontent.com","subject":"repo:<owner>/<repo>:environment:production","audiences":["api://AzureADTokenExchange"]}'
   ```
   If the school tenant does not let you create app registrations, ask IT for a service principal with
   Contributor on one resource group and a federated credential for `repo:<owner>/<repo>:environment:production`.
3. **SSH key for the VM:** `ssh-keygen -t ed25519 -f kv-demo-vm -N ''` → public key is a secret for infra,
   private key a secret for deploy.
4. **GitHub → Settings → Environments → `production`** (create it), then **Secrets**:

   | secret | value |
   |---|---|
   | `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` | from step 2 |
   | `VM_SSH_PUBLIC_KEY` | contents of `kv-demo-vm.pub` |
   | `VM_SSH_KEY` | contents of `kv-demo-vm` (private) |
   | `VM_USER` | `kvdemo` |
   | `VM_HOST`, `DEMO_DOMAIN` | the FQDN printed by the infra workflow, e.g. `kv-demo.eastus.cloudapp.azure.com` |
   | `DEMO_BASIC_AUTH_PASSWORD` | the demo password (used by the smoke test) |
   | `DEMO_BASIC_AUTH_HASH` | `docker run --rm caddy:2.9.1 caddy hash-password --plaintext '<that password>'` |
   | `GOODVISION_API_KEY` | the router key (test environment) |
   | `GHCR_PULL_TOKEN` | a classic PAT with `read:packages`, so the VM can pull the private image |

   Optional repository **variable** `DEMO_DISABLE_LIVE=1` turns the deployment replay-only.
5. **Actions → "Provision Azure VM" → Run workflow** (resource group, region, DNS label). It prints the FQDN;
   put it into `VM_HOST` and `DEMO_DOMAIN`. Wait ~2 min for cloud-init to finish installing Docker.
6. **Actions → "Deploy demo to Azure VM" → Run workflow** (or push to the branch). First deploy pulls the
   image (~1.5 GB) and the task images in the background (~0.7 GB each, see `demo/deploy/images.txt`);
   live runs on a chain become possible once its image is present (`GET /api/meta` → `chains[].sif`).
7. Open `https://<DEMO_DOMAIN>/` — user `demo`, your password. `/compare`, `/report` as usual.

### Day-to-day

- Every push to `demo-deploy` (or `main`) redeploys; the data volume (`kv_data`) keeps live runs, sandbox
  projects and images across deployments.
- The deploy workflow fails if the site is unhealthy or the router rejects the key; a *degraded* router
  (e.g. a model family blocked upstream, tool calls refused) is a warning in the job log.
  `demo/deploy/check_goodvision.py` is the same probe for your shell.
- To rotate the key: update `GOODVISION_API_KEY` and re-run the deploy workflow.
- Logs on the VM: `ssh kvdemo@<fqdn> 'cd /opt/kv && docker compose logs -f demo'`.
- School single sign-on instead of a password: add an `oauth2-proxy` service (Microsoft Entra provider,
  tenant-restricted) between `caddy` and `demo` in `docker-compose.yml`; Caddy then proxies to it.

---

## Manual options (kept for reference)

Three ways, from simplest to fullest. The data: the git checkout already contains the recorded paper
runs (`runs/v3main100` 315 MB, `runs/v2main100` 557 MB); our router/sandbox runs add ~3 MB
(`demo/deploy/pack_data.sh` bundles them); each task image for live runs is 0.6–0.9 GB.

## A. Static snapshot (no server) — Azure Static Web Apps or a Storage static website

What you get: the compare page with embedded recorded runs (what `demo/export_static.py` produces,
~4 MB) — replay, step chains, click-to-jump, counters. No live runs, chat or sandbox.

```bash
python demo/export_static.py --out site/index.html \
  --pick v3main100:pyccel_pyccel_20240307 --pick v2main100:pyccel_pyccel_20240307 \
  --pick v2main100:matthewwithanm_python-markdownify_20250302 \
  --pick v3main100:getmoto_moto_20220601 --pick v3main100:copier-org_copier_20240418
# the export has no <html>/<head> wrapper (it targets the claude.ai artifact skeleton); add one for plain hosting:
( echo '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body>'; cat site/index.html; echo '</body></html>' ) > site/i.html && mv site/i.html site/index.html

# Storage account static website (public URL, cheapest):
az storage account create -n <acct> -g <rg> -l eastus --sku Standard_LRS --kind StorageV2
az storage blob service-properties update --account-name <acct> --static-website --index-document index.html
az storage blob upload-batch --account-name <acct> -s site -d '$web' --overwrite
az storage account show -n <acct> -g <rg> --query primaryEndpoints.web -o tsv        # the link
```
For school-only access use **Azure Static Web Apps** (Standard plan) instead and require Microsoft
Entra login in `staticwebapp.config.json`: `{"routes":[{"route":"/*","allowedRoles":["authenticated"]}],
"auth":{"identityProviders":{"azureActiveDirectory":{"registration":{"openIdIssuer":
"https://login.microsoftonline.com/<tenant-id>/v2.0", ...}}}}}` — then only accounts of the
university tenant can open it.

## B. Replay + chat, no live runs — Azure App Service (Linux, Python 3.12)

What you get: everything except live/sandbox runs (App Service has no Apptainer): agent view, compare,
report, playback, the chat box through the GoodVision router. Set `DEMO_DISABLE_LIVE=1` (hides the
run/sandbox controls and refuses those endpoints).

```bash
az group create -n kv-demo -l eastus
az appservice plan create -g kv-demo -n kv-demo-plan --is-linux --sku B2
az webapp create -g kv-demo -p kv-demo-plan -n <app-name> --runtime "PYTHON:3.12"
az webapp config set -g kv-demo -n <app-name> --startup-file \
  "uvicorn demo.server:app --host 0.0.0.0 --port 8000"
az webapp config appsettings set -g kv-demo -n <app-name> --settings \
  DEMO_DISABLE_LIVE=1 DEMO_PROVIDER=goodvision-test GOODVISION_API_KEY=<key> \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true RSM_RUNS_DIR=/home/site/wwwroot/runs
# requirements: App Service installs demo/requirements.txt if it is at the repo root — copy it there
cp demo/requirements.txt requirements.txt
# deploy the checkout (runs/ included, ~900 MB; zip deploy accepts it but takes a few minutes).
# Alternative that keeps deploys small: mount an Azure Files share at /home/site/wwwroot/runs
#   az webapp config storage-account add ... --mount-path /home/site/wwwroot/runs
zip -qr ../kv-demo.zip . -x '.git/*' 'demo/.env' 'demo/live/*'
az webapp deploy -g kv-demo -n <app-name> --src-path ../kv-demo.zip --type zip
# school login in one step (Easy Auth, Microsoft Entra, this tenant only):
az webapp auth update -g kv-demo -n <app-name> --enabled true --action LoginWithAzureActiveDirectory \
  --aad-client-id <app-registration-id> --aad-token-issuer-url https://sts.windows.net/<tenant-id>/
```
The site is `https://<app-name>.azurewebsites.net/` (`/compare`, `/report`).

## C. Full demo with live runs — an Azure VM

What you get: everything, including live chain runs and sandbox tasks through the GoodVision router
(the harness executes commands inside Apptainer images; no GPU needed since inference is remote — the
local vLLM option is simply absent).

1. VM: Ubuntu 24.04, `Standard_D4s_v5` (4 vCPU / 16 GB) is plenty; 64 GB OS disk + a data disk if you
   keep many images. Open NSG ports 22, 80, 443. Give it a DNS name (the VM's public IP DNS label or a
   CNAME in the school's zone).
2. `sudo bash demo/deploy/azure_vm_setup.sh <git url> <dns name>` — installs Apptainer, Python 3.12,
   Caddy, creates the `kvdemo` user, checks out `share/inline-ledger-0922`, installs
   `demo/requirements.txt`, installs the systemd unit (`demo/deploy/kv-demo.service`) and a Caddyfile.
   The repo is private, so clone with a deploy key or a token, or `scp` a checkout instead.
3. Data: on the cluster run `demo/deploy/pack_data.sh /scratch/$USER/kv/sifs /scratch/$USER/kv/bundle`
   and copy the bundle (azcopy to a blob container, or scp). On the VM: `tar -xzf runs_extra.tgz` inside
   the repo, images into `/opt/kv/sifs`.
4. Secrets: `GOODVISION_API_KEY` in `/opt/kv/demo.env` (root-only) — or Key Vault with a managed
   identity if the school requires it. Never commit `demo/.env`.
5. Access control is mandatory: the server starts agent runs that spend router credits and execute
   commands in containers. The Caddyfile ships with `basic_auth` (`caddy hash-password`); for school
   single sign-on put `oauth2-proxy` with the Microsoft Entra provider in front and restrict to the
   tenant. Caddy obtains the TLS certificate automatically once DNS points at the VM.
6. `systemctl restart kv-demo caddy`; open `https://<dns name>/`.

Operational notes: the harness writes workspaces under `DEMO_CUSTOM_WORK` and `/tmp/rsm_chain`; a chain
run through the router takes 3–10 min and $1–5 of router credit; sessions and ledgers persist under
`runs/` on the VM (back them up or sync to blob storage). `GET /api/providers` on the deployed site
shows the router status (including the current Anthropic-upstream block).
