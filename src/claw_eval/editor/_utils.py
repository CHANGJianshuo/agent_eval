"""控制台多页共享工具 + 全局 CSS。"""
from __future__ import annotations

from pathlib import Path

import streamlit as st


# ===================== 全局 CSS =====================

_GLOBAL_CSS = """
<style>
:root {
    --primary: #3370ff;
    --success: #22c55e;
    --warning: #f59e0b;
    --danger:  #ef4444;
    --info:    #06b6d4;
    --gray-50: #f8fafc;
    --gray-100: #f1f5f9;
    --gray-200: #e2e8f0;
    --gray-500: #64748b;
    --gray-700: #334155;
}

/* 全局字体 */
html, body, [class*="css"], .stApp, .main, .block-container {
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC",
                  -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

/* 主区域留白 */
.main > div.block-container {
    padding-top: 2rem;
    padding-bottom: 2rem;
    max-width: 1400px;
}

/* Tab 样式 */
button[data-baseweb="tab"] {
    padding: 10px 20px !important;
    font-weight: 500 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: var(--primary) !important;
    border-bottom: 2px solid var(--primary) !important;
}

/* 标题间距 */
h1, h2, h3, h4 { margin-top: 8px !important; }
h2 { font-size: 1.4rem !important; }
h3 { font-size: 1.15rem !important; color: var(--gray-700); }

/* 卡片样式 */
.eval-card {
    background: white;
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    padding: 16px 18px;
    margin-bottom: 14px;
    transition: box-shadow 0.15s;
}
.eval-card:hover { box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
.eval-card h4 {
    margin: 0 0 8px 0;
    font-size: 1rem;
    color: var(--gray-700);
}

/* Persona 卡片 */
.persona-card {
    background: white;
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    padding: 14px;
    margin-bottom: 12px;
    min-height: 150px;
}
.persona-card .pc-title {
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--gray-700);
    margin-bottom: 6px;
}
.persona-card .pc-meta {
    font-size: 0.78rem;
    color: var(--gray-500);
    margin-bottom: 8px;
}
.persona-card .pc-weight {
    font-size: 1.4rem;
    font-weight: 700;
    color: var(--primary);
}

/* 徽章 */
.badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 0.78rem;
    font-weight: 500;
    margin-right: 4px;
}
.badge-success { background: #dcfce7; color: #15803d; }
.badge-warning { background: #fef3c7; color: #b45309; }
.badge-danger  { background: #fee2e2; color: #b91c1c; }
.badge-info    { background: #cffafe; color: #0e7490; }
.badge-gray    { background: var(--gray-100); color: var(--gray-700); }

/* 进度阶段 */
.lifecycle {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 0;
    color: var(--gray-500);
    font-size: 0.85rem;
}
.lifecycle .stage {
    padding: 4px 10px;
    border-radius: 16px;
    background: var(--gray-100);
}
.lifecycle .stage.done { background: #dcfce7; color: #15803d; }
.lifecycle .stage.current { background: #dbeafe; color: #1e40af; font-weight: 500; }
.lifecycle .arrow { color: var(--gray-200); }

/* 警告条 */
.warn-banner {
    padding: 10px 14px;
    border-radius: 6px;
    background: #fef3c7;
    border-left: 3px solid var(--warning);
    color: #92400e;
    margin-bottom: 12px;
    font-size: 0.92rem;
}
.danger-banner {
    padding: 10px 14px;
    border-radius: 6px;
    background: #fee2e2;
    border-left: 3px solid var(--danger);
    color: #991b1b;
    margin-bottom: 12px;
    font-size: 0.92rem;
}

/* 表格紧凑 */
[data-testid="stDataFrame"] { font-size: 0.9rem; }

/* 减少 Streamlit 元素之间的过大间距 */
[data-testid="stVerticalBlock"] > div { gap: 0.5rem; }

/* 隐藏 Streamlit 顶部菜单的「Deploy」按钮(部署相关,无关) */
[data-testid="stToolbar"] { visibility: hidden; }
</style>
"""


def inject_global_style() -> None:
    """每个页面顶部调一次,注入全局样式。"""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


# ===================== 路径常量 =====================

# 仓库根目录 = .../src/claw_eval/editor/_utils.py 的 parents[3]
ROOT = Path(__file__).resolve().parents[3]
TASKS_DIR = ROOT / "tasks"
PERSONALITIES_DIR = ROOT / "personalities"
NOISE_FILE = ROOT / "configs" / "noise_profiles.yaml"
REPORTS_DIR = ROOT / "reports"
TRACES_DIR = ROOT / "traces"


def list_tasks() -> list[str]:
    if not TASKS_DIR.exists():
        return []
    return sorted(d.name for d in TASKS_DIR.iterdir() if d.is_dir())


def list_personas(task: str) -> list[str]:
    pd = TASKS_DIR / task / "personas"
    if not pd.exists():
        return []
    return sorted(p.stem for p in pd.glob("*.yaml"))


def list_personalities() -> list[str]:
    if not PERSONALITIES_DIR.exists():
        return []
    return sorted(p.stem for p in PERSONALITIES_DIR.glob("*.yaml"))


def list_runs() -> list[str]:
    """返回 traces/ 下所有 run_id 子目录。"""
    if not TRACES_DIR.exists():
        return []
    return sorted(d.name for d in TRACES_DIR.iterdir() if d.is_dir())
