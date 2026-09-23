"""Выгрузки 1С → Dataset. Владелец — ядро (А).

Сигнатуры финальные.
"""

import logging
import re
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from typing import get_args

import pandas as pd

from app.contracts import FileRole, IngestError, Supplier
from app.ingest import common, iek
from app.ingest.dataset import Dataset, concat

logger = logging.getLogger(__name__)

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
    apply_product_groups(data, _CATEGORIES_CSV)
    return data


def apply_product_groups(data: Dataset, csv_path: Path) -> None:
    """Подмешивает товарную группу Laya из CSV (supplier?, sku, group, prob) в skus.product_group."""
    if not csv_path.exists() or not hasattr(data, "skus"):
        return
    cats = pd.read_csv(csv_path, dtype=str)
    if cats.empty or not {"sku", "group"} <= set(cats.columns):
        return
    on = ["supplier", "sku"] if "supplier" in cats.columns else ["sku"]
    groups = cats.drop_duplicates(subset=on).set_index(on)["group"]
    keys = data.skus.index if on == ["supplier", "sku"] else data.skus.index.get_level_values("sku")
    found = groups.reindex(keys).to_numpy()  # по позиции, а не по меткам индекса skus
    current = data.skus["product_group"].to_numpy()
    data.skus["product_group"] = pd.Series([g if isinstance(g, str) else c for g, c in zip(found, current)],
                                           index=data.skus.index, dtype=object)


def _friendly_ingest_error(exc: Exception, supplier: str) -> IngestError:
    """Человеческое сообщение вместо сырого str(exc) — сырую ошибку логируем,
    наружу отдаём код + роль файла (последний, что читал common.read_sheet до сбоя)."""
    role = common.last_read_role()
    logger.warning("Не разобралась выгрузка %s (роль=%s): %r", supplier, role, exc, exc_info=True)
    if role is None:  # сбой до чтения любого файла — роль неизвестна
        return IngestError("bad_format", f"Файлы не похожи на выгрузку 1С для {supplier}")
    if isinstance(exc, ValueError) and "Worksheet named" in str(exc):
        m = re.search(r"Worksheet named '([^']+)' not found", str(exc))
        sheet = m.group(1) if m else "?"
        return IngestError("bad_format", f"В файле «{role}» нет листа «{sheet}»", role)
    if isinstance(exc, (zipfile.BadZipFile, ValueError)):
        return IngestError("bad_format", f"Файл «{role}» повреждён или это не .xlsx", role)
    if isinstance(exc, KeyError) and isinstance(exc.args[0], str):
        return IngestError("bad_format", f"В файле «{role}» не найдена колонка «{exc.args[0]}»", role)
    if isinstance(exc, (KeyError, IndexError)):
        return IngestError("bad_format", f"В файле «{role}» меньше колонок, чем в выгрузке 1С", role)
    return IngestError("bad_format", f"Файл «{role}» не похож на выгрузку 1С", role)


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
        common._last_role = None  # роль прошлой загрузки не должна попасть в эту ошибку
        try:
            data = loader(folder, AS_OF)
        except Exception as exc:
            raise _friendly_ingest_error(exc, supplier) from exc
    apply_product_groups(data, _CATEGORIES_CSV)  # как в load_default: группы нужны фильтру и пулу сезонности
    return data
