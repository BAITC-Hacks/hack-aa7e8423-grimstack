"""Procurement API. Business calculations stay in app.engine."""

import math
from datetime import datetime, timezone
from io import BytesIO
from typing import cast, get_args
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from openpyxl import Workbook
from starlette.datastructures import UploadFile

from app import ai, engine, ingest
from app.contracts import (
    DatasetUploaded,
    LinePatch,
    Meta,
    OrderLine,
    RunParams,
    RunResult,
    SkuHistory,
    SummaryResponse,
    Supplier,
    SupplierSummary,
)
from app.store import Store


router = APIRouter()
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
EXPORT_COLUMNS = (
    "Код 1С", "Артикул поставщика", "Наименование", "Ед.", "Количество",
    "Цена", "Сумма", "Поставщик", "Срочность", "Обоснование",
)


def get_store(request: Request) -> Store:
    return request.app.state.store


def parse_supplier(raw: str) -> Supplier:
    # Path values are checked here instead of Literal validation: unknown paths are 404.
    if raw not in get_args(Supplier):
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    return cast(Supplier, raw)


def get_run(store: Store, run_id: str) -> RunResult:
    try:
        return store.runs[run_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Расчёт не найден") from exc


def get_supplier(run: RunResult, supplier: Supplier) -> SupplierSummary:
    found = next((s for s in run.suppliers if s.supplier == supplier), None)
    if found is None:
        raise HTTPException(status_code=404, detail="Поставщик не найден в расчёте")
    return found


@router.get("/meta", response_model=Meta)
def meta(store: Store = Depends(get_store)) -> Meta:
    return engine.meta(store.default_dataset)


@router.post("/runs", response_model=RunResult)
def create_run(params: RunParams, store: Store = Depends(get_store)) -> RunResult:
    try:
        dataset = store.dataset_for(params.dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Набор данных не найден") from exc
    result = engine.run(dataset, params)
    store.save_run(result, dataset)
    return result


@router.get("/runs/{run_id}", response_model=RunResult)
def read_run(run_id: str, store: Store = Depends(get_store)) -> RunResult:
    with store.lock:
        return get_run(store, run_id).model_copy(deep=True)


@router.patch("/runs/{run_id}/lines/{line_id}", response_model=OrderLine)
def patch_line(
    run_id: str, line_id: str, patch: LinePatch, store: Store = Depends(get_store)
) -> OrderLine:
    with store.lock:
        run = get_run(store, run_id)
        line = next((item for item in run.lines if item.line_id == line_id), None)
        if line is None:
            raise HTTPException(status_code=404, detail="Строка заказа не найдена")
        if get_supplier(run, line.supplier).status == "approved":
            raise HTTPException(status_code=409, detail="Заказ поставщика уже утверждён")

        quantity = patch.final_qty
        multiple = line.moq
        if not math.isfinite(quantity) or multiple <= 0 or not math.isfinite(multiple):
            raise HTTPException(status_code=422, detail="Некорректное количество или кратность")
        rounded = round(quantity / multiple) * multiple
        if not math.isclose(quantity, rounded, rel_tol=0, abs_tol=1e-7):
            raise HTTPException(
                status_code=422, detail=f"Количество должно быть кратно {multiple:g}"
            )

        line.final_qty = quantity
        line.amount = (
            round(quantity * line.unit_cost, 2)
            if line.unit_cost is not None and math.isfinite(line.unit_cost)
            else None
        )
        store.refresh_totals(run)
        return line.model_copy(deep=True)


@router.post("/runs/{run_id}/suppliers/{supplier}/approve", response_model=SupplierSummary)
def approve_supplier(
    run_id: str, supplier: str, store: Store = Depends(get_store)
) -> SupplierSummary:
    selected = parse_supplier(supplier)
    with store.lock:
        run = get_run(store, run_id)
        summary = get_supplier(run, selected)
        if summary.status == "approved":
            return summary.model_copy(deep=True)

        approved_at = datetime.now(timezone.utc)
        lines = [
            line.model_dump(mode="json")
            for line in run.lines
            if line.supplier == selected and line.final_qty > 0
        ]
        store.append_approval({
            "run_id": run_id,
            "supplier": selected,
            "approved_at": approved_at.isoformat(),
            "data_as_of": run.data_as_of.isoformat(),
            "lines": lines,
        })
        summary.status = "approved"
        summary.approved_at = approved_at
        return summary.model_copy(deep=True)


@router.get("/runs/{run_id}/export.xlsx")
def export_run(
    run_id: str, supplier: str, store: Store = Depends(get_store)
) -> Response:
    selected = parse_supplier(supplier)
    with store.lock:
        run = get_run(store, run_id)
        get_supplier(run, selected)
        lines = [
            line.model_copy(deep=True)
            for line in run.lines
            if line.supplier == selected and line.final_qty > 0
        ]
        filename = f"order_{selected}_{run.created_at.date().isoformat()}.xlsx"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = selected
    sheet.append(EXPORT_COLUMNS)
    for line in lines:
        values = (
            line.sku, line.article, line.name, line.unit, line.final_qty,
            line.unit_cost, line.amount, line.supplier, line.urgency, line.explanation,
        )
        sheet.append(values)
        # Prevent an uploaded SKU/name/explanation starting with '=' from becoming a formula.
        for index in (1, 2, 3, 4, 8, 9, 10):
            cell = sheet.cell(sheet.max_row, index)
            if isinstance(cell.value, str):
                cell.data_type = "s"
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/sku/{supplier}/{sku}/history", response_model=SkuHistory)
def sku_history(
    supplier: str, sku: str, run_id: str | None = None, store: Store = Depends(get_store)
) -> SkuHistory:
    selected = parse_supplier(supplier)
    with store.lock:
        if run_id is None:
            dataset, params = store.default_dataset, RunParams()
        else:
            run = get_run(store, run_id)
            get_supplier(run, selected)
            dataset, params = store.run_datasets[run_id], run.params
    try:
        return engine.history(dataset, selected, sku, params)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Товар не найден") from exc


@router.post("/datasets/{supplier}", response_model=DatasetUploaded)
async def upload_dataset(
    supplier: str, request: Request, store: Store = Depends(get_store)
) -> DatasetUploaded:
    selected = parse_supplier(supplier)
    form = await request.form()
    files: dict[str, bytes] = {}
    for role, item in form.multi_items():
        if role in files:
            raise HTTPException(status_code=422, detail=f"Файл {role} передан дважды")
        if not isinstance(item, UploadFile):
            raise HTTPException(status_code=422, detail=f"Поле {role} должно быть файлом")
        content = await item.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=422, detail=f"Файл {role} больше 30 МБ")
        files[role] = content

    dataset = ingest.load_uploaded(selected, files)
    dataset_id = uuid4().hex[:12]
    with store.lock:
        store.datasets[dataset_id] = dataset
    warnings = dataset.get("warnings", []) if isinstance(dataset, dict) else getattr(dataset, "warnings", [])
    return DatasetUploaded(dataset_id=dataset_id, supplier=selected, warnings=list(warnings))


@router.post("/runs/{run_id}/summary", response_model=SummaryResponse)
def summarize_run(
    run_id: str, supplier: str, store: Store = Depends(get_store)
) -> SummaryResponse:
    selected = parse_supplier(supplier)
    with store.lock:
        run = get_run(store, run_id)
        get_supplier(run, selected)
        snapshot = run.model_copy(deep=True)
    summary = ai.summary(snapshot, selected)
    if summary is None:
        raise HTTPException(status_code=503, detail="LLM недоступна: нет ключа и кэша")
    return summary
