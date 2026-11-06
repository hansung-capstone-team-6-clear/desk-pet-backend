"""모든 테스트의 기본값: 캐시를 꺼서 mock Eureka를 바로 읽는다. 캐시 동작은 test_task_cache.py에서 켜고 시험한다."""

import pytest

from desk_pet_backend.config import settings


@pytest.fixture(autouse=True)
def disable_task_cache(monkeypatch):
    monkeypatch.setattr(settings, "task_cache_seconds", 0)
