"""比例分配采样单测 —— 大余数法行为(总数严格、可复现)。"""
from __future__ import annotations

from pathlib import Path

import pytest

from claw_eval.sampling import allocate, load_sampling


def test_allocate_exact_weights_sum_to_total():
    out = allocate({"a": 50, "b": 30, "c": 20}, 100)
    assert out == {"a": 50, "b": 30, "c": 20}


def test_allocate_total_always_matches():
    """无论 total 多少,分配后求和必须 = total。"""
    for total in (1, 3, 7, 10, 17, 100, 1000):
        out = allocate({"a": 50, "b": 15, "c": 15, "d": 10, "e": 5, "f": 5}, total)
        assert sum(out.values()) == total, f"total={total} 不匹配"


def test_allocate_largest_remainder_breaks_ties_by_key():
    """同分时按 key 字母序优先,保证可复现。"""
    # 10/3 = 3.33 each → 3,3,3 + 1 余数给字母序最小的
    out = allocate({"c": 1, "b": 1, "a": 1}, 10)
    assert sum(out.values()) == 10
    assert out["a"] >= out["b"] >= out["c"]    # 字母序优先拿余


def test_allocate_zero_weight_gets_zero():
    out = allocate({"a": 10, "b": 0, "c": 5}, 30)
    assert out["b"] == 0
    assert out["a"] + out["c"] == 30


def test_allocate_total_zero():
    out = allocate({"a": 10, "b": 5}, 0)
    assert all(v == 0 for v in out.values())


def test_allocate_all_zero_weights():
    out = allocate({"a": 0, "b": 0}, 10)
    assert all(v == 0 for v in out.values())


def test_allocate_negative_weights_treated_as_zero():
    out = allocate({"a": 10, "b": -5}, 20)
    assert out["a"] == 20
    assert out["b"] == 0


def test_allocate_realistic_meituan_distribution():
    """实测美团权重分配出来比例合理。"""
    weights = {"cooperative": 50, "refuse": 15, "hesitant": 15,
               "info_missing": 10, "out_of_scope": 5, "argumentative": 5}
    out = allocate(weights, 100)
    assert sum(out.values()) == 100
    assert out["cooperative"] >= 45        # 主流
    assert out["argumentative"] <= 6       # 少数


# ----------------------------- 文件加载 -----------------------------

_ROOT = Path(__file__).resolve().parents[1]


def test_load_fixture_sampling_yaml():
    cfg = load_sampling(Path(__file__).parent / "fixtures"
                        / "meituan_rider_task" / "sampling.yaml")
    assert cfg.weights["cooperative"] > 0
    assert sum(cfg.weights.values()) > 0


def test_load_live_upgrade_sampling_yaml():
    cfg = load_sampling(_ROOT / "tasks" / "live_upgrade" / "sampling.yaml")
    # v2 剧本权重(剧本名会演化,只验证结构)
    assert cfg.weights
    assert all(w > 0 for w in cfg.weights.values())
