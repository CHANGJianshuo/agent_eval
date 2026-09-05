"""系统配置 endpoints。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


def _root() -> Path:
    cur = Path(__file__).resolve()
    for p in [cur, *cur.parents]:
        if (p / "pyproject.toml").exists():
            return p
    return Path.cwd()


ROOT = _root()
MODELS_FILE = ROOT / "configs" / "models.yaml"
KEYS_FILE = Path.home() / ".claw_eval" / "api_keys.yaml"


router = APIRouter()


class ModelRole(BaseModel):
    model: str = ""
    temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)
    reasoning_effort: str = "low"


class ModelsConfig(BaseModel):
    sut: ModelRole = ModelRole()
    simulator: ModelRole = ModelRole()
    judge: ModelRole = ModelRole(temperature=0.0, reasoning_effort="medium")
    extract_rubric: ModelRole | None = None
    extract_personas: ModelRole | None = None
    extract_flow: ModelRole | None = None
    extract_variables: ModelRole | None = None
    recommend: ModelRole | None = None
    apply_patch: ModelRole | None = None
    concurrency: int = Field(default=4, ge=1, le=32)


class ApiKeyReq(BaseModel):
    provider: str          # xiaomi_mimo / openai / anthropic
    api_key: str


class TestConnReq(BaseModel):
    provider: str
    api_key: str | None = None


_STEP_KEYS = ["extract_rubric", "extract_personas", "extract_flow",
              "extract_variables", "recommend", "apply_patch"]


@router.get("/config/models")
def get_models():
    if not MODELS_FILE.exists():
        return ModelsConfig().model_dump()
    try:
        d = yaml.safe_load(MODELS_FILE.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise HTTPException(500, str(e))
    out = ModelsConfig(
        sut=ModelRole(**d.get("sut", {})),
        simulator=ModelRole(**d.get("simulator", {})),
        judge=ModelRole(**d.get("judge", {})),
        concurrency=int(d.get("concurrency", 4)),
    )
    for k in _STEP_KEYS:
        if k in d and isinstance(d[k], dict):
            setattr(out, k, ModelRole(**d[k]))
    return out


@router.put("/config/models")
def update_models(cfg: ModelsConfig):
    # 读已有 yaml 保留 provider 段(前端不编辑 provider)
    existing = {}
    if MODELS_FILE.exists():
        try:
            existing = yaml.safe_load(MODELS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    data = cfg.model_dump(exclude_none=True)
    if "provider" in existing:
        data["provider"] = existing["provider"]
    MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODELS_FILE.write_text(
        yaml.safe_dump(data, allow_unicode=True,
                       sort_keys=False, default_flow_style=False),
        encoding="utf-8")
    return {"ok": True}


@router.post("/config/api-key")
def save_api_key(req: ApiKeyReq):
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    keys = {}
    if KEYS_FILE.exists():
        try:
            keys = yaml.safe_load(KEYS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    keys[req.provider] = req.api_key
    KEYS_FILE.write_text(
        yaml.safe_dump(keys, default_flow_style=False),
        encoding="utf-8")
    KEYS_FILE.chmod(0o600)
    return {"ok": True}


@router.get("/config/api-keys")
def list_api_keys():
    """返回各 provider 是否已配 key(masked)。

    内置 provider 有对应环境变量;用户自定义的 provider 只查本地存储。
    """
    out: dict[str, str | None] = {}
    keys: dict = {}
    if KEYS_FILE.exists():
        try:
            keys = yaml.safe_load(KEYS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    env_map = {
        "deepseek": "DEEPSEEK_API_KEY",
        "xiaomi_mimo": "XIAOMI_MIMO_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    # 内置 provider:查环境变量 + 本地存储
    for prov, env_var in env_map.items():
        key = keys.get(prov) or os.environ.get(env_var)
        out[prov] = ("*" * 8 + key[-6:]) if key else None
    # 用户自定义 provider:只查本地存储
    for prov, key in keys.items():
        if prov not in out and key:
            out[prov] = "*" * 8 + str(key)[-6:]
    return out


@router.post("/config/test-connection")
def test_connection(req: TestConnReq):
    """发一个最小 chat call 看通不通。"""
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    key_env = {"deepseek": "DEEPSEEK_API_KEY", "xiaomi_mimo": "XIAOMI_MIMO_API_KEY", "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(req.provider, f"{req.provider.upper()}_API_KEY")
    key = req.api_key or os.environ.get(key_env)
    if not key and KEYS_FILE.exists():
        try:
            keys = yaml.safe_load(KEYS_FILE.read_text(encoding="utf-8")) or {}
            key = keys.get(req.provider)
        except Exception:
            pass
    if not key:
        raise HTTPException(400, "未提供 api_key 且本地也没存")
    provider_env_map = {
        "deepseek": "DEEPSEEK_API_KEY",
        "xiaomi_mimo": "XIAOMI_MIMO_API_KEY",
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    env_var = provider_env_map.get(req.provider,
                                    f"{req.provider.upper()}_API_KEY")
    env[env_var] = key

    cfg = yaml.safe_load(MODELS_FILE.read_text(encoding="utf-8")) or {}
    configured_env = cfg.get("provider", {}).get("api_key_env")
    if configured_env and configured_env != env_var:
        raise HTTPException(422, f"当前运行使用 {configured_env} 对应的 Provider，请测试该配置")
    script = """
