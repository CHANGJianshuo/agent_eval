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


# ============================ Tab 2:Persona 库(全局) ============================
with tab_personality:
    st.subheader("🎭 Persona 库(跨任务复用)")
    st.caption("Persona = 性格 + 说话风格 + 人口学(MBTI/性别/年龄/教育)。"
                "任务里的 persona 引用这里的模板,改一个会影响所有引用者。")

    # 算每个的被引用次数 + 加载完整数据
    personality_usage: dict[str, list[str]] = {pid: [] for pid in list_personalities()}
    persona_data: dict[str, Personality] = {}
    for pid in list_personalities():
        try:
            persona_data[pid] = load_personality(PERSONALITIES_DIR / f"{pid}.yaml")
        except Exception:
            pass
    for task in list_tasks():
        for pf in (TASKS_DIR / task / "personas").glob("*.yaml"):
            try:
                d = yaml.safe_load(pf.read_text(encoding="utf-8")) or {}
                pers_id = d.get("personality")
                if pers_id in personality_usage:
                    personality_usage[pers_id].append(f"{task}/{pf.stem}")
            except Exception:
                pass

    # 筛选栏
    st.markdown("### 🔎 筛选")
    fc1, fc2, fc3, fc4 = st.columns(4)
    f_att = fc1.selectbox(
        "态度", ["全部", "cooperative", "refuse", "hesitant",
                  "argumentative", "confused", "blunt", "hurried",
                  "adversarial", "unspecified"], key="f_att")
    f_mbti = fc2.selectbox(
        "MBTI", ["全部"] + ["unspecified"] +
                 [a + b + c + d for a in "IE" for b in "NS"
                  for c in "FT" for d in "JP"],
        key="f_mbti")
    f_age = fc3.selectbox(
        "年龄", ["全部", "unspecified", "<20", "20-29",
                  "30-39", "40-49", "50+"], key="f_age")
    f_gen = fc4.selectbox(
        "性别", ["全部", "unspecified", "male", "female"], key="f_gen")

    # 应用筛选
    pids = []
    for pid, p in persona_data.items():
        d = p.demographics
        if f_att != "全部" and d.attitude != f_att:
            continue
        if f_mbti != "全部" and d.mbti != f_mbti:
            continue
        if f_age != "全部" and d.age_range != f_age:
            continue
        if f_gen != "全部" and d.gender != f_gen:
            continue
        pids.append(pid)
    st.caption(f"显示 {len(pids)} / {len(persona_data)} 个")

    # 卡片网格 + 编辑
    cards_per_row = 3
    for row_i in range(0, len(pids), cards_per_row):
        cols = st.columns(cards_per_row)
        for ci, pid in enumerate(pids[row_i:row_i + cards_per_row]):
            with cols[ci]:
                try:
                    p = load_personality(PERSONALITIES_DIR / f"{pid}.yaml")
                    refs = personality_usage[pid]
                    refs_html = f'{len(refs)} 个 persona 在用'
                    GENDER_ICON = {"male": "🧔", "female": "👩",
                                    "unspecified": "👤"}
                    g_icon = GENDER_ICON.get(p.demographics.gender, "👤")
                    st.markdown(f"""
<div class="persona-card">
  <div class="pc-title">{g_icon} {p.name}</div>
  <div class="pc-meta" style="font-size:0.72rem; color:#94a3b8;">{pid}</div>
  <table style="margin-top:8px; font-size:0.82rem; width:100%; color:#475569;">
    <tr><td style="padding:2px 4px;color:#94a3b8;">MBTI</td>
        <td style="padding:2px 4px;"><strong>{p.demographics.mbti}</strong></td></tr>
    <tr><td style="padding:2px 4px;color:#94a3b8;">性别</td>
        <td style="padding:2px 4px;">{p.demographics.gender}</td></tr>
    <tr><td style="padding:2px 4px;color:#94a3b8;">年龄</td>
        <td style="padding:2px 4px;">{p.demographics.age_range}</td></tr>
    <tr><td style="padding:2px 4px;color:#94a3b8;">教育</td>
        <td style="padding:2px 4px;">{p.demographics.education}</td></tr>
    <tr><td style="padding:2px 4px;color:#94a3b8;">态度</td>
        <td style="padding:2px 4px;"><strong>{p.demographics.attitude}</strong></td></tr>
  </table>
  <div class="pc-meta" style="margin-top:8px; padding-top:6px; border-top:1px solid #e2e8f0;">
    {refs_html}
  </div>
</div>""", unsafe_allow_html=True)
                    with st.expander("✏ 编辑", expanded=False):
                        new_name = st.text_input("name", p.name, key=f"pn_{pid}")
                        new_desc = st.text_area("description", p.description,
                                                  height=80, key=f"pd_{pid}")
                        new_style = st.text_area("speaking_style",
                                                   p.speaking_style, height=60,
                                                   key=f"ps_{pid}")
                        st.markdown("**Demographics**")
                        d_c1, d_c2 = st.columns(2)
                        new_mbti = d_c1.selectbox(
                            "mbti",
                            ["unspecified"] + [a + b + c + d
                                for a in "IE" for b in "NS" for c in "FT" for d in "JP"],
                            index=0 if p.demographics.mbti == "unspecified"
                                  else (["unspecified"] + [a + b + c + d
                                      for a in "IE" for b in "NS" for c in "FT" for d in "JP"]).index(p.demographics.mbti),
                            key=f"pmbti_{pid}")
                        new_age = d_c2.selectbox(
                            "age_range",
                            ["unspecified", "<20", "20-29", "30-39", "40-49", "50+"],
                            index=["unspecified", "<20", "20-29", "30-39", "40-49", "50+"].index(p.demographics.age_range),
                            key=f"page_{pid}")
                        new_gender = d_c1.selectbox(
                            "gender",
                            ["unspecified", "male", "female"],
                            index=["unspecified", "male", "female"].index(p.demographics.gender),
                            key=f"pg_{pid}")
                        new_edu = d_c2.selectbox(
                            "education",
                            ["unspecified", "primary", "middle", "high",
                             "college", "postgrad"],
                            index=["unspecified", "primary", "middle", "high",
                                  "college", "postgrad"].index(p.demographics.education),
                            key=f"ped_{pid}")
                        new_att = d_c1.selectbox(
                            "attitude",
                            ["unspecified", "cooperative", "refuse", "hesitant",
                             "argumentative", "confused", "blunt", "hurried",
                             "adversarial"],
                            index=["unspecified", "cooperative", "refuse",
                                   "hesitant", "argumentative", "confused",
                                   "blunt", "hurried", "adversarial"].index(p.demographics.attitude),
                            key=f"pa_{pid}")
                        if refs:
                            st.caption(f"⚠ 改这个性格会影响 {len(refs)} 个 persona:"
                                       + ", ".join(refs[:5])
                                       + ("…" if len(refs) > 5 else ""))
                        if st.button("💾 保存", key=f"savep_{pid}"):
                            new_p = Personality(
                                id=pid, name=new_name,
                                description=new_desc,
                                speaking_style=new_style,
                                demographics=Demographics(
                                    mbti=new_mbti,
                                    age_range=new_age,
                                    gender=new_gender,
                                    education=new_edu,
                                    attitude=new_att,
                                ),
                            )
                            (PERSONALITIES_DIR / f"{pid}.yaml").write_text(
                                yaml.safe_dump(new_p.model_dump(),
                                                allow_unicode=True,
                                                sort_keys=False,
                                                default_flow_style=False),
                                encoding="utf-8")
                            st.success(f"✓ {pid} 已保存"); st.rerun()
                        if refs:
                            st.caption("(被引用,无法删除)")
                        else:
                            if st.button("🗑 删除", key=f"delp_{pid}"):
                                (PERSONALITIES_DIR / f"{pid}.yaml").unlink()
                                st.success(f"✓ 已删 {pid}"); st.rerun()
                except Exception as e:
                    st.error(f"{pid}:{e}")


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
