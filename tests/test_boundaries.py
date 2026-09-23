"""Слои: engine и ingest — чистый домен и инфраструктура, про HTTP не знают (docs/structure.md)."""

import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
WEB_IMPORT = re.compile(r"^\s*(from|import)\s+(fastapi|starlette|uvicorn|app\.api|app\.main|app\.store)\b", re.M)


def test_core_does_not_import_web_layer():
    offenders = [str(p.relative_to(APP)) for layer in ("engine", "ingest")
                 for p in (APP / layer).rglob("*.py") if WEB_IMPORT.search(p.read_text(encoding="utf-8"))]
    assert not offenders, f"ядро импортирует веб-слой: {offenders}"
