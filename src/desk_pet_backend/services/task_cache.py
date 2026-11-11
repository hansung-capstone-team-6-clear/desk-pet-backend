"""미완료 업무 목록 캐시 (contracts § 4). 담당: backend.

요청마다 Eureka를 읽는 대신 서버 시작 시와 주기적으로 받아 둔 목록을 쓴다.
- 쓰기 성공: 캐시를 바로 고치고, 잠시 뒤 다시 조회한다. 변경 직후 조회에는 수정 전 값이 나올 수 있어서다.
- 쓰기 실패: 바로 다시 조회한다. Eureka 웹에서 이미 바뀐 업무일 수 있어서다.
- 조회 실패: 기존 캐시를 유지한다.

settings.task_cache_seconds가 0이면 캐시를 쓰지 않고 매번 Eureka를 읽는다.
서버 프로세스 하나를 전제로 한다. 프로세스를 늘리면 캐시가 서로 어긋난다.
"""

import asyncio
import logging
from datetime import datetime
from time import monotonic

from desk_pet_backend.config import settings
from desk_pet_backend.eureka import EurekaClient, EurekaError, EurekaMock
from desk_pet_backend.schemas import Task

log = logging.getLogger(__name__)

Eureka = EurekaClient | EurekaMock

_tasks: dict[str, Task] | None = None
_loaded_at = 0.0
_version = 0  # 쓰기마다 늘린다. 조회 중 쓰기가 있었으면 그 조회 결과를 버린다.
_delayed: asyncio.Task | None = None
_background: set[asyncio.Task] = set()


def _enabled() -> bool:
    return settings.task_cache_seconds > 0


async def get_open_tasks(eureka: Eureka) -> list[Task]:
    """캐시된 미완료 업무를 돌려준다. 비었거나 오래됐으면 먼저 조회한다."""
    if not _enabled():
        return await eureka.list_open_tasks()
    if _tasks is None or monotonic() - _loaded_at >= settings.task_cache_seconds:
        try:
            await refresh(eureka)
        except EurekaError:
            if _tasks is None:
                raise
            log.exception("task cache refresh failed; keeping existing tasks")
    return list(_tasks.values())


async def refresh(eureka: Eureka) -> None:
    """Eureka에서 목록을 다시 받는다. 조회 중 쓰기가 있었으면 결과를 버린다."""
    global _tasks, _loaded_at
    started_version = _version
    fresh = await eureka.list_open_tasks()
    if _tasks is None or _version == started_version:
        _tasks = {task.item_id: task for task in fresh}
        _loaded_at = monotonic()


def on_created(eureka: Eureka, task: Task) -> None:
    if _tasks is not None:
        _tasks[task.item_id] = task
    _after_write(eureka)


def on_completed(eureka: Eureka, item_id: str) -> None:
    if _tasks is not None:
        _tasks.pop(item_id, None)
    _after_write(eureka)


def on_deleted(eureka: Eureka, item_id: str) -> None:
    on_completed(eureka, item_id)


def on_due_updated(eureka: Eureka, item_id: str, due_at: datetime) -> None:
    if _tasks is not None and item_id in _tasks:
        _tasks[item_id] = _tasks[item_id].model_copy(update={"due_at": due_at})
    _after_write(eureka)


def on_write_failed(eureka: Eureka) -> None:
    """바로 다시 조회한다. 앞선 쓰기의 재조회 예약은 그대로 둔다."""
    if _enabled():
        _spawn(_refresh_quietly(eureka))


async def run_forever(eureka: Eureka) -> None:
    """main.py가 앱 시작 시 실행한다. 시작 직후 한 번, 이후 주기마다 조회한다."""
    if not _enabled():
        return
    while True:
        await _refresh_quietly(eureka)
        await asyncio.sleep(settings.task_cache_seconds)


async def stop() -> None:
    """앱 종료 시 예약된 조회를 멈추고 캐시를 비운다."""
    global _tasks, _loaded_at, _version, _delayed
    pending = list(_background)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    _tasks, _loaded_at, _version, _delayed = None, 0.0, 0, None


def _after_write(eureka: Eureka) -> None:
    """연속 쓰기는 마지막 쓰기 기준으로 한 번만 다시 조회한다."""
    global _version, _delayed
    _version += 1
    if not _enabled():
        return
    if _delayed is not None:
        _delayed.cancel()
    _delayed = _spawn(_refresh_later(eureka))


async def _refresh_later(eureka: Eureka) -> None:
    await asyncio.sleep(settings.task_refresh_delay_seconds)
    await _refresh_quietly(eureka)


async def _refresh_quietly(eureka: Eureka) -> None:
    try:
        await refresh(eureka)
    except EurekaError:
        log.exception("task cache refresh failed; keeping existing tasks")


def _spawn(coroutine) -> asyncio.Task:
    task = asyncio.create_task(coroutine)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task
