"""Acceptance cases from the feature audit; all model calls stay offline."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml
from fastapi import BackgroundTasks, HTTPException
from typer.testing import CliRunner

from test_run_integrity import project, _batch
from claw_eval import cli
from claw_eval.api import routes_tasks, routes_tests
from claw_eval.adversarial import build_red_team_report
from claw_eval.db import repo
from claw_eval.graders.rubric import RubricGrader
from claw_eval.models.rubric import Rubric
from claw_eval.models.task import TaskDefinition
from claw_eval.models.trace import DimensionScores, GradingResult, RubricScore, TraceMessage
from claw_eval.persona_factory import build_persona
from claw_eval.models.persona import Demographics, Persona
from claw_eval.user_simulator.simulator import UserSimulator
from claw_eval.report.aggregate import load_results_dir
from claw_eval.task_gen.config_store import revision
from claw_eval.task_gen.versioning import list_versions


def test_zero_weight_never_calls_judge_or_passes(project, monkeypatch):
    root, td, _ = project
    rubric = Rubric(id="quality", dimension="completion", method="llm_judge", weight=0, check="Quality")
    calls = []
    result = RubricGrader().grade([TraceMessage(turn=1, role="assistant", text="bad")],
        TaskDefinition(task_id="demo", prompt="hello"), [rubric],
        SimpleNamespace(evaluate=lambda *a, **k: calls.append(True)))
    assert result.status == "error" and result.passed is None and result.task_score is None
    assert not calls
    (td / "rubrics.yaml").write_text(yaml.safe_dump({"rubrics": [rubric.model_dump()]}))
    with pytest.raises(HTTPException) as exc:
        routes_tests.start_test("demo", routes_tests.NewTestRequest(test_id="invalid"), BackgroundTasks())
    assert exc.value.status_code == 422
    assert not (root / "traces/invalid").exists()


def test_failed_dialogue_keeps_case_and_report_denominator(project, monkeypatch):
    root, td, _ = project
    normal = cli.run_dialogue
    def fail_second(*args, simulator_seed, **kwargs):
        if simulator_seed == 44:
            raise TimeoutError("offline transport timeout")
        return normal(*args, simulator_seed=simulator_seed, **kwargs)
    monkeypatch.setattr(cli, "run_dialogue", fail_second)
    run = _batch("partial_transport")
    assert run.exit_code == 1, run.output
    results = routes_tests.get_test_results("partial_transport")["results"]
    assert len(results) == 2
    assert sum(r["status"] == "complete" for r in results) == 1
    assert any("transport timeout" in r["error_message"] for r in results)
    assert "完整评分 1/2" in (root / "reports/partial_transport/task_demo.html").read_text()
    result_file = next((root / "traces/partial_transport").glob("*.result.json"))
    result_file.write_text("broken")
    results = load_results_dir(root / "traces/partial_transport")
    assert len(results) == 2 and all(r.passed is None for r in results)


def test_safety_unknown_is_not_safe():
    rubric = Rubric(id="safety.x", dimension="safety", method="llm_judge", check="Safe", is_safety=True)
    def result(status):
        return GradingResult(task_id="t", dimension_scores=DimensionScores(), task_score=0, passed=False,
            rubric_scores=[RubricScore(rubric_id="safety.x", dimension="safety", method="llm_judge", weight=0, status=status, score=0)])
    report = build_red_team_report([result("scored"), result("error")], [rubric])
    assert report["overall_breach_rate"] == 1 and report["coverage"] == .5
    assert build_red_team_report([result("error")], [rubric])["overall_breach_rate"] is None


def test_demographics_are_sent_to_simulator():
    script = Persona(id="script", name="Script", personality_id="generic", description="", speaking_style="", scenario="Finish")
    calls = []
    with patch("claw_eval.user_simulator.simulator.llm_client.chat", side_effect=lambda m, messages, *a, **kw: calls.append(messages) or "好"):
        for age in ("20-29", "50+"):
            p = build_persona(Demographics(attitude="cooperative", age_range=age, education="college", gender="female", mbti="INTJ"), script, 0)
            UserSimulator("offline", p)._generate([])
    assert calls[0] != calls[1]
    assert all(value in calls[0][0]["content"] for value in ("20-29", "college", "female", "INTJ"))


def test_legacy_metrics_are_reconciled_without_changing_raw_result(tmp_path):
    run_dir = tmp_path / "traces/old"
    run_dir.mkdir(parents=True)
    raw = {"task_id": "demo", "dimension_scores": {}, "task_score": 1, "passed": True,
           "rubric_scores": [{"rubric_id": "q", "dimension": "completion", "method": "llm_judge",
                              "weight": 1, "triggered": False, "score": 0, "reasoning": "未提供 LLM Judge,跳过"}]}
    path = run_dir / "case.result.json"
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    db = tmp_path / "test.db"
    repo.append_run("old", "demo", {}, db_path=db)
    repo.update_run("old", db_path=db, status="done", pass_rate=1, task_score_avg=1)
    repo.migrate_existing_traces(tmp_path / "traces", db_path=db)
    run = repo.get_run("old", db_path=db)
    assert run["pass_rate"] is None and run["n_results"] == 0 and run["status"] == "failed"
    assert path.read_bytes() == before
    assert load_results_dir(run_dir)[0].passed is None


def test_prompt_edits_validate_version_and_conflict(project, monkeypatch):
    root, td, _ = project
    monkeypatch.setattr(routes_tasks, "TASKS_DIR", root / "tasks")
    before = revision(td)
    with pytest.raises(HTTPException):
        routes_tasks.update_task_prompt("demo", routes_tasks.UpdatePromptReq(prompt="Hello {missing}", expected_revision=before))
    assert revision(td) == before
    saved = routes_tasks.update_task_prompt("demo", routes_tasks.UpdatePromptReq(prompt="Hello {name}", variables={"name":"张"}, expected_revision=before))
    assert saved["version"] and len(list_versions(td)) == 2
    with pytest.raises(HTTPException) as exc:
        routes_tasks.update_task_prompt("demo", routes_tasks.UpdatePromptReq(prompt="Overwrite", expected_revision=before))
    assert exc.value.status_code == 409
    initial = list_versions(td)[0].label
    routes_tasks.restore_version("demo", initial, routes_tasks.RestoreReq(expected_revision=saved["revision"]))
    assert revision(td) == before


def _background(background):
    for task in background.tasks:
        task.func(*task.args, **task.kwargs)


def test_suggestion_accept_fixed_cases_and_regression_flow(project, monkeypatch):
    from claw_eval.api import routes_workflows as flow
    from claw_eval.report import recommend
    from claw_eval.task_gen import apply_recommendation
    from claw_eval.graders.llm_judge import JudgeResult
    from claw_eval.api import jobs
    import subprocess
    root, td, _ = project
    monkeypatch.setattr(routes_tasks, 'TASKS_DIR', root / 'tasks')
    monkeypatch.setattr(cli, '_make_judge', lambda cfg: SimpleNamespace(evaluate=lambda *a, **k: JudgeResult(0, 'Needs improvement', 1)))
    assert _batch('baseline', '--total', '3').exit_code == 0
    source = (root / 'traces/baseline/inputs/tasks/demo/task.yaml').read_bytes()
    monkeypatch.setattr(recommend, 'generate_recommendation', lambda *a, **k: {'suggested_prompt_change':'Add a clear greeting', 'rationale':'Evidence in turn 1'})
    def process(job_id, command, **kwargs):
        result = CliRunner().invoke(cli.app, command[3:])
        return subprocess.CompletedProcess(command, result.exit_code, result.output, '')
    monkeypatch.setattr(jobs, 'run_process', process)
    bg = BackgroundTasks()
    generated = flow.generate_recommendations('baseline', bg)
    _background(bg)
    assert jobs.get(generated['job_id'])['status'] == 'done'
    recommendations = routes_tests.get_test_recommendations('baseline')
    assert recommendations['status'] == 'completed'
    sample = recommendations['recommendations'][0]['violation_samples'][0]
    assert flow.case_detail('baseline', sample['case_id'])['turns']
    monkeypatch.setattr(apply_recommendation, 'generate_prompt_patch', lambda prompt, *a: prompt + '\nSay hello clearly.')
    bg = BackgroundTasks()
    drafted = flow.draft_patch('baseline', 'quality', bg)
    _background(bg)
    candidate = flow.get_candidate('baseline', drafted['candidate_id'])
    assert candidate['status'] == 'draft' and '+Say hello clearly.' in candidate['diff']
    saved = flow.accept_candidate('baseline', drafted['candidate_id'], flow.AcceptCandidateReq(expected_revision=candidate['base_revision'], prompt=candidate['prompt']))
    assert saved['version']
    assert (root / 'traces/baseline/inputs/tasks/demo/task.yaml').read_bytes() == source
    monkeypatch.setattr(cli, '_make_judge', lambda cfg: SimpleNamespace(evaluate=lambda *a, **k: JudgeResult(1, 'Good', 1)))
    bg = BackgroundTasks()
    launched = flow.start_candidate_test('baseline', flow.CandidateTestReq(test_id='candidate', expected_revision=saved['revision']), bg)
    _background(bg)
    assert jobs.get(launched['job_id'])['status'] == 'done'
    assert json.loads((root / 'traces/baseline/cases.json').read_text()) == json.loads((root / 'traces/candidate/cases.json').read_text())
    report = flow.regression_comparison('baseline', 'candidate')
    assert report['comparable'] and report['gate_passed']
    assert report['new_pass_rate'] == 1 and report['old_pass_rate'] == 0
    assert (root / 'reports/candidate/comparisons/baseline.json').exists()
    original = routes_tasks.version_detail('demo', list_versions(td)[0].label)
    assert 'Say hello clearly.' in original['diff']


def test_recommendation_errors_are_saved_and_cli_fails(project, monkeypatch):
    from claw_eval.report import recommend
    from claw_eval.graders.llm_judge import JudgeResult
    root, td, _ = project
    monkeypatch.setattr(cli, '_make_judge', lambda cfg: SimpleNamespace(evaluate=lambda *a, **k: JudgeResult(0, 'Weak', 1)))
    assert _batch('weak', '--total', '3').exit_code == 0
    def fail(*a, **k):
        raise TimeoutError('offline advice failure')
    monkeypatch.setattr(recommend, 'generate_recommendation', fail)
    result = CliRunner().invoke(cli.app, ['recommend', '--task', 'demo', '--run-id', 'weak'])
    assert result.exit_code == 1
    state = routes_tests.get_test_recommendations('weak')
    assert state['status'] == 'failed' and state['failed'] == 1
    assert state['recommendations'][0]['llm_error']
    assert routes_tests.get_test('weak').milestones['m4'] is False


def test_regression_gate_blocks_decline_and_changed_criteria(project, monkeypatch):
    from claw_eval.graders.llm_judge import JudgeResult
    from claw_eval.report.regression import compare_runs
    root, td, _ = project
    assert _batch('good').exit_code == 0
    monkeypatch.setattr(cli, '_make_judge', lambda cfg: SimpleNamespace(evaluate=lambda *a, **k: JudgeResult(0, 'Bad', 1)))
    assert _batch('bad').exit_code == 0
    result = CliRunner().invoke(cli.app, ['regression', '--task', 'demo', '--old', 'good', '--new', 'bad'])
    assert result.exit_code == 1
    assert compare_runs(root, 'good', 'bad')['gate_passed'] is False
    rows = yaml.safe_load((td / 'rubrics.yaml').read_text())
    rows['rubrics'][0]['check'] = 'A different criterion'
    (td / 'rubrics.yaml').write_text(yaml.safe_dump(rows))
    assert _batch('different_criteria').exit_code == 0
    rep = compare_runs(root, 'good', 'different_criteria')
    assert rep['gate_passed'] is None and not rep['comparable']
    assert any('rules' in reason for reason in rep['issues'])


def test_independent_calibration_batches_and_reviewers(project, monkeypatch):
    from claw_eval.api import routes_meta_eval as meta
    from pydantic import ValidationError
    root, td, _ = project
    monkeypatch.setattr(meta, 'ROOT', root)
    assert _batch('calibration').exit_code == 0
    batch = meta.create_samples('demo', meta.SampleRequest(n=1, run_id='calibration'))['batch_id']
    sample = meta.list_samples('demo', batch, 'alice')['samples'][0]
    assert sample['rubric_check'] == 'Quality'
    assert 'judge_score' not in sample and 'judge_reasoning' not in sample
    assert 'judge_score' not in meta.get_item_conversation('demo', sample['item_id'], batch, 'alice')['item']
    with pytest.raises(ValidationError):
        meta.AnnotationRequest(item_id=sample['item_id'], human_score=99, annotator='alice')
    with pytest.raises(ValidationError):
        meta.SampleRequest(n=-1)
    with pytest.raises(HTTPException):
        meta.submit_annotation('demo', meta.AnnotationRequest(item_id='unknown', human_score=.5, annotator='alice', batch_id=batch))
    with pytest.raises(HTTPException):
        meta.calibration_report('demo', batch, 'alice')
    for actor, score in [('alice',1),('bob',0)]:
        assert 'judge_score' not in meta.list_samples('demo', batch, actor)['samples'][0]
        meta.submit_annotation('demo', meta.AnnotationRequest(item_id=sample['item_id'], human_score=score, annotator=actor, batch_id=batch))
        assert meta.list_samples('demo', batch, actor)['samples'][0]['judge_score'] == 1
    report = meta.calibration_report('demo', batch, 'alice')
    assert report['n_annotated'] == 1 and report['n_ratings'] == 2
    assert not report['sufficient_sample'] and len(report['annotator_disagreements']) == 1
    later = meta.create_samples('demo', meta.SampleRequest(n=1, run_id='calibration'))['batch_id']
    assert later != batch and meta.list_samples('demo', later, 'alice')['n_annotated'] == 0
    assert meta.calibration_report('demo', batch, 'alice')['n_ratings'] == 2


def test_blacklist_legacy_parameter_is_effective():
    r = Rubric(id='safety.promise', dimension='safety', method='blacklist', check='No promises', is_safety=True,
               params={'keywords':['绝对保证'], 'scope':'all_assistant', 'mode':'any'})
    result = RubricGrader().grade([TraceMessage(turn=1, role='assistant', text='我绝对保证')], TaskDefinition(task_id='t', prompt='hello'), [r])
    assert result.status == 'complete' and result.passed is False and result.task_score == 0
    assert result.violations


def test_noise_edit_conflicts_and_referenced_deletion(project, monkeypatch):
    from claw_eval.api import routes_config as config
    root, td, _ = project
    monkeypatch.setattr(config, 'ROOT', root)
    initial = config.get_noise()
    saved = config.update_noise(config.NoiseLibraryReq(kinds={'filler':{'name':'口头语','instruction':'使用少量口头语'}}, expected_revision=initial['revision']))
    with pytest.raises(HTTPException) as exc:
        config.update_noise(config.NoiseLibraryReq(kinds={}, expected_revision=initial['revision']))
    assert exc.value.status_code == 409
    path = td / 'personas/happy.yaml'
    script = yaml.safe_load(path.read_text())
    script['noise'] = {'rate':.5, 'kinds':['filler']}
    path.write_text(yaml.safe_dump(script))
    with pytest.raises(HTTPException) as exc:
        config.update_noise(config.NoiseLibraryReq(kinds={}, expected_revision=saved['revision']))
    assert exc.value.status_code == 422
    assert 'filler' in config.get_noise()['kinds']
    script['noise'] = {'rate':0, 'kinds':[]}
    path.write_text(yaml.safe_dump(script))
    (td / 'sampling.yaml').write_text(yaml.safe_dump({'weights':{'happy':100}, 'noise_overlay':{'rate':.2,'kinds':['filler']}}))
    with pytest.raises(HTTPException, match='采样配置'):
        config.update_noise(config.NoiseLibraryReq(kinds={}, expected_revision=saved['revision']))


@pytest.mark.parametrize('change', ['task_id', 'draft_params', 'probe_turn', 'noise'])
def test_invalid_document_edits_do_not_change_configuration(project, change):
    from claw_eval.task_gen.config_store import commit_files
    root, td, _ = project
    before = revision(td)
    if change == 'task_id':
        name, document = 'task.yaml', {'task_id':'renamed', 'prompt':'Hi'}
    elif change == 'draft_params':
        name, document = 'rubrics.draft.yaml', {'rubrics':[{'id':'quality','dimension':'completion','method':'keyword','weight':1,'check':'Quality','params':{'keyword':['hello']}}]}
    else:
        name, document = 'personas/happy.yaml', {'id':'happy', 'scenario':'Finish', 'max_rounds':1}
        document.update({'probes':[{'id':'late','inject_at_turn':2,'text':'Hi'}]} if change == 'probe_turn' else {'noise':{'rate':.5, 'kinds':['missing']}})
    with pytest.raises(ValueError):
        commit_files(td, {name:yaml.safe_dump(document)}, expected_revision=before)
    assert revision(td) == before and list_versions(td) == []


def test_result_from_another_run_cannot_pass(project):
    root, td, _ = project
    assert _batch('origin').exit_code == 0
    assert _batch('target').exit_code == 0
    source = next((root / 'traces/origin').glob('*.result.json'))
    target = root / 'traces/target' / source.name
    target.write_bytes(source.read_bytes())
    results = load_results_dir(root / 'traces/target')
    assert sum(r.status == 'complete' for r in results) == 1
    assert any(r.passed is None and '不一致' in r.error_message for r in results)


def test_candidate_rejects_old_engine_before_reserving_or_calling(project):
    from claw_eval.runs import prepare_candidate
    root, td, seen = project
    assert _batch('old_engine').exit_code == 0
    path = root / 'traces/old_engine/manifest.json'
    data = json.loads(path.read_text())
    data.pop('grading_hash')
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='重建基准'):
        prepare_candidate(root, 'old_engine', 'blocked', 'New prompt')
    assert not (root / 'traces/blocked').exists() and len(seen) == 2


def test_safety_gate_detects_new_violation_even_when_already_failing(project, monkeypatch):
    from claw_eval.runner.trace_io import TraceWriter
    from claw_eval.report.regression import compare_runs
    root, td, _ = project
    rules = yaml.safe_load((td / 'rubrics.yaml').read_text())
    rules['rubrics'].extend({'id':f'safety.{word}', 'dimension':'safety', 'method':'blacklist',
                            'is_safety':True, 'weight':0, 'check':f'No {word}', 'params':{'banned_words':[word]}}
                           for word in ('BAN_A', 'BAN_B'))
    (td / 'rubrics.yaml').write_text(yaml.safe_dump(rules))
    changed = False
    def dialogue(task, persona, *, trace_path, simulator_seed, **kwargs):
        with TraceWriter(trace_path) as writer:
            writer.write({'event':'dialogue_start','task_id':task.task_id})
            writer.write({'event':'turn','turn':1,'role':'assistant','text':'BAN_B' if changed and simulator_seed == 43 else 'BAN_A'})
            writer.write({'event':'dialogue_end','end_reason':'done'})
        return trace_path
    monkeypatch.setattr(cli, 'run_dialogue', dialogue)
    assert _batch('safety_before', '--total', '11').exit_code == 0
    changed = True
    assert _batch('safety_after', '--total', '11').exit_code == 0
    report = compare_runs(root, 'safety_before', 'safety_after', .2)
    assert report['old_pass_rate'] == report['new_pass_rate'] == 0
    assert report['gate_reasons'] == ['相同用例出现新的安全违规'] and report['gate_passed'] is False


def test_flow_plan_and_scores_are_not_trace_coverage(project):
    root, td, _ = project
    (td / 'flow.yaml').write_text(yaml.safe_dump({'nodes':[{'id':'greeting','label':'Hello','rubric':'quality'}], 'edges':[]}))
    script = yaml.safe_load((td / 'personas/happy.yaml').read_text())
    script['covers_flow_nodes'] = ['greeting']
    (td / 'personas/happy.yaml').write_text(yaml.safe_dump(script))
    assert _batch('coverage').exit_code == 0
    report = routes_tests.get_coverage('coverage')
    assert report['path_recording'] == 'unavailable'
    assert report['nodes'][0]['planned_cases'] == report['nodes'][0]['rubric_scored_cases'] == 2
    assert report['nodes'][0]['observed_cases'] == 0
    path = next((root / 'traces/coverage').glob('*.jsonl'))
    events = [json.loads(line) for line in path.read_text().splitlines()]
    next(e for e in events if e['event'] == 'turn')['flow_node_id'] = 'greeting'
    path.write_text('\n'.join(json.dumps(e) for e in events) + '\n')
    report = routes_tests.get_coverage('coverage')
    assert report['nodes'][0]['observed_cases'] == 1
    assert report['nodes'][0]['evidence'][0]['case_id'] == path.stem


def test_old_html_is_not_presented_as_current_report(project):
    root, td, _ = project
    assert _batch('report_version').exit_code == 0
    assert routes_tests.get_test_report_status('report_version')['exists']
    (root / 'reports/report_version/report_version.json').unlink()
    assert routes_tests.get_test_report_status('report_version') == {'exists':False, 'url':None}
    bg = BackgroundTasks()
    routes_tests.generate_test_report('report_version', bg)
    _background(bg)
    assert routes_tests.get_test_report_status('report_version')['exists']


def test_legacy_safety_summary_does_not_reintroduce_zero_percent(project):
    from claw_eval.report.builder import build_dashboard_from_dir
    root, td, _ = project
    assert _batch('legacy_safety').exit_code == 0
    out = root / 'reports/legacy_safety'
    (out / 'safety_test_demo.json').write_text(json.dumps({'n_results':2, 'n_breached_cases':0,
         'overall_breach_rate':0, 'safety_rubrics':[], 'by_rubric':[], 'by_persona':[]}))
    build_dashboard_from_dir(root / 'traces/legacy_safety', out)
    assert '历史红队统计缺少有效样本数，无法判断破防率' in (out / 'task_demo.html').read_text()


@pytest.mark.parametrize('value', [None, False, '', []])
def test_empty_or_non_text_advice_is_a_generation_error(monkeypatch, value):
    from claw_eval.report import recommend
    monkeypatch.setattr(recommend.llm_client, 'chat', lambda *a, **kw: yaml.safe_dump({'suggested_prompt_change':value, 'rationale':'Reason'}))
    with pytest.raises(ValueError, match='仍失败'):
        recommend.generate_recommendation(TaskDefinition(task_id='t', prompt='Hi'),
            {'rubric_id':'quality','avg_score':0,'n_triggered':3}, [], 'offline', max_attempts=1)


def test_long_run_id_can_track_candidate_generation(project):
    from claw_eval.api import routes_workflows as flow
    root, td, _ = project
    label = 'r' * 128
    (root / 'traces' / label).mkdir(parents=True)
    repo.append_run(label, 'demo', {})
    key = flow._new_job(label, 'patch')
    assert flow.get_candidate(label, key)['status'] == 'running'
