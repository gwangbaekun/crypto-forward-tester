"""DB config: DATABASE_URL from env."""
import os

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def get_engine_url() -> str:
    if not DATABASE_URL:
        return "sqlite:///./data/forwardtest.db"
    return DATABASE_URL
