#!/bin/bash
#SBATCH --job-name=kv_demo_actor
#SBATCH --partition=rtx-batch
#SBATCH --account=zy286_ea_yale
#SBATCH --gres=gpu:rtx_pro_6000:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --time=8:00:00
#SBATCH --output=/scratch/%u/kv/logs/%x_%j.out
# Serve the ChainSWE actor model for the demo's live runs (TEAM_GUIDE §1: Qwen3.8-27B-FP8,
# served name coder-next, 98304 context) on one RTX Pro 6000 and idle. The demo server then
# points its live runs at http://<node>:8850/v1 (this file prints SERVER_HOST when ready).
#   sbatch demo/slurm/serve_actor.sbatch
#   MODEL=Qwen/Qwen3.8-27B-FP8 PORT=8850 sbatch demo/slurm/serve_actor.sbatch
set -euo pipefail
mkdir -p /scratch/$USER/kv/logs
VLLM_VENV="${VLLM_VENV:-/scratch/$USER/venvs/vllm031}"      # vLLM >= 0.30 (shared with rememgraph)
export HF_HOME="${HF_HOME:-/scratch/$USER/rmg/hf_cache}"
export HF_HUB_OFFLINE=1                                      # weights pre-downloaded (demo/README.md)
export CUDA_HOME="${CUDA_HOME:-$HOME/sw/cuda130/nvidia/cu13}"  # nvcc for JIT kernels (rememgraph recipe)
export PATH="$VLLM_VENV/bin:$CUDA_HOME/bin:$PATH"
export VLLM_USE_FLASHINFER_SAMPLER=0
MODEL="${MODEL:-Qwen/Qwen3.8-27B-FP8}"
PORT="${PORT:-8850}"
SERVED="${SERVED:-coder-next}"
MAX_LEN="${MAX_LEN:-98304}"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
"$VLLM_VENV/bin/python" -m vllm.entrypoints.openai.api_server --model "$MODEL" --port "$PORT" \
  --served-model-name "$SERVED" --max-model-len "$MAX_LEN" --gpu-memory-utilization 0.90 \
  --enable-prefix-caching --max-num-seqs 32 \
  > "/scratch/$USER/kv/logs/vllm_${SLURM_JOB_ID}.log" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true' EXIT
for i in $(seq 1 180); do
  curl -sf "http://localhost:$PORT/v1/models" >/dev/null && break
  kill -0 $PID 2>/dev/null || { echo "vLLM died; see /scratch/$USER/kv/logs/vllm_${SLURM_JOB_ID}.log"; exit 1; }
  sleep 10
done
curl -sf "http://localhost:$PORT/v1/models" >/dev/null || { echo "vLLM not ready after 30 min"; exit 1; }
echo "vLLM ready after ~$((i * 10)) s"
echo "SERVER_HOST=$(hostname)"
echo "API_BASE=http://$(hostname):$PORT/v1"
echo "http://$(hostname):$PORT/v1" > /scratch/$USER/kv/actor_endpoint
while kill -0 $PID 2>/dev/null; do sleep 60; done
echo "vLLM exited"
