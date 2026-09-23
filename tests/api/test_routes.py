"""API contract checks with isolated sample responses from the calculation core."""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import ai, engine, ingest, store as store_module
from app.engine import pipeline
from app.api import routes as api_routes
from app.api.routes import EXPORT_COLUMNS, MAX_UPLOAD_BYTES
from app.contracts import Meta, RunResult, SkuHistory
from app.main import create_app
from tests.engine.factory import make_dataset, noisy


CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


@pytest.fixture(autouse=True)
def sample_core(monkeypatch):
    """Keep HTTP tests deterministic while ingestion and forecasting evolve separately."""

    def sample(name):
        return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))

    def run(_data, params):
        result = RunResult.model_validate(sample("sample_run.json"))
        result.run_id = uuid4().hex[:12]
        result.created_at = datetime.now(timezone.utc)
        result.params = params
        if params.supplier is not None:
            result.lines = [line for line in result.lines if line.supplier == params.supplier]
            result.suppliers = [s for s in result.suppliers if s.supplier == params.supplier]
        return result

    def history(_data, supplier, sku, _params):
        result = SkuHistory.model_validate(sample("sample_sku_history.json"))
        result.supplier = supplier
        result.sku = sku
        return result

    monkeypatch.setattr(ingest, "load_default", lambda: {"source": "default"})
    monkeypatch.setattr(engine, "run", run)
    monkeypatch.setattr(engine, "meta", lambda _data: Meta.model_validate(sample("sample_meta.json")))
    monkeypatch.setattr(engine, "history", history)
    monkeypatch.setitem(ingest._LOADERS, "IEK", lambda _folder, _as_of: make_dataset({"A": noisy(10)}))
    monkeypatch.setattr(ai, "summary", lambda _run, _supplier: None)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(approval_path=tmp_path / "app.db")) as test_client:
        yield test_client


def new_run(client, **params):
    response = client.post("/api/runs", json=params)
    assert response.status_code == 200, response.text
    return response.json()


def assert_error(response, status):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"detail", "code", "meta"}
    assert isinstance(body["detail"], str) and body["detail"]
    return body


def test_startup_and_request_logging(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="app.main"):
        with TestClient(create_app(approval_path=tmp_path / "app.db")) as client:
            assert client.get("/health").status_code == 200
    messages = [record.getMessage() for record in caplog.records if record.name == "app.main"]
    assert any("Данные загружены за" in message for message in messages)
    assert any("GET /health -> 200 за" in message for message in messages)


