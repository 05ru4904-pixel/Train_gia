"""FastAPI-приложение: API для Mini App и отдача самого приложения."""
import logging
import re
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.routers import profile, stats, training, variant
from db.crud import total_tasks_count, variants_count
from db.database import SessionMaker

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"

# Версия сборки. Telegram держит WebView в памяти и переоткрывает его без нового
# запроса, поэтому статику версионируем при каждом старте процесса (playbook 5.3).
APP_VERSION = int(time.time())

app = FastAPI(
    title="Тренажёр ЕГЭ",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# Сжатие бандла и JSON — минус 60-75% трафика (playbook 6). Второй совет оттуда,
# ORJSONResponse, здесь не нужен: FastAPI 0.139 сериализует в JSON сам и помечает
# этот класс устаревшим.
app.add_middleware(GZipMiddleware, minimum_size=500)

app.include_router(profile.router, prefix="/api")
app.include_router(training.router, prefix="/api")
app.include_router(variant.router, prefix="/api")
app.include_router(stats.router, prefix="/api")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_index_cache: str | None = None


def _render_index() -> str:
    """Читает index.html один раз и проставляет версию в ссылки на статику."""
    global _index_cache
    if _index_cache is None:
        if not INDEX_FILE.exists():
            return "<h1>static/index.html не найден</h1>"
        html = INDEX_FILE.read_text(encoding="utf-8")
        # Пути к статике только абсолютные (/static/...), иначе Telegram запросит
        # их от корня и получит 404 (playbook 5.1).
        html = re.sub(
            r'((?:src|href)="/static/[^"?]+)"',
            rf'\1?v={APP_VERSION}"',
            html,
        )
        _index_cache = html
    return _index_cache


@app.get("/app", response_class=HTMLResponse)
async def mini_app() -> HTMLResponse:
    return HTMLResponse(
        _render_index(),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse("/app")


@app.get("/health")
async def health() -> dict:
    """Проверка живости вместе с доступностью базы."""
    async with SessionMaker() as db:
        tasks = await total_tasks_count(db)
        variants = await variants_count(db)
    return {"status": "ok", "version": APP_VERSION, "tasks": tasks, "variants": variants}
