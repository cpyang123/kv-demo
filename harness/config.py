"""集群路径、agent 模板(钉版 yaml)、模型 kwargs、臂命名 —— 全部单点定义。
# F18(2026-08-28 定性,v2main100 内不修——五臂对称,臂间公平;v3 协议修正包一并处理):
# 本文件只加载 yaml 的 agent: 段,静默丢弃 model: 段两模板 →
#   ①观测截断(上游 10K head/tail)缺失:agent 吃全量命令输出,token/context 被抬高;
#   ②finish_reason 感知格式纠错缺失:截断回复收到泛用 EXACTLY-ONE 训斥而非"超 token 请精简"。
# 叠加:actor thinking 未关未剥(vLLM 无 --reasoning-parser,think 段进解析器计块)+ max_tokens=3500
# (Qwen 思考模式官方建议 32K+)→ 全臂 ~50% RepeatedFormatError 地板的三个自因。
# 文献核查:Qwen 官方 SWE-bench=非思考;MemOp/SWE-MeM 全用非思考 Instruct;ChainSWE=API 内部推理;
# "原始 think 进 content + 小帽 + 逐块解析"在所有被查源中均非标准配置。

import 副作用契约:HF_HOME/MSWEA_* 必须在 import minisweagent 之前设置,本模块顶部完成。"""
import json, os, pathlib, sys

HERE = pathlib.Path(__file__).parent          # chainswe/harness/
ROOT = HERE.parent                            # chainswe/

# ---- 集群探测(Misha vs Bouchet),env 可覆盖 ----
if pathlib.Path("/gpfs/radev/scratch/dijk/pl662").exists():
    _SIF_DEF = "/gpfs/radev/scratch/dijk/pl662/chainswe_sifs"
    _HF_DEF = "/gpfs/radev/scratch/dijk/pl662/hf_home"
    _RUNS_DEF = str(ROOT / "runs")
else:
    _P = "/nfs/roberts/project/pi_dv292/pl662"
    _SIF_DEF = f"{_P}/rsm_assets/chainswe_sifs"
    _HF_DEF = f"{_P}/hf_home"
    _RUNS_DEF = f"{_P}/rsm_runs/chainswe_v2"

os.environ.setdefault("HF_HOME", os.environ.get("RSM_HF_HOME", _HF_DEF))
os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")
os.environ.setdefault("MSWEA_SINGULARITY_EXECUTABLE", "apptainer")

import yaml  # noqa: E402  (副作用设置之后)

CHAIN_DATA = ROOT / "data/chainswe_data.jsonl"
CHAINS_DIR = ROOT / "data/chains"
LINK_FLAGS = ROOT / "data/task_link_flags_v2.json"
SIF_DIR = pathlib.Path(os.environ.get("RSM_SIF_DIR", _SIF_DEF))
RUNS = pathlib.Path(os.environ.get("RSM_RUNS_DIR", _RUNS_DEF))

# ---- agent 模板:钉版 yaml(不再依赖安装包内文件),取 ["agent"] 段 ----
_YAML = yaml.safe_load((HERE / "swebench_backticks.yaml").read_text())
CFG = _YAML["agent"]


def model_template_kwargs():
    """F18 修复(env 门控,默认关——v2main100 保持原行为):RSM_MODEL_TPL=1 时把钉版 yaml
    model: 段的观测截断模板与 finish_reason 感知纠错模板传给模型类(回归上游语义)。"""
    if os.environ.get("RSM_MODEL_TPL") != "1":
        return {}
    m = _YAML.get("model", {})
    return {k: m[k] for k in ("observation_template", "format_error_template") if k in m}

TERMINATION_ADDENDUM = (
    "\n\nIMPORTANT BUDGET RULE: you have a hard limit of {limit} commands. "
    "The moment your fix makes the relevant behavior correct (verified ONCE), "
    "IMMEDIATELY perform the submission steps. Never re-verify more than once; "
    "never keep exploring after the fix works.")

# F5:模板层禁令(执行层检测+回滚在 runner;双防)
COMMIT_BAN = (
    "\n\nGIT PROTOCOL RULE: NEVER run `git commit`, `git reset`, `git rebase`, "
    "`git stash` or `git checkout` that moves HEAD or a branch. Leave ALL your "
    "changes uncommitted in the working tree; the harness manages git history.")


def system_template(step_limit):
    # 官方对齐(2026-08-27):步数上限外部强制,不进 prompt(TERMINATION_ADDENDUM 为 v1 遗物,弃用);
    # 预算播报会与记忆注入交互(压力放大验证收益),去除后测的是自然行为
    return CFG["system_template"] + COMMIT_BAN


def instance_template(repo_path):
    return CFG["instance_template"].replace("/testbed", repo_path)


def model_config(model=None, api_key_config=None, served_name="coder-next", endpoint_file=None):
    """API actor(--model 设置时)或本地 vLLM 两套 kwargs,单点定义。
    返回 (model_name, model_kwargs, ep_file)。"""
    from . import constants as K
    if model:
        key = json.load(open(api_key_config))["agent"]["api_key"]
        mk = {"api_key": key, "max_tokens": K.API_MAX_TOKENS,
              "reasoning_effort": "low", "num_retries": 10}
        return model, mk, None
    mk = {"api_key": "EMPTY", "temperature": 0.0, "max_tokens": K.LOCAL_MAX_TOKENS}
    if os.environ.get("RSM_ACTOR_NOTHINK") == "1":
        # v3 修正包(env 门控,默认关):actor 关思考,对齐 Qwen 官方 SWE-bench 非思考口径
        # 与 MemOp/SWE-MeM 的非思考 Instruct actor;经 extra_body 直达 vLLM,litellm 不剥
        mk["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    return f"openai/{served_name}", mk, endpoint_file


def arm_name(arm_or_system, is_memory):
    """臂名单点计算(旧代码 __init__/main 双算分叉的修复)。RSM_ARM_SUFFIX 只在这里生效。"""
    base = f"mem_{arm_or_system}" if is_memory else arm_or_system
    return base + os.environ.get("RSM_ARM_SUFFIX", "")


def out_dir(run_id, chain_id, arm, seed_tag="s0"):
    return RUNS / run_id / f"{chain_id}__{seed_tag}__{arm}"


def load_chain_ids(chains_file, shard="0/1"):
    ids = [l.strip() for l in open(chains_file) if l.strip()]
    i, n = map(int, shard.split("/"))
    return [cid for j, cid in enumerate(ids) if j % n == i]


def load_chains():
    out = {}
    for l in open(CHAIN_DATA):
        r = json.loads(l)
        out[r["continuous_id"]] = r
    return out
