from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI-compatible Open-Weight LLM & Embeddings (Apache 2.0)
    llm_base_url: str = "https://api.proxyapi.ru/v1"
    llm_api_key: str = ""
    llm_model: str = "qwen/qwen3-30b-a3b-instruct-2507"
    embedding_model: str = "qwen/qwen3-embedding-4b"
    embedding_dim: int = 2560

    # Storage
    qdrant_url: str = "http://localhost:6333"
    qdrant_local_path: str = ""  # Optional local embedded path for tests
    sqlite_path: str = "./data/app.db"
    docs_dir: str = "./Поиск ответов по корпоративным документам"

    # Email HITL
    smtp_host: str = "mail"
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    imap_host: str = "mail"
    imap_port: int = 993
    imap_use_ssl: bool = True
    mail_validate_certs: bool = False

    bot_email: str = "bot@corp.local"
    bot_email_password: str = "BotPass2026!"
    quality_email: str = "quality@corp.local"
    quality_whitelist: str = "quality@corp.local"
    default_employee_email: str = "employee@corp.local"
    imap_poll_interval_seconds: int = 10

    # App & RBAC
    public_url: str = "https://139-100-218-28.sslip.io"
    webmail_url: str = "https://139-100-218-28.sslip.io/webmail/"
    max_clarification_rounds: int = 3
    quality_admin_pin: str = "Quality2026"
    max_upload_mb: int = 5

    @property
    def whitelist_set(self) -> set[str]:
        return {
            addr.strip().lower()
            for addr in self.quality_whitelist.split(",")
            if addr.strip()
        }

    def ensure_dirs(self) -> None:
        Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
