#!/bin/bash
# Bundle what the hosted demo needs beyond the git checkout: untracked run directories (live runs,
# sandbox projects, configuration runs), the Apptainer images for live runs, and model_prices/configs.
# Usage: demo/deploy/pack_data.sh /scratch/$USER/kv/sifs /scratch/$USER/kv/bundle
set -euo pipefail
SIFS="${1:?sif dir}"; OUT="${2:?output dir}"; mkdir -p "$OUT"
cd "$(dirname "$0")/../.."
# runs/ not tracked by git (v2main100/v3main100 ARE tracked and come with the clone)
tar -czf "$OUT/runs_extra.tgz" $(ls -d runs/*/ | grep -v -E 'runs/(v2main100|v3main100|v2equiv|clinembsmoke|memopsmoke|rbanksmoke)/' | sed 's#/$##')
echo "runs_extra.tgz: $(du -h "$OUT/runs_extra.tgz" | cut -f1)"
# images (only needed for live runs; ~0.6-0.9 GB each)
mkdir -p "$OUT/sifs"; for f in "$SIFS"/*.sif; do cp -L "$f" "$OUT/sifs/"; done
echo "sifs: $(du -sh "$OUT/sifs" | cut -f1)"
echo "Now copy $OUT to the VM, e.g.:  azcopy copy '$OUT' 'https://<storage>.blob.core.windows.net/<container>?<sas>' --recursive"
echo "or:                             scp -r '$OUT' kvdemo@<vm>:/opt/kv/bundle"
echo "Do NOT bundle demo/.env — set GOODVISION_API_KEY on the VM (/opt/kv/demo.env) or in Key Vault."
