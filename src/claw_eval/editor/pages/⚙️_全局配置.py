"""⚙️ 全局设置 —— API & 模型 / 性格库 / 噪音库 三个 Tab。

资源管理(reusable assets)和系统配置;跟具体任务无关。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from claw_eval.editor._utils import (
    NOISE_FILE,
    PERSONALITIES_DIR,
    ROOT,
    TASKS_DIR,
    inject_global_style,
    list_personalities,
    list_tasks,
)
from claw_eval.models.persona import (
    Demographics,
    Personality,
    load_noise_kinds,
    load_personality,
)

st.set_page_config(page_title="设置", page_icon="⚙️", layout="wide")
inject_global_style()

st.title("⚙️ 全局配置")
st.caption("跨任务复用的资源(Persona 库 / 噪音库)+ 系统配置(API & 模型)。")

tab_api, tab_personality, tab_noise = st.tabs([
    "🔑 API & 模型",
    "🎭 Persona 库",
    "📚 噪音库",
])


# ============================ Tab 1:API & 模型 ============================
with tab_api:
    st.subheader("LLM 模型配置")
    st.caption("`configs/models.yaml` —— SUT / 模拟器 / Judge 三个角色 + API key。")

    cfg_path = ROOT / "configs" / "models.yaml"
    if cfg_path.exists():
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        cfg = {}

    # API key 段
    st.markdown("### 🔑 API Key(优先级:UI 输入 > 环境变量)")
    key_file = Path.home() / ".claw_eval" / "api_keys.yaml"
    saved_keys = {}
    if key_file.exists():
        try:
            saved_keys = yaml.safe_load(key_file.read_text(encoding="utf-8")) or {}
        except Exception:
            pass

    provider = st.selectbox("Provider", ["xiaomi_mimo", "openai", "anthropic"],
                              index=0)
    cur_key = (saved_keys.get(provider)
               or os.environ.get(
                   {"xiaomi_mimo": "XIAOMI_MIMO_API_KEY",
                    "openai": "OPENAI_API_KEY",
                    "anthropic": "ANTHROPIC_API_KEY"}.get(provider, "")
               , "") or "")
    masked = ("*" * 8 + cur_key[-6:]) if len(cur_key) > 8 else cur_key
    new_key = st.text_input(
        "API key(明文输入,保存后写到 ~/.claw_eval/api_keys.yaml)",
        value="", placeholder=f"当前:{masked or '(未设置)'}",
        type="password")
    c_kbtn1, c_kbtn2 = st.columns([1, 1])
    if c_kbtn1.button("💾 保存 key", disabled=not new_key):
        key_file.parent.mkdir(parents=True, exist_ok=True)
        saved_keys[provider] = new_key
        key_file.write_text(
            yaml.safe_dump(saved_keys, default_flow_style=False),
            encoding="utf-8")
        st.success(f"✓ 已保存到 {key_file}")
        st.rerun()
    if c_kbtn2.button("🩺 测试连接", disabled=not cur_key):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        if provider == "xiaomi_mimo":
            env["XIAOMI_MIMO_API_KEY"] = cur_key
        elif provider == "openai":
            env["OPENAI_API_KEY"] = cur_key
        elif provider == "anthropic":
            env["ANTHROPIC_API_KEY"] = cur_key
        script = """
import sys
sys.path.insert(0, "src")
from claw_eval.runner import llm_client
import yaml
cfg = yaml.safe_load(open("configs/models.yaml"))
model = cfg.get("judge", {}).get("model", "mimo-v2.5-pro")
try:
    out = llm_client.chat(model, [{"role":"user","content":"ping"}],
                          temperature=0.0, max_tokens=10)
    print(f"OK: {out[:80]}")
except Exception as e:
    print(f"FAIL: {e}")
