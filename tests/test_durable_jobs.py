"""Exercise real local worker lifecycle without any model/network requests."""
import importlib
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from claw_eval.api import jobs
from claw_eval.db import repo
from claw_eval.runner import llm_client


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, 'DEFAULT_DB', tmp_path / 'jobs.db')


def test_job_survives_module_reload_and_new_job_types_share_registry():
    jobs.create('generation', job_type='generate', task_id='t')
    jobs.create('evaluation', job_type='test', task_id='t')
    jobs.create('report', job_type='report', task_id='t')
    importlib.reload(jobs)
    assert {j['job_type'] for j in jobs.list_all()} == {'generate', 'test', 'report'}
    assert jobs.get('evaluation')['status'] == 'running'


def test_dead_owner_marks_job_and_run_interrupted_without_reexecution():
    repo.append_run('r', 't', {})
    jobs.create('test_r', test_id='r', job_type='test', owner_token='dead-owner-token')
    assert jobs.get('test_r')['status'] == 'interrupted'
    assert repo.get_run('r')['status'] == 'interrupted'
    jobs.update('test_r', status='done')
    assert jobs.get('test_r')['status'] == 'interrupted'


def test_multiple_writers_preserve_independent_progress_fields():
    jobs.create('shared')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: jobs.update('shared', **{f'field_{i}': i}), range(12)))
    saved = jobs.get('shared')
    assert all(saved[f'field_{i}'] == i for i in range(12))


def test_success_captures_exit_code_and_bounded_unicode_log(tmp_path):
    jobs.create('output')
    proc = jobs.run_process('output', [sys.executable, '-c', "print('内容' * 20000); print('TAIL')"],
                            env=dict(os.environ), cwd=str(tmp_path))
    assert proc.returncode == 0
    assert proc.stdout.endswith('TAIL\n')
    assert len(proc.stdout.encode('utf-8')) <= 16004
    assert jobs.get('output')['log'] == proc.stdout


def test_cancel_stops_subprocess_and_keeps_terminal_status(tmp_path):
    jobs.create('cancel')
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(jobs.run_process, 'cancel', [sys.executable, '-c', 'import time; time.sleep(30)'],
                             env=dict(os.environ), cwd=str(tmp_path))
        deadline = time.monotonic() + 5
        while not jobs.get('cancel').get('worker_pid') and time.monotonic() < deadline:
            time.sleep(.02)
        pid = jobs.get('cancel')['worker_pid']
        jobs.update('cancel', status='canceling')
        with pytest.raises(RuntimeError, match='取消'):
            future.result(timeout=8)
    assert jobs.get('cancel')['status'] == 'canceled'
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    jobs.update('cancel', status='failed')
    assert jobs.get('cancel')['status'] == 'canceled'


def test_subprocess_timeout_does_not_leave_worker_running(tmp_path):
    jobs.create('timeout')
    with pytest.raises(TimeoutError):
        jobs.run_process('timeout', [sys.executable, '-c', 'import time; time.sleep(30)'],
                         env=dict(os.environ), cwd=str(tmp_path), timeout=.1)
    pid = jobs.get('timeout')['worker_pid']
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize('status,expected_calls', [(401, 1), (400, 1), (429, 3), (503, 3)])
def test_llm_retries_only_transient_errors(monkeypatch, status, expected_calls):
    calls = []
    sleeps = []
    class ProviderError(Exception):
        status_code = status
    def completion(**kwargs):
        calls.append(kwargs)
        raise ProviderError('offline failure')
    monkeypatch.setitem(sys.modules, 'litellm', SimpleNamespace(completion=completion))
    monkeypatch.setattr(llm_client.time, 'sleep', sleeps.append)
    with pytest.raises(RuntimeError):
        llm_client.chat('test', [], max_retries=3)
    assert len(calls) == expected_calls
    assert len(sleeps) == expected_calls - 1
    assert all(c['timeout'] == 120 and c['num_retries'] == 0 for c in calls)


def test_empty_model_response_is_not_a_successful_dialogue(monkeypatch):
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=''))])
    monkeypatch.setitem(sys.modules, 'litellm', SimpleNamespace(completion=lambda **kwargs: response))
    with pytest.raises(RuntimeError, match='空文本'):
        llm_client.chat('test', [])
