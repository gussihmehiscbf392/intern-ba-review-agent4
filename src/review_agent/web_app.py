from __future__ import annotations

import os
import secrets
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .engine import run_review
from .reporting import build_criteria_view
from .reporting import build_llm_status_view
from .reporting import save_outputs


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "web_templates"
UPLOADS_DIR = REPO_ROOT / "uploads"
OUTPUTS_DIR = REPO_ROOT / "outputs"
DEFAULT_PROFILE = "analysts_2026_requirements"

UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="BA Review MVP")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
security = HTTPBasic(auto_error=False)


def _safe_filename(name: str) -> str:
    base = Path(name).name
    safe_chars = []
    for char in base:
        if char.isalnum() or char in {" ", ".", "-", "_", "(", ")"}:
            safe_chars.append(char)
        else:
            safe_chars.append("_")
    return "".join(safe_chars).strip() or "submission.docx"


def _resolve_output_path(filename: str) -> Path:
    candidate = (OUTPUTS_DIR / filename).resolve()
    output_root = OUTPUTS_DIR.resolve()
    if output_root not in candidate.parents and candidate != output_root:
        raise HTTPException(status_code=404, detail="Файл не найден")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="Файл не найден")
    if candidate.suffix.lower() not in {".json", ".md"}:
        raise HTTPException(status_code=400, detail="Недопустимый формат файла")
    return candidate


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Требуется вход",
        headers={"WWW-Authenticate": "Basic"},
    )


def _require_auth(credentials: HTTPBasicCredentials | None = Depends(security)) -> None:
    expected_password = os.getenv("REVIEW_APP_PASSWORD", "").strip()
    if not expected_password:
        return

    expected_user = os.getenv("REVIEW_APP_USER", "mentor").strip() or "mentor"
    if credentials is None:
        raise _unauthorized()

    user_ok = secrets.compare_digest(credentials.username, expected_user)
    password_ok = secrets.compare_digest(credentials.password, expected_password)
    if not user_ok or not password_ok:
        raise _unauthorized()


def _max_upload_bytes() -> int:
    raw_value = os.getenv("REVIEW_MAX_UPLOAD_MB", "25").strip()
    try:
        max_mb = int(raw_value)
    except ValueError:
        max_mb = 25
    return max(1, max_mb) * 1024 * 1024


def _expected_points_discrete(max_points: int, score_pct: float) -> int:
    # По критериям оценки допускаются только дискретные баллы:
    # 0/1 для большинства критериев и 0/1/2 для критерия с весом 2.
    raw_points = max_points * score_pct / 100.0
    rounded = int(raw_points + 0.5)
    return max(0, min(max_points, rounded))


def _total_expected_points(result: dict) -> int:
    total = 0
    for item in result.get("criteria", []):
        max_points = int(item.get("weight", 0))
        score_pct = float(item.get("score", 0))
        total += _expected_points_discrete(max_points, score_pct)
    return total


@app.get("/", response_class=HTMLResponse)
def page_index(request: Request, _: None = Depends(_require_auth)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "default_profile": DEFAULT_PROFILE,
            "error_message": "",
        },
    )


@app.post("/review", response_class=HTMLResponse)
async def page_review(
    request: Request,
    submission_file: UploadFile = File(...),
    profile_id: str = Form(DEFAULT_PROFILE),
    disable_llm: bool = Form(False),
    _: None = Depends(_require_auth),
) -> HTMLResponse:
    filename = _safe_filename(submission_file.filename or "submission.docx")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".docx", ".pdf"}:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "default_profile": profile_id or DEFAULT_PROFILE,
                "error_message": "Поддерживаются только DOCX и PDF.",
            },
            status_code=400,
        )

    storage_name = f"{uuid4().hex}_{filename}"
    upload_path = UPLOADS_DIR / storage_name

    file_bytes = await submission_file.read()
    max_upload_bytes = _max_upload_bytes()
    if len(file_bytes) > max_upload_bytes:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "default_profile": profile_id or DEFAULT_PROFILE,
                "error_message": f"Файл слишком большой. Максимум: {max_upload_bytes // (1024 * 1024)} МБ.",
            },
            status_code=413,
        )
    upload_path.write_bytes(file_bytes)

    result = run_review(
        input_path=str(upload_path),
        profile_id=profile_id or DEFAULT_PROFILE,
        enable_llm=not disable_llm,
    )
    criteria_view = build_criteria_view(result) if result.get("status") == "ok" else []
    llm_status_view = build_llm_status_view(result) if result.get("status") == "ok" else {}
    max_total_points = (
        sum(int(item.get("weight", 0)) for item in result.get("criteria", []))
        if result.get("status") == "ok"
        else 0
    )
    total_expected_points = _total_expected_points(result) if result.get("status") == "ok" else 0
    base_name = f"review_web_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    json_path, md_path = save_outputs(result, str(OUTPUTS_DIR), base_name)

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "request": request,
            "result": result,
            "criteria_view": criteria_view,
            "llm_status_view": llm_status_view,
            "max_total_points": max_total_points,
            "total_expected_points": total_expected_points,
            "json_name": json_path.name,
            "md_name": md_path.name,
            "input_filename": filename,
        },
    )


@app.get("/outputs/{filename}")
def page_output_file(filename: str, _: None = Depends(_require_auth)) -> FileResponse:
    path = _resolve_output_path(filename)
    media_type = "application/json" if path.suffix.lower() == ".json" else "text/markdown"
    return FileResponse(path=str(path), filename=path.name, media_type=media_type)
