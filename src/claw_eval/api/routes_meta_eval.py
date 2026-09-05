"""Versioned calibration cohorts and independent human annotations."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..meta_eval import (append_annotation, collect_judge_scores, compute_calibration,
                         load_annotations, load_samples, save_samples, stratified_sample,
                         load_batch, list_batches)
from ..runs import validate_id

ROOT = Path(__file__).resolve().parents[3]
router = APIRouter()


class SampleRequest(BaseModel):
    n: int = Field(default=30, ge=1, le=1000)
    run_id: str | None = None
    seed: int = Field(default=42, ge=0, le=4294967295)
    mode: Literal['independent', 'assisted'] = 'independent'


class AnnotationRequest(BaseModel):
    item_id: str = Field(min_length=1)
    agree: bool | None = None
    human_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    comment: str = ''
    annotator: str = Field(min_length=1, max_length=80)
    batch_id: str | None = None


def _batch(task_id: str, batch_id: str | None = None):
    try:
        validate_id(task_id)
        return load_batch(ROOT, task_id, batch_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc))


@router.post('/tasks/{task_id}/meta-eval/sample')
def create_samples(task_id: str, req: SampleRequest):
    try:
        validate_id(task_id)
        if req.run_id:
            validate_id(req.run_id)
        items = collect_judge_scores(ROOT / 'traces', task_id=task_id, run_id=req.run_id)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc))
    if not items:
        raise HTTPException(422, '没有有效的 LLM Judge 评分，请先完成一次测试')
    if req.mode == 'independent':
        if any(not i.rubric_check or not i.input_hash for i in items):
            raise HTTPException(422, '独立校准需要保存了评分标准快照的运行，请选择新运行')
        if len({i.input_hash for i in items}) > 1:
            raise HTTPException(422, '抽样池包含不同评测版本，请限定一次运行')
    samples = stratified_sample(items, n=req.n, seed=req.seed)
    path = save_samples(ROOT, task_id, samples, metadata={
        'mode': req.mode, 'run_id': req.run_id, 'seed': req.seed,
        'n_pool': len(items), 'rubrics_in_pool': len({i.rubric_id for i in items}),
        'rubrics_sampled': len({i.rubric_id for i in samples}),
        'input_hashes': sorted({i.input_hash for i in samples})})
    return {'batch_id': path.stem, 'n_pool': len(items), 'n_sampled': len(samples)}


@router.get('/tasks/{task_id}/meta-eval/batches')
def batches(task_id: str):
    _batch(task_id)
    return {'batches': list_batches(ROOT, task_id)}


def _present_sample(sample: dict, batch: dict, ann: dict | None):
    shown = {**sample, 'annotated': ann is not None, 'annotation': ann}
    if batch.get('mode') == 'independent' and ann is None:
        for key in ('judge_score', 'judge_reasoning', 'evidence_turn'):
            shown.pop(key, None)
    return shown


@router.get('/tasks/{task_id}/meta-eval/samples')
def list_samples(task_id: str, batch_id: str | None = None, annotator: str = ''):
    batch = _batch(task_id, batch_id)
    anns = {a['item_id']: a for a in load_annotations(ROOT, task_id, batch['batch_id']) if a.get('annotator', '') == annotator}
    samples = [_present_sample(s, batch, anns.get(s['item_id'])) for s in batch['samples']]
    return {'samples': samples, 'n_total': len(samples), 'n_annotated': sum(s['annotated'] for s in samples),
            'batch_id': batch['batch_id'], 'mode': batch['mode'],
            'rubrics_in_pool': batch.get('rubrics_in_pool'), 'rubrics_sampled': batch.get('rubrics_sampled')}


@router.get('/tasks/{task_id}/meta-eval/items/{item_id:path}/conversation')
def get_item_conversation(task_id: str, item_id: str, batch_id: str | None = None, annotator: str = ''):
    batch = _batch(task_id, batch_id)
    sample = next((s for s in batch['samples'] if s['item_id'] == item_id), None)
    if sample is None:
        raise HTTPException(404, '标注样本不存在')
    run_and_case = item_id.split('#')[0]
    path = (ROOT / 'traces' / f'{run_and_case}.jsonl').resolve()
    if not path.is_relative_to((ROOT / 'traces').resolve()) or not path.is_file():
        raise HTTPException(404, '对话记录不存在')
    turns = []
    for line in path.read_text(encoding='utf-8').splitlines():
        try:
            event = json.loads(line)
            if event.get('event') == 'turn':
                turns.append(event)
        except ValueError:
            continue
    ann = next((a for a in load_annotations(ROOT, task_id, batch['batch_id'])
                if a['item_id'] == item_id and a.get('annotator', '') == annotator), None)
    return {'item': _present_sample(sample, batch, ann), 'turns': turns}


@router.post('/tasks/{task_id}/meta-eval/annotations')
def submit_annotation(task_id: str, req: AnnotationRequest):
    batch = _batch(task_id, req.batch_id)
    if not any(s['item_id'] == req.item_id for s in batch['samples']):
        raise HTTPException(422, '样本 ID 不属于所选批次')
    independent = batch.get('mode') == 'independent'
    if independent and (req.human_score is None or req.agree is True):
        raise HTTPException(422, '独立评分必须先提供人工分，不能直接同意 Judge')
    if not independent and req.agree is not True and req.human_score is None:
        raise HTTPException(422, '请输入人工分')
    if not req.annotator.strip():
        raise HTTPException(422, '请输入标注者标识')
    append_annotation(ROOT, task_id, {
        'item_id': req.item_id, 'agree': False if independent else req.agree,
        'human_score': req.human_score, 'comment': req.comment,
        'annotator': req.annotator.strip(), 'mode': 'independent' if independent else 'assisted',
        'ts': datetime.now(timezone.utc).isoformat(),
    }, batch_id=batch['batch_id'])
    return {'ok': True}


@router.get('/tasks/{task_id}/meta-eval/report')
def calibration_report(task_id: str, batch_id: str | None = None, annotator: str = ''):
    batch = _batch(task_id, batch_id)
    annotations = load_annotations(ROOT, task_id, batch['batch_id'])
    if batch.get('mode') == 'independent':
        reviewed = {a['item_id'] for a in annotations if a.get('annotator') == annotator}
        if not {s['item_id'] for s in batch['samples']} <= reviewed:
            raise HTTPException(409, '请先完成自己的独立评分，再查看校准报告')
    rep = compute_calibration(batch['samples'], annotations)
    return {**rep.to_dict(), 'batch_id': batch['batch_id'], 'mode': batch['mode'],
            'scope': '分层样本的描述性统计；不能直接外推为所有对话的准确率'}
