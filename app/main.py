"""FastAPI application and optional single-page frontend."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import ingest
from app.api.routes import router
from app.contracts import ErrorBody, IngestError
from app.store import DEFAULT_APPROVALS_PATH, Store


logger = logging.getLogger(__name__)
DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def error_response(status: int, detail: str, code: str, meta: dict | None = None) -> JSONResponse:
    body = ErrorBody(detail=detail, code=code, meta=meta or {})
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: dict):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404 or scope["method"] not in ("GET", "HEAD"):
                raise
            return FileResponse(Path(self.directory) / "index.html")


def create_app(
    approval_path: Path | None = None, frontend_dist: Path | None = None
) -> FastAPI:
    approvals = approval_path or DEFAULT_APPROVALS_PATH
    dist = frontend_dist or DEFAULT_FRONTEND_DIST

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(default_dataset=ingest.load_default(), approvals_path=approvals)
        yield

    app = FastAPI(title="GrimStack закупки", lifespan=lifespan)

    @app.exception_handler(IngestError)
    async def ingest_error(_request: Request, exc: IngestError) -> JSONResponse:
        return error_response(
            422, exc.message, exc.code, {"file_role": exc.file_role}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            errors.append({"field": location, "type": error["type"]})
        return error_response(
            422, "Некорректные данные запроса", "validation_error", {"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        codes = {404: "not_found", 409: "conflict", 422: "validation_error", 503: "unavailable"}
        return error_response(
            exc.status_code, str(exc.detail), codes.get(exc.status_code, "http_error")
        )

    @app.exception_handler(Exception)
    async def unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Необработанная ошибка API", exc_info=exc)
        return error_response(500, "Внутренняя ошибка сервера", "internal_error")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router, prefix="/api")

    # Keep unknown API paths in the same ErrorBody format even with SPA at '/'.
    @app.api_route(
        "/api", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def unknown_api_root():
        raise HTTPException(status_code=404, detail="Маршрут API не найден")

    @app.api_route(
        "/api/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    def unknown_api_path(path: str):
        raise HTTPException(status_code=404, detail="Маршрут API не найден")

    if (dist / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()
