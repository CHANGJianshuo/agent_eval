# CLAUDE.md

本文件为 Claude Code 在本仓库工作时的上下文说明。

## 项目目标

构建一套**对话模型指令遵循能力的自动化评测系统**,用于「履约数字人外呼」场景。

**背景**:系统自动发起与用户的通话,对话模型(SUT)需根据预设的任务指令完成具体任务。指令含复杂流程与多种约束,人工评估成本高、难量化。需要一套**可解释、可量化**的自动评估能力。

**交付目标**:
1. **用户模拟器** —— 充分有效地测试对话模型在特定任务指令下的效果。
2. **自动评测报告** —— 评测过程可解释、结果可量化。
   (脱敏数据将在报名后一个工作日内发至队长邮箱。)

## 待完成任务清单

### 已完成 ✅(P0 全部 + P1 大半)

**评测引擎核心**
- [x] **用户模拟器**:状态机 + LLM + 探针;persona **三层结构**(性格 / 剧本 / 噪音 rate)。
- [x] **对话 Runner**:多轮主循环,trace JSONL,`reasoning_effort` 调优,并发对话。
- [x] **评分器**:8 类规则匹配器(length / placeholder / keyword / number_whitelist / ordered_keyword / pace_checker / blacklist) + LLM Judge(`evidence_turn_id` 强制返回)。
- [x] **聚合 + Pass^k**:三维度合并 + Safety 乘子 + Pass^k 公式。
- [x] **多页 HTML 报告**:跨任务总览 + 每任务详情 + 单 case 对话回放(违规高亮 + 雷达图)。

**P0 核心交付(全部完成)**
- [x] **Rubric 抽取器 + 7 类分类 + 人审 gate**(T2):LLM 抽 rubric YAML 草稿,7 类(opening/flow/faq/constraint/role/behavior/safety) + 置信度,safety 强制人审。
- [x] **比例分配采样**(T3):`sampling.yaml` 真实流量占比,batch 按 weights 分配 trial。
- [x] **任务流程图 + 报告按成功率着色**(T14):每任务 `flow.yaml`,ECharts 渲染节点,绿/黄/红/灰按 pass 率上色。
- [x] **persona 编辑器 UI**(T11):Streamlit 网页 —— 选性格 + 画状态机 + 配探针 + 设 noise rate + 实时校验 + YAML 预览保存。
- [x] **显式六步流水线**(T5):`pipeline` 命令编排 extract-rubric → extract-personas → validate → review → batch → dashboard,可 `--from N` 续跑。
- [x] **noise rate per-turn**(T1):seed 可复现,kinds 库化。
- [x] **直播补 persona**(T4):busy_owner / discount_seeker / non_owner 让原死规则有人触发。
- [x] **validate 一致性检查**(T9):命名 / weight / safety 标记 / 触发可达性 / 状态机终止 / sampling 引用合法。

**P1 可信度与质量(3/4 完成,余者卡数据)**
- [x] **actionable 改进建议**(T7):找弱 rubric(严重度=(1-avg)×n)+ LLM 给「改 Prompt 哪几句」+ 违规样本溯源;效果由回归测试衡量，嵌入 dashboard 顶部。
- [x] **对抗探针 / 安全红队**(T8):3 类对抗性格(prompt_injector / social_engineer / coercive)+ 4 个对抗剧本 + `safety-test` 命令 + 红队报告(破防率矩阵)。
- [x] **回归对比**(T10):`batch --label v1 → batch --label v2 → regression --old v1 --new v2`,三层 diff(维度/rubric/persona)。run_id 子目录做轻量版本管理。
- [ ] **meta-eval**(T6):Judge 校准 + rubric 查全率 —— **卡:需人工标注样本**。
- [ ] **线上 KPI 对标**(T12):评测分 vs 线上完成率/客诉率相关性 —— **卡:需线上真实数据**。

