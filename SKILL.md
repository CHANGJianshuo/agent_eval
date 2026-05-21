# claw-eval · 全流程 Skill

把一个新对话任务从「任务描述」做到「评测报告」的完整工作流。**6 步,显式步骤可见,关键节点人审,不黑盒。**

适用场景:你有一份外呼任务的 Prompt(以及业务变量真值),想得到一份可解释、可量化的自动评测系统。

---

## 前提

环境就绪:
```bash
pip install -e '.[dev,ui]'       # 评测引擎 + 编辑器 UI
export XIAOMI_MIMO_API_KEY=...   # 或别的 LLM 提供商(在 configs/models.yaml 改)
```

任务初始化:在 `tasks/<task_id>/` 下放好:
- `task.yaml` —— 任务 Prompt + variables(业务真值)
- `sampling.yaml` —— persona 比例分配权重(初稿可均匀)
- `flow.yaml` —— 可选,任务流程图,报告里按 rubric pass 率着色

`personalities/`(任务无关性格库,跨任务复用)已存在,不需要每任务造。

---

## 一键模式

```bash
claw-eval pipeline --task <task_id> --total 30
```

跑 6 步,关键节点(rubric review)会暂停等你 a/r 输入。失败或想重做某步,用 `--from <N>` 续跑。

---

## 6 步详解

### ① extract-rubric · LLM 抽 rubric 草稿
```bash
claw-eval extract-rubric --task <task_id>
```
输出:`tasks/<task_id>/rubrics.draft.yaml`(带 category / confidence / 推荐 method)。
LLM 按 7 类(opening/flow/faq/constraint/role/behavior/safety)拆解任务 Prompt。

### ② extract-personas · LLM 推荐 persona 集
```bash
claw-eval extract-personas --task <task_id>
```
输出:`tasks/<task_id>/personas_draft/<id>.yaml`,5-8 个 persona 剧本,
每个引用现有性格库的一个 personality(不造新性格)。
人工挑选后**复制 / 编辑后写到 `personas/`**(或者用 `claw-eval editor` 网页里调)。

### ③ validate · 一致性机械检查
```bash
claw-eval validate --task <task_id>
```
查:rubric 命名 / safety 标记 / 触发可达性 / 状态机终止 / sampling 引用合法。
**error 必须修;warning 不阻塞 pipeline。**

### ④ review · rubric 草稿人审 gate
```bash
claw-eval review --task <task_id>
```
终端逐条 `[a]接受 / [r]拒绝 / [s]跳过` —— **safety 类不可 skip**,必须 a/r。
通过后写入 `rubrics.yaml`(旧的备份为 `.bak`)。

### ⑤ batch · 跑评测
```bash
claw-eval batch --task <task_id> --total 30
```
按 `sampling.yaml` 比例分配 30 个 trial,case 间并发(默认 4)。

### ⑥ dashboard · 多页可视化报告
```bash
claw-eval dashboard
```
出 `reports/index.html`(跨任务总览)+ `task_<id>.html`(任务详情 + 流程图 + Persona×Rubric 热力图)+ `cases/*.html`(单 case 回放)。

---

## 辅助命令

| 命令 | 用途 |
|---|---|
| `claw-eval editor` | Streamlit 编辑器(浏览器选性格 + 画状态机 + 配 noise rate + 保存 YAML) |
| `claw-eval run --task X --persona Y` | 单 persona 单次跑通,调试用 |
| `claw-eval grade --trace X --task Y` | 对已有 trace 重评(改 rubric 后回归用) |
| `claw-eval report --result X.result.json` | 单 case 报告 |

---

## 关键设计原则

1. **每一步可独立运行 + 可暂停 + 可改产物续跑** —— `--from N` 跳到任一步
2. **safety 类强制人审** —— review 命令对 safety 类不允许 skip
3. **能用代码就别用 LLM** —— 8 类规则 matcher(length / placeholder / keyword / number_whitelist / ordered_keyword / pace_checker / blacklist) 优先;llm_judge 只判语义类
4. **三角色三模型** —— SUT / 模拟器 / Judge 用不同模型,避免「自己评自己」
5. **trace-first** —— 评分完全基于 JSONL trace,可复现可审计
6. **触发型 rubric 不计分母** —— 没触发就不考,避免拖分;`validate` 检测「死规则」

---

## 给 AI agent 的速查

如果你是另一个 AI 接手这个项目:
- **总体目标**:对话模型指令遵循能力的自动评测(履约数字人外呼场景)
- **设计哲学**:可解释 + 可量化 + 可复现 + 人审 gate
- **新任务流程**:`pipeline --task X` 一条命令走完;有问题查日志 + 用 `--from N` 重做某步
- **改 rubric**:改 `tasks/<task>/rubrics.yaml`,再 `validate` + `batch` 看效果差异
- **加 persona**:`editor` 或写 YAML 到 `tasks/<task>/personas/`
- **改性格**:`personalities/<id>.yaml`(改一处全任务生效,这是三层拆分的收益)
