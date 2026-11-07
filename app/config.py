from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    admin_telegram_ids: str = ""
    database_url: str = "sqlite+aiosqlite:///./market.db"
    app_encryption_key: str
    shop_name: str = "SMTP Store"
    shop_support_username: str = ""
    shop_banner: str = "📧"
    log_level: str = "INFO"
    catalog_page_size: int = 8

    @property
    def admins(self) -> set[int]:
        return {
            int(x.strip())
            for x in self.admin_telegram_ids.split(",")
            if x.strip().isdigit()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