def test_swagger_documents_routes_errors_and_upload(client):
    docs = client.get("/docs")
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text

    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "Автозаказ поставщикам — Электрокомплект"
    assert schema["info"]["description"]
    assert schema["info"]["version"]
    expected_errors = {
        ("/api/meta", "get"): {404},
        ("/api/backtest", "get"): {404},
        ("/api/runs", "post"): {404, 422},
        ("/api/runs/compare", "post"): {404, 422},
        ("/api/runs/{run_id}", "get"): {404},
        ("/api/runs/{run_id}/lines/{line_id}", "patch"): {404, 409, 422},
        ("/api/runs/{run_id}/suppliers/{supplier}/approve", "post"): {404},
        ("/api/runs/{run_id}/export.xlsx", "get"): {404, 422},
        ("/api/sku/{supplier}/{sku}/history", "get"): {404},
        ("/api/datasets/{supplier}", "post"): {404, 422},
        ("/api/datasets/new", "post"): {422},
        ("/api/runs/{run_id}/summary", "post"): {404, 422, 503},
    }
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            assert operation["tags"] and operation["summary"] and operation["description"]
            for status in expected_errors.get((path, method), set()):
                error_schema = operation["responses"][str(status)]["content"]["application/json"]["schema"]
                assert error_schema["$ref"].endswith("/ErrorBody")

    upload = schema["paths"]["/api/datasets/{supplier}"]["post"]["requestBody"]
    form = upload["content"]["multipart/form-data"]["schema"]
    assert set(form["required"]) == {"monthly_sales", "monthly_stock", "in_transit", "moq"}
    assert form["properties"]["monthly_sales"]["format"] == "binary"
    new_form = schema["paths"]["/api/datasets/new"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert {"name", "template", "monthly_sales", "monthly_stock", "in_transit", "moq"} <= set(new_form["required"])
    assert new_form["properties"]["template"]["enum"] == ["IEK", "SE"]


def test_backtest_report_and_missing_file(client, tmp_path, monkeypatch):
    report = client.get("/api/backtest")
    assert report.status_code == 200
    assert {"suppliers", "checkpoints"} <= report.json().keys()

    monkeypatch.setattr(api_routes, "BACKTEST_REPORT_PATH", tmp_path / "missing.json")
    error = assert_error(client.get("/api/backtest"), 404)
    assert error["code"] == "backtest_not_found"


def test_compare_runs_counts_amount_and_added_removed_lines(client, monkeypatch):
    def fake_run(_dataset, params):
        result = RunResult.model_validate(json.loads(
            (CONTRACTS / "sample_run.json").read_text(encoding="utf-8")
        ))
        result.run_id = uuid4().hex[:12]
        result.params = params
        se_lines = [line for line in result.lines if line.supplier == "SE"]
        if params.lead_time_days == 38:
            selected = [(se_lines[0], 20, "critical"),
                        (se_lines[2], 30, "critical"),
                        (se_lines[4], 7, "planned")]
        else:
            selected = [(se_lines[0], 10, "planned"),
                        (se_lines[1], 5, "critical")]
        for line, quantity, urgency in selected:
            line.recommended_qty = line.final_qty = quantity
            line.urgency = urgency
            line.amount = round(quantity * line.unit_cost, 2)
        result.lines = [line for line, _, _ in selected]
        result.suppliers = [summary for summary in result.suppliers if summary.supplier == "SE"]
        return result

    monkeypatch.setattr(engine, "run", fake_run)
    response = client.post("/api/runs/compare", json={
        "base": {"supplier": "SE"},
        "scenario": {"supplier": "SE", "lead_time_days": 38},
    })
    assert response.status_code == 200, response.text
    comparison = response.json()
    assert comparison["base_run_id"] != comparison["scenario_run_id"]
    assert set(client.app.state.store.runs) == {
        comparison["base_run_id"], comparison["scenario_run_id"]
    }
    assert comparison["delta"] == {
        "lines_to_order": 1,
        "critical": 1,
        "total_qty": 42,
        "total_amount": 33519.72,
    }
    assert [line["line_id"] for line in comparison["changed"]] == [
        "SE:300200700_", "SE:130300027_", "SE:030200193_", "SE:130300028_"
    ]
    added = comparison["changed"][0]
    assert (added["base_qty"], added["scenario_qty"]) == (0, 30)
    assert (added["base_urgency"], added["scenario_urgency"]) == ("none", "critical")
    removed = comparison["changed"][-1]
    assert (removed["base_qty"], removed["scenario_qty"]) == (5, 0)
    assert (removed["base_urgency"], removed["scenario_urgency"]) == ("critical", "none")
    assert client.get(f"/api/runs/{comparison['base_run_id']}").status_code == 200
    assert client.get(f"/api/runs/{comparison['scenario_run_id']}").status_code == 200


def test_compare_runs_missing_dataset_and_unknown_amount(client):
    missing = assert_error(client.post("/api/runs/compare", json={
        "base": {}, "scenario": {"dataset_id": "missing"}
    }), 404)
    assert missing["code"] == "not_found"
    assert client.app.state.store.runs == {}

    response = client.post("/api/runs/compare", json={
        "base": {"supplier": "IEK"},
        "scenario": {"supplier": "IEK", "lead_time_days": 38},
    })
    assert response.status_code == 200, response.text
    assert response.json()["delta"]["total_amount"] is None
    assert response.json()["changed"] == []


def test_compare_runs_rejects_invalid_params_and_equal_base_scenario(client):
    assert_error(client.post("/api/runs/compare", json={
        "base": {}, "scenario": {"growth_pct": 9999}
    }), 422)

    response = client.post("/api/runs/compare", json={
        "base": {"supplier": "SE"}, "scenario": {"supplier": "SE"}
    })
    assert response.status_code == 200, response.text
    assert response.json()["delta"] == {
        "lines_to_order": 0, "critical": 0, "total_qty": 0, "total_amount": 0
    }
    assert response.json()["changed"] == []


def test_health_meta_and_saved_filtered_run(client):
    assert client.get("/health").json() == {"status": "ok"}
    meta = client.get("/api/meta")
    assert meta.status_code == 200
    assert {s["supplier"] for s in meta.json()["suppliers"]} == {"IEK", "SE"}

    run = new_run(client, supplier="IEK")
    assert {line["supplier"] for line in run["lines"]} == {"IEK"}
    assert run["kpi"]["lines_to_order"] == len(run["lines"])
    assert run["kpi"]["total_amount"] is None
    assert client.get(f"/api/runs/{run['run_id']}").json() == run
    assert_error(client.get("/api/runs/missing"), 404)
    assert_error(client.post("/api/runs", json={"dataset_id": "missing"}), 404)
    assert_error(client.get("/api/not-a-route"), 404)
    assert_error(client.get("/api"), 404)


def test_patch_recalculates_totals_and_checks_quantity(client):
    run = new_run(client)
    line = next(item for item in run["lines"] if item["supplier"] == "SE" and item["unit_cost"] is not None)
    original_se = next(s for s in run["suppliers"] if s["supplier"] == "SE")
    new_qty = line["final_qty"] + line["moq"]
    path = f"/api/runs/{run['run_id']}/lines/{line['line_id']}"

    updated = client.patch(path, json={"final_qty": new_qty})
    assert updated.status_code == 200
    assert updated.json()["amount"] == round(new_qty * line["unit_cost"], 2)
    saved = client.get(f"/api/runs/{run['run_id']}").json()
    se = next(s for s in saved["suppliers"] if s["supplier"] == "SE")
    delta = updated.json()["amount"] - line["amount"]
    assert se["total_qty"] == original_se["total_qty"] + line["moq"]
    assert se["total_amount"] == round(original_se["total_amount"] + delta, 2)
    assert saved["kpi"]["total_amount"] == se["total_amount"]

    assert_error(client.patch(path, json={"final_qty": -1}), 422)
    assert "кратно" in assert_error(
        client.patch(path, json={"final_qty": new_qty + 1}), 422
    )["detail"]
    assert_error(client.patch(f"/api/runs/{run['run_id']}/lines/unknown", json={"final_qty": 1}), 404)

    assert client.patch(path, json={"final_qty": 0}).status_code == 200
    saved = client.get(f"/api/runs/{run['run_id']}").json()
    assert saved["kpi"]["lines_to_order"] == run["kpi"]["lines_to_order"] - 1
    assert next(s for s in saved["suppliers"] if s["supplier"] == "SE")["lines_count"] == original_se["lines_count"] - 1


def test_approval_is_persisted_once_and_blocks_changes(tmp_path):
    approval_path = tmp_path / "app.db"
    with TestClient(create_app(approval_path=approval_path)) as client:
        assert client.get("/api/approvals").json() == []
        run = new_run(client)
        endpoint = f"/api/runs/{run['run_id']}/suppliers/IEK/approve"
        first = client.post(endpoint)
        assert first.status_code == 200
        assert first.json()["status"] == "approved"
        assert first.json()["approved_at"]
        assert client.post(endpoint).status_code == 200
        records = client.get("/api/approvals").json()
        assert len(records) == 1
        assert records[0]["run_id"] == run["run_id"]
        assert records[0]["supplier"] == "IEK"
        assert records[0]["approved_at"] == first.json()["approved_at"]
        assert len(records[0]["lines"]) == sum(line["supplier"] == "IEK" for line in run["lines"])
        with sqlite3.connect(approval_path) as database:
            assert database.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1

        line_id = next(line["line_id"] for line in run["lines"] if line["supplier"] == "IEK")
        assert_error(
            client.patch(f"/api/runs/{run['run_id']}/lines/{line_id}", json={"final_qty": 0}),
            409,
        )
        assert_error(client.post(f"/api/runs/{run['run_id']}/suppliers/XYZ/approve"), 404)

    with TestClient(create_app(approval_path=approval_path)) as restarted:
        assert restarted.get("/api/approvals").json() == records
        assert_error(restarted.get(f"/api/runs/{run['run_id']}"), 404)


def test_existing_json_approvals_are_imported_once(tmp_path, monkeypatch):
    legacy = tmp_path / "approvals.json"
    legacy.write_text(json.dumps([{
        "run_id": "old-run", "supplier": "IEK",
        "approved_at": "2026-09-22T12:00:00+00:00",
        "data_as_of": "2026-09-22", "lines": [],
    }]), encoding="utf-8")
    database_path = tmp_path / "app.db"
    monkeypatch.setattr(store_module, "DEFAULT_APPROVALS_PATH", database_path)
    monkeypatch.setattr(store_module, "LEGACY_APPROVALS_PATH", legacy)

    for _ in range(2):
        with TestClient(create_app(approval_path=database_path)) as client:
            records = client.get("/api/approvals").json()
            assert len(records) == 1
            assert records[0]["run_id"] == "old-run"
    assert legacy.exists()
    with sqlite3.connect(database_path) as database:
        assert database.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 1


def test_export_contains_only_positive_order_lines_and_blank_iek_prices(client):
    run = new_run(client)
    iek_line = next(line for line in run["lines"] if line["supplier"] == "IEK")
    assert client.patch(
        f"/api/runs/{run['run_id']}/lines/{iek_line['line_id']}", json={"final_qty": 0}
    ).status_code == 200

    exported = client.get(f"/api/runs/{run['run_id']}/export.xlsx?supplier=IEK")
    assert exported.status_code == 200
    assert "attachment;" in exported.headers["content-disposition"]
    workbook = load_workbook(BytesIO(exported.content), read_only=True, data_only=True)
    assert workbook.sheetnames == ["IEK", "Параметры расчёта"]
    rows = list(workbook.active.values)
    default_settings = dict(list(workbook["Параметры расчёта"].values)[1:])
    workbook.close()
    assert rows[0] == EXPORT_COLUMNS
    assert len(rows) == 2 + sum(line["supplier"] == "IEK" for line in run["lines"]) - 1
    assert iek_line["sku"] not in {row[0] for row in rows[1:-1]}
    assert all(row[4] > 0 and row[5] is None and row[6] is None for row in rows[1:-1])
    assert rows[-1][0] == "Итого" and rows[-1][6] is None
    assert (default_settings["L, дни"], default_settings["R, дни"]) == (24, 7)
    assert default_settings["Статус"] == "draft"
    assert default_settings["Дата утверждения"] is None
    assert_error(client.get(f"/api/runs/{run['run_id']}/export.xlsx?supplier=XYZ"), 404)


def test_export_formats_order_and_records_effective_parameters(client):
    run = new_run(client, supplier="SE", lead_time_days=38, review_period_days=12)
    approved = client.post(f"/api/runs/{run['run_id']}/suppliers/SE/approve")
    assert approved.status_code == 200
    exported = client.get(f"/api/runs/{run['run_id']}/export.xlsx?supplier=SE")
    assert exported.status_code == 200

    workbook = load_workbook(BytesIO(exported.content))
    assert workbook.sheetnames == ["SE", "Параметры расчёта"]
    orders = workbook["SE"]
    assert tuple(cell.value for cell in orders[1]) == EXPORT_COLUMNS
    assert all(cell.font.bold for cell in orders[1])
    assert orders.freeze_panes == "A2"
    assert orders.column_dimensions["C"].width > len("Наименование")
    assert orders["E2"].number_format == "#,##0.###"
    assert orders["G2"].number_format == "#,##0.00"
    total = orders.max_row
    assert orders.cell(total, 1).value == "Итого"
    assert orders.cell(total, 1).font.bold
    assert orders.cell(total, 5).value == sum(line["final_qty"] for line in run["lines"])
    assert orders.cell(total, 7).value == approved.json()["total_amount"]
    assert orders.auto_filter.ref == f"A1:J{total - 1}"

    settings = dict(list(workbook["Параметры расчёта"].values)[1:])
    assert {key: value for key, value in settings.items() if key != "Дата утверждения"} == {
        "Дата данных": run["data_as_of"],
        "Метод": "analyze",
        "L, дни": 38,
        "R, дни": 12,
        "Поставщик": "SE",
        "Статус": "approved",
    }
    assert settings["Дата утверждения"]
    workbook.close()


def test_upload_validation_and_uploaded_dataset_selection(client):
    roles = ("monthly_sales", "monthly_stock", "in_transit", "moq")

    def files(content=b"PKsample"):
        return [(role, (f"{role}.xlsx", content)) for role in roles]

    assert_error(client.post("/api/datasets/XYZ", files=files()), 404)
    missing = assert_error(client.post("/api/datasets/IEK", files=files()[:-1]), 422)
    assert missing["meta"]["file_role"] == "moq"
    assert_error(client.post("/api/datasets/IEK", files=files(b"")), 422)
    assert_error(client.post("/api/datasets/IEK", files=files(b"not xlsx")), 422)
    oversized = [("monthly_sales", ("sales.xlsx", b"PK" + b"x" * MAX_UPLOAD_BYTES))]
    oversized += files()[1:]
    assert_error(client.post("/api/datasets/IEK", files=oversized), 422)

    uploaded = client.post("/api/datasets/IEK", files=files())
    assert uploaded.status_code == 200
    assert uploaded.json()["supplier"] == "IEK"
    assert uploaded.json()["dataset_id"]
    assert new_run(client, dataset_id=uploaded.json()["dataset_id"])["params"][
        "dataset_id"
    ] == uploaded.json()["dataset_id"]


@pytest.mark.parametrize("template,lead,review", [("IEK", 24, 7), ("SE", 40, 30)])
def test_new_supplier_upload_meta_run_history_approval_and_export(
    client, monkeypatch, template, lead, review,
):
    monkeypatch.setitem(
        ingest._LOADERS, template,
        lambda _folder, _as_of: make_dataset(
            {"A": noisy(10)}, supplier=template, stock_now=0, in_transit=2,
        ),
    )
    monkeypatch.setattr(engine, "run", pipeline.run)
    monkeypatch.setattr(engine, "meta", pipeline.meta)
    monkeypatch.setattr(engine, "history", pipeline.history)
    files = [
        (role, (f"{role}.xlsx", b"PKsample"))
        for role in ("monthly_sales", "monthly_stock", "in_transit", "moq", "sales_tx")
    ]
    uploaded = client.post(
        "/api/datasets/new", data={"name": "Тестовый поставщик", "template": template},
        files=files,
    )
    assert uploaded.status_code == 200, uploaded.text
    payload = uploaded.json()
    code = payload["supplier"]
    assert code.startswith("CUSTOM_") and len(code) == 15
    assert payload["supplier_name"] == "Тестовый поставщик"
    dataset_id = payload["dataset_id"]

    info = client.get("/api/meta", params={"dataset_id": dataset_id})
    assert info.status_code == 200, info.text
    assert [(item["supplier"], item["supplier_name"], item["lead_time_days"],
             item["review_period_days"]) for item in info.json()["suppliers"]] == [
        (code, "Тестовый поставщик", lead, review),
    ]
    assert_error(client.get("/api/meta", params={"dataset_id": "missing"}), 404)
    assert_error(client.post(
        "/api/runs", json={"dataset_id": dataset_id, "supplier": template},
    ), 422)

    run_response = client.post("/api/runs", json={"dataset_id": dataset_id, "supplier": code})
    assert run_response.status_code == 200, run_response.text
    run = run_response.json()
    assert run["suppliers"][0]["supplier_name"] == "Тестовый поставщик"
    assert run["lines"] and all(line["supplier"] == code for line in run["lines"])
    line = run["lines"][0]
    history = client.get(f"/api/sku/{code}/{line['sku']}/history", params={"run_id": run["run_id"]})
    assert history.status_code == 200, history.text
    assert history.json()["supplier"] == code
    approved = client.post(f"/api/runs/{run['run_id']}/suppliers/{code}/approve")
    assert approved.status_code == 200, approved.text
    assert any(item["supplier"] == code for item in client.get("/api/approvals").json())

    exported = client.get(f"/api/runs/{run['run_id']}/export.xlsx", params={"supplier": code})
    assert exported.status_code == 200, exported.text
    assert f"order_{code}_" in exported.headers["content-disposition"]
    workbook = load_workbook(BytesIO(exported.content), read_only=True)
    assert code in workbook.sheetnames
    settings = dict(list(workbook["Параметры расчёта"].values)[1:])
    assert settings["Поставщик"] == "Тестовый поставщик"
    assert (settings["L, дни"], settings["R, дни"]) == (lead, review)
    workbook.close()


def test_new_supplier_upload_rejects_missing_name_and_unknown_template(client):
    files = [(role, (f"{role}.xlsx", b"PKsample")) for role in (
        "monthly_sales", "monthly_stock", "in_transit", "moq",
    )]
    missing = assert_error(client.post("/api/datasets/new", data={"template": "IEK"}, files=files), 422)
    assert "название" in missing["detail"]
    invalid = assert_error(client.post(
        "/api/datasets/new", data={"name": "Поставщик", "template": "XYZ"}, files=files,
    ), 422)
    assert "формат" in invalid["detail"]
    assert_error(client.post("/api/runs", json={"supplier": "UNKNOWN"}), 422)


def test_history_and_summary_errors(client, monkeypatch):
    run = new_run(client)
    sku = run["lines"][0]["sku"]
    supplier = run["lines"][0]["supplier"]
    history = client.get(f"/api/sku/{supplier}/{sku}/history?run_id={run['run_id']}")
    assert history.status_code == 200
    assert history.json()["sku"] == sku
    assert_error(client.get(f"/api/sku/{supplier}/{sku}/history?run_id=missing"), 404)

    def unknown_sku(*_args, **_kwargs):
        raise KeyError("missing")

    monkeypatch.setattr(engine, "history", unknown_sku)
    assert_error(client.get("/api/sku/IEK/missing/history"), 404)
    assert_error(client.get("/api/sku/XYZ/missing/history"), 404)
    assert_error(client.post(f"/api/runs/{run['run_id']}/summary?supplier=IEK"), 503)


def test_spa_fallback_does_not_mask_api_errors(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>app</h1>", encoding="utf-8")
    (dist / "favicon.txt").write_text("icon", encoding="utf-8")
    with TestClient(create_app(approval_path=tmp_path / "app.db", frontend_dist=dist)) as client:
        assert "<h1>app</h1>" in client.get("/orders/123").text
        assert client.get("/favicon.txt").text == "icon"
        assert_error(client.get("/api/not-a-route"), 404)
