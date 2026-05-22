# DialAgentEval · 对话模型指令遵循能力自动评测系统

![tests](https://github.com/CHANGJianshuo/agent_eval/actions/workflows/test.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![React](https://img.shields.io/badge/react-18-61dafb)
![License](https://img.shields.io/badge/license-MIT-green)

针对「**履约数字人外呼**」场景的对话模型指令遵循能力自动评测系统。给定一份**任务 Prompt** + 一个**被测对话模型(SUT)**,自动产出**可解释、可量化**的评测报告 + **可执行的改进建议**。

> ### 🌐 在线 Demo
> **[https://changjianshuo.github.io/agent_eval/](https://changjianshuo.github.io/agent_eval/)**
>
> 完整 React UI 部署在 GitHub Pages,带 Demo 模式 banner;脱敏样本数据展示「任务列表 / 测试历史 / 5 维度 persona 工厂 / 优化建议」全流程。

---

## 项目背景

### 问题:外呼对话评测,人工不可持续

履约数字人(美团骑手通知、课程平台升级通知、保险续保提醒…)是一个**多轮、复杂指令、强约束**的对话场景。SUT 容易出错的地方:

- **完成度问题**:任务有 4-7 步固定流程,模型容易**漏说某一步**
- **鲁棒性问题**:每轮字数限制、口语化、不重复 —— 模型容易**啰嗦、重复、太书面**
- **安全问题**:用户会**诱导承诺**(优惠/特殊照顾)、**编造数字**、**社工诱导**暴露内部信息
- **覆盖问题**:用户类型多样(合作型/抵触型/犹豫型/抬杠型/对抗型),需要全部测到

传统做法:
- **人工标注**:贵、不一致、不能批量回归
- **离线指标**(BLEU/ROUGE):跟「对话指令是否遵循」无关
- **直接 LLM Judge**:语义判断主观、不可解释、不可复现

### 解决方案:三件套 + trace-first

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│ 用户模拟器   │ ──────> │  对话 Runner │ ──────> │   评分器     │
│ 状态机+LLM   │ <────── │  trace JSONL │         │ 规则 + LLM   │
│ +定向探针    │         │  可复现可审  │         │ Judge        │
└──────────────┘         └──────────────┘         └──────────────┘
```

- **用户模拟器** = 有限状态机控走向 + LLM 生成话术 + 定向探针强制覆盖关键场景
- **trace-first**:所有事件落 JSONL,评分基于 trace 可复现可审计
- **评分** = 8 类代码 matcher + LLM Judge(必须返回 `evidence_turn_id`);三维度(完成度/鲁棒性/安全),safety 做乘子违规即归零

### 跟同类工作的关系

| 项目 | 类型 | 跟本项目的关系 |
|---|---|---|
| **claw-eval** | LLM-as-agent 评测框架(300 任务) | 借鉴 trace-first / scoring 公式 / Pass^k |
| **τ-bench / τ²-bench** | 客服 agent + 用户模拟器 | 架构最接近;τ² 双控环境 |
| **AgentProcessBench** | 步骤级过程评分 | 可解释步骤评分 |
| **AgentRewardBench** | 评 LLM judge 本身 | meta-eval 思路 |

**本项目的差异化**:
- 用户模拟器加**状态机 + 定向探针**(claw-eval 是裸 LLM,我们求可复现 + 强制覆盖)
- Persona **5 维度独立采样工厂**(性格 × MBTI × 性别 × 年龄 × 教育)
- Rubric **LLM 抽取 + 7 类分类 + safety 强制人审 gate**
- 安全红队**对抗 persona 专项**(prompt 注入 / 社工 / 施压)
- 报告产「**可执行修改建议 + 预期提升**」,不止打分
- 回归对比 + **task.yaml 版本管理**

---

## 在线 Demo

🌐 **[https://changjianshuo.github.io/agent_eval/](https://changjianshuo.github.io/agent_eval/)**

Demo 是用 mock 数据驱动的完整 React UI,**不需要装任何东西就能体验交互**。能看到:

- 📋 **任务列表**:3 个任务卡片 + 4 步进度灯 + 通过率配色
- 🧪 **任务概览**:测试历史卡片 + 「➕ 新建测试」入口
- 🎯 **5 维度 persona 工厂**:每个维度勾选属性 + 配比例 + 实时迷你饼图 + 预览采样分布
- 🧪 **测试详情**:6 列元信息 + 4 步进度 + 报告嵌入 + 改进建议
- ⚙️ **全局配置**:API key / 模型 / Persona 库 5 维度字典
- 📖 **使用文档**:完整 8 步工作流

> Demo 模式下「新建任务 / 启动测试」等操作会显示「demo 模式不可执行」 —— 跑真评测需要本地后端。

---

## 完整能力 · 一句话总结

| | 能力 | 实现 |
|---|---|---|
| 1 | 贴一段 prompt,**一键生成评测方案** | LLM 抽 task.yaml / flow / rubrics / personas / grader.py(5 步 wizard) |
| 2 | 5 维度**独立采样**生成模拟用户 | 性格 × MBTI × 性别 × 年龄 × 教育 各自比例 → 独立采样组合 |
| 3 | 一次任务**多次测试**版本管理 | 每次测试一个 test_id + agent_version + 跑批参数 + 结果指标(SQLite) |
| 4 | 跑批后**自动出报告 + 改进建议** | dashboard HTML + LLM 找弱 rubric + 给「改 prompt 哪几句」 |
| 5 | **自动应用建议** → 备份新版本 | LLM 改写 prompt + unified diff + 接受/拒绝 + 自动版本管理 |
| 6 | 改完后**回归对比** | regression 三层 diff(总览 / rubric / persona)+ 显著性阈值 |
| 7 | **安全红队**专项 | 3 类对抗 persona(注入 / 社工 / 施压)+ 破防率矩阵 |
| 8 | 报告含**模拟用户维度分析** | 每个属性值的通过率 → 找出「最难搞定的用户特征」 |

---

## 本地安装与启动

### 推荐:Web UI(React + FastAPI,Linear/Vercel 风格)

```bash
git clone https://github.com/CHANGJianshuo/agent_eval claw_eval
cd claw_eval

# 安装 Python 依赖
pip install -e '.[dev,web]'

# 构建前端(需要 Node.js ≥ 18,WSL 可用 `nvm install 20`)
cd web && npm install && npm run build && cd ..

# 启动单端口(API + UI + 报告托管全在 :8000)
PYTHONPATH=src python3 -m claw_eval.cli web --port 8000
```

打开 **http://localhost:8000/**

### Legacy:Streamlit UI(不要 Node.js)

```bash
pip install -e '.[dev,ui]'
PYTHONPATH=src python3 -m claw_eval.cli editor   # http://localhost:8501
```

⚠ Streamlit UI 已**冻结开发**,新功能只在 React 加;保留作 fallback。

### 配 API key

UI「全局配置」里输入 → 保存到 `~/.claw_eval/api_keys.yaml`(不进 git)。或环境变量:

```bash
export XIAOMI_MIMO_API_KEY=...
# 或 OPENAI_API_KEY / ANTHROPIC_API_KEY
```

---

## 端到端最小 demo(命令行)

```bash
# 1. 一键生成任务(LLM 4-6 次调用,~3-5 分钟)
echo "贴你的完整 SUT prompt..." > /tmp/prompt.md
claw-eval generate-task --prompt /tmp/prompt.md --id my_task

# 2. 审核 rubric 草稿(safety 必审)
claw-eval review --task my_task

# 3. 跑基线测试
claw-eval batch --task my_task --total 30 --label v1

# 4. 出报告 + 改进建议
claw-eval dashboard
claw-eval recommend --task my_task

# 5. 按建议改 task.yaml,跑 v2
claw-eval batch --task my_task --total 30 --label v2

# 6. 回归对比看实际提升
claw-eval regression --task my_task --old v1 --new v2
```

> CLI 命令仍叫 `claw-eval`(entry_point 不变,避免破坏现有用法);产品名是 **DialAgentEval**(README / UI 显示)。

或者**全 UI 里点**,体验跟命令行等价。

---

## 全部 CLI 命令

| 命令 | 用途 |
|---|---|
| **生成 + 审核** | |
| `generate-task` | 贴 prompt → 一键产 task.yaml + flow + rubrics + personas + grader |
| `extract-rubric` | 单独跑 rubric 抽取(7 类 + 置信度) |
| `extract-personas` | 单独跑 persona 推荐 |
| `review` | 终端逐条人审 rubric,safety 强制审 |
| `validate` | 一致性机械检查 |
| **跑评测** | |
| `run` | 单 persona × N trials,调试用 |
| `batch` | 多 persona 跑批;`--total` 比例分配;`--weights` JSON 覆盖;`--dimensions` 5 维度采样 |
| `safety-test` | 对抗 persona × safety 红队专项 |
| `pipeline` | 6 步全流程编排,`--from N` 续跑 |
| **分析 + 改进** | |
| `recommend` | 找弱 rubric + LLM 给改 Prompt 建议 |
| `regression` | 两次 run 三层 diff |
| **报告** | |
| `dashboard` | 多页 HTML 报告 |
| `report` | 单 case HTML |
| `grade` | 对已有 trace 重评 |
| **UI** | |
| `web` | 启 FastAPI + React UI(推荐) |
| `editor` | 启 Streamlit UI(legacy) |

---

## 报告页结构

每个任务一张详情页(`reports/task_<task>.html`):

1. **📋 优化建议** —— LLM 推荐的 Top-5 修改方向 + 预期提升 + 违规样本溯源
2. **🔴 安全红队报告** —— 破防率矩阵(若跑过)
3. **📊 回归对比** —— v1 → v2 三层 diff
4. **🌲 任务流程图** —— 节点按 rubric 跨 case 平均得分着色
5. **👥 模拟用户维度分析** —— 5 维度各属性值的通过率
6. **按 Persona 表** —— 含 MBTI/年龄/性别/教育 demographics
7. **按 Rubric 表** + **Persona × Rubric 热力图**
8. **全部 case 列表** —— 点进去看单 case 对话回放(违规高亮)

---

## 核心设计原则

1. **能用规则就别用 LLM** —— 8 类代码 matcher 优先(长度 / 占位符 / 关键词 / 数字白名单 / 顺序关键词 / 节奏 / 黑名单);LLM Judge 只判语义类
2. **LLM Judge 必须给 evidence_turn_id** —— 评分指向具体对话轮,这是「可解释」的命门
3. **Safety 做乘子** —— 违反则 task_score × 0,「分高就过」不能掩盖安全问题
4. **触发型 rubric 不计分母** —— 该条 trigger 没满足就不参与计分,避免拖分
5. **Pass^k 多轮采样** —— 同 case 跑 k 次,全过才算稳定通过
6. **Persona 多维独立采样** —— 5 个维度(性格/MBTI/性别/年龄/教育)各自配比例,系统按比例独立采样组合;模拟真实用户的多样性
7. **三模型分离** —— SUT / 模拟器 / Judge 用不同模型,避免「自己评自己」
8. **关键节点人审 gate** —— safety 类 rubric 强制审核才能转正
9. **trace + run_id 分目录** —— 评测产物按 run_id 子目录归档,SQLite 索引便查询
10. **task.yaml 版本管理** —— 每次「自动应用建议」前后自动备份,可一键回滚

---

## 架构 · 代码组织

```
claw_eval/
├── README.md                    # 本文档
├── CLAUDE.md                    # 给 Claude Code 的项目上下文
├── SKILL.md                     # 给 AI agent 的工作流文档
├── 方案设计.md / 方案设计_直播升级外呼.md
├── pyproject.toml
├── .github/workflows/test.yml   # CI(pytest matrix)
│
├── src/claw_eval/               # Python 业务逻辑(UI 无关,可复用)
│   ├── cli.py                   # 16 个 CLI 命令
│   ├── api/                     # FastAPI 后端(27 个 endpoints + Swagger)
│   ├── models/                  # Pydantic schema(task/rubric/persona/trace/flow)
│   ├── runner/                  # 对话主循环 + LLM 客户端 + trace 读写
│   ├── user_simulator/          # 状态机 + 模拟器 + 探针 + persona 抽取器
│   ├── graders/                 # 8 matcher + LLM Judge + scoring
│   ├── rubric/                  # rubric 抽取 + 人审 gate
│   ├── report/                  # aggregate + builder + flow_viz + recommend + regression
│   ├── task_gen/                # flow/variables/grader 生成器 + 自动应用 + 版本管理
│   ├── editor/                  # Streamlit UI(legacy)
│   ├── persona_factory.py       # 5 维度独立采样生成 Persona 实例
│   ├── db/                      # SQLite 索引(runs 表 / 跑批参数 / 复用)
│   ├── adversarial.py           # 安全红队报告
│   └── pipeline.py / validator.py / sampling.py
│
├── web/                         # React 前端
│   ├── src/
│   │   ├── App.tsx              # 路由 + Demo banner
│   │   ├── pages/               # TaskList / TaskOverview / TestDetail / Settings / Docs
│   │   ├── components/          # NewTestForm / TaskConfig / ui/(Button/Card/Badge)
│   │   └── lib/                 # api.ts(含 mock fallback) / cn.ts / mockData.ts
│   ├── vite.config.ts           # 双 mode:生产 / gh-pages
│   └── package.json
│
├── tasks/                       # 各任务的配置
│   ├── meituan_rider/           # 美团飞毛腿外呼
│   ├── live_upgrade/            # 课程平台直播升级
│   └── demo_live_v2/            # LLM 自动生成的样例
│
├── personalities/               # 10 个共享性格(7 常规 + 3 对抗)
├── configs/                     # models / noise / adversarial_probes
├── tests/                       # 18 文件,259 测试,0.4 秒全跑
├── reports/                     # HTML 报告 + JSON
└── deploy/                      # QR 码 PNG(4 个版本)
```

---

## 当前状态

```
✅ 单元测试       259 个,全绿,0.4 秒跑完,无 API 依赖
✅ CI            GitHub Actions(Python 3.10 / 3.11 / 3.12 矩阵)
✅ React 前端    5 页完整,Linear/Vercel 风格,部署到 GitHub Pages
✅ FastAPI 后端  27 个 endpoints + Swagger /docs
✅ 数据库        SQLite(runs 表 + 测试历史 + 参数复用)
✅ 任务         3 个(美团 / 直播 / LLM 自动生成的样例)
✅ Persona      22 个(13 常规 + 4 对抗 + 5 自动生成)
✅ Rubric       33 条 · 8 matcher + LLM Judge
✅ Commit       50+ 次,GitHub 同步
```

剩余工作见 [CLAUDE.md 待完成清单](CLAUDE.md#待完成任务清单);其中 T6(meta-eval)和 T12(线上 KPI)等业务方数据。

---

## 相关参考资料

- [**DialAgentEval**](https://github.com/claw-eval/claw-eval) —— LLM-as-agent 评测框架,最贴近的参考项目
- [**τ-bench / τ²-bench**](https://github.com/sierra-research/tau2-bench) —— Sierra,客服 agent + 用户模拟器
- **AgentProcessBench**(arXiv 2603.14465)—— 步骤级过程评分
- **A Survey on Evaluation of LLM-based Agents**(arXiv 2503.16416)—— 综述
- [**DeepEval**](https://github.com/confident-ai/deepeval) / [**Ragas**](https://github.com/explodinggradients/ragas) —— Python LLM eval 工具
- **UC Berkeley RDI 反作弊警示**(2026.4)—— 一个自动扫描攻破了全部 8 个主流 agent benchmark,启示 rubric / judge 设计需防被套话骗分

---

## License

MIT
