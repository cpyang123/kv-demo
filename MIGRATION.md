# chainswe/ — ICLR 线与 KV 线代码剥离方案(2026-08-26 起草,待审查结论+批准后执行)

## 目标布局与文件映射

```
chainswe/
├── data/                       # 1. 数据集与划分
│   ├── chainswe_data.jsonl         ← w1/data/chainswe_data.jsonl(复制)
│   ├── task_link_flags_v2.json     ← w1/(复制;v1 冻结档留 w1 原地为预注册工件)
│   ├── make_link_flags.py          ← w1/(移入,路径参数化)
│   └── chains/q38_chains*.txt      ← w1/orchestration/chains/(复制)
│   (SIF 镜像不迁:引用 /gpfs/radev/scratch/dijk/pl662/chainswe_sifs)
├── harness/                    # 2. mini-swe-agent × ChainSWE pipeline
│   ├── runner.py                   ← run_kv_chain.py 的 main 循环+claim+KVChainJob(论文臂部分)
│   ├── env.py                      ← demo_arms.DemoEnv
│   ├── grading.py                  ← train_rollout.grade + demo_arms.parse_pytest(单一来源,修 review-C)
│   ├── config.py                   ← demo_arms.CFG + chain_batch.TERMINATION_ADDENDUM
│   └── constants.py                ← 全部截断魔数集中定义(修 review-D)
├── memory/                     # 3. memory layer 管理(统一插件接口)
│   ├── base.py                     ← 接口: write(material, store)->store / read(store, task, env)->text
│   ├── attribution/                # (a) 预实验归因臂(冻结,只读)
│   │   └── rawcat/rawprose/rawcmd/rawdiff/repomap/mapdiff/statenote/factsall
│   ├── baselines/                  # (b) 开源 baseline 适配器(同学接入点,每家一文件)
│   │   └── mem0.py / reasoningbank.py / memos.py / evermemos.py (待接)
│   └── ours/                       # (c) 我们的方法
│       ├── fusednote.py / trainednote.py / structmem.py / gate.py(待训)
│       └── prompts/                ← train-corpus/{fused,lean,xl,free}_writer_prompt.txt(复制)
├── analysis/
│   └── component_attribution.py / stratified_ledger.py ← w1/orchestration/launchers/(移入)
└── README.md
```

## 设计决定
1. **memory 插件接口统一签名**(base.py)——这是给同学的接入合同:接一个开源 baseline =
   在 baselines/ 写一个实现两方法的文件,runner 零改动;
2. **迁移=先复制不删旧**:旧路径(w1/kv-chainswe)继续服务在跑实验;冻结点(方法定案+在跑清空)
   后旧目录归档,git 历史两侧都保留;
3. **KV 遗留不迁**:run_kv_chain 里的 KVClient/mem0-union/factnode-r2 等 KV 线内容留在
   kv-chainswe/,剥离即天然完成;
4. **审查驱动**:五维审查(wf_7a5433ba)的确认缺陷,修复直接写进新结构,不给旧代码打补丁。

## 执行顺序(待批)
① 审查结论出 → ② data/+prompts/ 复制(零风险,先行) → ③ harness/ 重构(含缺陷修复) →
④ memory/ 拆臂(attribution 冻结原样,ours 重构) → ⑤ 等价性验证:新 runner 重跑 3 条链
× {seq, fusednote},逐字段比对旧产物 → ⑥ 切换 lane 模板指向新入口 → ⑦ 旧路径归档。

## 执行进度(2026-08-26)
- ② data/+prompts/ ✅(make_link_flags 新址复跑对账一致;flags v2=204 槽)
- ③ harness/ ✅ 初版(constants/config/env/grading/runner,F1-F13 全落;yaml 模板钉版)
- ④ memory/ ✅ 初版(base 合同 v2 + 9 探针冻结移植 + fusednote/trainednote;split/route/pack 对旧实现 29 份真实原料逐字节等价 ✅;mem0_official 骨架留同学)
- 对抗验证工作流已发(F1-F13 覆盖核对 + 双找茬视角),结论待回
- ⑤ 等价性验证 lane(3 链 × seq/fusednote 新旧对比)待排;⑥⑦ 等在跑臂清空
