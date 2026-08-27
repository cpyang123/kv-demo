# chainswe — ChainSWE 评测管线(ICLR 线独立版,审计修复后)

从 w1/kv-chainswe 剥离的最终版:数据集+依赖划分、mini-SWE-agent pipeline、memory layer 插件体系。
14 项审计缺陷(见 AUDIT-2026-08-26.md)的根修复全部落在本目录;旧代码不打补丁。

## 布局

```
data/       chainswe_data.jsonl(100 链)· task_link_flags_v2.json(204 槽)· chains/ 分片 · make_link_flags.py
harness/    constants(全部魔数)· config(路径/模板/臂名单点)· env(RepoEnv)· grading(判分单一来源)· runner(统一入口)
memory/     base.py(插件合同)· attribution/(9 个归因探针,冻结)· ours/(fusednote/trainednote+prompts)· baselines/(同学接入点)
analysis/   main_format_table.py(统一口径出数器,自检复现 tab:main 后出表)
```

## 跑法

```bash
# 基线臂
python -m harness.runner --arm seq --chains-file data/chains/q38_chains_s0.txt --run-id v2pilot
# memory 臂(本地 vLLM 经 RSM_API_BASE 或 endpoint 文件)
RSM_API_BASE=http://127.0.0.1:8470/v1 \
python -m harness.runner --memory-system fusednote --chains-file data/chains/q38_chains_s0.txt --run-id v2pilot
```

结果落 `runs/<run-id>/<chain>__s0__<arm>/results.jsonl`(原子写,行含统一 schema)。

## 关键语义(与旧管线的差异)

- **官方对齐 commit 协议(2026-08-27)**:与 ChainSWE 论文 Seq 模式一致,每题结束由 harness
  提交本题改动(`rsm: task k`);agent 仍禁止 commit(模板禁令+守卫回滚)。判分测试重置/
  oracle-indep 重置/守卫全部锚定链首 base oid;本题增量 diff = 相邻 commit 之差(F1 天然精确);

- **steps = api_calls**(`n_steps` 键,旧 assistant 计数保留在 `n_assistant_msgs`);
- **agent commit 守卫**:模板禁令 + 每任务后 HEAD 漂移检测回滚,`agent_committed` 落行(修 F5);
- **判分**:一次性拷贝、逐文件测试重置、P2P 蒸发检测(`p2p_missing` 判失败,`p2p_unscoped` 不计)、
  no-op 任务 `gradable=false` 不硬跑、超时收敛为 error 行(修 F2/F3/F4/F13);
- **锁**:`.claim` 心跳 + `os.rename` 原子接管,全臂共用;完成删 claim(修 F11/F12);
- **注入度量分账**:`injected_chars`(跨任务记忆载荷)与 `map_chars`(现算结构图)分列(修 F10);
- **基建异常中止链不固化**(ChainAbort,修 F9);results 原子写+JSON 校验完成判定(修 F8)。

## 接一个新 memory baseline

在 `memory/baselines/` 写一个文件:`MemoryLayer` 子类 + `@register`,实现
`write(spec, material, delta, env)` 与(可选)`wrap(question, env)`;runner 零改动,
`--memory-system <name>` 即可跑。合同细节见 `memory/base.py` docstring。
