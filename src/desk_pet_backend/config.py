"""환경 변수 설정. 값은 레포 루트의 .env에서 읽는다 (.env.example 참고)."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Eureka Process (contracts § 4)
    eureka_mode: Literal["real", "mock"] = "mock"
    eureka_base_url: str = "https://api.eureka.codes/flw-v1"
    eureka_api_key: str = ""
    eureka_process_id: str = ""

    # LLM (services/ai.py) — 모델 확정 후 ai 담당이 채운다
    llm_model: str = ""
    llm_api_key: str = ""

    # Raspberry Pi 인증 (contracts § 0) — 비어 있으면 모든 요청을 거절한다
    device_token: str = ""

    # 알림 간격 기본값 (분). GET /api/tasks로 Pi에 내려간다
    default_reminder_minutes: int = 60

    # 업무 목록 캐시 (services/task_cache.py) — 0이면 캐시를 끄고 매번 Eureka를 읽는다
    task_cache_seconds: int = 86_400
    task_refresh_delay_seconds: float = 10

    # SQLite (db.py)
    db_path: str = "data/desk_pet.db"


settings = Settings()
