"""GET /api/tasks의 인증, 응답 모양, 알림 간격, 오류 처리를 확인한다. 실제 Eureka는 호출하지 않는다."""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from desk_pet_backend import db
from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, EurekaError, EurekaMock
from desk_pet_backend.main import app
from desk_pet_backend.services import tasks

client = TestClient(app)
HEADERS = {"x-device-token": "secret"}


@pytest.fixture
def eureka(monkeypatch, tmp_path):
    mock = EurekaMock()
    monkeypatch.setattr(tasks, "get_eureka", lambda: mock)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(settings, "device_token", "secret")
    monkeypatch.setattr(settings, "default_reminder_minutes", 60)
    return mock


def create(mock, name, due_at):
    return asyncio.run(mock.create_task(name, due_at))


def test_requires_device_token(eureka):
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks", headers={"x-device-token": "wrong"}).status_code == 401


def test_empty_list(eureka):
    response = client.get("/api/tasks", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {"tasks": []}


def test_returns_contract_fields_with_saved_or_default_offset(eureka):
    due = datetime(2030, 11, 20, 17, tzinfo=KST)
    saved = create(eureka, "보고서 검토", due)
    default = create(eureka, "회의 준비", due + timedelta(days=1))
    db.set_reminder(saved.item_id, 120)

    response = client.get("/api/tasks", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {"tasks": [
        {"item_id": saved.item_id, "name": "보고서 검토",
         "due_at": "2030-11-20T17:00:00+09:00", "offset_minutes": 120},
        {"item_id": default.item_id, "name": "회의 준비",
         "due_at": "2030-11-21T17:00:00+09:00", "offset_minutes": 60},
    ]}


def test_sorted_by_due_excludes_done_and_keeps_past_due(eureka):
    now = datetime.now(KST)
    create(eureka, "나중", now + timedelta(days=3))
    create(eureka, "지난 업무", now - timedelta(days=1))
    create(eureka, "곧", now + timedelta(days=1))
    done = create(eureka, "끝난 업무", now + timedelta(days=2))
    asyncio.run(eureka.complete_task(done.item_id))

    names = [task["name"] for task in client.get("/api/tasks", headers=HEADERS).json()["tasks"]]

    assert names == ["지난 업무", "곧", "나중"]


def test_eureka_failure_returns_503(eureka, monkeypatch):
    async def fail():
        raise EurekaError("test failure")

    monkeypatch.setattr(eureka, "list_open_tasks", fail)
    response = client.get("/api/tasks", headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "업무 목록을 가져오지 못했습니다"}
