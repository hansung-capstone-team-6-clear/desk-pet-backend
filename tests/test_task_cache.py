"""업무 목록 캐시의 읽기·쓰기 반영·재조회·실패 처리를 확인한다. 실제 Eureka는 호출하지 않는다."""

import asyncio
from datetime import datetime, timedelta

import pytest

from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, EurekaError, EurekaMock
from desk_pet_backend.schemas import Decision, DecisionArgs
from desk_pet_backend.services import task_cache, tasks

DELAY = 0.05


class CountingEureka(EurekaMock):
    def __init__(self):
        super().__init__()
        self.reads = 0
        self.fail_reads = False

    async def list_open_tasks(self):
        self.reads += 1
        if self.fail_reads:
            raise EurekaError("test failure")
        return await super().list_open_tasks()


@pytest.fixture
async def eureka(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "task_cache_seconds", 3600)
    monkeypatch.setattr(settings, "task_refresh_delay_seconds", DELAY)
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "tasks.db"))
    await task_cache.stop()
    mock = CountingEureka()
    monkeypatch.setattr(tasks, "get_eureka", lambda: mock)
    yield mock
    await task_cache.stop()


def due(days=1):
    return datetime.now(KST) + timedelta(days=days)


async def settle():
    await asyncio.sleep(DELAY * 3)


def names(items):
    return sorted(task.name for task in items)


async def test_reads_eureka_once_while_fresh(eureka):
    await eureka.create_task("보고서", due())
    first = await task_cache.get_open_tasks(eureka)
    second = await task_cache.get_open_tasks(eureka)
    assert names(first) == names(second) == ["보고서"]
    assert eureka.reads == 1


async def test_rereads_after_expiry(eureka, monkeypatch):
    await task_cache.get_open_tasks(eureka)
    monkeypatch.setattr(task_cache, "_loaded_at", task_cache._loaded_at - 3601)
    await task_cache.get_open_tasks(eureka)
    assert eureka.reads == 2


async def test_read_failure_keeps_existing_tasks(eureka, monkeypatch):
    await eureka.create_task("보고서", due())
    await task_cache.get_open_tasks(eureka)
    monkeypatch.setattr(task_cache, "_loaded_at", task_cache._loaded_at - 3601)
    eureka.fail_reads = True
    assert names(await task_cache.get_open_tasks(eureka)) == ["보고서"]


async def test_first_read_failure_raises(eureka):
    eureka.fail_reads = True
    with pytest.raises(EurekaError):
        await task_cache.get_open_tasks(eureka)


async def test_disabled_cache_reads_every_time(eureka, monkeypatch):
    monkeypatch.setattr(settings, "task_cache_seconds", 0)
    await task_cache.get_open_tasks(eureka)
    await task_cache.get_open_tasks(eureka)
    assert eureka.reads == 2


async def test_write_updates_cache_before_refresh_then_refreshes_once(eureka):
    await task_cache.get_open_tasks(eureka)
    created = await eureka.create_task("보고서", due())
    task_cache.on_created(eureka, created)
    second = await eureka.create_task("회의", due())
    task_cache.on_created(eureka, second)

    assert names(await task_cache.get_open_tasks(eureka)) == ["보고서", "회의"]
    assert eureka.reads == 1

    await settle()
    assert eureka.reads == 2


async def test_complete_and_due_update_change_cache_only_for_that_task(eureka):
    keep = await eureka.create_task("유지", due())
    change = await eureka.create_task("변경", due())
    finish = await eureka.create_task("완료", due())
    await task_cache.get_open_tasks(eureka)
    new_due = due(5)

    task_cache.on_completed(eureka, finish.item_id)
    task_cache.on_due_updated(eureka, change.item_id, new_due)

    cached = {task.name: task for task in await task_cache.get_open_tasks(eureka)}
    assert set(cached) == {"유지", "변경"}
    assert cached["변경"].due_at == new_due
    assert cached["유지"].due_at == keep.due_at


async def test_refresh_started_before_write_does_not_overwrite_it(eureka, monkeypatch):
    await eureka.create_task("기존", due())
    await task_cache.get_open_tasks(eureka)
    stale = await eureka.list_open_tasks()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_list():
        started.set()
        await release.wait()
        return stale

    monkeypatch.setattr(eureka, "list_open_tasks", slow_list)
    refreshing = asyncio.create_task(task_cache.refresh(eureka))
    await started.wait()
    created = await eureka.create_task("새 업무", due())
    task_cache.on_created(eureka, created)
    release.set()
    await refreshing

    assert "새 업무" in names(await task_cache.get_open_tasks(eureka))


async def test_handle_create_is_visible_without_another_read_and_failure_rereads(eureka, monkeypatch):
    await task_cache.get_open_tasks(eureka)
    reads_before = eureka.reads

    async def create_decision(**kwargs):
        return Decision(transcript="회의 등록", action="create_task", args=DecisionArgs(name="회의", due_at=due()))

    monkeypatch.setattr(tasks.ai, "analyze", create_decision)
    response = await tasks.handle(user_id="pet-01", request_id="req-1", text="회의 등록")
    assert response.type == "task_created"
    assert eureka.reads == reads_before
    assert names(await task_cache.get_open_tasks(eureka)) == ["회의"]
    await settle()
    reads_before = eureka.reads

    async def fail_create(name, due_at):
        raise EurekaError("test failure")

    monkeypatch.setattr(eureka, "create_task", fail_create)
    response = await tasks.handle(user_id="pet-01", request_id="req-2", text="회의 등록")
    assert response.status == "ERROR"
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert eureka.reads == reads_before + 1


async def test_device_tasks_read_from_cache(eureka):
    await eureka.create_task("보고서", due())
    await tasks.list_device_tasks()
    result = await tasks.list_device_tasks()
    assert [task.name for task in result.tasks] == ["보고서"]
    assert eureka.reads == 1


async def test_run_forever_loads_immediately_and_survives_failure(eureka):
    runner = asyncio.create_task(task_cache.run_forever(eureka))
    await asyncio.sleep(0.01)
    assert eureka.reads == 1
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    await task_cache.stop()

    eureka.fail_reads = True
    runner = asyncio.create_task(task_cache.run_forever(eureka))
    await asyncio.sleep(0.01)
    assert not runner.done()
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
