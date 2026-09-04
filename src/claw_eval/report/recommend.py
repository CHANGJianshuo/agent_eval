"""改进建议产出 —— 从评测结果找最弱的 rubric,LLM 生成「改 Prompt 哪几句」。

两步:
  ① 聚合(纯代码):跨 case 找最弱 rubric + 收集违规样本
  ② LLM 生成(可选):喂任务 Prompt + 弱 rubric + 样本 → 输出可执行的修改建议
"""
from __future__ import annotations

import json
import re
from typing import Any

import yaml

from ..models.task import TaskDefinition
from ..models.trace import GradingResult
from ..runner import llm_client
from .aggregate import AggregateSummary


# ============================== 聚合 ==============================

def find_weak_rubrics(summary: AggregateSummary,
                      top_n: int = 5,
                      min_triggered: int = 3,
                      score_threshold: float = 0.8) -> list[dict]:
    """找最弱的 N 条 rubric。

    评分标准:严重度 = (1 - 平均分) × 触发次数,既差又频繁的最优先。
    过滤:触发次数 < min_triggered 或平均分 ≥ score_threshold 的不算弱。
    """
    candidates: list[dict] = []
    for rid, info in summary.by_rubric.items():
        n = info.get("n", 0)
        avg = info.get("avg_score", 1.0)
        if n < min_triggered or avg >= score_threshold:
            continue
        candidates.append({
            "rubric_id": rid,
            "dimension": info.get("dimension", ""),
            "n_triggered": n,
            "avg_score": round(avg, 4),
            "severity": round((1.0 - avg) * n, 4),
        })
    candidates.sort(key=lambda x: (-x["severity"], x["rubric_id"]))
    return candidates[:top_n]


def collect_violation_samples(results: list[GradingResult],
                              rubric_id: str,
                              top_k: int = 3) -> list[dict]:
    """从 result 列表里找该 rubric 的违规样本,取最差的 top_k 个。"""
    samples: list[dict] = []
    for r in results:
        # 找该 rubric 的得分(确定是否违规)
        score_row = next(
            (rs for rs in r.rubric_scores if rs.rubric_id == rubric_id),
            None,
        )
        if not score_row or not score_row.triggered or score_row.score >= 0.6:
            continue
        # 找对应 violation(可能没有,用 reasoning 兜底)
        vio = next(
            (v for v in r.violations if v.rubric_id == rubric_id),
            None,
        )
        samples.append({
            "case": f"{r.task_id}/{r.persona_id or '?'}",
            "score": round(score_row.score, 4),
            "turn": vio.turn if vio else score_row.evidence_turn,
            "evidence": (vio.evidence if vio else "")[:200],
            "reasoning": score_row.reasoning[:200],
        })
    samples.sort(key=lambda x: x["score"])     # 分最低排前
    return samples[:top_k]


# ============================== LLM 建议 ==============================

_SYSTEM_PROMPT = """\
你是评测优化顾问。任务:对评测中表现弱的 rubric,推荐**具体可执行的任务 Prompt 修改建议**。

设计原则:
1. 建议必须**可执行** —— 不要说「优化 Prompt」,要说「把第 X 行/某段改成 Y」
2. 用违规样本作为依据 —— 引用具体 turn / evidence 说明问题在哪
3. **估算提升要保守** —— 完成度类提升一般 0.02-0.08,鲁棒性类 0.05-0.15

输出 YAML(只输出 YAML 内容,不要其他文字):
  suggested_prompt_change: |
    <具体修改建议,告诉作者改 Prompt 的哪几句话,新内容是什么>
  rationale: <一句话:为什么这么改,以及证据指向>
  estimated_lift: <0-0.3 浮点数,估算这条改完后该 rubric pass 率提升空间>
  confidence: <0-1,你对建议的把握>

★ YAML 引号规则:任何含 `:`、`#`、`{`、`}`、特殊字符的字符串必须双引号包裹。
"""


_USER_TEMPLATE = """\
## 任务 Prompt(可能要修改)
{prompt}

## 弱 rubric
id: {rubric_id}
dimension: {dimension}
权重: {weight}
检查: {check}
当前平均分: {avg_score}(共触发 {n_triggered} 次,失败率较高)

## 违规样本({n_samples} 个,从最差排起)
{violations_block}

请输出 YAML 建议。
"""


def _format_violations(samples: list[dict]) -> str:
    if not samples:
        return "(无具体样本)"
    lines = []
    for i, s in enumerate(samples, 1):
        turn = f"第{s['turn']}轮" if s.get("turn") else "?"
        lines.append(
            f"  样本{i} [case={s['case']}, 分={s['score']}, {turn}]:\n"
            f"    evidence: {s['evidence']}\n"
            f"    judge: {s['reasoning']}"
        )
    return "\n".join(lines)


