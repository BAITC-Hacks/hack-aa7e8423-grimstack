"""API contract checks with isolated sample responses from the calculation core."""

import json
import logging
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import ai, engine, ingest
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
    with TestClient(create_app(approval_path=tmp_path / "approvals.json")) as test_client:
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
        with TestClient(create_app(approval_path=tmp_path / "approvals.json")) as client:
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
        ("/api/backtest", "get"): {404},
        ("/api/runs", "post"): {404, 422},
        ("/api/runs/compare", "post"): {404, 422},
        ("/api/runs/{run_id}", "get"): {404},
        ("/api/runs/{run_id}/lines/{line_id}", "patch"): {404, 409, 422},
        ("/api/runs/{run_id}/suppliers/{supplier}/approve", "post"): {404},
        ("/api/runs/{run_id}/export.xlsx", "get"): {404, 422},
        ("/api/sku/{supplier}/{sku}/history", "get"): {404},
        ("/api/datasets/{supplier}", "post"): {404, 422},
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
    approval_path = tmp_path / "approvals.json"
    with TestClient(create_app(approval_path=approval_path)) as client:
        run = new_run(client)
        endpoint = f"/api/runs/{run['run_id']}/suppliers/IEK/approve"
        first = client.post(endpoint)
        assert first.status_code == 200
        assert first.json()["status"] == "approved"
        assert first.json()["approved_at"]
        assert client.post(endpoint).status_code == 200
        records = json.loads(approval_path.read_text(encoding="utf-8"))
        assert len(records) == 1
        assert records[0]["supplier"] == "IEK"
        assert len(records[0]["lines"]) == sum(line["supplier"] == "IEK" for line in run["lines"])

        line_id = next(line["line_id"] for line in run["lines"] if line["supplier"] == "IEK")
        assert_error(
            client.patch(f"/api/runs/{run['run_id']}/lines/{line_id}", json={"final_qty": 0}),
            409,
        )
        assert_error(client.post(f"/api/runs/{run['run_id']}/suppliers/XYZ/approve"), 404)


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
    rows = list(workbook.active.values)
    workbook.close()
    assert rows[0] == EXPORT_COLUMNS
    assert len(rows) == 1 + sum(line["supplier"] == "IEK" for line in run["lines"]) - 1
    assert iek_line["sku"] not in {row[0] for row in rows[1:]}
    assert all(row[4] > 0 and row[5] is None and row[6] is None for row in rows[1:])
    assert_error(client.get(f"/api/runs/{run['run_id']}/export.xlsx?supplier=XYZ"), 404)


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
    with TestClient(create_app(approval_path=tmp_path / "approvals.json", frontend_dist=dist)) as client:
        assert "<h1>app</h1>" in client.get("/orders/123").text
        assert client.get("/favicon.txt").text == "icon"
        assert_error(client.get("/api/not-a-route"), 404)
