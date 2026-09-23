"""Операции заказа; расчёт выполняет только engine."""

from io import BytesIO
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook

from app import ai, engine
from app.contracts import LinePatch, OrderLine, RunParams, RunResult, SkuHistory, SummaryResponse, SupplierSummary
from app.store import store

router = APIRouter()


def _run(run_id: str) -> RunResult:
    result = store.runs.get(run_id)
    if result is None:
        raise HTTPException(404, "Прогон не найден")
    return result


def _supplier(value: str) -> str:
    if value not in ("IEK", "SE"):
        raise HTTPException(404, "Поставщик не найден")
    return value


@router.post("/runs", response_model=RunResult)
def create_run(params: RunParams):
    dataset = store.dataset(params.dataset_id)
    if dataset is None:
        raise HTTPException(404, "Набор данных не найден")
    result = engine.run(dataset, params)
    store.save_run(result, dataset)
    return result


@router.get("/runs/{run_id}", response_model=RunResult)
def get_run(run_id: str):
    return _run(run_id)


@router.patch("/runs/{run_id}/lines/{line_id}", response_model=OrderLine)
def patch_line(run_id: str, line_id: str, patch: LinePatch):
    result = _run(run_id)
    line = store.line(result, line_id)
    if line is None:
        raise HTTPException(404, "Строка заказа не найдена")
    summary = store.supplier(result, line.supplier)
    if summary.status == "approved":
        raise HTTPException(409, "Заказ поставщику уже утверждён")
    ratio = patch.final_qty / line.moq
    if abs(ratio - round(ratio)) > 1e-8:
        raise HTTPException(422, f"Количество должно быть кратно {line.moq:g}")
    store.update_line(result, line, patch.final_qty)
    return line


@router.post("/runs/{run_id}/suppliers/{supplier}/approve", response_model=SupplierSummary)
def approve(run_id: str, supplier: str):
    result = _run(run_id)
    summary = store.supplier(result, _supplier(supplier))
    if summary is None:
        raise HTTPException(404, "Поставщик в прогоне не найден")
    if summary.status == "approved":
        raise HTTPException(409, "Заказ поставщику уже утверждён")
    store.approve(result, summary)
    return summary


@router.get("/runs/{run_id}/export.xlsx")
def export_xlsx(run_id: str, supplier: str):
    result = _run(run_id)
    code = _supplier(supplier)
    if store.supplier(result, code) is None:
        raise HTTPException(404, "Поставщик в прогоне не найден")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Заказ"
    sheet.append(["Код 1С", "Артикул поставщика", "Наименование", "Ед.", "Количество", "Цена",
                  "Сумма", "Поставщик", "Срочность", "Обоснование"])
    for line in result.lines:
        if line.supplier == code and line.final_qty > 0:
            sheet.append([line.sku, line.article, line.name, line.unit, line.final_qty, line.unit_cost,
                          line.amount, line.supplier, line.urgency, line.explanation])
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    filename = f"order_{code}_{date.today().isoformat()}.xlsx"
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/sku/{supplier}/{sku}/history", response_model=SkuHistory)
def sku_history(supplier: str, sku: str):
    code = _supplier(supplier)
    dataset = store.latest_dataset or store.datasets["default"]
    try:
        return engine.history(dataset, code, sku, RunParams(supplier=code))
    except KeyError:
        raise HTTPException(404, "SKU не найден") from None


@router.post("/runs/{run_id}/summary", response_model=SummaryResponse)
def summary(run_id: str, supplier: str):
    result = _run(run_id)
    code = _supplier(supplier)
    if store.supplier(result, code) is None:
        raise HTTPException(404, "Поставщик в прогоне не найден")
    response = ai.summary(result, code)
    if response is None:
        raise HTTPException(503, "LLM недоступна: нет ключа и кэша")
    return response
