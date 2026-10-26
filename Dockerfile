# Knowledge Vault × ChainSWE demo — server + harness runtime, with Apptainer so live runs can execute
# agent commands inside the ChainSWE task images. Run with `privileged: true` (docker-compose.yml):
# Apptainer needs user namespaces / FUSE inside the container.
FROM ubuntu:24.04

ARG APPTAINER_VERSION=1.5.4
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git python3 python3-venv python3-pip \
        uidmap squashfs-tools squashfuse fuse2fs fuse3 fuse-overlayfs \
    && curl -fsSL -o /tmp/apptainer.deb \
        "https://github.com/apptainer/apptainer/releases/download/v${APPTAINER_VERSION}/apptainer_${APPTAINER_VERSION}_amd64.deb" \
    && apt-get install -y --no-install-recommends /tmp/apptainer.deb && rm /tmp/apptainer.deb \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY demo/requirements.txt demo/requirements.txt
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install -r demo/requirements.txt
ENV PATH="/opt/venv/bin:$PATH"

# repository code and data (the recorded paper runs under runs/ are tracked in git and ship with the image)
COPY . /app

# persistent state lives on volumes (docker-compose.yml); the entrypoint seeds runs/ and pulls task images
ENV RSM_SIF_DIR=/data/sifs RSM_RUNS_DIR=/data/runs DEMO_CUSTOM_WORK=/data/custom RSM_HF_HOME=/data/hf_home \
    DEMO_HOST=0.0.0.0 DEMO_PORT=8765 DEMO_ENV_FILE=/data/demo.env APPTAINER_CACHEDIR=/data/apptainer_cache
VOLUME ["/data"]
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD curl -fsS http://127.0.0.1:8765/api/meta >/dev/null || exit 1
ENTRYPOINT ["/bin/bash", "/app/demo/deploy/entrypoint.sh"]
