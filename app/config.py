"""Cấu hình tập trung. Mọi thứ đọc từ biến môi trường, không hardcode ở nơi khác."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./dev.db")
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")  # none | openai
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", os.getenv("OPEN_AI_KEY", ""))
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_temperature: float = float(os.getenv("OPENAI_TEMPERATURE", "0.2"))
    max_react_iterations: int = int(os.getenv("MAX_REACT_ITERATIONS", "3"))
    hotline: str = os.getenv("HOTLINE", "0900 000 000")
    handover_timeout_minutes: float = float(os.getenv("HANDOVER_TIMEOUT_MINUTES", "10"))


settings = Settings()
