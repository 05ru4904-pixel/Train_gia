"""Настройки приложения. Читаются из окружения / .env."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = ""
    admin_bot_token: str = ""
    admin_ids: str = ""
    webapp_url: str = ""
    database_url: str = ""
    port: int = 8080
    run_admin_bot_inline: bool = True

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy async требует драйвер asyncpg, а Railway отдаёт схему
        postgresql:// (playbook 2.2)."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def admin_id_set(self) -> set[int]:
        return {int(x) for x in self.admin_ids.replace(" ", "").split(",") if x}

    def webapp_url_versioned(self, version: int) -> str:
        """Telegram агрессивно кэширует Mini App — версионируем URL (playbook 5.3)."""
        sep = "&" if "?" in self.webapp_url else "?"
        return f"{self.webapp_url}{sep}v={version}"


settings = Settings()
