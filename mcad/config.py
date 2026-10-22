import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./data/mcad.sqlite3"
    admin_api_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    public_base_url: str = ""
    enable_workers: bool = True
    enable_feed_polling: bool = True
    enable_telegram_polling: bool = True
    static_dir: Path = ROOT / "frontend" / "dist" / "client"
    cors_origins: tuple = ()

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        if os.getenv("RAILWAY_ENVIRONMENT_ID") and not os.getenv("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL is required in the hosted environment.")
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/mcad.sqlite3"),
            admin_api_token=os.getenv("ADMIN_API_TOKEN", "").strip(),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/"),
            enable_workers=env_bool("ENABLE_BACKGROUND_WORKERS", True),
            enable_feed_polling=env_bool("ENABLE_FEED_POLLING", True),
            enable_telegram_polling=env_bool("ENABLE_TELEGRAM_POLLING", True),
            cors_origins=tuple(x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip()),
        )
