"""Recommendation review and fixed-benchmark evaluation workflows."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from . import jobs, routes_tests as tests, routes_tasks as tasks
from ..runs import atomic_json, load_manifest, prepare_candidate, validate_id
from ..task_gen.config_store import revision

router = APIRouter()


def _new_job(test_id: str, kind: str) -> str:
    run = tests._require_run(test_id)
    # A per-run file lock also coordinates separate API processes.
    from ..task_gen.config_store import task_lock
    directory = tests.ROOT / 'traces' / test_id
    with task_lock(directory):
        if any(j.get('test_id') == test_id and j['job_type'] == kind and j['status'] in jobs.ACTIVE for j in jobs.list_all()):
            raise HTTPException(409, '同类操作正在执行，请等待当前操作完成')
        key = f'{kind}_{uuid4().hex}'
        jobs.create(key, task_id=run['task_id'], test_id=test_id, job_type=kind)
    return key


def _process(job_id: str, command: list[str]):
    try:
        proc = jobs.run_process(job_id, command, env={**os.environ, 'PYTHONPATH': str(tests.ROOT / 'src')},
                                cwd=str(tests.ROOT), timeout=1800)
        jobs.update(job_id, status='done' if proc.returncode == 0 else 'failed')
    except Exception as exc:
        jobs.update(job_id, status='failed', log=str(exc))


@router.post('/tests/{test_id}/recommendations')
def generate_recommendations(test_id: str, background: BackgroundTasks):
    run = tests._require_run(test_id)
    try:
        load_manifest(tests.ROOT, test_id)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    key = _new_job(test_id, 'recommend')
    background.add_task(_process, key, [sys.executable, '-m', 'claw_eval.cli', 'recommend',
                                       '--task', run['task_id'], '--run-id', test_id])
    return {'job_id': key, 'status': 'running'}


def _candidate_file(test_id: str, candidate_id: str) -> Path:
    try:
        validate_id(candidate_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    tests._require_run(test_id)
    return tests.REPORTS_DIR / test_id / 'candidates' / f'{candidate_id}.json'


@router.post('/tests/{test_id}/recommendations/{rubric_id}/patch')
def draft_patch(test_id: str, rubric_id: str, background: BackgroundTasks):
    run = tests._require_run(test_id)
    data = tests.get_test_recommendations(test_id)
    rec = next((r for r in data['recommendations'] if r['rubric_id'] == rubric_id), None)
    if not rec or not rec.get('suggested_prompt_change'):
        raise HTTPException(422, '没有可采纳的建议，请先成功生成建议')
    from ..models.task import TaskDefinition
    td = tasks._existing_task(run['task_id'])
    current = TaskDefinition.from_yaml(td / 'task.yaml')
    manifest = load_manifest(tests.ROOT, test_id)
    source = TaskDefinition.from_yaml(tests.ROOT / 'traces' / test_id / 'inputs/tasks' / run['task_id'] / 'task.yaml')
    if current.prompt != source.prompt or current.variables != source.variables:
        raise HTTPException(409, '当前 Prompt 或变量已改变，请先评测当前版本再生成对应建议')
    base_revision = revision(td)
    key = _new_job(test_id, 'patch')
    path = _candidate_file(test_id, key)

    def build():
        try:
            from ..cli import _load_models_cfg, _configure_provider, _step_cfg
            from ..task_gen.apply_recommendation import generate_prompt_patch, unified_diff
            cfg = _load_models_cfg(None)
            _configure_provider(cfg)
            model = _step_cfg(cfg, 'apply_patch')
            prompt = generate_prompt_patch(current.prompt, rec, model['model'], model['reasoning_effort'])
            checked = current.model_copy(update={'prompt': prompt})
            checked.rendered_prompt()
            if not prompt.strip():
                raise ValueError('候选 Prompt 为空')
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(path, {'candidate_id': key, 'source_run': test_id, 'rubric_id': rubric_id,
                              'base_revision': base_revision, 'base_prompt': current.prompt,
                              'prompt': prompt, 'diff': unified_diff(current.prompt, prompt),
                              'input_hash': manifest['input_hash'], 'status': 'draft'})
            jobs.update(key, status='done')
        except Exception as exc:
            jobs.update(key, status='failed', log=str(exc))
    background.add_task(build)
    return {'candidate_id': key, 'job_id': key, 'status': 'running'}


@router.get('/tests/{test_id}/candidates/{candidate_id}')
def get_candidate(test_id: str, candidate_id: str):
    path = _candidate_file(test_id, candidate_id)
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    job = jobs.get(candidate_id)
    if not job or job.get('test_id') != test_id:
        raise HTTPException(404, '候选修改不存在')
    return {'status': job['status'], 'error': job.get('log', '') if job['status'] not in jobs.ACTIVE else ''}


class AcceptCandidateReq(BaseModel):
    expected_revision: str
    prompt: str = Field(min_length=1)


@router.post('/tests/{test_id}/candidates/{candidate_id}/accept')
def accept_candidate(test_id: str, candidate_id: str, req: AcceptCandidateReq):
    candidate = get_candidate(test_id, candidate_id)
    if candidate['status'] != 'draft':
        raise HTTPException(409, '候选修改尚未生成或已经采纳')
    if req.expected_revision != candidate['base_revision']:
        raise HTTPException(409, '候选修改的基线不一致')
    run = tests._require_run(test_id)
    saved = tasks.update_task_prompt(run['task_id'], tasks.UpdatePromptReq(
        prompt=req.prompt, expected_revision=req.expected_revision,
        applied_recs=[f"{test_id}:{candidate['rubric_id']}"]))
    candidate.update(status='accepted', prompt=req.prompt, accepted_version=saved['version'])
    atomic_json(_candidate_file(test_id, candidate_id), candidate)
    return saved


class CandidateTestReq(BaseModel):
    test_id: str = ''
    expected_revision: str


@router.post('/tests/{test_id}/candidate-test')
def start_candidate_test(test_id: str, req: CandidateTestReq, background: BackgroundTasks):
    run = tests._require_run(test_id)
    td = tasks._existing_task(run['task_id'])
    from ..task_gen.config_store import task_lock
    from ..models.task import TaskDefinition
    with task_lock(td):
        if revision(td) != req.expected_revision:
            raise HTTPException(409, '配置已修改，请刷新后复测')
        current = TaskDefinition.from_yaml(td / 'task.yaml')
        label = req.test_id or f'candidate_{uuid4().hex[:16]}'
        try:
            manifest = prepare_candidate(tests.ROOT, test_id, label, current.prompt)
        except FileExistsError as exc:
            raise HTTPException(409, str(exc))
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc))
    key = f'test_{label}'
    jobs.create(key, task_id=run['task_id'], test_id=label, job_type='test')

    def evaluate():
        try:
            p = manifest['params']
            command = [sys.executable, '-m', 'claw_eval.cli', 'batch', '--task', run['task_id'],
                       '--label', label, '--total', str(p['total']), '--trials', str(p['trials']),
                       '--seed', str(p['seed']), '--concurrency', str(p['concurrency']),
                       '--personas', p['personas'], '--weights', json.dumps(p['weights']),
                       '--dimensions', json.dumps(p['dimensions']), '--prepared']
            if p['no_judge']:
                command.append('--no-judge')
            proc = jobs.run_process(key, command, env={**os.environ, 'PYTHONPATH': str(tests.ROOT / 'src')}, cwd=str(tests.ROOT))
            from ..report.regression import compare_runs
            comparison = compare_runs(tests.ROOT, test_id, label)
            from ..report.builder import build_dashboard_from_dir
            build_dashboard_from_dir(tests.ROOT / 'traces' / label, tests.REPORTS_DIR / label)
            status = 'done' if proc.returncode == 0 else 'failed'
            jobs.update(key, status=status, log='复测完成；' + ('回归检查通过' if comparison['gate_passed'] else '回归检查未通过，请查看对比'))
        except Exception as exc:
            job = jobs.get(key)
            state = 'canceled' if job and job['status'] in {'canceling', 'canceled'} else 'failed'
            jobs.update(key, status=state, log=str(exc))
            from ..db import update_run
            update_run(label, status=state, note=str(exc))
    background.add_task(evaluate)
    return {'job_id': key, 'test_id': label, 'status': 'running'}


@router.get('/regression')
def regression_comparison(old: str, new: str, threshold: float = .05):
    tests._require_run(old)
    tests._require_run(new)
    from ..report.regression import compare_runs
    try:
        return compare_runs(tests.ROOT, old, new, threshold)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, f'无法进行受控比较: {exc}')


@router.get('/tests/{test_id}/cases/{case_id}')
def case_detail(test_id: str, case_id: str):
    tests._require_run(test_id)
    from ..report.aggregate import load_results_dir
    result = next((r for r in load_results_dir(tests.ROOT / 'traces' / test_id) if r.case_id == case_id), None)
    if not result:
        raise HTTPException(404, '用例不存在')
    turns = []
    path = tests.ROOT / 'traces' / test_id / f'{result.case_id}.jsonl'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                event = json.loads(line)
                if event.get('event') == 'turn':
                    turns.append(event)
            except ValueError:
                continue
    return {'result': result.model_dump(), 'turns': turns}
