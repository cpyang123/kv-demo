# ChainSWE Memory Pipeline — Team Guide

这是我们在 **ChainSWE**(链式 SWE 任务,仓库状态跨 session 继承)上评测记忆机制的完整管线,
基于 **mini-swe-agent** harness,协议 **v3.0**。你可以在此基础上:跑新的 memory baseline、
换 actor 模型、或接入新的 harness(OpenHands / SWE-Edit)。

```
chainswe/
├── harness/          # 执行层:跑链、评分、协议
│   ├── mini_swe_agent.py    # 主入口(runner):跑一条链 = 逐任务 session + 记忆读写 + 评分
│   ├── env.py               # RepoEnv:Apptainer 容器内的 repo 工作区(带命令超时)
│   ├── grading.py           # 单一评分入口 grade():per-file reset + F2P/P2P 判定
│   ├── config.py            # ★ actor 模型、模板、协议开关全在这
│   ├── constants.py         # 步数帽/token 帽/超时等(大多可 env 覆盖)
│   └── swebench_backticks.yaml  # 钉版 agent 模板(勿改;改=协议变更)
├── memory/           # ★ 记忆层插件体系(加 baseline 改这里,runner 零改动)
│   ├── base.py              # 插件合同:wrap(question)→注入 / write(spec,material,delta)→写库
│   ├── ours/fusednote.py    # 我们的方法(绑定模板便签)
│   ├── baselines/           # Mem0 / MemOp-prompt / ReasoningBank / Cline Memory Bank
│   └── attribution/         # 归因探针臂(rawcat/factsall 等)
├── data/
│   ├── chainswe_data.jsonl  # 全部链与任务(含补丁与测试命令)
│   ├── chains/v2main100.txt # 100 链主评测清单
│   └── task_link_flags_v2.json  # linked/unlinked 分层标签(冻结,勿改)
├── analysis/main_format_table.py  # 统一口径出数器(九列主表式)
└── runs/             # 结果(不在共享分支;跑完自动生成)
```

---

## 1. 快速开始

**依赖**(python 3.10+ venv):`pip install mini-swe-agent==2.4.5 pyyaml`
(集群上直接用 `/gpfs/radev/pi/dijk/pl662/swe_explore/swebench-verified/venv`。)

**任务镜像**:每个 SWE 实例一个 Apptainer SIF,目录由 `RSM_SIF_DIR` 指定
(集群默认 `/gpfs/radev/scratch/dijk/pl662/chainswe_sifs`)。

**起本地 actor**(vLLM,单卡):
```bash
vllm serve Qwen/Qwen3.8-27B-FP8 --port 8850 --max-model-len 98304 \
     --gpu-memory-utilization 0.90 --served-model-name coder-next
export RSM_API_BASE="http://127.0.0.1:8850/v1"
```

**协议 v3.0 环境五件套(必须设,否则退回旧协议)**:
```bash
export RSM_ACTOR_NOTHINK=1        # actor 关思考(对齐 Qwen 官方 SWE-bench 口径)
export RSM_MODEL_TPL=1            # 恢复上游观测截断 + 截断感知纠错模板
export RSM_LOCAL_MAX_TOKENS=16384 # 输出帽
export RSM_PROTO_TAG=v3.0         # 协议戳(写进每行结果)
export RSM_GRADE_TIMEOUT=5400     # 评分超时(有一条链需要它)
# writer 全量输入口径:
export RSM_RAW_MSG_CAP=999999 RSM_RAW_TOTAL_CAP=999999 RSM_RAW_FINAL_CAP=220000 RSM_DISTILL_CAP=220000
```

**跑一个臂**:
```bash
# 无记忆基线
python harness/mini_swe_agent.py --arm seq \
    --chains-file data/chains/v2main100.txt --run-id v3main100 --step-limit 100
# 记忆臂(名字 = memory/ 里注册的插件名)
python harness/mini_swe_agent.py --memory-system fusednote \
    --chains-file data/chains/v2main100.txt --run-id v3main100 --step-limit 100
```
多 lane 并行安全:runner 自带链级认领锁(claim + 心跳 + 接管),同一臂开多进程会自动分链。

**出表**:
```bash
RSM_RUN_DIR=$PWD/runs/v3main100 RSM_ARMS="seq,mem_fusednote" python analysis/main_format_table.py
```