"""
        try:
            r = subprocess.run([sys.executable, "-c", script],
                                  env=env, capture_output=True, text=True, timeout=20)
            if "OK:" in r.stdout:
                st.success(r.stdout.strip())
            else:
                st.error(r.stdout.strip() or r.stderr.strip() or "未知错误")
        except subprocess.TimeoutExpired:
            st.error("超时(20s)。API key 不对 / 网络不通?")

    st.markdown("---")
    # 三个模型角色配置
    st.markdown("### 🤖 三个模型角色")

    for role_name, role_cn, role_help, default_temp, default_effort in [
        ("sut", "SUT(被测模型)", "评测对象。温度通常 0.7 模拟真实使用。", 0.7, "low"),
        ("simulator", "模拟器(用户)", "扮演电话另一端的用户。", 0.7, "low"),
        ("judge", "Judge(评委)", "给 rubric 打分。**必须 ≥ SUT 能力**,温度 0,确保一致。",
         0.0, "medium"),
    ]:
        with st.expander(f"**{role_cn}**", expanded=True):
            st.caption(role_help)
            role_cfg = cfg.get(role_name, {})
            c1, c2, c3 = st.columns([2, 1, 1])
            new_model = c1.text_input(
                "model", value=role_cfg.get("model", ""),
                key=f"m_{role_name}")
            new_temp = c2.slider(
                "temperature", 0.0, 1.0,
                float(role_cfg.get("temperature", default_temp)), 0.1,
                key=f"t_{role_name}")
            new_effort = c3.selectbox(
                "reasoning_effort", ["low", "medium", "high"],
                index=["low", "medium", "high"].index(
                    role_cfg.get("reasoning_effort", default_effort)),
                key=f"e_{role_name}")
            cfg.setdefault(role_name, {})
            cfg[role_name]["model"] = new_model
            cfg[role_name]["temperature"] = new_temp
            cfg[role_name]["reasoning_effort"] = new_effort

    cfg["concurrency"] = st.number_input(
        "默认并发数", 1, 16, int(cfg.get("concurrency", 4)),
        help="batch 命令默认用此并发数(case 间并行)")

    if st.button("💾 保存所有 模型配置", type="primary"):
        cfg_path.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False,
                           default_flow_style=False),
            encoding="utf-8")
        st.success(f"✓ 已保存到 {cfg_path}")


# ============================ Tab 2:Persona 库 = 5 个并列维度的属性字典 ============================
with tab_personality:
    st.subheader("🎭 Persona 库 · 5 个并列维度的属性字典")
    st.caption("**性格 / MBTI / 性别 / 年龄段 / 教育**这 5 个**并列的「类」**;"
                "每个类下面有多个**属性值**(选项)。任务的「新建测试」时可以直接勾选这些属性组合。")

    # 加载所有 persona 模板(供维度统计和「具体模板列表」共用)
    persona_data: dict[str, Personality] = {}
    for pid in list_personalities():
        try:
            persona_data[pid] = load_personality(PERSONALITIES_DIR / f"{pid}.yaml")
        except Exception:
            pass

    # 5 个维度的可选值 + 使用统计
    DIMS = {
        "attitude": {
            "name": "📌 性格 attitude",
            "values": ["cooperative", "refuse", "hesitant",
                       "argumentative", "confused", "blunt",
                       "hurried", "adversarial"],
            "desc": {
                "cooperative": "合作型 — 配合、礼貌、简短",
                "refuse": "抵触型 — 不愿做、坚决",
                "hesitant": "犹豫型 — 反复追问",
                "argumentative": "抬杠型 — 质疑、爱反问",
                "confused": "茫然型 — 不清楚",
                "blunt": "直接强势型 — 追着问、直接",
                "hurried": "匆忙型 — 急、话少",
                "adversarial": "对抗型 — prompt 注入 / 社工 / 施压",
            },
        },
        "mbti": {
            "name": "📌 MBTI",
            "values": [a + b + c + d for a in "IE" for b in "NS"
                       for c in "FT" for d in "JP"],
            "desc": {},
        },
        "gender": {
            "name": "📌 性别",
            "values": ["male", "female"],
            "desc": {"male": "男", "female": "女"},
        },
        "age_range": {
            "name": "📌 年龄段",
            "values": ["<20", "20-29", "30-39", "40-49", "50+"],
            "desc": {},
        },
        "education": {
            "name": "📌 教育",
            "values": ["primary", "middle", "high", "college", "postgrad"],
            "desc": {
                "primary": "小学", "middle": "初中",
                "high": "高中", "college": "本科",
                "postgrad": "研究生及以上",
            },
        },
    }

    # 统计每个属性值的使用次数
    usage = {dim: {v: 0 for v in cfg["values"]}
              for dim, cfg in DIMS.items()}
    for p in persona_data.values():
        for dim in DIMS:
            v = getattr(p.demographics, dim)
            if v in usage[dim]:
                usage[dim][v] += 1

    st.markdown("---")
    for dim, cfg in DIMS.items():
        with st.expander(f"{cfg['name']} · {len(cfg['values'])} 种",
                           expanded=(dim in ("attitude",))):
            # 每个属性值一行
            for v in cfg["values"]:
                n_use = usage[dim].get(v, 0)
                desc = cfg["desc"].get(v, "")
                badge = (f'<span class="badge badge-success">{n_use} 在用</span>'
                         if n_use > 0
                         else '<span class="badge badge-gray">未使用</span>')
                st.markdown(
                    f'<div style="display:flex; padding:6px 8px;'
                    f' border-bottom:1px solid #f1f5f9;">'
                    f'<div style="width:130px;"><strong>{v}</strong></div>'
                    f'<div style="flex:1; color:#475569;">{desc}</div>'
                    f'<div>{badge}</div></div>',
                    unsafe_allow_html=True,
                )

    st.markdown("---")
    st.caption("💡 **在「📋 任务详情 → 任务概览 → ➕ 新建测试」表单里**,"
                "可以根据这些维度勾选 persona,系统按勾选组合分配权重。")


# ============================ Tab 3:噪音库 ============================
with tab_noise:
    st.subheader("📚 噪音库")
    st.caption("噪音种类定义 —— 任务的 sampling.yaml noise_overlay.kinds 从这里引用。")

    if not NOISE_FILE.exists():
        st.warning(f"{NOISE_FILE} 不存在")
    else:
        data = yaml.safe_load(NOISE_FILE.read_text(encoding="utf-8")) or {}
        st.markdown(f"**共 {len(data)} 种噪音**")
        rows = []
        for kid, kdata in data.items():
            if not isinstance(kdata, dict):
                continue
            rows.append({
                "id": kid,
                "name": kdata.get("name", ""),
                "instruction": kdata.get("instruction", "")[:80],
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True,
                          use_container_width=True)

        st.markdown("---")
        st.markdown("### 编辑 / 新增")
        choices = ["✨ 新建"] + list(data.keys())
        sel = st.selectbox("选择", choices)
        if sel == "✨ 新建":
            new_id = st.text_input("id(英文小写下划线)")
            cur = {"name": "", "instruction": ""}
        else:
            new_id = sel
            cur = data.get(sel, {})
        new_name = st.text_input("name", cur.get("name", ""), key=f"nn_{sel}")
        new_inst = st.text_area("instruction(给模拟器看的指令)",
                                  cur.get("instruction", ""),
                                  height=80, key=f"ni_{sel}")
        if st.button("💾 保存", disabled=not new_id):
            data[new_id] = {"name": new_name, "instruction": new_inst}
            NOISE_FILE.write_text(
                yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                                default_flow_style=False),
                encoding="utf-8")
            st.success(f"✓ {new_id} 已保存"); st.rerun()
