"""Выгрузки 1С → Dataset. Владелец — ядро (А).

Сигнатуры финальные. Тело load_default — заглушка до готовности загрузчиков.
"""

from typing import get_args

from app.contracts import FileRole, IngestError, Supplier

ROLES: tuple[str, ...] = get_args(FileRole)
REQUIRED_ROLES = ("monthly_sales", "monthly_stock", "in_transit", "moq")

Dataset = dict  # ponytail: заглушка, станет dataclass с каноническими таблицами


def load_default() -> Dataset:
    return {"source": "default"}


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
    return {"source": "upload", "supplier": supplier}
