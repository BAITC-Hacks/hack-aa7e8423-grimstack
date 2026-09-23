"""LLM-сводка: модель пересказывает готовые цифры, ответы кэшируются для запуска без ключа."""

import json
from pathlib import Path

import pytest

from app.ai import llm as summary_mod
from app.contracts import RunResult

SAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "sample_run.json"


@pytest.fixture
def result() -> RunResult:
    return RunResult.model_validate(json.loads(SAMPLE.read_text(encoding="utf-8")))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(summary_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def fake_llm(monkeypatch, text="Сводка: всё посчитано.", calls=None):
    def _complete(system: str, user: str) -> str:
        if calls is not None:
            calls.append(json.loads(user))
        return text
    monkeypatch.setattr(summary_mod, "_complete", _complete)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_no_key_and_no_cache_means_unavailable(result):
    assert summary_mod.summary(result, "SE") is None


def test_live_answer_is_cached_and_served_without_key(result, monkeypatch, tmp_path):
    fake_llm(monkeypatch, "Живой ответ")
    live = summary_mod.summary(result, "SE")
    assert live.text == "Живой ответ" and live.cached is False
    assert len(list((tmp_path / "cache").glob("*.json"))) == 1

    monkeypatch.delenv("OPENAI_API_KEY")
    cached = summary_mod.summary(result, "SE")
    assert cached.text == "Живой ответ" and cached.cached is True


def test_cache_key_ignores_run_identity(result):
    other = result.model_copy(update={"run_id": "another", "created_at": result.created_at.replace(year=2030)})
    assert summary_mod.cache_key(summary_mod.build_facts(result, "SE")) == \
        summary_mod.cache_key(summary_mod.build_facts(other, "SE"))


def test_facts_cover_only_the_requested_supplier(result, monkeypatch):
    calls = []
    fake_llm(monkeypatch, calls=calls)
    summary_mod.summary(result, "IEK")
    facts = calls[0]
    iek = next(s for s in result.suppliers if s.supplier == "IEK")
    assert facts["supplier"] == "IEK"
    assert facts["lines_count"] == iek.lines_count
    listed = {line["sku"] for key in ("urgent", "vs_excel") for line in facts[key]}
    se_skus = {l.sku for l in result.lines if l.supplier == "SE"}
    assert listed and not listed & se_skus


def test_llm_failure_falls_back_to_cache_or_none(result, monkeypatch):
    fake_llm(monkeypatch, "Первый ответ")
    summary_mod.summary(result, "SE")

    def broken(system: str, user: str) -> str:
        raise TimeoutError("сеть недоступна")
    monkeypatch.setattr(summary_mod, "_complete", broken)
    assert summary_mod.summary(result, "SE").text == "Первый ответ"
    assert summary_mod.summary(result, "IEK") is None


def test_unknown_supplier_is_unavailable(result, monkeypatch):
    fake_llm(monkeypatch)
    only_se = result.model_copy(update={"suppliers": [s for s in result.suppliers if s.supplier == "SE"]})
    assert summary_mod.summary(only_se, "IEK") is None


def test_warnings_of_other_supplier_are_dropped(result):
    tagged = result.model_copy(update={"warnings": ["IEK: остаток — оценка", "SE: нет цены у 2 SKU", "Общее предупреждение"]})
    assert summary_mod.build_facts(tagged, "SE")["warnings"] == ["SE: нет цены у 2 SKU", "Общее предупреждение"]
    assert summary_mod.build_facts(tagged, "IEK")["warnings"] == ["IEK: остаток — оценка", "Общее предупреждение"]


def test_broken_cache_file_means_unavailable_not_crash(result, tmp_path):
    key = summary_mod.cache_key(summary_mod.build_facts(result, "SE"))
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / f"{key}.json").write_text("{not valid json", encoding="utf-8")
    assert summary_mod.summary(result, "SE") is None
    (tmp_path / "cache" / f"{key}.json").write_text('{"model": "x"}', encoding="utf-8")
    assert summary_mod.summary(result, "SE") is None


def test_live_answer_survives_cache_write_failure(result, monkeypatch, tmp_path):
    fake_llm(monkeypatch, "Живой ответ")
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("файл на месте папки", encoding="utf-8")
    monkeypatch.setattr(summary_mod, "CACHE_DIR", blocker)
    answer = summary_mod.summary(result, "SE")
    assert answer is not None and answer.text == "Живой ответ" and answer.cached is False


def test_zero_amount_is_a_real_amount_when_ranking_urgent(result):
    se_lines = [l for l in result.lines if l.supplier == "SE"]
    base = se_lines[0]
    free_bulk = base.model_copy(update={"sku": "FREE", "line_id": "SE:FREE", "urgency": "critical",
                                        "amount": 0.0, "final_qty": 5000.0})
    pricey = base.model_copy(update={"sku": "PRICEY", "line_id": "SE:PRICEY", "urgency": "critical",
                                     "amount": 200.0, "final_qty": 2.0})
    ranked = result.model_copy(update={"lines": [free_bulk, pricey]})
    assert [l["sku"] for l in summary_mod.build_facts(ranked, "SE")["urgent"]] == ["PRICEY", "FREE"]


def test_empty_model_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("MODEL", "")  # «MODEL=» из .env.example
    assert summary_mod._model() == summary_mod.DEFAULT_MODEL
    monkeypatch.setenv("MODEL", "gpt-custom")
    assert summary_mod._model() == "gpt-custom"


def test_empty_base_url_env_falls_back_to_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "")  # «OPENAI_BASE_URL=» из .env.example; иначе клиент берёт пустой URL
    assert summary_mod._base_url() == summary_mod.DEFAULT_BASE_URL
    monkeypatch.setenv("OPENAI_BASE_URL", "https://integrate.api.nvidia.com/v1")
    assert summary_mod._base_url() == "https://integrate.api.nvidia.com/v1"


def test_facts_state_currency_and_prompt_forbids_rubles(result):
    assert summary_mod.build_facts(result, "SE")["currency"] == "тенге (₸)"
    assert "тенге" in summary_mod.SYSTEM_PROMPT and "руб" in summary_mod.SYSTEM_PROMPT  # явный запрет рублей
    assert "пробел" in summary_mod.SYSTEM_PROMPT  # 14 800, а не 14,800


def test_excel_method_run_has_no_comparison_with_itself(result):
    baseline = result.model_copy(update={"params": result.params.model_copy(update={"method": "baseline"})})
    facts = summary_mod.build_facts(baseline, "SE")
    assert facts["vs_excel"] == []
    assert "Excel" in facts["note"]