from claw_eval.cli import _configure_provider, _step_cfg
from claw_eval.runner import llm_client
import yaml, sys
cfg = yaml.safe_load(open(sys.argv[1])) or {}
_configure_provider(cfg)
model = _step_cfg(cfg, "judge")
try:
    out = llm_client.chat(model["model"], [{"role":"user","content":"ping"}],
                          temperature=0.0, max_tokens=10, max_retries=1, timeout=15)
    print(f"OK: {out[:80]}")
except Exception as e:
    print(f"FAIL: {e}")
"""
    try:
        r = subprocess.run([sys.executable, "-c", script, str(MODELS_FILE)],
                              env=env, capture_output=True, text=True,
                              timeout=20)
        ok = "OK:" in r.stdout
        return {"ok": ok, "message": r.stdout.strip() or r.stderr.strip()[:300]}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "测连接超时(20s)")


class NoiseLibraryReq(BaseModel):
    kinds: dict[str, dict[str, str]]
    expected_revision: str


@router.get('/config/noise')
def get_noise():
    from ..runs import digest
    path = ROOT / 'configs/noise_profiles.yaml'
    kinds = yaml.safe_load(path.read_text(encoding='utf-8')) if path.exists() else {}
    kinds = kinds or {}
    return {'kinds': kinds, 'revision': digest(kinds)}


@router.put('/config/noise')
def update_noise(req: NoiseLibraryReq):
    from ..runs import digest, validate_id
    from ..task_gen.config_store import write_text, task_lock
    from ..models.persona import NoiseKind
    directory = ROOT / 'configs'
    directory.mkdir(parents=True, exist_ok=True)
    with task_lock(directory):
        current = get_noise()
        if req.expected_revision != current['revision']:
            raise HTTPException(409, '噪音库已改变，请刷新后重试')
        kinds = {}
        try:
            for key, value in req.kinds.items():
                validate_id(key)
                kind = NoiseKind(id=key, **value)
                if not kind.name.strip() or not kind.instruction.strip():
                    raise ValueError('噪音名称和模拟指令不能为空')
                kinds[key] = kind.model_dump(exclude={'id'})
            removed = set(current['kinds']) - set(kinds)
            for path in (ROOT / 'tasks').glob('*/personas*/*.yaml'):
                script = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
                if removed & set(script.get('noise', {}).get('kinds', [])):
                    raise ValueError(f'剧本 {path.parent.parent.name}/{path.stem} 仍在使用待删除噪音')
            for path in (ROOT / 'tasks').glob('*/sampling.yaml'):
                sampling = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
                if removed & set(sampling.get('noise_overlay', {}).get('kinds', [])):
                    raise ValueError(f'任务 {path.parent.name} 的采样配置仍在使用待删除噪音')
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc))
        write_text(directory / 'noise_profiles.yaml', yaml.safe_dump(kinds, allow_unicode=True, sort_keys=False))
    return {'ok': True, 'revision': digest(kinds)}
