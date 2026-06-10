# DialAgentEval · 对话模型指令遵循能力自动评测系统

![tests](https://github.com/CHANGJianshuo/agent_eval/actions/workflows/test.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![React](https://img.shields.io/badge/react-18-61dafb)
![License](https://img.shields.io/badge/license-MIT-green)

针对「**履约数字人外呼**」场景的对话模型指令遵循能力自动评测系统。通过状态机驱动的用户模拟器与被测模型多轮对话，结合 8 类规则匹配器 + LLM Judge 自动评分，输出**可解释、可量化**的评测报告与**可执行的改进建议**。支持三维度评分（完成度/鲁棒性/安全）、5 维 Persona 工厂、对抗红队测试、回归对比分析，配套 React + FastAPI 完整可视化平台。

---

## 系统截图

### 任务管理

<p align="center">
  <img src="docs/screenshots/task_list.png" width="100%" alt="任务列表"/>
</p>

> 任务列表：4 步进度灯（评测方案 → 模拟用户 → 评测 → 报告）、Rubric/Persona 数量、通过率一览。

<p align="center">
  <img src="docs/screenshots/task_overview.png" width="100%" alt="任务详情"/>
</p>

> 任务详情：测试历史 + 对话流程图（节点按 rubric 覆盖着色）+ 模拟用户剧本列表。

### 评测报告

<p align="center">
  <img src="docs/screenshots/report_index.png" width="100%" alt="跨任务总览"/>
</p>

> 跨任务总览：各任务运行数、通过率（阈值 0.6）、三维度平均分对比。

<p align="center">
  <img src="docs/screenshots/report_task_detail.png" width="100%" alt="任务评测详情"/>
</p>

> 单任务详情：按 Persona 统计表 + **Persona x Rubric 热力图**（蓝=完成度 / 橙=鲁棒性 / 绿=安全，深浅按得分）+ 按 Rubric 汇总表。

### 单 Case 对话回放

<p align="center">
  <img src="docs/screenshots/case_report.png" width="100%" alt="单case对话回放"/>
</p>

> 单 case 报告：task_score + 雷达图 + 评分明细 + 对话回放（违规红框高亮 + 探针标记）。

---

## 项目背景

### 问题：外呼对话评测，人工不可持续

履约数字人（美团骑手通知、课程平台升级通知、保险续保提醒……）是一个**多轮、复杂指令、强约束**的对话场景：

- **完成度问题**：任务有 4-7 步固定流程，模型容易**漏说某一步**
- **鲁棒性问题**：每轮字数限制、口语化、不重复 —— 模型容易**啰嗦、重复、太书面**
- **安全问题**：用户会**诱导承诺**（优惠/特殊照顾）、**编造数字**、**社工诱导**暴露内部信息
- **覆盖问题**：用户类型多样（合作型/抵触型/犹豫型/抬杠型/对抗型），需要全部测到

### 解决方案：三件套 + trace-first

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│ 用户模拟器   │ ──────> │  对话 Runner │ ──────> │   评分器     │
│ 状态机+LLM   │ <────── │  trace JSONL │         │ 规则 + LLM   │
│ +定向探针    │         │  可复现可审  │         │ Judge        │
└──────────────┘         └──────────────┘         └──────────────┘
```

- **用户模拟器** = 有限状态机控走向 + LLM 生成话术 + 定向探针强制覆盖关键场景
- **trace-first**：所有事件落 JSONL，评分基于 trace 可复现可审计
- **评分** = 8 类代码 matcher + LLM Judge（必须返回 `evidence_turn_id`）；三维度（完成度/鲁棒性/安全），safety 做乘子违规即归零

---

## 完整能力

| | 能力 | 实现 |
|---|---|---|
| 1 | 贴一段 prompt，**一键生成评测方案** | LLM 抽 task.yaml / flow / rubrics / personas / grader.py |
| 2 | 5 维度**独立采样**生成模拟用户 | 性格 x MBTI x 性别 x 年龄 x 教育，各自比例独立采样组合 |
| 3 | 一次任务**多次测试**版本管理 | 每次测试 test_id + agent_version + 跑批参数 + 结果指标（SQLite） |
| 4 | 跑批后**自动出报告 + 改进建议** | HTML 报告 + LLM 找弱 rubric + 给「改 prompt 哪几句」 |
| 5 | **自动应用建议** + 备份新版本 | LLM 改写 prompt + unified diff + 接受/拒绝 + 自动版本管理 |
| 6 | 改完后**回归对比** | regression 三层 diff（总览 / rubric / persona）+ 显著性阈值 |
| 7 | **安全红队**专项 | 3 类对抗 persona（注入 / 社工 / 施压）+ 破防率矩阵 |
| 8 | **模拟用户维度分析** | 每个属性值的通过率 → 找出「最难搞定的用户特征」 |

---

## 本地安装与启动

```bash
git clone https://github.com/CHANGJianshuo/agent_eval
cd agent_eval

# 安装 Python 依赖
pip install -e '.[dev,web]'

# 构建前端（需要 Node.js >= 18）
cd web && npm install && npm run build && cd ..

# 启动（API + UI + 报告托管全在 :8000）
PYTHONPATH=src python3 -m claw_eval.cli web --port 8000
```

打开 **http://localhost:8000/**

### 配 API key

UI「全局配置」里输入，保存到 `~/.claw_eval/api_keys.yaml`（不进 git）。或环境变量：

```bash
export DEEPSEEK_API_KEY=...
```

---

## 核心设计原则

1. **能用规则就别用 LLM** —— 8 类代码 matcher 优先；LLM Judge 只判语义类
2. **LLM Judge 必须给 evidence_turn_id** —— 评分指向具体对话轮，这是「可解释」的命门
3. **Safety 做乘子** —— 违反则 task_score x 0，「分高就过」不能掩盖安全问题
4. **触发型 rubric 不计分母** —— 该条 trigger 没满足就不参与计分，避免拖分
5. **Pass^k 多轮采样** —— 同 case 跑 k 次，全过才算稳定通过
6. **Persona 多维独立采样** —— 5 个维度各自配比例，独立采样组合
7. **三模型分离** —— SUT / 模拟器 / Judge 用不同模型，避免「自己评自己」
8. **trace + run_id 分目录** —— 评测产物按 run_id 子目录归档

---

## 代码组织

```
src/claw_eval/               # Python 业务逻辑
├── cli.py                   # 16 个 CLI 命令
├── api/                     # FastAPI 后端（27 个 endpoints）
├── models/                  # Pydantic schema
├── runner/                  # 对话主循环 + LLM 客户端 + trace 读写
├── user_simulator/          # 状态机 + 模拟器 + 探针
├── graders/                 # 8 matcher + LLM Judge + scoring
├── rubric/                  # rubric 抽取 + 人审 gate
├── report/                  # 报告生成 + 聚合 + 流程图 + 改进建议 + 回归
├── task_gen/                # flow/variables/grader 生成器 + 版本管理
├── persona_factory.py       # 5 维度独立采样
├── db/                      # SQLite 索引
└── adversarial.py           # 安全红队

web/src/                     # React 前端（Linear/Vercel 风格）
├── pages/                   # TaskList / TaskOverview / TestDetail / Settings
├── components/              # NewTestForm / FlowGraph / ScriptList
└── lib/                     # api.ts（含 mock fallback）

tasks/                       # 各任务配置目录
├── meituan_rider_contract/  # 美团骑手签约外呼
├── meituan_feimaotui/       # 美团飞毛腿外呼
└── live_upgrade/            # 课程平台直播升级
```

---

## 相关参考

- [**claw-eval**](https://github.com/claw-eval/claw-eval) —— LLM-as-agent 评测框架，最贴近的参考项目
- [**tau-bench / tau2-bench**](https://github.com/sierra-research/tau2-bench) —— Sierra，客服 agent + 用户模拟器
- **AgentProcessBench**（arXiv 2603.14465）—— 步骤级过程评分
- [**DeepEval**](https://github.com/confident-ai/deepeval) / [**Ragas**](https://github.com/explodinggradients/ragas) —— Python LLM eval 工具
- **UC Berkeley RDI 反作弊警示**（2026.4）—— 一个自动扫描攻破了全部 8 个主流 agent benchmark

---

## 团队分工

| 成员 | 角色 | 主要负责 |
|---|---|---|
| **常建烁** | 队长 / 主力开发 | 系统架构设计、评测引擎核心（Runner / 评分器 / 状态机模拟器）、Rubric 抽取与分类、报告可视化、React 前端 + FastAPI 后端、CI/CD 部署 |
| **张树阳** | 核心开发 | 用户模拟器策略优化、对抗探针与安全红队、Persona 维度设计与采样、回归对比分析、测试用例编写与质量保障 |

两人全程紧密协作，代码交叉 review，贡献相当。

---

## License

MIT
