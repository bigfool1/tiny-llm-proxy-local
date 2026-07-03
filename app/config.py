"""应用配置，所有可变项通过环境变量注入。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "mysql+asyncmy://root@localhost:3306/llm_proxy"
    test_database_url: str = "sqlite+aiosqlite:///:memory:"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "llm_proxy_memories"

    mem0_backend: str = "mem0"

    admin_key: str = "dev-admin-key"

    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_api_key: str = ""

    default_model: str = "deepseek-chat"
    anthropic_base_url: str = "https://api.deepseek.com/anthropic"
    anthropic_api_key: str = ""
    anthropic_version: str = "2023-06-01"
    anthropic_max_tokens: int = 4096
    model_api_base_url: str = "https://api.deepseek.com"
    model_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    mem0_history_db_path: str = ".mem0/history.db"
    model_timeout_seconds: float = 60.0

    context_token_budget: int = 12000
    history_message_limit: int = 12
    memory_top_k: int = 5

    app_host: str = "127.0.0.1"
    app_port: int = 8000


settings = Settings()
