"""Reject rule parameters that otherwise disappear into permissive matcher kwargs."""
import math
import re


def validate_rule_params(rubric):
    p = rubric.params
    allowed = {
        "llm_judge": set(), "keyword": {"keywords", "mode", "scope"},
        "length": {"max_chars", "tolerance"}, "placeholder": {"pattern", "patterns"},
        "number_whitelist": {"whitelist", "extra_whitelist"},
        "ordered_keyword": {"sequence", "scope"},
        "pace_checker": {"min_assistant_turns", "after_user_keyword"},
        "blacklist": {"banned_words", "scope", "mode"},
    }
    unknown = set(p) - allowed[rubric.method]
    if unknown:
        raise ValueError(f"不支持的参数: {sorted(unknown)}")
    required = {"keyword": "keywords", "ordered_keyword": "sequence", "blacklist": "banned_words"}
    key = required.get(rubric.method)
    if key and (not isinstance(p.get(key), list) or not p[key] or any(not isinstance(v, str) or not v for v in p[key])):
        raise ValueError(f"{key} 必须是非空字符串列表")
    if "scope" in p and p["scope"] not in {"first_assistant", "last_assistant", "all_assistant"}:
        raise ValueError("不支持的 scope")
    if rubric.method == "keyword" and p.get("mode", "any") not in {"any", "all"}:
        raise ValueError("关键词 mode 必须是 any 或 all")
    if rubric.method == "blacklist" and (p.get("scope", "all_assistant") != "all_assistant" or p.get("mode", "any") != "any"):
        raise ValueError("黑名单检查所有 assistant 轮；scope 必须是 all_assistant、mode 必须是 any")
    for key in ("max_chars", "min_assistant_turns"):
        if key in p and (type(p[key]) is not int or p[key] < 1):
            raise ValueError(f"{key} 必须是正整数")
    if "tolerance" in p and (type(p['tolerance']) not in (int, float) or not math.isfinite(p['tolerance']) or not 0 <= p['tolerance'] <= 1):
        raise ValueError("tolerance 必须在 0～1 内")
    if rubric.method == "placeholder":
        patterns = p.get('patterns', [p['pattern']] if 'pattern' in p else [])
        if not isinstance(patterns, list):
            raise ValueError("patterns 必须是列表")
        for pattern in patterns:
            re.compile(pattern)
