# claw-eval · 对话模型指令遵循能力自动评测系统

![tests](https://github.com/CHANGJianshuo/agent_eval/actions/workflows/test.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

针对「履约数字人外呼」场景的自动评测系统。给定一份**任务 Prompt** + 一个**被测对话模型(SUT)**,自动产出一份**可解释、可量化**的评测报告。

> **当前状态**:MVP 完成 · 9 个 P0 + 3 个 P1 全部交付 · 192 单元测试全绿 · 真实跑过 34+ case
> [📊 在线 dashboard 截图](reports/index.html) · [详细设计](方案设计.md) · [给 AI 看的工作流](SKILL.md) · [GitHub](https://github.com/CHANGJianshuo/agent_eval)

---

## 它解决什么问题

`外呼对话` 这种业务,**指令复杂、流程多、约束多**,而模型容易:
- 漏说某一步(完成度问题)
- 字数超限 / 重复 / 不像人话(鲁棒性问题)
- 被诱导承诺、编造数字、暴露内部信息(安全问题)

人工评估贵 + 不一致 + 没法批量回归。这个系统提供:

1. **可控的用户模拟器** —— 有限状态机 + LLM 生成话术 + 定向探针,强制覆盖关键场景
2. **三维度自动评分** —— Completion(0.80)/ Robustness(0.20)+ Safety(乘子,违规归零)
3. **可解释的报告** —— 每条违规带 evidence_turn_id 溯源到具体对话轮,LLM Judge 给打分理由
4. **可执行的改进建议** —— 不止给分,还告诉你「改 Prompt 哪几句、预期提升多少」
5. **回归对比** —— 改完 Prompt 跑一次 `regression`,看预期对不对

---

## 快速开始

### 安装

```bash
git clone https://github.com/CHANGJianshuo/agent_eval claw_eval
cd claw_eval

# 推荐:Web UI(React + FastAPI,现代界面)
pip install -e '.[dev,web]'
cd web && npm install && npm run build && cd ..
PYTHONPATH=src python3 -m claw_eval.cli web    # http://localhost:8000

# 或:Streamlit UI(legacy,不需要 Node.js)
pip install -e '.[dev,ui]'
PYTHONPATH=src python3 -m claw_eval.cli editor    # http://localhost:8501

# API key 在 UI 里配,或:
export XIAOMI_MIMO_API_KEY=...
```

> **两套 UI 怎么选**:
> - **React UI**(`claw-eval web`)是**主推 / 持续开发**的版本,Linear/Vercel 风格,需要 Node.js 18+
> - **Streamlit UI**(`claw-eval editor`)是 **legacy**,已冻结开发,只保留给不想装 Node 的用户作 fallback。

### 跑一个最小 demo

```bash
# 全流程 6 步(任务 Prompt → 评测方案 → 评测 → 报告)
claw-eval pipeline --task meituan_rider --total 30

# 或单步走
claw-eval batch --task meituan_rider --total 30 --label v1     # 跑批
claw-eval dashboard                                              # 出报告
```

打开 `reports/index.html`(WSL2 里 `explorer.exe reports/index.html`)。

### 改完 Prompt 看效果

```bash
# 1) 找最该改的 rubric(LLM 给修改建议 + 预期提升)
claw-eval recommend --task meituan_rider

# 2) 按建议改 tasks/meituan_rider/task.yaml,再跑一遍
claw-eval batch --task meituan_rider --total 30 --label after-fix

# 3) 对比看实际提升
claw-eval regression --task meituan_rider --old v1 --new after-fix
```

---

## 全部 13 个 CLI 命令

| 命令 | 用途 |
|---|---|
| **跑评测** | |
| `run` | 单 persona × N trials,调试用 |
| `batch` | 多 persona 跑批,支持 `--total N` 按 sampling.yaml 比例分配;`--label` 给 run_id |
| `safety-test` | 对抗 persona × safety 红队专项,出破防率报告 |
| **抽取 + 人审** | |
| `extract-rubric` | LLM 抽 rubric 草稿(7 类 + 置信度) |
| `extract-personas` | LLM 推荐 persona 剧本(引用现有性格库) |
| `review` | 终端逐条人审 rubric,safety 强制审 |
| `validate` | 一致性机械检查(命名 / safety / 触发可达性 / 状态机终止) |
| `pipeline` | 6 步全流程编排,`--from N` 续跑 |
| **分析** | |
| `recommend` | 找弱 rubric + LLM 给改 Prompt 建议 + 预期提升 |
| `regression` | 对比两次 run 的差异(rubric / persona / 维度三层 diff) |
| **报告** | |
| `dashboard` | 多页 HTML 报告 |
| `report` | 单 case HTML |
| `grade` | 对已有 trace 重评(改 rubric 后不用重跑批) |
| **UI** | |
| `editor` | Streamlit persona 编辑器(选性格 + 画状态机 + 配探针 + 保存 YAML) |

---

## 报告页里有什么

每个任务一张详情页(`reports/task_<task>.html`),从上到下:

1. **📋 优化建议** —— LLM 推荐的 Top-5 修改方向,带预期提升 + 违规样本溯源
2. **任务流程图** —— ECharts 节点按 rubric 跨 case 平均得分着色(绿 ≥0.8 / 黄 ≥0.5 / 红 <0.5 / 灰 未触发)
3. **按 Persona 表** —— 每个 persona 通过率 / 三维度分,带中文备注
4. **Persona × Rubric 热力图** —— 看是哪个 persona 哪条 rubric 弱
5. **按 Rubric 表** —— 全 persona 合并,带 check 描述备注
6. **全部 case 列表** —— 点进去看单 case 对话回放(违规高亮 + 雷达图 + 单 case 流程图)

---

## 架构概览

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│  用户模拟器  │ ──────> │  对话 Runner │ ──────> │   评分器     │
│  状态机+LLM  │ <────── │  trace JSONL │         │ 规则+LLM     │
│  +定向探针   │         │  可复现可审  │         │ Judge        │
└──────────────┘         └──────────────┘         └──────────────┘
       ▲                                                   │
       │                                                   ▼
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│ persona 三层 │         │   被测 SUT   │         │ 多页 HTML 报告 │
│ 性格+剧本    │         │ (薄 adapter) │         │ + 优化建议    │
│ +噪音 rate   │         │ 无辅助逻辑   │         │ + 回归对比    │
└──────────────┘         └──────────────┘         └──────────────┘
```

**三件套 + trace-first**:用户模拟器 ↔ 被测模型对话,所有事件落 JSONL,评分器只看 trace,可复现可审计。

---

## 核心设计原则

1. **能用规则就别用 LLM** —— 8 类代码 matcher(长度 / 占位符 / 关键词 / 数字白名单 / 顺序关键词 / 节奏 / 黑名单)优先;LLM Judge 只判语义类
2. **LLM Judge 必须给 evidence_turn_id** —— 评分指向具体对话轮,是可解释的命门
3. **Safety 类做成乘子** —— 违反则 task_score × 0,不能用「分高就过」掩盖安全问题
4. **触发型 rubric 不计分母** —— 该条 trigger 没满足就不参与计分,避免拖分
5. **Pass^k 多轮采样** —— 同 case 跑 k 次,全过才算稳定通过
6. **Persona 三层拆分** —— 性格(任务无关,跨任务复用)+ 剧本(任务专属)+ 噪音(per-turn rate,seed 可复现)
7. **三模型分离** —— SUT / 模拟器 / Judge 用不同模型,避免「自己评自己」
8. **关键节点人审 gate** —— safety 类 rubric 强制审核才能转正
9. **trace + run_id 分目录** —— 评测产物按 run_id 子目录归档,配合 git commit 双重版本管理

---

## 仓库结构

```
claw_eval/
├── README.md                       # 你正在看
├── CLAUDE.md                       # 给 Claude Code 的项目上下文
├── SKILL.md                        # 给 AI agent 的 6 步工作流
├── 方案设计.md                      # 通用方案(美团飞毛腿外呼为例)
├── 方案设计_直播升级外呼.md          # 直播任务特化方案
├── pyproject.toml
│
├── src/claw_eval/                  # 共享引擎
│   ├── cli.py                      # 13 个 CLI 命令
│   ├── models/                     # Pydantic 数据模型(task / rubric / persona / trace / flow)
│   ├── runner/                     # 对话主循环 + LLM 客户端 + SUT adapter + trace 读写
│   ├── user_simulator/             # 状态机引擎 + 模拟器 + 探针 + persona 抽取器
│   ├── graders/                    # 8 类 matcher + LLM Judge + scoring + base + registry
│   ├── rubric/                     # rubric 抽取器 + 人审 gate
│   ├── report/                     # aggregate + builder + flow_viz + recommend + regression
│   ├── editor/                     # Streamlit persona 编辑器
│   ├── adversarial.py              # 安全红队报告
│   ├── pipeline.py / validator.py / sampling.py
│   └── ...
│
├── tasks/
│   ├── meituan_rider/              # 美团飞毛腿外呼
│   │   ├── task.yaml               # 任务 Prompt + 变量
│   │   ├── rubrics.yaml            # 评分项(33 条)
│   │   ├── sampling.yaml           # persona 比例
│   │   ├── flow.yaml               # 任务流程图节点
│   │   ├── grader.py               # 任务专属评分编排
│   │   └── personas/               # 含 adv_*.yaml 对抗剧本
│   └── live_upgrade/               # 课程平台直播升级
│
├── personalities/                  # 10 个共享性格(7 常规 + 3 对抗)
│   ├── cooperative.yaml / refuse.yaml / hesitant.yaml / ...
│   └── adv_prompt_injector.yaml / adv_social_engineer.yaml / adv_coercive.yaml
│
├── configs/
│   ├── models.yaml                 # SUT / 模拟器 / Judge 三方配置
│   ├── noise_profiles.yaml         # 噪音种类库(filler / asr_error / broken / interrupt)
│   └── adversarial_probes.yaml     # 对抗话术库(注入 / 社工 / 施压)
│
├── tests/                          # 17 个测试文件,192 个测试
└── reports/                        # HTML 报告 + recommendations / regression / safety_test JSON
```

---

## 与同类工作的关系

主要参考 [**claw-eval**](https://github.com/claw-eval/claw-eval)(LLM-as-agent 评测框架)的核心机制:
- 每任务独立 `task.yaml` + `grader.py`,registry 动态加载
- `multi_turn` split 含 `user_agent`,LLM 模拟用户
- 评分公式 `base = 0.80*completion + 0.20*robustness;task_score = safety * base`,Pass^3
- 全程 JSONL trace

**本项目的差异**:
- 用户模拟器加了**有限状态机** + **定向探针**(claw-eval 是裸 LLM,我们追求可复现 + 强制覆盖)
- Persona **三层结构**(性格库跨任务复用)
- Rubric **LLM 抽取 + 人审 gate**(7 类语义分类,safety 强制审)
- 安全红队**对抗 persona 专项**(测 prompt 注入 / 社工 / 施压)
- 报告产「**可执行修改建议 + 预期提升**」,不止打分
- 回归对比 + run_id **轻量版本管理**

其他相关:[τ-bench / τ²-bench](https://github.com/sierra-research/tau2-bench),[AgentProcessBench](https://arxiv.org/abs/2603.14465),[DeepEval](https://github.com/confident-ai/deepeval)。

---

## 状态

```
192 单元测试全绿     · 0.4 秒跑完     · 无 API 依赖
12 git commit       · 已推 GitHub    · MIT 风格(待补 LICENSE)
2 任务  / 16 persona / 33 rubric  / 10 性格 / 8+1 matcher
13 CLI 命令        · 17 测试文件
```

剩余工作见 [CLAUDE.md 待完成清单](CLAUDE.md#待完成任务清单);其中 T6(meta-eval)和 T12(线上 KPI)等业务方数据。

---

## License

MIT(待补 LICENSE 文件)。
