#!/usr/bin/env python3
"""Record and assemble a concise end-to-end Tel Agent Eval demo.

The expensive generation/evaluation work runs while no browser page is being
recorded.  The resulting edit therefore preserves the workflow without long
loading screens.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np
from playwright.sync_api import Browser, Locator, Page, Playwright, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "demo"
RAW_DIR = DEMO_DIR / "raw"
BUILD_DIR = DEMO_DIR / "build"
STATE_FILE = BUILD_DIR / "demo_state.json"
PROMPT_FILE = DEMO_DIR / "demo_prompt.md"

BASE_URL = os.environ.get("TEL_EVAL_URL", "http://127.0.0.1:8001").rstrip("/")
TASK_ID = os.environ.get("TEL_EVAL_DEMO_TASK", "rider_delivery_demo_0904")
TEST_ID = os.environ.get("TEL_EVAL_DEMO_TEST", "demo_eval_v2_0904")
VIEWPORT = {"width": 1440, "height": 900}

CHROMIUM_CANDIDATES = [
    Path("/home/chang/.cache/ms-playwright/chromium-1234/chrome-linux64/chrome"),
    Path("/home/chang/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome"),
]
FFMPEG_CANDIDATES = [Path("/home/chang/bin/ffmpeg"), Path("/usr/bin/ffmpeg")]
FFPROBE_CANDIDATES = [Path("/home/chang/bin/ffprobe"), Path("/usr/bin/ffprobe")]


def log(message: str) -> None:
    print(f"[demo] {message}", flush=True)


def api(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    body = None
    headers: dict[str, str] = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{BASE_URL}/api{path}", data=body, method=method, headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"API {method} {path} failed ({exc.code}): {detail}") from exc


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def wait_for_job(path: str, label: str, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    last_status = ""
    last_heartbeat = 0.0
    while time.monotonic() - started < timeout:
        data = api("GET", path)
        status = data.get("status", "unknown")
        elapsed = int(time.monotonic() - started)
        if status != last_status or elapsed - last_heartbeat >= 20:
            step = data.get("step_label") or ""
            log(f"{label}: {status} {step} ({elapsed}s)")
            last_status = status
            last_heartbeat = elapsed
        if status == "done":
            return data
        if status == "failed":
            raise RuntimeError(f"{label} failed: {data.get('message', '')[-1200:]}")
        time.sleep(5)
    raise TimeoutError(f"{label} did not finish within {timeout}s")


def locate_binary(candidates: list[Path], fallback: str) -> str:
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    found = shutil.which(fallback)
    if not found:
        raise FileNotFoundError(f"Missing required executable: {fallback}")
    return found


class Recorder:
    def __init__(self, playwright: Playwright):
        executable = locate_binary(CHROMIUM_CANDIDATES, "chromium")
        self.browser: Browser = playwright.chromium.launch(
            headless=True,
            executable_path=executable,
            args=["--disable-dev-shm-usage", "--font-render-hinting=none"],
        )
        self.segments: list[Path] = []

    def close(self) -> None:
        self.browser.close()

    @contextmanager
    def segment(self, name: str) -> Iterator[Page]:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        context = self.browser.new_context(
            viewport=VIEWPORT,
            record_video_dir=str(RAW_DIR),
            record_video_size=VIEWPORT,
            color_scheme="light",
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = context.new_page()
        page.set_default_timeout(20_000)
        target = RAW_DIR / f"{name}.webm"
        try:
            yield page
        finally:
            page.wait_for_timeout(350)
            video = page.video
            context.close()
            if video is None:
                raise RuntimeError(f"No video was captured for {name}")
            if target.exists():
                target.unlink()
            video.save_as(str(target))
            self.segments.append(target)
            log(f"saved {target.relative_to(ROOT)}")

    @staticmethod
    def goto(page: Page, path: str) -> None:
        page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        Recorder.decorate(page)

    @staticmethod
    def decorate(page: Page) -> None:
        page.evaluate(
            """
            () => {
              if (!document.getElementById('__demo_style')) {
                const style = document.createElement('style');
                style.id = '__demo_style';
                style.textContent = `
                  #__demo_caption {
                    position: fixed; left: 50%; bottom: 20px;
                    transform: translateX(-50%); z-index: 2147483646;
                    max-width: 1050px; padding: 10px 22px;
                    color: white; background: rgba(18, 18, 20, .86);
                    border: 1px solid rgba(255,255,255,.14);
                    border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.22);
                    font: 600 20px/1.45 'Microsoft YaHei','PingFang SC',sans-serif;
                    letter-spacing: .02em; text-align: center;
                    pointer-events: none; opacity: 0;
                    transition: opacity .18s ease;
                  }
                  #__demo_cursor {
                    position: fixed; left: 80px; top: 80px; width: 18px; height: 18px;
                    margin: -9px 0 0 -9px; z-index: 2147483647;
                    border: 2px solid white; border-radius: 999px;
                    background: rgba(24,24,27,.82); box-shadow: 0 1px 5px rgba(0,0,0,.5);
                    pointer-events: none; transition: transform .08s ease;
                  }
                  #__demo_title {
                    position: fixed; inset: 0; z-index: 2147483645;
                    display: flex; flex-direction: column; align-items: center;
                    justify-content: center; color: #fafafa;
                    background: linear-gradient(135deg, rgba(9,9,11,.97), rgba(39,39,42,.95));
                    font-family: 'Microsoft YaHei','PingFang SC',sans-serif;
                    pointer-events: none;
                  }
                `;
                document.head.appendChild(style);
              }
              if (!document.getElementById('__demo_caption')) {
                const caption = document.createElement('div');
                caption.id = '__demo_caption';
                document.body.appendChild(caption);
              }
              if (!document.getElementById('__demo_cursor')) {
                const cursor = document.createElement('div');
                cursor.id = '__demo_cursor';
                document.body.appendChild(cursor);
                document.addEventListener('mousemove', event => {
                  cursor.style.left = `${event.clientX}px`;
                  cursor.style.top = `${event.clientY}px`;
                }, { passive: true });
                document.addEventListener('mousedown', () => {
                  cursor.style.transform = 'scale(.72)';
                });
                document.addEventListener('mouseup', () => {
                  cursor.style.transform = 'scale(1)';
                });
              }
            }
            """
        )

    @staticmethod
    def caption(page: Page, text: str, hold_ms: int = 900) -> None:
        Recorder.decorate(page)
        page.evaluate(
            """text => {
              const el = document.getElementById('__demo_caption');
              el.textContent = text;
              el.style.opacity = text ? '1' : '0';
            }""",
            text,
        )
        page.wait_for_timeout(hold_ms)

    @staticmethod
    def title(page: Page, title: str, subtitle: str, hold_ms: int = 2600) -> None:
        Recorder.decorate(page)
        page.evaluate(
            """([title, subtitle]) => {
              const old = document.getElementById('__demo_title');
              if (old) old.remove();
              const el = document.createElement('div');
              el.id = '__demo_title';
              el.innerHTML = `
                <div style="font-size:42px;font-weight:750;letter-spacing:.02em">${title}</div>
                <div style="margin-top:14px;font-size:21px;color:#d4d4d8">${subtitle}</div>
                <div style="margin-top:28px;width:64px;height:3px;border-radius:9px;background:#f4f4f5"></div>`;
              document.body.appendChild(el);
            }""",
            [title, subtitle],
        )
        page.wait_for_timeout(hold_ms)
        page.evaluate("document.getElementById('__demo_title')?.remove()")
        page.wait_for_timeout(450)

    @staticmethod
    def click(page: Page, locator: Locator, pause_ms: int = 650) -> None:
        locator.wait_for(state="visible")
        locator.scroll_into_view_if_needed()
        page.wait_for_timeout(250)
        box = locator.bounding_box()
        if box:
            page.mouse.move(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
                steps=12,
            )
            page.wait_for_timeout(180)
        locator.click()
        page.wait_for_timeout(pause_ms)

    @staticmethod
    def scroll_page(page: Page, target: float | str, duration_ms: int = 2600) -> None:
        Recorder.decorate(page)
        page.mouse.move(VIEWPORT["width"] - 9, VIEWPORT["height"] / 2, steps=14)
        start = page.evaluate("window.scrollY")
        end = page.evaluate(
            "Math.max(0, document.documentElement.scrollHeight - window.innerHeight)"
        ) if target == "bottom" else float(target)
        steps = max(6, duration_ms // 180)
        for index in range(1, steps + 1):
            eased = (1 - math.cos(math.pi * index / steps)) / 2
            page.evaluate("y => window.scrollTo(0, y)", start + (end - start) * eased)
            page.wait_for_timeout(max(60, duration_ms // steps))

    @staticmethod
    def scroll_element(page: Page, locator: Locator, to_bottom: bool = True,
                       duration_ms: int = 1900) -> None:
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if box:
            page.mouse.move(box["x"] + box["width"] - 7, box["y"] + box["height"] / 2, steps=10)
        start = locator.evaluate("el => el.scrollTop")
        end = locator.evaluate("el => Math.max(0, el.scrollHeight - el.clientHeight)") if to_bottom else 0
        steps = max(5, duration_ms // 160)
        for index in range(1, steps + 1):
            eased = (1 - math.cos(math.pi * index / steps)) / 2
            locator.evaluate("(el, y) => { el.scrollTop = y; }", start + (end - start) * eased)
            page.wait_for_timeout(max(60, duration_ms // steps))


def task_exists() -> bool:
    return any(row.get("task_id") == TASK_ID for row in api("GET", "/tasks"))


def test_exists() -> bool:
    try:
        return api("GET", f"/tests/{TEST_ID}") is not None
    except RuntimeError as exc:
        if "(404)" in str(exc):
            return False
        raise


def record_create(recorder: Recorder, state: dict[str, Any]) -> None:
    if task_exists():
        raise RuntimeError(
            f"Task {TASK_ID} already exists; refusing to overwrite an existing task."
        )
    prompt = PROMPT_FILE.read_text(encoding="utf-8")
    with recorder.segment("01_create_task") as page:
        recorder.goto(page, "/")
        page.get_by_role("heading", name="任务").wait_for(state="visible")
        recorder.title(page, "Tel Agent Eval", "智能外呼 Agent 评测 · 全流程演示")
        recorder.caption(page, "从任务配置、自动评测，到报告与优化建议，一条链路完成。", 1700)

        recorder.click(page, page.get_by_role("button", name="新建任务"))
        recorder.caption(page, "新建任务：复用之前验证过的“飞毛腿骑手履约提醒” Prompt。", 1100)
        textarea = page.locator("textarea").first
        textarea.fill(prompt)
        page.wait_for_timeout(1200)
        recorder.scroll_element(page, textarea, to_bottom=True, duration_ms=1700)
        recorder.caption(page, "Prompt 明确业务变量、对话流程、FAQ，以及每轮 50 字上限。", 1600)

        with page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.rstrip("/").endswith("/api/tasks"),
            timeout=30_000,
        ) as response_info:
            recorder.click(page, page.get_by_role("button", name="一键生成"), pause_ms=200)
        response = response_info.value
        result = response.json()
        if not response.ok:
            raise RuntimeError(f"Task creation failed: {result}")
        state["task_job_id"] = result["job_id"]
        save_state(state)
        page.get_by_text("生成中", exact=False).first.wait_for(state="visible")
        recorder.caption(page, "一键生成任务配置。耗时步骤在后台执行，成片直接切到完成状态。", 2400)

    wait_for_job(f"/jobs/{state['task_job_id']}", "task generation", timeout=1200)


def record_task_review(recorder: Recorder, state: dict[str, Any]) -> None:
    scripts = api("GET", f"/tasks/{TASK_ID}/scripts").get("scripts", [])
    review = api("GET", f"/tasks/{TASK_ID}/review-status")
    log(f"generated task has {len(scripts)} scripts; review={review}")

    with recorder.segment("02_review_task") as page:
        recorder.goto(page, f"/tasks/{TASK_ID}")
        page.get_by_role("heading", name=TASK_ID).wait_for(state="visible")
        recorder.caption(page, "后台生成完成：Prompt、流程、Rubrics、剧本和评分器均已就绪。", 1800)

        flow_summary = page.locator("summary").filter(has_text="对话流程图").first
        recorder.scroll_page(page, max(0, flow_summary.evaluate("el => el.getBoundingClientRect().top + window.scrollY - 90")), 1200)
        recorder.caption(page, "先展开流程图，核对关键分支和结束条件。", 700)
        flow_details = flow_summary.locator("xpath=..")
        if flow_details.get_attribute("open") is not None:
            recorder.click(page, flow_summary, 350)
        recorder.click(page, flow_summary, 1500)

        script_summary = page.locator("summary").filter(has_text="模拟用户剧本").first
        recorder.click(page, script_summary, 350)
        recorder.click(page, script_summary, 700)
        recorder.caption(page, "逐个打开剧本，检查场景、覆盖路径、探针和最大对话轮数。", 700)
        for script in scripts:
            button = page.locator("button").filter(has_text=script["id"]).first
            if button.count():
                recorder.click(page, button, 520)

        config_summary = page.locator("summary").filter(has_text="任务级配置").first
        recorder.click(page, config_summary, 800)
        recorder.caption(page, "任务级配置保留原始 Prompt、已抽取业务变量和可编辑 Rubrics。", 900)
        prompt_area = page.locator("textarea").first
        prompt_area.wait_for(state="visible")
        recorder.scroll_element(page, prompt_area, to_bottom=True, duration_ms=1600)
        page.wait_for_timeout(600)

        recorder.click(page, page.get_by_role("button", name="📐 Rubrics"), 900)
        recorder.caption(page, "Rubric 表展示维度、评分方法、权重、安全项与检查标准。", 900)
        recorder.scroll_page(page, "bottom", 2600)

        recorder.scroll_page(page, 0, 1800)
        recorder.click(page, page.get_by_role("button", name="AI 修改助手"), 700)
        recorder.caption(page, "右侧 AI 修改助手支持用自然语言调整 Prompt、Rubrics 与测试剧本。", 1400)
        panel = page.locator("div.fixed.inset-y-0.right-0")
        panel.wait_for(state="visible")
        page.mouse.move(VIEWPORT["width"] - 8, VIEWPORT["height"] / 2, steps=14)
        page.mouse.wheel(0, 520)
        page.wait_for_timeout(900)
        recorder.click(page, panel.locator("button").first, 700)

        approve = page.get_by_role("button", name="确认已审核并转正")
        if approve.count():
            recorder.caption(page, "人工确认生成物后，再将草稿转为正式评测配置。", 700)
            page.once("dialog", lambda dialog: dialog.accept())
            recorder.click(page, approve, 300)
            approve.wait_for(state="hidden")
            page.wait_for_timeout(800)
        recorder.caption(page, "审核完成，任务现在可以启动正式评测。", 1600)

    state["reviewed"] = True
    save_state(state)


def configure_personas(page: Page, recorder: Recorder) -> None:
    total_input = page.locator('input[type="number"]').first
    total_input.fill("5")
    auto_rec = page.locator("label").filter(has_text="自动出建议").locator('input[type="checkbox"]')
    auto_rec.check()

    dimension_blocks = page.locator("div.border.rounded-lg.transition-colors")
    attitude = dimension_blocks.filter(has_text="性格").first
    attitude.scroll_into_view_if_needed()
    attitude_checkboxes = attitude.locator('input[type="checkbox"]')
    if attitude_checkboxes.count() >= 3:
        attitude_checkboxes.nth(1).uncheck()
        attitude_checkboxes.nth(2).uncheck()
    attitude.locator('input[type="number"]').first.fill("100")

    attitude_heading = attitude.get_by_role("button", name="性格", exact=False)
    recorder.click(page, attitude_heading, 300)
    recorder.click(page, attitude_heading, 500)

    gender = dimension_blocks.filter(has_text="性别").first
    recorder.click(page, gender.locator("button").first, 450)
    gender_boxes = gender.locator('input[type="checkbox"]')
    if gender_boxes.count() >= 2:
        gender_boxes.nth(0).check()
        gender_boxes.nth(1).check()
    page.wait_for_timeout(700)


def record_new_test(recorder: Recorder, state: dict[str, Any]) -> None:
    if test_exists():
        raise RuntimeError(
            f"Test {TEST_ID} already exists; refusing to overwrite an existing evaluation."
        )
    with recorder.segment("03_create_evaluation") as page:
        recorder.goto(page, f"/tasks/{TASK_ID}")
        recorder.click(page, page.get_by_role("button", name="新建测试"), 800)
        recorder.caption(page, "新建评测：设置唯一测试号、样本数和自动改进建议。", 900)

        test_id_input = page.get_by_text("测试号", exact=True).locator("xpath=following::input[1]")
        test_id_input.fill(TEST_ID)
        configure_personas(page, recorder)
        recorder.caption(page, "展开 Persona 维度：本次以合作型用户为主，并覆盖不同性别。", 1200)

        preview = page.get_by_role("button", name="预览生成", exact=False)
        with page.expect_response(
            lambda response: "/preview-personas" in response.url,
            timeout=20_000,
        ):
            recorder.click(page, preview, 300)
        page.get_by_text("前 10 个样本", exact=True).wait_for(state="visible")
        recorder.caption(page, "先预览实际采样分布，确认权重和样本组合符合预期。", 1100)
        recorder.scroll_page(page, "bottom", 2300)

        with page.expect_response(
            lambda response: response.request.method == "POST"
            and response.url.rstrip("/").endswith(f"/api/tasks/{TASK_ID}/tests"),
            timeout=30_000,
        ) as response_info:
            recorder.click(page, page.get_by_role("button", name="启动测试"), 300)
        result = response_info.value.json()
        if not response_info.value.ok:
            raise RuntimeError(f"Evaluation start failed: {result}")
        state["test_job_id"] = result["job_id"]
        save_state(state)
        page.get_by_text("测试跑批中", exact=False).wait_for(state="visible")
        recorder.decorate(page)
        recorder.caption(page, "模拟用户对话、自动评分和建议生成在后台执行；这里直接跳过等待。", 2200)

    wait_for_job(
        f"/jobs/test/{state['test_job_id']}", "evaluation", timeout=2400,
    )
    result = api("GET", f"/tests/{TEST_ID}")
    state["test_result"] = result
    save_state(state)
    log(
        f"evaluation result: status={result.get('status')} "
        f"pass_rate={result.get('pass_rate')} score={result.get('task_score_avg')}"
    )


def rebuild_report_after_recommendations() -> str:
    report_dir = ROOT / "reports" / TEST_ID
    prior_mtime = max(
        (path.stat().st_mtime for path in report_dir.glob("task_*.html")),
        default=0.0,
    )
    api("POST", f"/tests/{TEST_ID}/report")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        pages = sorted(report_dir.glob("task_*.html"))
        if pages and max(path.stat().st_mtime for path in pages) > prior_mtime:
            status = api("GET", f"/tests/{TEST_ID}/report-status")
            return status["url"]
        time.sleep(1)
    status = api("GET", f"/tests/{TEST_ID}/report-status")
    if status.get("exists"):
        return status["url"]
    raise TimeoutError("Report regeneration did not produce an HTML report")


def open_all_details(page: Page, recorder: Recorder, limit: int | None = None) -> None:
    summaries = page.locator("details:not([open]) > summary")
    count = summaries.count()
    if limit is not None:
        count = min(count, limit)
    for _ in range(count):
        current = page.locator("details:not([open]) > summary").first
        if not current.count():
            break
        recorder.click(page, current, 420)


def record_results_and_report(recorder: Recorder, state: dict[str, Any]) -> None:
    report_url = rebuild_report_after_recommendations()
    state["report_url"] = report_url
    save_state(state)
    recommendations = api("GET", f"/tasks/{TASK_ID}/recommendations")
    log(f"recommendations available: {len(recommendations.get('recommendations', []))}")

    with recorder.segment("04_results_report") as page:
        recorder.goto(page, f"/tests/{TEST_ID}")
        page.get_by_role("heading", name=TEST_ID).wait_for(state="visible")
        recorder.caption(page, "评测完成：状态、样本数、通过率和平均得分集中展示。", 1700)
        recorder.scroll_page(page, 480, 1500)
        recorder.caption(page, "流程图与 Persona 热力表帮助定位具体场景和用户类型。", 1300)

        params = page.locator("summary").filter(has_text="测试参数").first
        recorder.click(page, params, 950)
        recorder.caption(page, "展开只读测试参数，可追溯本次样本量、维度权重和评分设置。", 1200)

        rec_tab = page.get_by_role("button", name="💡 建议 + 自动应用")
        recorder.click(page, rec_tab, 1100)
        recorder.caption(page, "建议页按严重度列出薄弱 Rubric、修改方向、理由和预期收益。", 1100)
        recorder.scroll_page(page, "bottom", 2600)

        compare_tab = page.get_by_role("button", name="🔄 对比其他测试")
        recorder.click(page, compare_tab, 850)
        recorder.caption(page, "选择上一轮评测作为基线，为后续 Prompt 回归验证做好准备。", 1100)

        report_tab = page.get_by_role("button", name="📊 报告")
        recorder.click(page, report_tab, 800)
        iframe = page.locator('iframe[title="dashboard"]')
        iframe.wait_for(state="visible")
        recorder.caption(page, "HTML 报告已在后台生成完成，成片中不保留生成等待。", 1200)
        full_link = page.get_by_text("全屏新窗口 ↗", exact=True)
        full_link.evaluate("el => el.setAttribute('target', '_self')")
        recorder.click(page, full_link, 300)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(700)
        recorder.decorate(page)

        recorder.caption(page, "报告总览汇总运行数、通过率、完成度、鲁棒性和优化建议。", 1100)
        open_all_details(page, recorder)
        recorder.scroll_page(page, page.evaluate("document.documentElement.scrollHeight * 0.34"), 2600)
        recorder.caption(page, "继续拖动右侧滚动条，查看各维度、剧本与 Persona 的分项表现。", 1100)
        recorder.scroll_page(page, page.evaluate("document.documentElement.scrollHeight * 0.68"), 2600)
        recorder.caption(page, "Rubric 表保留触发次数、平均分和低分证据，便于精准定位问题。", 1100)

        case_link = page.get_by_text("单 case →", exact=True).first
        case_link.scroll_into_view_if_needed()
        page.wait_for_timeout(700)
        recorder.caption(page, "报告底部列出全部 case；进入一条明细查看完整对话和逐项评分。", 1000)
        recorder.click(page, case_link, 300)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(600)
        recorder.decorate(page)
        recorder.caption(page, "单 case 报告展示 Persona、对话回放、总分与每条 Rubric 的证据。", 1000)

        open_all_details(page, recorder)
        recorder.scroll_page(page, page.evaluate("document.documentElement.scrollHeight * 0.48"), 2500)
        recorder.caption(page, "所有折叠框均已打开，包括报告解读与未触发评分项。", 1000)
        recorder.scroll_page(page, "bottom", 3000)
        recorder.caption(page, "右侧滚动条到达底部：本条对话的全部内容已经展示。", 1500)


def record_meta_eval(recorder: Recorder, state: dict[str, Any]) -> None:
    samples_data = api("GET", f"/tasks/{TASK_ID}/meta-eval/samples")

    def trace_quality(sample: dict[str, Any]) -> tuple[int, int]:
        """Prefer a concise, legible conversation for the short demo shot."""
        trace_path = ROOT / str(sample.get("trace_path", ""))
        lengths: list[int] = []
        try:
            for line in trace_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("event") == "turn":
                    lengths.append(len(str(event.get("text", ""))))
        except (OSError, json.JSONDecodeError):
            return (10**9, 10**9)
        return (max(lengths, default=0), sum(lengths))

    available_samples = samples_data.get("samples", [])
    preferred = min(available_samples, key=trace_quality) if available_samples else None
    with recorder.segment("05_meta_eval_outro") as page:
        recorder.goto(page, f"/tasks/{TASK_ID}/meta-eval")
        page.get_by_role("heading", name="Meta-Eval 人工校准").wait_for(state="visible")
        recorder.caption(page, "最后用 Meta-Eval 抽样复核自动评分，验证评测系统本身是否可信。", 1400)
        sample_input = page.locator('input[type="number"]').first
        sample_input.fill("5")
        sample_button = page.get_by_role("button", name="抽样", exact=True)
        if sample_button.count():
            with page.expect_response(
                lambda response: "/meta-eval/sample" in response.url,
                timeout=20_000,
            ):
                recorder.click(page, sample_button, 300)
            page.get_by_text("标注任务", exact=False).first.wait_for(state="visible")
        recorder.caption(page, "分层抽样覆盖不同 Rubric 与高低分样本，形成待人工复核列表。", 1200)

        row = (
            page.locator("button").filter(has_text=preferred["rubric_id"]).first
            if preferred
            else page.locator("h2", has_text="标注任务").locator("xpath=following::button").first
        )
        if row.count():
            recorder.click(page, row, 1000)
            recorder.caption(page, "展开样本即可回放对话，并选择同意 Judge 或录入人工分。", 1200)
            conversation = page.locator("div.max-h-72.overflow-y-auto").first
            if conversation.count():
                recorder.scroll_element(page, conversation, to_bottom=True, duration_ms=1600)

        recorder.scroll_page(page, "bottom", 1800)
        recorder.caption(page, "至此，新建任务、审核、评测、报告、建议与人工校准全部走通。", 1600)
        recorder.title(page, "全流程演示完成", "Tel Agent Eval · 可追溯、可诊断、可持续优化", 2800)

    state["recording_complete"] = True
    save_state(state)


def write_concat_manifest(segments: list[Path]) -> Path:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    manifest = BUILD_DIR / "segments.ffconcat"
    lines = ["ffconcat version 1.0"]
    for segment in segments:
        escaped = str(segment.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def synthesize_music(path: Path) -> None:
    """Create an original 32-second ambient loop (four softly cross-faded chords)."""
    sample_rate = 48_000
    duration = 32.0
    n = int(sample_rate * duration)
    t = np.arange(n, dtype=np.float64) / sample_rate
    audio = np.zeros(n, dtype=np.float64)
    chords = [
        (130.81, 164.81, 196.00, 246.94),  # Cmaj7
        (110.00, 130.81, 164.81, 196.00),  # Am7
        (87.31, 130.81, 164.81, 220.00),   # Fmaj7
        (98.00, 146.83, 196.00, 220.00),   # Gsus2
    ]
    chord_len = 8.0
    fade = 1.5
    for chord_index, chord in enumerate(chords):
        start_time = chord_index * chord_len
        local = t - start_time
        active = (local >= 0) & (local < chord_len)
        envelope = np.zeros(n, dtype=np.float64)
        envelope[active] = 1.0
        attack = active & (local < fade)
        release = active & (local > chord_len - fade)
        envelope[attack] = 0.5 - 0.5 * np.cos(np.pi * local[attack] / fade)
        envelope[release] = 0.5 - 0.5 * np.cos(
            np.pi * (chord_len - local[release]) / fade
        )
        pad = np.zeros(n, dtype=np.float64)
        for frequency in chord:
            pad += np.sin(2 * np.pi * frequency * t)
            pad += 0.20 * np.sin(2 * np.pi * frequency * 2 * t)
        audio += 0.035 * envelope * pad / len(chord)

    melody = [261.63, 293.66, 329.63, 392.00, 329.63, 293.66, 246.94, 293.66]
    for note_index, frequency in enumerate(melody):
        start_time = note_index * 4.0 + 0.6
        local = t - start_time
        active = (local >= 0) & (local < 3.2)
        bell = np.zeros(n, dtype=np.float64)
        bell[active] = np.exp(-local[active] / 1.35)
        tone = (
            np.sin(2 * np.pi * frequency * t)
            + 0.28 * np.sin(2 * np.pi * frequency * 2 * t)
        )
        audio += 0.018 * bell * tone

    peak = max(float(np.max(np.abs(audio))), 1e-9)
    pcm = np.int16(np.clip(audio / peak * 0.32, -1, 1) * 32767)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(pcm.tobytes())


def probe_duration(path: Path, ffmpeg: str, ffprobe: str | None = None) -> float:
    if ffprobe:
        output = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            text=True,
        )
        return float(output.strip())

    # imageio-ffmpeg ships a standalone ffmpeg binary without ffprobe.
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise RuntimeError(f"Could not read video duration from {path}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def assemble() -> Path:
    segments = sorted(RAW_DIR.glob("[0-9][0-9]_*.webm"))
    if len(segments) < 5:
        raise RuntimeError(f"Expected five recorded segments, found {len(segments)}")
    ffmpeg = locate_binary(FFMPEG_CANDIDATES, "ffmpeg")
    ffprobe = next(
        (str(path) for path in FFPROBE_CANDIDATES if path.exists()),
        shutil.which("ffprobe"),
    )
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    manifest = write_concat_manifest(segments)
    silent = BUILD_DIR / "tel_agent_eval_demo_silent.mp4"
    music = BUILD_DIR / "original_calm_ambient.wav"
    final = DEMO_DIR / "tel_agent_eval_full_demo_zh.mp4"

    log("encoding recorded segments")
    subprocess.run(
        [
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-an", "-vf", "fps=30,format=yuv420p", "-c:v", "libx264",
            "-preset", "medium", "-crf", "19", "-movflags", "+faststart",
            str(silent),
        ],
        check=True,
    )
    synthesize_music(music)
    duration = probe_duration(silent, ffmpeg, ffprobe)
    fade_out = max(0.0, duration - 3.0)
    log(f"mixing original calm soundtrack ({duration:.1f}s)")
    subprocess.run(
        [
            ffmpeg, "-y", "-stream_loop", "-1", "-i", str(music), "-i", str(silent),
            "-filter_complex",
            f"[0:a]volume=0.23,afade=t=in:st=0:d=2,afade=t=out:st={fade_out:.3f}:d=3[a]",
            "-map", "1:v:0", "-map", "[a]", "-t", f"{duration:.3f}",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            str(final),
        ],
        check=True,
    )
    log(f"final video: {final} ({final.stat().st_size / 1024 / 1024:.1f} MiB)")
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=["all", "create", "review", "evaluation", "results", "meta", "assemble"],
        default="all",
    )
    args = parser.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()

    if args.phase == "assemble":
        assemble()
        return 0

    with sync_playwright() as playwright:
        recorder = Recorder(playwright)
        try:
            if args.phase in {"all", "create"}:
                record_create(recorder, state)
            if args.phase in {"all", "review"}:
                record_task_review(recorder, state)
            if args.phase in {"all", "evaluation"}:
                record_new_test(recorder, state)
            if args.phase in {"all", "results"}:
                record_results_and_report(recorder, state)
            if args.phase in {"all", "meta"}:
                record_meta_eval(recorder, state)
        finally:
            recorder.close()

    if args.phase == "all":
        assemble()
    return 0


if __name__ == "__main__":
    sys.exit(main())