def _parse_recommendation(text: str) -> dict[str, Any]:
    """从 LLM 返回解出 yaml dict。容错 ```yaml``` 包装。"""
    m = re.search(r"```(?:yaml)?\s*\n(.+?)```", text, re.DOTALL)
    if m:
        text = m.group(1)
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return {}
    # 标准化字段
    out: dict[str, Any] = {}
    if "suggested_prompt_change" in data:
        out["suggested_prompt_change"] = str(data["suggested_prompt_change"])
    if "rationale" in data:
        out["rationale"] = str(data["rationale"])
    if "estimated_lift" in data:
        try:
            out["estimated_lift"] = float(data["estimated_lift"])
        except (TypeError, ValueError):
            out["estimated_lift"] = 0.0
    if "confidence" in data:
        try:
            out["confidence"] = float(data["confidence"])
        except (TypeError, ValueError):
            out["confidence"] = 0.0
    return out


def generate_recommendation(task: TaskDefinition,
                            weakness: dict,
                            samples: list[dict],
                            judge_model: str,
                            rubric_check: str = "",
                            rubric_weight: float = 0.0,
                            reasoning_effort: str = "medium",
                            max_attempts: int = 2) -> dict:
    """调 LLM 产单条建议(只针对一条弱 rubric)。"""
    user = (
        _USER_TEMPLATE
        .replace("{prompt}", task.prompt)
        .replace("{rubric_id}", weakness["rubric_id"])
        .replace("{dimension}", weakness.get("dimension", ""))
        .replace("{weight}", str(rubric_weight))
        .replace("{check}", rubric_check or "(未提供 check 文本)")
        .replace("{avg_score}", str(weakness["avg_score"]))
        .replace("{n_triggered}", str(weakness["n_triggered"]))
        .replace("{n_samples}", str(len(samples)))
        .replace("{violations_block}", _format_violations(samples))
    )
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    last_err: Exception | None = None
    for attempt in range(max_attempts):
        response = llm_client.chat(
            judge_model,
            messages,
            temperature=0.0,
            reasoning_effort=reasoning_effort,
            max_tokens=6000,                 # 兼顾 reasoning 与完整 YAML 正文
        )
        try:
            parsed = _parse_recommendation(response)
            if not parsed.get("suggested_prompt_change"):
                raise ValueError("缺少 suggested_prompt_change")
            return parsed
        except (ValueError, yaml.YAMLError) as exc:
            last_err = exc
            if attempt < max_attempts - 1:
                messages.extend([
                    {"role": "assistant", "content": response},
                    {
                        "role": "user",
                        "content": (
                            "上面的建议不是可解析的完整 YAML，错误为："
                            f"{exc}。请只返回包含 suggested_prompt_change、rationale、"
                            "estimated_lift、confidence 四个字段的修正 YAML；长文本用 |，"
                            "含冒号的单行文本加双引号，不要输出推理过程。"
                        ),
                    },
                ])
    raise ValueError(f"建议 YAML 修正 {max_attempts} 次后仍失败: {last_err}")


# ============================ 主入口 ============================

def build_recommendations(task: TaskDefinition,
                          results: list[GradingResult],
                          rubrics: list,             # list[Rubric] —— 取 check / weight
                          judge_model: str | None = None,
                          top_n: int = 5,
                          reasoning_effort: str = "medium") -> list[dict]:
    """聚合 + 可选 LLM,产出完整推荐列表。

    judge_model=None 时只出聚合数据(无 suggested_prompt_change),便于离线分析。
    """
    from .aggregate import aggregate
    summary = aggregate(results)
    weak_list = find_weak_rubrics(summary, top_n=top_n)

    rubric_map = {r.id: r for r in rubrics}

    recs: list[dict] = []
    for w in weak_list:
        samples = collect_violation_samples(results, w["rubric_id"])
        rec = dict(w)
        rec["violation_samples"] = samples
        if judge_model:
            r_obj = rubric_map.get(w["rubric_id"])
            check_text = r_obj.check if r_obj else ""
            weight = r_obj.weight if r_obj else 0.0
            try:
                llm_rec = generate_recommendation(
                    task, w, samples, judge_model,
                    rubric_check=check_text, rubric_weight=weight,
                    reasoning_effort=reasoning_effort,
                )
                rec.update(llm_rec)
            except Exception as exc:  # noqa: BLE001
                rec["llm_error"] = str(exc)
        recs.append(rec)
    return recs


def save_recommendations(task_id: str, recs: list[dict], path) -> None:
    from datetime import datetime
    data = {
        "task_id": task_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "recommendations": recs,
    }
    from pathlib import Path
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def load_recommendations(path) -> list[dict]:
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("recommendations", [])
