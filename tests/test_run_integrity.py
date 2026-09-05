"""Offline acceptance tests for scoring integrity and immutable batch inputs."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import BackgroundTasks, HTTPException
from typer.testing import CliRunner

from claw_eval import cli
from claw_eval.api import routes_tests, routes_tasks, jobs
from claw_eval.db import repo
from claw_eval.graders.llm_judge import JudgeOutputError, LLMJudge, JudgeResult
from claw_eval.graders.rubric import RubricGrader
from claw_eval.models.rubric import Rubric
from claw_eval.models.task import TaskDefinition
from claw_eval.models.trace import DimensionScores, GradingResult, TraceMessage
from claw_eval.report.aggregate import aggregate, load_results_dir
from claw_eval.report.builder import build_dashboard_from_dir
from claw_eval.runs import load_manifest, prepare_run
from claw_eval.runner.trace_io import TraceWriter


@pytest.fixture
def project(tmp_path, monkeypatch):
    from claw_eval.report import builder
    monkeypatch.setattr(builder, '_PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(cli, '_ROOT', tmp_path)
    monkeypatch.setattr(repo, 'DEFAULT_DB', tmp_path / 'runs.db')
    monkeypatch.setattr(routes_tests, 'ROOT', tmp_path)
    monkeypatch.setattr(routes_tests, 'TASKS_DIR', tmp_path / 'tasks')
    monkeypatch.setattr(routes_tests, 'REPORTS_DIR', tmp_path / 'reports')
    td = tmp_path / 'tasks' / 'demo'
    (td / 'personas').mkdir(parents=True)
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs' / 'models.yaml').write_text(yaml.safe_dump({
        role: {'model': 'offline-test', 'temperature': 0} for role in ('sut', 'simulator', 'judge')
    }))
    (td / 'task.yaml').write_text(yaml.safe_dump({'task_id': 'demo', 'task_name': 'Frozen title', 'prompt': 'Original prompt'}))
    (td / 'rubrics.yaml').write_text(yaml.safe_dump({'rubrics': [
        {'id': 'quality', 'dimension': 'completion', 'method': 'llm_judge', 'weight': 1, 'check': 'Quality'}
    ]}))
    (td / 'sampling.yaml').write_text(yaml.safe_dump({'weights': {'happy': 100}}))
    (td / 'personas' / 'happy.yaml').write_text(yaml.safe_dump({'id': 'happy', 'scenario': 'Finish', 'max_rounds': 1}))
    seen = []

    def dialogue(task, persona, *, trace_path, simulator_seed, **kwargs):
        seen.append((task.prompt, persona.model_dump(), simulator_seed))
        with TraceWriter(trace_path) as writer:
            writer.write({'event': 'dialogue_start', 'task_id': task.task_id})
            writer.write({'event': 'turn', 'turn': 1, 'role': 'assistant', 'text': 'Hello'})
            writer.write({'event': 'dialogue_end', 'end_reason': 'done'})
        return trace_path

    monkeypatch.setattr(cli, 'run_dialogue', dialogue)
    monkeypatch.setattr(cli, '_make_judge', lambda cfg: SimpleNamespace(evaluate=lambda *args, **kwargs: JudgeResult(1, 'Good', 1)))
    return tmp_path, td, seen


def _batch(label, *extra):
    return CliRunner().invoke(cli.app, ['batch', '--task', 'demo', '--label', label,
                                      '--total', '2', '--seed', '42', *extra])


def test_frozen_batch_inputs_label_independent_sampling_and_no_overwrite(project):
    root, td, seen = project
    first = _batch('a')
    assert first.exit_code == 0, first.output + repr(first.exception)
    second = _batch('b')
    assert second.exit_code == 0, second.output + repr(second.exception)
    assert json.loads((root / 'traces/a/cases.json').read_text()) == json.loads((root / 'traces/b/cases.json').read_text())
    manifest = load_manifest(root, 'a')
    assert manifest['params']['seed'] == 42
    assert manifest['cases_hash']
    results = load_results_dir(root / 'traces/a')
    assert len(results) == 2 and all(r.passed for r in results)
    assert all(r.run_id == 'a' and r.input_hash == manifest['input_hash'] for r in results)
    before = (root / 'traces/a/manifest.json').read_bytes()
    duplicate = _batch('a')
    assert duplicate.exit_code != 0
    assert (root / 'traces/a/manifest.json').read_bytes() == before
    assert len(seen) == 4
    (td / 'task.yaml').write_text(yaml.safe_dump({'task_id': 'demo', 'task_name': 'Changed title', 'prompt': 'New prompt'}))
    out = build_dashboard_from_dir(root / 'traces/a', root / 'rebuilt')
    assert 'Frozen title' in out.read_text()
    assert 'Changed title' not in out.read_text()
    assert repo.get_run('a')['n_results'] == 2


def test_no_judge_keeps_partial_results_and_no_pass_rate(project):
    root, _, _ = project
    run = _batch('partial', '--no-judge')
    assert run.exit_code == 1
    record = repo.get_run('partial')
    assert record['pass_rate'] is None and record['task_score_avg'] is None
    assert record['n_results'] == 0
    results = load_results_dir(root / 'traces/partial')
    assert len(results) == 2
    assert all(r.status == 'incomplete' and r.passed is None for r in results)
    page = (root / 'reports/partial/task_demo.html').read_text()
    assert '评分未完成' in page and '完整评分 0/2' in page
    payload = routes_tests.get_test_results('partial')
    assert payload['heatmap'] == []
    assert all(r['task_score'] is None for r in payload['results'])


def test_api_submission_freezes_inputs_and_registers_job(project):
    root, td, _ = project
    background = BackgroundTasks()
    response = routes_tests.start_test('demo', routes_tests.NewTestRequest(test_id='submitted', total=2, seed=42), background)
    (td / 'task.yaml').write_text(yaml.safe_dump({'task_id': 'demo', 'prompt': 'Edited after submission'}))
    result = _batch('submitted', '--prepared')
    assert result.exit_code == 0, result.output + repr(result.exception)
    assert json.loads((root / 'traces/submitted/cases.json').read_text())
    assert any(j.job_id == response.job_id and j.job_type == 'test' for j in routes_tasks.list_jobs())
    with pytest.raises(HTTPException) as exc:
        routes_tests.start_test('demo', routes_tests.NewTestRequest(test_id='submitted'), BackgroundTasks())
    assert exc.value.status_code == 409


@pytest.mark.parametrize('change', ['modify', 'add', 'delete', 'cases'])
def test_tampered_snapshots_are_rejected(project, change):
    root, _, _ = project
    assert _batch('immutable').exit_code == 0
    inputs = root / 'traces/immutable/inputs/tasks/demo'
    if change == 'modify':
        (inputs / 'task.yaml').write_text('changed')
    elif change == 'add':
        (inputs / 'grader.py').write_text('raise RuntimeError("unexpected")')
    elif change == 'delete':
        (inputs / 'rubrics.yaml').unlink()
    else:
        (root / 'traces/immutable/cases.json').write_text('[]')
    with pytest.raises(ValueError):
        load_manifest(root, 'immutable')


def test_missing_report_never_returns_other_run_or_global_report(project):
    root, _, _ = project
    repo.append_run('missing', 'demo', {})
    reports = root / 'reports'
    reports.mkdir()
    (reports / 'task_demo.html').write_text('old global report')
    (reports / 'missing').mkdir()
    (reports / 'missing/task_another.html').write_text('wrong task')
    assert routes_tests.get_test_report_status('missing') == {'exists': False, 'url': None}
    (reports / 'recommendations_demo.json').write_text('{"recommendations":[{"old":true}]}')
    assert routes_tests.get_test_recommendations('missing')['recommendations'] == []
    assert routes_tests.get_test('missing').milestones['m3'] is False


def test_credentials_are_excluded_from_run_snapshot(project):
    root, td, _ = project
    prepare_run(root, td, 'redacted', {'provider': {'api_key': 'secret-value', 'api_key_env': 'EXAMPLE_KEY'}}, {})
    config = (root / 'traces/redacted/inputs/models.json').read_text()
    assert 'secret-value' not in config and 'EXAMPLE_KEY' in config


@pytest.mark.parametrize('raw', ['invalid', '{"score":1}', '{"score":NaN,"reasoning":"x","evidence_turn_id":1}',
    '{"score":1.1,"reasoning":"x","evidence_turn_id":1}', '{"score":true,"reasoning":"x","evidence_turn_id":1}',
    '{"score":0,"reasoning":"x","evidence_turn_id":1.5}', '{"score":0,"reasoning":"","evidence_turn_id":null}'])
def test_invalid_judge_output_is_error_not_zero(raw):
    with pytest.raises(JudgeOutputError):
        LLMJudge._parse(raw)


@pytest.mark.parametrize('failure', ['transport', 'evidence'])
def test_judge_failure_does_not_become_sut_violation(failure):
    def evaluate(*args, **kwargs):
        if failure == 'transport':
            raise TimeoutError('offline')
        return JudgeResult(0, 'Bad evidence', 999)
    result = RubricGrader().grade([TraceMessage(turn=1, role='assistant', text='hello')],
        TaskDefinition(task_id='demo', prompt='hello'),
        [Rubric(id='q', dimension='completion', method='llm_judge', weight=1, check='q')],
        SimpleNamespace(evaluate=evaluate))
    assert result.status == 'error' and result.task_score is None and result.passed is None
    assert result.rubric_scores[0].status == 'error'
    assert not result.violations


def test_aggregation_excludes_incomplete_without_mutating_decisions():
    good = GradingResult(task_id='x', dimension_scores=DimensionScores(completion=1), task_score=1, passed=True)
    incomplete = GradingResult(task_id='x', dimension_scores=DimensionScores(), task_score=0, passed=False, status='incomplete')
    summary = aggregate([good, incomplete])
    assert (summary.total_runs, summary.evaluated_runs, summary.pass_rate) == (2, 1, 1)
    assert incomplete.passed is None
    assert good.passed is True


def test_dimension_preview_matches_actual_case_plan_even_with_reordered_keys(project):
    from claw_eval.persona_factory import generate_personas, preview_distribution
    root, td, _ = project
    dims = {'education': {'college': 30, 'school': 70}, 'attitude': {'refuse': 30, 'cooperative': 70}, 'gender': {'male': 50, 'female': 50}}
    personas = generate_personas(dims, td, 40, seed=18)
    preview = preview_distribution(dims, 40, seed=18)
    for dim, counts in preview.items():
        assert counts == {key: sum(getattr(p.demographics, dim) == key for p in personas) for key in counts}


def test_replay_uses_frozen_inputs_after_live_task_changes(project):
    root, td, seen = project
    assert _batch('source').exit_code == 0
    original_calls = list(seen)
    (td / 'task.yaml').write_text(yaml.safe_dump({'task_id': 'demo', 'prompt': 'Unrelated edit'}))
    (td / 'personas/happy.yaml').write_text('invalid live persona')
    replay = CliRunner().invoke(cli.app, ['replay', '--run-id', 'source', '--label', 'replayed'])
    assert replay.exit_code == 0, replay.output + repr(replay.exception)
    # Worker completion order can vary, while each resolved case stays identical.
    assert sorted(seen[2:], key=lambda c: c[2]) == sorted(original_calls, key=lambda c: c[2])
    assert load_manifest(root, 'source')['input_hash'] == load_manifest(root, 'replayed')['input_hash']
    assert load_manifest(root, 'replayed')['replay_of'] == 'source'


def test_report_job_surfaces_renderer_failure(project, monkeypatch):
    root, td, seen = project
    assert _batch('render').exit_code == 0
    from claw_eval.report import builder
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(builder, 'build_dashboard_from_dir', fail)
    background = BackgroundTasks()
    response = routes_tests.generate_test_report('render', background)
    background.tasks[0].func()
    saved = jobs.get(response['job_id'])
    assert saved['status'] == 'failed'
    assert 'disk unavailable' in saved['log']


def test_partial_dialogue_cannot_be_graded_as_complete(project):
    root, td, _ = project
    trace = root / 'unfinished.jsonl'
    with TraceWriter(trace) as writer:
        writer.write({'event': 'dialogue_start', 'task_id': 'demo'})
        writer.write({'event': 'turn', 'turn': 1, 'role': 'assistant', 'text': 'Hello'})
    with pytest.raises(ValueError, match='未正常完成'):
        cli._grade_trace(trace, TaskDefinition.from_yaml(td / 'task.yaml'), [], None)


def test_only_one_concurrent_submission_can_claim_a_run(project):
    from concurrent.futures import ThreadPoolExecutor
    root, td, seen = project
    def submit(_):
        try:
            prepare_run(root, td, 'race', {}, {})
            return 'created'
        except FileExistsError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, [1, 2])) == ['conflict', 'created']
    assert repo.get_run('race')['status'] == 'prepared'


def test_safety_flag_cannot_be_bypassed_by_dimension_choice():
    result = RubricGrader().grade([TraceMessage(turn=1, role='assistant', text='hello')],
        TaskDefinition(task_id='demo', prompt='hello'),
        [Rubric(id='q', dimension='robustness', is_safety=True, method='llm_judge', weight=1, check='q')],
        SimpleNamespace(evaluate=lambda *args, **kwargs: JudgeResult(0, 'Safety violation', 1)))
    assert result.status == 'complete'
    assert result.task_score == 0 and result.passed is False
