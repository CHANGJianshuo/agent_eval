"""Opt-in browser acceptance against a real API and isolated, offline model stubs.

Build web/dist first, then EVAL_BROWSER=1 pytest -q tests/test_browser_workflows.py.
"""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from test_run_integrity import project, _batch

pytestmark = pytest.mark.skipif(os.environ.get('EVAL_BROWSER') != '1', reason='Browser acceptance is opt-in')


@pytest.fixture
def browser_project(project, monkeypatch):
    from claw_eval import cli
    from claw_eval.api import app as api_app, routes_config, routes_meta_eval, routes_tasks, jobs
    from claw_eval.graders.llm_judge import JudgeResult
    from claw_eval.report import recommend
    from claw_eval.runner import llm_client
    from claw_eval.runner.trace_io import TraceWriter
    from claw_eval.task_gen import apply_recommendation
    from playwright.sync_api import sync_playwright
    import uvicorn

    root, td, _ = project
    dist = Path(__file__).resolve().parents[1] / 'web/dist'
    assert (dist / 'index.html').exists(), 'Run cd web && npm run build first'
    monkeypatch.setattr(api_app, '_find_repo_root', lambda: root)
    monkeypatch.setattr(api_app, '_find_web_dist', lambda: dist)
    monkeypatch.setattr(routes_tasks, 'TASKS_DIR', root / 'tasks')
    monkeypatch.setattr(routes_tasks, 'REPORTS_DIR', root / 'reports')
    monkeypatch.setattr(routes_config, 'ROOT', root)
    monkeypatch.setattr(routes_config, 'MODELS_FILE', root / 'configs/models.yaml')
    monkeypatch.setattr(routes_config, 'KEYS_FILE', root / 'keys.yaml')
    monkeypatch.setattr(routes_meta_eval, 'ROOT', root)

    def no_network(*a, **kw):
        raise AssertionError('Real model calls are forbidden in this acceptance test')
    monkeypatch.setattr(llm_client, 'chat', no_network)

    def dialogue(task, persona, *, trace_path, **kwargs):
        with TraceWriter(trace_path) as writer:
            writer.write({'event': 'dialogue_start', 'task_id': task.task_id})
            writer.write({'event': 'turn', 'turn': 1, 'role': 'assistant',
                          'text': 'Clear greeting' if 'Say hello clearly.' in task.prompt else 'Hello'})
            writer.write({'event': 'dialogue_end', 'end_reason': 'done'})
        return trace_path
    monkeypatch.setattr(cli, 'run_dialogue', dialogue)
    monkeypatch.setattr(cli, '_make_judge', lambda cfg: SimpleNamespace(
        evaluate=lambda check, conversation, **kw: JudgeResult(float('Clear greeting' in conversation), 'Greeting quality', 1)))
    (td / 'flow.yaml').write_text(yaml.safe_dump({'nodes': [{'id': 'greeting', 'label': '问候', 'rubric': 'quality'}], 'edges': []}))
    script = yaml.safe_load((td / 'personas/happy.yaml').read_text())
    script['covers_flow_nodes'] = ['greeting']
    (td / 'personas/happy.yaml').write_text(yaml.safe_dump(script))
    first = _batch('baseline', '--total', '3')
    assert first.exit_code == 0, first.output
    monkeypatch.setattr(recommend, 'generate_recommendation', lambda *a, **kw: {
        'suggested_prompt_change': 'Add a clear greeting', 'rationale': 'Evidence in turn 1'})
    monkeypatch.setattr(apply_recommendation, 'generate_prompt_patch', lambda prompt, *a: prompt + '\nSay hello clearly.')

    def process(job_id, command, **kwargs):
        result = CliRunner().invoke(cli.app, command[3:])
        jobs.update(job_id, log=result.output[-16000:])
        return subprocess.CompletedProcess(command, result.exit_code, result.output, '')
    monkeypatch.setattr(jobs, 'run_process', process)

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    base = f'http://127.0.0.1:{sock.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(api_app.create_app(), log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    try:
        with sync_playwright() as p:
            executable = os.environ.get('EVAL_CHROMIUM')
            browser = p.chromium.launch(headless=True, **({'executable_path': executable} if executable else {}))
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.set_default_timeout(15000)
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            # The report's CDN chart library is irrelevant to these application workflows.
            page.route('https://**/*', lambda route: route.abort())
            yield SimpleNamespace(page=page, base=base, root=root, td=td, errors=errors)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def test_browser_suggestion_to_fixed_regression(browser_project):
    from playwright.sync_api import expect
    b = browser_project
    p = b.page
    p.goto(b.base + '/tests/baseline')
    p.get_by_text('计划覆盖与实际证据', exact=True).click()
    expect(p.get_by_text('未知', exact=True)).to_be_visible()
    p.get_by_role('button', name='💡 本次建议', exact=True).click()
    p.get_by_role('button', name='生成 / 重试本次建议', exact=True).click()
    expect(p.get_by_text('建议已生成，修改效果需要回归验证', exact=True)).to_be_visible()
    p.get_by_role('button', name='查看证据对话', exact=True).first.click()
    expect(p.locator('p.whitespace-pre-wrap').get_by_text('Hello', exact=True)).to_be_visible()
    p.get_by_role('button', name='生成候选修改并预览差异', exact=True).click()
    expect(p.locator('pre').filter(has_text='+Say hello clearly.')).to_be_visible()
    p.get_by_role('button', name='采纳并保存新版本', exact=True).click()
    expect(p.get_by_text(re.compile('已保存版本：'))).to_be_visible()
    p.get_by_role('button', name='用固定基准复测当前 Prompt', exact=True).click()
    p.wait_for_url(re.compile('/tests/candidate_'))
    candidate = p.url.rsplit('/', 1)[1]
    p.get_by_role('button', name='🔄 对比其他测试', exact=True).click()
    expect(p.get_by_text('回归检查通过', exact=True)).to_be_visible()
    report = p.request.get(b.base + '/api/regression', params={'old': 'baseline', 'new': candidate}).json()
    assert report['new_pass_rate'] == 1 and report['old_pass_rate'] == 0
    assert (b.root / f'traces/{candidate}/cases.json').read_bytes() == (b.root / 'traces/baseline/cases.json').read_bytes()
    # Only report iframes may lack their explicitly blocked CDN dependency.
    assert not [e for e in b.errors if 'echarts is not defined' not in e]


