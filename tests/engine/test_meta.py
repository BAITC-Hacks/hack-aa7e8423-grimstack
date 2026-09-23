from app import engine
from app.engine import pipeline
from tests.engine.factory import make_dataset, noisy


def test_llm_unavailable_with_only_service_files_in_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(pipeline, "LLM_CACHE_DIR", tmp_path)
    (tmp_path / ".DS_Store").write_bytes(b"\0")
    assert engine.meta(make_dataset({"A": noisy(10)})).llm_available is False


def test_llm_available_with_cached_answer(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(pipeline, "LLM_CACHE_DIR", tmp_path)
    (tmp_path / "abc123.json").write_text("{}", encoding="utf-8")
    assert engine.meta(make_dataset({"A": noisy(10)})).llm_available is True
