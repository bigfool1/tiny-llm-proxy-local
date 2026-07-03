from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_settings_have_dev_services_defaults(monkeypatch) -> None:
    monkeypatch.delenv("SILICONFLOW_BASE_URL", raising=False)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("RERANK_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.database_url == "mysql+asyncmy://root@localhost:3306/llm_proxy"
    assert settings.test_database_url == "sqlite+aiosqlite:///:memory:"
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.mem0_backend == "mem0"
    assert settings.siliconflow_base_url == "https://api.siliconflow.cn/v1"
    assert settings.siliconflow_api_key == ""
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.rerank_model == "BAAI/bge-reranker-v2-m3"
    assert settings.mem0_history_db_path == ".mem0/history.db"
    assert settings.anthropic_base_url == "https://api.deepseek.com/anthropic"
    assert settings.anthropic_api_key == ""
    assert settings.context_token_budget == 12000


def test_settings_accept_siliconflow_env_names(monkeypatch) -> None:
    monkeypatch.setenv("SILICONFLOW_BASE_URL", "https://sf.test/v1")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sf-key")
    monkeypatch.setenv("EMBEDDING_MODEL", "custom-embedding")
    monkeypatch.setenv("RERANK_MODEL", "custom-reranker")

    settings = Settings()

    assert settings.siliconflow_base_url == "https://sf.test/v1"
    assert settings.siliconflow_api_key == "sf-key"
    assert settings.embedding_model == "custom-embedding"
    assert settings.rerank_model == "custom-reranker"


def test_settings_accept_anthropic_env_names(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://deepseek.test/anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    settings = Settings()

    assert settings.anthropic_base_url == "https://deepseek.test/anthropic"
    assert settings.anthropic_api_key == "anthropic-key"


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