**P2 工程完善 + Backlog**
- [ ] T10/T8 dashboard 集成(当前只有 CLI 输出 + JSON,没接到 dashboard 卡片)。
- [ ] 端到端闭环 demo(按 T7 建议改 Prompt → batch v2 → regression 对比基线与新版本)。
- [ ] 概率转移状态机(`transitions: {a: {b: 0.6, c: 0.4}}`)。
- [ ] embedding 版 no_repeat(代替 llm_judge,~10% 加速)。

## 当前进度(最新)

- **里程碑**:全 P0 完成 + P1 三件套(T7 / T8 / T10)完成。**192 单测全绿**;真实跑过 34+ case;**12 次 commit** 已推 https://github.com/CHANGJianshuo/agent_eval
- **技术栈**:Python + Pydantic + Jinja2 + ECharts(报告)+ LiteLLM(OpenAI 兼容)+ Streamlit(编辑器)+ pytest;模型走小米 MiMo Token Plan(SUT=`mimo-v2.5`,模拟器=`mimo-v2-pro`,Judge=`mimo-v2.5-pro`,`reasoning_effort=low/medium`)。
- **仓库结构**:
  - `src/claw_eval/` 共享引擎(models / runner / user_simulator / graders / rubric / report / editor / adversarial)
  - `tasks/<id>/` 每任务一个目录(task.yaml + rubrics.yaml + sampling.yaml + flow.yaml + personas/)，默认共享 RubricGrader，grader.py 仅用于自定义评分逻辑
  - `personalities/` 10 个共享性格(7 常规 + 3 对抗,前缀 `adv_`)
  - `configs/`:noise_profiles.yaml + adversarial_probes.yaml + models.yaml
  - `tests/` 17 个测试文件,192 个测试,0.4 秒跑完
  - `reports/` 多页 HTML 产物(index + task 详情 + cases + recommendations + regression JSON)
- **当前数据规模**:**2 任务 / 16 persona(美团 8 + 直播 8,含各 2 个对抗) / 33 rubric / 8 类 matcher + LLM Judge / 7 类 rubric 分类**。
- **13 个 CLI 命令**:`run / batch / grade / report / dashboard / validate / extract-rubric / extract-personas / review / pipeline / recommend / regression / safety-test / editor`。
- 详见 `reports/index.html` 查看可视化跑批结果。

## 仓库文件

| 文件 | 内容 |
|---|---|
| `方案设计.md` | 通用方案:任务理解、系统架构、用户模拟器、Rubric 评分、语言选型、开发清单。以「美团飞毛腿外呼」任务为例。 |
| `方案设计_直播升级外呼.md` | 「课程发布平台直播升级通知」任务的特化方案:7 步流程 + 步进引导 + 多边界行为;新增 4 类匹配器(顺序/节奏/数字白名单/词黑名单)。 |

## 核心设计决策(对话中已确认)

- **语言**:Python 为主。理由——LLM/NLP/数据分析生态全在 Python;同类项目(DialAgentEval、τ-bench、DeepEval)均为 Python。报告页若需强交互可用 TS。
- **架构**:用户模拟器 + 被测模型(SUT) + 评分器,三件套;trace-first(全事件 JSONL 落盘,评分基于 trace,可复现可审计)。
- **用户模拟器**:不用纯 LLM 自由发挥,改用**有限状态机控制走向 + LLM 生成话术**,保证可复现与覆盖率;配合**定向探针**强制触发关键场景。
- **评分原则**:
  1. 能用规则就别用 LLM(长度、占位符、关键词、相似度、黑名单、数字白名单)。
  2. LLM Judge 只判语义类,**必须返回 `{score, reasoning, evidence_turn_id}`** —— evidence 指向具体对话轮次,是「可解释」的命门。
  3. **Safety / 越界类做成乘子**(0 或 1),违规则总分归零。
  4. **Pass^k 多轮采样**:每个 case 跑 3 次,全过才算稳定通过。
  5. **触发型 rubric**(`trigger:`)未触发不计入分母,避免拖分。
