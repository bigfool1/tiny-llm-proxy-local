"""应用配置，所有可变项通过环境变量注入。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库
    database_url: str = "mysql+asyncmy://root@localhost:3306/llm_proxy"

    # ChromaDB
    chroma_persist_dir: str = "./chroma_data"

    # 管理密钥
    admin_key: str = "dev-admin-key"

    # 默认模型路由
    default_model: str = "claude-3-5-sonnet-20240620"

    # 服务监听
    app_host: str = "127.0.0.1"
    app_port: int = 8000


settings = Settings()
