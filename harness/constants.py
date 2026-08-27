"""全部截断魔数与阈值,集中定义(审计 review-D)。env 可覆盖的走 _env_int。
改任一 raw 轨迹相关截断需同步 memory/attribution/probes.py 的 split_components 解析锚。"""
import os


def _env_int(name, default):
    return int(os.environ.get(name, str(default)))


# ---- agent 环境(env.py) ----
EXEC_TIMEOUT = 300            # agent 单命令超时(秒)
RAW_TIMEOUT = 600             # 判分/setup 通道默认超时
EXTRACT_TIMEOUT = 600         # 镜像内 repo 提取超时
OBS_CAP = 4000                # agent 观测截断阈值
OBS_HEAD = 2000               # 截断保留头
OBS_TAIL = 2000               # 截断保留尾

# ---- 判分(grading.py) ----
GRADE_TIMEOUT = _env_int("RSM_GRADE_TIMEOUT", 1200)   # 判分 pytest 超时(F13:必须被捕获;大套件仓库经 env 放宽)
APPLY_TAIL = 300              # patch apply 失败时保留的输出尾
SIF_MIN_BYTES = 100_000_000   # SIF 完整性下限("下载了一半"经验检测)

# ---- 主循环(runner.py) ----
STEP_LIMIT_DEFAULT = 100   # 官方 ChainSWE 对齐(100 turns;外部强制,不播报)
LOCAL_MAX_TOKENS = 3500       # 本地 vLLM actor
API_MAX_TOKENS = 6000         # API actor
CLAIM_STALE_SECS = 4 * 3600   # 认领锁陈旧阈值;心跳线程每 CLAIM_HEARTBEAT 秒刷新
CLAIM_HEARTBEAT = 300
COST_LIMIT = 10 ** 9          # 刻意禁用 mini-swe-agent 成本刹车

# ---- writer 原料(raw trajectory;全量输入臂经 env 拉满,如 RSM_RAW_MSG_CAP=999999) ----
def raw_msg_cap():
    return _env_int("RSM_RAW_MSG_CAP", 1200)


def raw_total_cap():
    return _env_int("RSM_RAW_TOTAL_CAP", 19000)


def raw_final_cap():
    return _env_int("RSM_RAW_FINAL_CAP", 23000)


DIFF_CAP = 4000               # FINAL DIFF 段截断

# memory 插件的截断/预算常数以各插件内 env 默认值为准(RSM_FACTS_INPUT_CAP 等),
# 不在此重复定义,避免"改这里不生效"的陷阱(rev2-P4)。
