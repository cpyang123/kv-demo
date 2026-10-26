#!/bin/bash
# Container entrypoint: seed the persistent runs/ volume from the image, pull missing task images in the
# background (demo/deploy/images.txt), then start the demo server.
set -euo pipefail
mkdir -p "$RSM_SIF_DIR" "$RSM_RUNS_DIR" "$DEMO_CUSTOM_WORK" "$RSM_HF_HOME" "$APPTAINER_CACHEDIR"
# recorded runs shipped in the image -> volume, once (live runs then persist there across image updates)
for d in /app/runs/*/; do
    n=$(basename "$d"); [ -e "$RSM_RUNS_DIR/$n" ] || cp -r "$d" "$RSM_RUNS_DIR/$n"
done
if [ "${DEMO_PULL_IMAGES:-1}" = "1" ]; then
    ( while read -r tag; do
          [ -z "$tag" ] && continue; case "$tag" in \#*) continue;; esac
          name="${tag##*:}"; [ -s "$RSM_SIF_DIR/$name.sif" ] && continue
          echo "[entrypoint] pulling $tag -> $name.sif" >&2
          apptainer pull --name "$RSM_SIF_DIR/$name.sif" "docker://$tag" >&2 || echo "[entrypoint] pull failed: $tag" >&2
      done < /app/demo/deploy/images.txt
      echo "[entrypoint] image pulls finished" >&2 ) &
fi
cd /app
exec python demo/server.py
