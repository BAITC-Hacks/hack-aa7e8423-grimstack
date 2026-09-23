"""Выгрузки 1С → Dataset. Владелец — ядро (А).

Сигнатуры финальные.
"""

import tempfile
from datetime import date
from pathlib import Path
from typing import get_args

import pandas as pd

from app.contracts import FileRole, IngestError, Supplier
from app.ingest import iek
from app.ingest.dataset import Dataset, concat

try:
    from app.ingest import se
except ImportError:  # загрузчик SE ещё не готов — грузим только IEK
    se = None

ROLES: tuple[str, ...] = get_args(FileRole)
REQUIRED_ROLES = ("monthly_sales", "monthly_stock", "in_transit", "moq")

AS_OF = date(2026, 9, 22)
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_CATEGORIES_CSV = DATA_DIR / "categories" / "sku_categories.csv"
_LOADERS = {"IEK": iek.load, "SE": getattr(se, "load", None)}


def load_default() -> Dataset:
    parts = [iek.load(DATA_DIR / "raw" / "iek", AS_OF)]
    if se is not None:
        parts.append(se.load(DATA_DIR / "raw" / "se", AS_OF))
    data = concat(parts)
    _apply_product_groups(data)
    return data


def _apply_product_groups(data: Dataset) -> None:
    """Подмешивает группу Laya (data/categories/sku_categories.csv: supplier?, sku, group, prob)."""
    if not _CATEGORIES_CSV.exists():
        return
    cats = pd.read_csv(_CATEGORIES_CSV, dtype=str)
    if cats.empty or "sku" not in cats.columns or "group" not in cats.columns:
        return
    on = ["supplier", "sku"] if "supplier" in cats.columns else ["sku"]
    cats = cats.drop_duplicates(subset=on, keep="first")
    skus = data.skus.reset_index()
    merged = skus.merge(cats[on + ["group"]], on=on, how="left")
    data.skus["product_group"] = merged["group"].where(merged["group"].notna(), data.skus["product_group"].values)


def load_uploaded(supplier: Supplier, files: dict[str, bytes]) -> Dataset:
    """Проверяет набор файлов; при некорректном вводе бросает IngestError (API → 422)."""
    unknown = sorted(set(files) - set(ROLES))
    if unknown:
        raise IngestError("unknown_role", f"Неизвестные поля файлов: {', '.join(unknown)}")
    missing = [r for r in REQUIRED_ROLES if r not in files]
    if missing:
        raise IngestError("missing_file", f"Не хватает файлов: {', '.join(missing)}", missing[0])
    for role, content in files.items():
        if not content:
            raise IngestError("empty_file", "Файл пустой", role)
        if not content.startswith(b"PK"):  # xlsx — это zip
            raise IngestError("not_xlsx", "Ожидается файл Excel .xlsx", role)

    loader = _LOADERS.get(supplier)
    if loader is None:
        raise IngestError("bad_format", f"Загрузка для поставщика {supplier} недоступна")

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for role, content in files.items():
            (folder / f"{role}.xlsx").write_bytes(content)
        try:
            return loader(folder, AS_OF)
        except Exception as exc:
            raise IngestError("bad_format", f"Не удалось разобрать файл: {exc}") from exc
