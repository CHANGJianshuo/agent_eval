"""噪音 overlay 单测 —— sampling.yaml 新字段 / select_noise_cases 比例 / 可复现。"""
from __future__ import annotations

from pathlib import Path

import yaml

from claw_eval.sampling import (
    NoiseOverlay,
    SamplingConfig,
    load_sampling,
    save_sampling,
    select_noise_cases,
)


# ============================ NoiseOverlay 模型 ============================

def test_default_overlay_is_inactive():
    c = SamplingConfig(weights={"a": 1.0})
    assert c.noise_overlay.rate == 0.0
    assert c.noise_overlay.kinds == []


def test_overlay_loaded_from_yaml(tmp_path: Path):
    yaml_text = """\
weights:
  cooperative: 50
  refuse: 20
noise_overlay:
  rate: 0.15
  kinds: [filler, asr_error]
"""
    p = tmp_path / "sampling.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    cfg = load_sampling(p)
    assert cfg.weights == {"cooperative": 50, "refuse": 20}
    assert cfg.noise_overlay.rate == 0.15
    assert cfg.noise_overlay.kinds == ["filler", "asr_error"]


def test_overlay_save_roundtrip(tmp_path: Path):
    cfg = SamplingConfig(
        weights={"a": 1.0, "b": 2.0},
        noise_overlay=NoiseOverlay(rate=0.2, kinds=["filler"]),
    )
    p = tmp_path / "sampling.yaml"
    save_sampling(cfg, p)
    text = p.read_text(encoding="utf-8")
    assert "noise_overlay" in text
    cfg2 = load_sampling(p)
    assert cfg2.noise_overlay.rate == 0.2
    assert cfg2.noise_overlay.kinds == ["filler"]


def test_overlay_save_skips_default(tmp_path: Path):
    """noise_overlay 全默认时不写到 YAML 里(保持简洁)。"""
    cfg = SamplingConfig(weights={"a": 1.0})
    p = tmp_path / "sampling.yaml"
    save_sampling(cfg, p)
    assert "noise_overlay" not in p.read_text(encoding="utf-8")


# ============================ select_noise_cases ============================

def test_select_zero_rate_empty():
    assert select_noise_cases(100, 0.0, seed=0) == set()


def test_select_rate_picks_round_count():
    """round(20 × 0.1) = 2 个噪音 case。"""
    s = select_noise_cases(20, 0.1, seed=42)
    assert len(s) == 2
    assert all(0 <= i < 20 for i in s)


def test_select_indices_in_range():
    s = select_noise_cases(50, 0.3, seed=1)
    assert len(s) == 15
    assert all(0 <= i < 50 for i in s)


def test_select_same_seed_same_set():
    """同 seed → 同样的索引集合(可复现)。"""
    assert select_noise_cases(100, 0.2, seed=42) == select_noise_cases(100, 0.2, seed=42)


def test_select_different_seed_different_set():
    """不同 seed 不一定相同(在合理 N 下大概率不同)。"""
    s1 = select_noise_cases(100, 0.2, seed=1)
    s2 = select_noise_cases(100, 0.2, seed=2)
    # 各 20 个,完全相同的概率极低
    assert s1 != s2 or len(s1) < 20    # 给个保底


def test_select_full_rate_returns_all():
    """rate=1.0 时所有索引都被选。"""
    assert select_noise_cases(10, 1.0, seed=0) == set(range(10))


def test_select_zero_total_returns_empty():
    assert select_noise_cases(0, 0.5, seed=0) == set()
