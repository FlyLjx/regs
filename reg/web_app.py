from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from reg.runtime import ensure_runtime_environment
from reg.web_manager import RegWebManager


BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_CANDIDATES = [
    BASE_DIR / "web",
]


class SaveAllRequest(BaseModel):
    settings: dict = Field(default_factory=dict)
    register_config_text: str = "{}\n"
    env_text: str = ""


class ProxyTestRequest(BaseModel):
    proxy: str = ""


def _resolve_static_asset(requested_path: str) -> Path | None:
    clean_path = requested_path.strip("/")
    for base_dir in STATIC_CANDIDATES:
        if not base_dir.exists():
            continue
        candidates = [base_dir / "index.html"] if not clean_path else [
            base_dir / Path(clean_path),
            base_dir / clean_path / "index.html",
        ]
        resolved_base = base_dir.resolve()
        for candidate in candidates:
            try:
                candidate.resolve().relative_to(resolved_base)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
    return None


def create_app(manager: RegWebManager | None = None) -> FastAPI:
    ensure_runtime_environment()
    runtime_manager = manager or RegWebManager()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            runtime_manager.shutdown()

    app = FastAPI(title="ChatGPT2API Register Console", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/api/bootstrap")
    async def bootstrap() -> dict:
        return {
            **await run_in_threadpool(runtime_manager.settings_payload),
            "runtime": await run_in_threadpool(runtime_manager.runtime_snapshot),
            "logs": await run_in_threadpool(runtime_manager.get_logs, 0),
        }

    @app.get("/api/settings")
    async def get_settings() -> dict:
        return await run_in_threadpool(runtime_manager.settings_payload)

    @app.put("/api/settings")
    async def save_settings(body: SaveAllRequest) -> dict:
        try:
            return await run_in_threadpool(
                runtime_manager.save_all,
                settings=body.settings,
                register_config_text=body.register_config_text,
                env_text=body.env_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc

    @app.get("/api/runtime")
    async def runtime() -> dict:
        return await run_in_threadpool(runtime_manager.runtime_snapshot)

    @app.get("/api/logs")
    async def logs(cursor: int = 0) -> dict:
        return await run_in_threadpool(runtime_manager.get_logs, cursor)

    @app.get("/api/logs/export")
    async def export_logs() -> Response:
        filename, content = await run_in_threadpool(runtime_manager.export_logs_text)
        return Response(
            content,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/cloud-summary")
    async def cloud_summary() -> dict:
        try:
            return await run_in_threadpool(runtime_manager.cloud_summary)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @app.post("/api/actions/proxy/test")
    async def test_proxy(body: ProxyTestRequest | None = None) -> dict:
        try:
            return await run_in_threadpool(runtime_manager.proxy_test, proxy=(body.proxy if body else ""))
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": str(exc)}) from exc

    @app.post("/api/actions/register")
    async def start_register() -> dict:
        try:
            runtime_manager.start_register()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"ok": True, "runtime": runtime_manager.runtime_snapshot()}

    @app.post("/api/actions/refill")
    async def start_refill() -> dict:
        try:
            runtime_manager.start_refill()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"ok": True, "runtime": runtime_manager.runtime_snapshot()}

    @app.post("/api/actions/monitor/start")
    async def start_monitor() -> dict:
        try:
            runtime_manager.start_monitor()
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"ok": True, "runtime": runtime_manager.runtime_snapshot()}

    @app.post("/api/actions/monitor/stop")
    async def stop_monitor() -> dict:
        try:
            runtime_manager.stop_monitor()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc)}) from exc
        return {"ok": True, "runtime": runtime_manager.runtime_snapshot()}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        asset = _resolve_static_asset(full_path)
        if asset is not None:
            return FileResponse(asset)
        fallback = _resolve_static_asset("")
        if fallback is None:
            raise HTTPException(status_code=404, detail="Frontend not found")
        return FileResponse(fallback)

    return app
