"""FastAPI 后端：提供逐行审校 API 与静态前端托管。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import analyze_plan

app = FastAPI(title="编织行指令审校", version="1.0.0")


class AnalyzeRequest(BaseModel):
    start_stitches: int = Field(..., description="起始针数（非负整数）", ge=0)
    rows: list[str] = Field(..., description="逐行指令源文本")


class AnalyzeResponse(BaseModel):
    rows: list[dict]


@app.post("/api/analyze", response_model=AnalyzeResponse)
def api_analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    return AnalyzeResponse(rows=[r.to_dict() for r in analyze_plan(req.start_stitches, req.rows)])


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# 静态前端（目录不存在时不挂载，方便只跑引擎测试）
_static_dir = None
_static_path = None
try:
    from pathlib import Path

    _static_path = Path(__file__).resolve().parent / "static"
    if _static_path.is_dir():
        _static_dir = str(_static_path)
        app.mount("/static", StaticFiles(directory=_static_dir), name="static")
except Exception:  # pragma: no cover - 静态目录问题不应阻断 API
    pass


@app.get("/")
def index() -> FileResponse:
    if _static_dir is None:
        raise HTTPException(status_code=404, detail="前端目录不存在")
    return FileResponse(f"{_static_dir}/index.html")
