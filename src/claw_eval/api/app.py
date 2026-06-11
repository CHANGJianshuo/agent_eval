"""FastAPI app —— 路由聚合 + CORS + 静态文件服务。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import (
    routes_config,
    routes_meta_eval,
    routes_personas,
    routes_tasks,
    routes_tests,
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="DialAgentEval API",
        description="对话模型指令遵循自动评测系统",
        version="0.1.0",
    )

    # CORS:允许前端 dev 服务器(Vite 默认 5173)调
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://localhost:3000",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 健康检查
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    # 托管 reports/(供前端 iframe 嵌入 dashboard HTML)
    reports_dir = _find_repo_root() / "reports"
    if reports_dir.exists():
        app.mount("/reports", StaticFiles(directory=reports_dir),
                  name="reports")

    # 挂业务路由
    app.include_router(routes_tasks.router, prefix="/api", tags=["tasks"])
    app.include_router(routes_tests.router, prefix="/api", tags=["tests"])
    app.include_router(routes_personas.router, prefix="/api", tags=["personas"])
    app.include_router(routes_config.router, prefix="/api", tags=["config"])
    app.include_router(routes_meta_eval.router, prefix="/api",
                       tags=["meta-eval"])

    # 生产环境:挂载前端 build 产物(dev 时跳过)
    web_dist = _find_web_dist()
    if web_dist and web_dist.exists():
        app.mount("/assets", StaticFiles(directory=web_dist / "assets"),
                  name="assets")

        @app.get("/{full_path:path}")
        def serve_spa(full_path: str):
            """前端 SPA 路由:任何非 /api/* 都回退到 index.html。"""
            if full_path.startswith("api/"):
                return {"error": "not found"}, 404
            index_html = web_dist / "index.html"
            if index_html.exists():
                return FileResponse(str(index_html))
            return {"error": "frontend not built;先 cd web && npm run build"}

    return app


def _find_repo_root() -> Path:
    """找仓库根目录。"""
    cur = Path(__file__).resolve()
    for parent in [cur, *cur.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _find_web_dist() -> Path | None:
    """找前端 build 产物(web/dist)。"""
    web_dist = _find_repo_root() / "web" / "dist"
    return web_dist if web_dist.exists() else None