- **Judge 模型**:用比 SUT 更强的模型,避免同级偏袒;Judge 温度 0,SUT 温度 0.7。
- **用户模拟器与 SUT 用不同模型**,避免「自己评自己」。

## 已知的两个示例任务

1. **美团飞毛腿骑手外呼**:站长致电骑手,4 步流程,~30 字限制,FAQ,越权回退话术,安抚后挂断。
2. **课程发布平台直播升级**:客服通知机构新增「标准/低延迟直播」选项,7 步流程 + 子分支,15-20 字限制,步进式引导(分轮发送),边界行为(开车挂断/说忙挽留/非负责人转达),Safety 红线(不承诺优惠券、不编造价格)。

## 参考资料

### DialAgentEval(最贴近的参考项目)
- 仓库:https://github.com/claw-eval/claw-eval
- LLM-as-agent 评测框架,300 人工校验任务、2159 rubrics、9 类别。
- 三大评测维度:**Completion**(完成度)、**Safety**(安全)、**Robustness**(鲁棒性)。
- **Pass^3** 指标:同一任务独立跑 3 次,全部通过才计success。
- 关键机制(可直接借鉴):
  - 每个任务有独立 `task.yaml` + `grader.py`,grader 继承 `AbstractGrader`,registry 动态加载。
  - `multi_turn` split 含 `user_agent`(persona + max_rounds),用 LLM 模拟用户。
  - `judge_rubric` + `scoring_components`(带 weight)+ LLM Judge(默认 gemini-3-flash)。
  - 评分公式:`base = 0.80*completion + 0.20*robustness;task_score = safety * base`;`pass` 阈值 0.75。
  - 全程 JSONL trace,grader 完全基于 trace。
  - Mock services 本地起 HTTP,避免外网不稳定污染结果。
  - CLI:`run / grade / batch / build-image / cleanup / list`。

### 其他相关 Benchmark / 文献(2025-2026)
| 名称 | 类型 | 借鉴点 |
|---|---|---|
| **τ-bench / τ²-bench** (Sierra) | 客服 agent + 用户模拟器 | **最值得照搬架构**;τ² 双控环境。arXiv 2406.12045 / 2506.07982;github.com/sierra-research/tau2-bench |
| **AgentProcessBench** | 步骤级过程评分 | 可解释的步骤级打分。arXiv 2603.14465 |
| **AgentRewardBench** | 评 LLM judge 本身 | rubric 风格审查(成功/副作用) |
| **Proxy State-Based Eval** | 可验证奖励 | 用代理状态评分多轮工具调用。arXiv 2602.16246 |
| **A Survey on Evaluation of LLM-based Agents** | 综述 | arXiv 2503.16416 |
| **Multi-Turn Conversations Survey** | 综述 | arXiv 2503.22458 |
| **DeepEval / Ragas** | Python LLM eval 工具 | 评分器工具链 |
| **promptfoo** | TS,prompt 测试 CLI+Web | 报告页设计参考 |

### 反作弊警示(必读)
- UC Berkeley RDI(2026.4):一个自动扫描 agent 通过 reward hacking 攻破了全部 8 个主流 agent benchmark。
  https://rdi.berkeley.edu/blog/trustworthy-benchmarks-cont/
- **启示**:rubric / judge 设计需防被 SUT 套话骗分;Safety 红线、数字白名单、evidence 溯源都是对策。

## 给 Claude Code 的工作约定

- 新任务上线时,优先用「Rubric 抽取 Prompt 模板」(见 `方案设计_直播升级外呼.md` 附录)自动产出 rubric YAML。
- 评分器代码必须带 pytest 单测——评测系统本身的可靠性优先于功能数量。
- 通用骨架在 `方案设计.md`,任务特化(新增匹配器等)放 `rubric/matchers/`,不污染通用层。