def test_browser_config_versions_noise_scripts_and_blind_review(browser_project):
    from playwright.sync_api import expect
    b = browser_project
    p = b.page
    p.goto(b.base + '/tasks/demo')
    p.get_by_text('⚙️ 任务级配置(Prompt / Rubrics)', exact=True).click()
    p.get_by_label('任务 Prompt', exact=True).fill('Hello {customer}')
    p.get_by_label('新变量名', exact=True).fill('customer')
    p.get_by_role('button', name='添加变量', exact=True).click()
    p.get_by_label('变量 customer', exact=True).fill('张先生')
    with p.expect_response(lambda r: r.request.method == 'PUT' and r.url.endswith('/configuration')) as response:
        p.get_by_role('button', name='保存并创建版本', exact=True).click()
    assert response.value.status == 200
    expect(p.get_by_role('button', name='保存并创建版本', exact=True)).to_be_disabled()
    p.get_by_role('button', name='版本与恢复', exact=True).click()
    versions = p.request.get(b.base + '/api/tasks/demo/versions').json()['versions']
    p.get_by_label('历史版本', exact=True).select_option(versions[0]['label'])
    expect(p.locator('pre').filter(has_text='customer')).to_be_visible()
    with p.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/restore')) as restored:
        p.get_by_role('button', name='恢复此版本并备份当前配置', exact=True).click()
    assert restored.value.status == 200
    p.get_by_role('button', name='Prompt 与变量', exact=True).click()
    expect(p.get_by_label('任务 Prompt', exact=True)).to_have_value('Original prompt')
    p.get_by_role('button', name='剧本与探针', exact=True).click()
    p.get_by_label('配置文件', exact=True).select_option('personas/happy.yaml')
    p.get_by_label('场景', exact=True).fill('Finish after asking about the greeting')
    with p.expect_response(lambda r: r.request.method == 'PUT' and r.url.endswith('/configuration')) as edited:
        p.get_by_role('button', name='校验并保存版本', exact=True).click()
    assert edited.value.status == 200
    p.goto(b.base + '/settings')
    p.get_by_role('button', name='📚 噪音库', exact=True).click()
    p.get_by_label('新噪音 ID', exact=True).fill('filler')
    p.get_by_role('button', name='添加噪音种类', exact=True).click()
    p.get_by_label('噪音 filler 名称', exact=True).fill('口头语')
    p.get_by_label('噪音 filler 指令', exact=True).fill('使用少量口头语')
    with p.expect_response(lambda r: r.request.method == 'PUT' and r.url.endswith('/noise')) as noise:
        p.get_by_role('button', name='保存噪音库', exact=True).click()
    assert noise.value.status == 200

    p.goto(b.base + '/tasks/demo/meta-eval')
    p.get_by_label('标注者标识', exact=True).fill('alice')
    p.get_by_label('抽样来源', exact=True).select_option('baseline')
    p.get_by_label('抽样数', exact=True).fill('1')
    p.get_by_role('button', name='创建新批次', exact=True).click()
    p.get_by_role('button', name=re.compile('quality.*happy')).click()
    expect(p.get_by_text('Quality', exact=True)).to_be_visible()
    expect(p.get_by_text(re.compile('^Judge：'))).to_have_count(0)
    score = p.get_by_label('人工分', exact=True)
    score.fill('2')
    expect(p.get_by_role('button', name='提交人工分', exact=True)).to_be_disabled()
    score.fill('0')
    p.get_by_role('button', name='提交人工分', exact=True).click()
    expect(p.get_by_text(re.compile('^Judge：'))).to_be_visible()
    expect(p.get_by_text('本批次校准统计', exact=True)).to_be_visible()
    expect(p.get_by_text(re.compile('独立样本不足 20 个'))).to_be_visible()
    p.get_by_label('标注者标识', exact=True).fill('bob')
    expect(p.get_by_text(re.compile('^Judge：'))).to_have_count(0)
    expect(p.get_by_text('本批次校准统计', exact=True)).to_have_count(0)
    score.fill('1')
    p.get_by_role('button', name='提交人工分', exact=True).click()
    expect(p.get_by_text(re.compile('标注者之间的分歧'))).to_be_visible()
    assert not b.errors
