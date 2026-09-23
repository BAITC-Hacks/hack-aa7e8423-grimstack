"""Сборка FastAPI и общие обработчики ошибок."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import datasets, meta, runs
from app.contracts import ErrorBody, IngestError
from app.store import store

LOG = logging.getLogger(__name__)
DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.load()
    yield


app = FastAPI(lifespan=lifespan)


def error(status: int, detail: str, code: str, meta_data: dict | None = None):
    body = ErrorBody(detail=detail, code=code, meta=meta_data or {})
    return JSONResponse(status_code=status, content=body.model_dump())


@app.exception_handler(IngestError)
async def ingest_error(_request: Request, exc: IngestError):
    return error(422, exc.message, exc.code, {"file_role": exc.file_role} if exc.file_role else {})


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    message = "Некорректные параметры запроса"
    if exc.errors():
        field = ".".join(str(part) for part in exc.errors()[0]["loc"] if part != "body")
        message = f"Некорректное значение: {field}"
    return error(422, message, "validation_error")


@app.exception_handler(StarletteHTTPException)
async def http_error(_request: Request, exc: StarletteHTTPException):
    codes = {404: "not_found", 409: "conflict", 422: "invalid_input", 503: "unavailable"}
    return error(exc.status_code, str(exc.detail), codes.get(exc.status_code, "http_error"))


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception):
    LOG.exception("Необработанная ошибка API", exc_info=exc)
    return error(500, "Внутренняя ошибка сервера", "internal_error")


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(meta.router, prefix="/api")
app.include_router(datasets.router, prefix="/api")
app.include_router(runs.router, prefix="/api")


if (DIST / "index.html").is_file():
    class SPAFiles(StaticFiles):
        async def get_response(self, path: str, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404 and not scope["path"].startswith("/api") and "." not in Path(path).name:
                    return FileResponse(DIST / "index.html")
                raise

    app.mount("/", SPAFiles(directory=DIST, html=True), name="frontend")