---

## 2. 加一个 memory baseline(最常见任务)

看 `memory/base.py` 的合同,50 行内可完成。骨架:

```python
from ..base import MemoryLayer, WrapResult, MEM_WRAP_HEAD, register

@register
class MyBaseline(MemoryLayer):
    name = "mybase"          # --memory-system mybase
    llm_writer = True        # 若写侧调 LLM,启用连续写错哨兵

    def wrap(self, question, env):       # 任务开始:决定注入什么
        if not self.store.items: return WrapResult(question)
        return self.store.wrap(question) # 或自定义拼装,统一用 MEM_WRAP_HEAD 壳

    def write(self, spec, material, delta, env):  # session 结束:写库
        # material=全量 session 轨迹(220K 帽);delta=本任务 git diff
        # spec["_rsm_resolved"]=本任务是否 resolve(grade 先于 write)
        ...
```
然后在 `memory/__init__.py` 加一行 import。**纪律**:
- 写侧 LLM 一律走本地端点(`RSM_API_BASE`/`RSM_WRITER_API`),零外部 API;
- 官方 prompt 逐字 vendor 进 `baselines/prompts/`,出处/license 写进 docstring,偏离要列全(见 memop_prompt.py 的范例);
- **先离线单测**(monkeypatch urllib,见 scratchpad 测试范式)**再 3 链 smoke,过了才排全量**——我们被"静默零写入"坑过三次;
- OSError 必须上抛(落 mem_write_error),其他失败返回空/0,禁止静默吞。

## 3. 换 actor 模型

单点:`harness/config.py::model_config()`。
- **本地模型**:vLLM serve 换模型 + `--served-model-name coder-next` 不变即可(或改 served_name 参数);上下文窗口小于 98304 时注意长链溢出;
- **API 模型**:`--model <litellm名> --api-key-config <json路径>`(json 格式 `{"agent": {"api_key": "..."}}`),走 `API_MAX_TOKENS`/reasoning_effort 配置;
- 关思考语义只对本地 vLLM 生效(`extra_body.chat_template_kwargs`);API 推理模型的思考是内部的,不冲突。

## 4. 接一个新 harness(OpenHands / SWE-Edit)

原则:**grading、memory、data、协议规则全部复用,只换"跑 session"那一段**。
`harness/mini_swe_agent.py` 里的接缝(搜这几处):
1. **环境**:`env.py::RepoEnv`(Apptainer + 每任务 commit 协议 `_commit_task`/`_guard_commits`)——新 harness 若自带容器,需保证同样的"每任务一 commit、agent 禁 commit、task_base 锚点"语义,否则评分口径破;
2. **session 执行**:`DefaultAgent(...).run(...)` 那一小段 —— 换成 OpenHands/SWE-Edit 的调用,输入 = `w.text`(已含记忆注入的题面),输出需要:轨迹(存 traj_*.json)、步数、exit status;
3. **材料构建**:`_material(agent, delta)` —— 新 harness 的轨迹转成 writer 输入文本(格式随意,长度守 220K 帽);
4. **评分**:直接调 `grading.grade()`,不要自己写判定;
5. 结果行 schema 保持一致(results.jsonl:order/instance/resolved/n_steps/injected_chars/proto 等),`analysis/` 就能直接出表。
建议做法:复制 mini_swe_agent.py 为 `openhands_runner.py`,只改 2/3 两处,其余原样。

---

## 5. 协议红线(动之前先问)

- `swebench_backticks.yaml`、`task_link_flags_v2.json`、grading 逻辑 = 协议本体,改动即协议版本变更(PROTO_TAG 必须递增,全臂重跑);
- 训练永不碰 ChainSWE(它只做评测);
- 结果表一律用 `analysis/main_format_table.py` 出数(固定分母、缺行按未解决计);
- **已知坑**:results.jsonl 无尾换行(计数用 `grep -c '"order"'` 别用 wc -l);拼 lane 的 env 前缀务必留空格(我们吃过 `v3.0RSM_GRADE_TIMEOUT` 的亏);评测期 GPU 0%+无输出最长 90 分钟属正常,别当卡死杀 pytest。

有问题找 Peiwen。
