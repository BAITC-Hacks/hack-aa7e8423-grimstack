from fastapi import APIRouter, HTTPException, Request

from app.contracts import DatasetUploaded
from app.ingest import ROLES
from app.store import store

router = APIRouter()
MAX_FILE_BYTES = 30 * 1024 * 1024


@router.post("/datasets/{supplier}", response_model=DatasetUploaded)
async def upload_dataset(supplier: str, request: Request):
    if supplier not in ("IEK", "SE"):
        raise HTTPException(404, "Поставщик не найден")
    form = await request.form()
    files = {}
    for role, value in form.multi_items():
        if role not in ROLES:
            from app.contracts import IngestError
            raise IngestError("unknown_role", f"Неизвестная роль файла: {role}", None)
        if role in files or not hasattr(value, "read"):
            raise HTTPException(422, "Для каждой роли требуется ровно один файл")
        content = await value.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(422, f"Файл {role} превышает 30 МБ")
        files[role] = content
    return store.upload(supplier, files)
