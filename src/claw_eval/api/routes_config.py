"""系统配置 endpoints。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


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
    temperature: float = 0.7
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
    concurrency: int = 4


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
    key = req.api_key
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
                              env=env, capture_output=True, text=True,
                              timeout=20)
        ok = "OK:" in r.stdout
        return {"ok": ok, "message": r.stdout.strip() or r.stderr.strip()[:300]}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "测连接超时(20s)")
