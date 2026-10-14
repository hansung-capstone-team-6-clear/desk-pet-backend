"""Eureka Process 연결 (contracts § 4). 담당: backend.

Eureka 형식(id, name, dueAt=epoch ms, stage$$)과 이 프로젝트 형식(schemas.Task) 사이의 변환은 이 파일에서만 한다.
실제 호출 예시는 tests/integration/Eureka_Process_API_Test/ 의 PoC 스크립트에 있다.

services는 get_eureka()로만 클라이언트를 얻는다. EUREKA_MODE=mock이면 메모리 가짜(EurekaMock),
real이면 실제 API(EurekaClient)가 나온다. 두 클래스의 메서드 이름과 반환 형식은 항상 같아야 한다.
"""

from datetime import datetime, timedelta, timezone
from functools import cache
from itertools import count

from desk_pet_backend.config import settings
from desk_pet_backend.schemas import Task

KST = timezone(timedelta(hours=9))


class EurekaError(Exception):
    """Eureka 호출 실패. 호출한 쪽은 성공 문구를 보내지 말고 error 응답을 만든다."""


def to_epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("시간대가 없는 시각은 받지 않는다 (+09:00을 붙일 것)")
    return int(dt.timestamp() * 1000)


def from_epoch_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=KST)


class EurekaClient:
    """실제 Eureka API. 인증은 x-api-key 헤더(settings.eureka_api_key).

    입력 검증 오류가 400이 아니라 500으로 올 때가 있다 (PoC 결과).
    상태 코드와 응답 본문을 함께 확인하고, 실패면 EurekaError를 낸다.
    """

    async def list_open_tasks(self) -> list[Task]:
        """GET /_api_/items/0/list?sites&detail=true&limit=100 → status가 done인 업무 제외.

        100개를 넘으면 다음 페이지까지 읽는다 (페이지 파라미터는 멘토에게 확인).
        """
        # TODO(backend, 10/18)
        raise NotImplementedError

    async def create_task(self, name: str, due_at: datetime) -> Task:
        """POST /_api_/items/0/start  {processId, name, dueAt: to_epoch_ms(due_at), priority}"""
        # TODO(backend, 10/18)
        raise NotImplementedError

    async def complete_task(self, item_id: str) -> None:
        """POST /_api_/items/{item_id}/complete"""
        # TODO(backend, 10/18)
        raise NotImplementedError

    async def update_due(self, item_id: str, due_at: datetime) -> None:
        """PUT /_api_/items/{item_id}  {dueAt: to_epoch_ms(due_at)}"""
        # TODO(backend, 10/18)
        raise NotImplementedError

    async def get_status(self, item_id: str) -> str:
        """GET /_api_/items/{item_id}/status → status (todo | doing | done | hold)"""
        # TODO(backend, 11/14)
        raise NotImplementedError


class EurekaMock:
    """메모리에만 있는 가짜 Eureka. 실제 키 없이 /api/text 흐름과 알림을 개발할 때 쓴다. 재시작하면 사라진다."""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self._ids = count(1000001)

    async def list_open_tasks(self) -> list[Task]:
        return [task for task in self.tasks.values() if task.status != "done"]

    async def create_task(self, name: str, due_at: datetime) -> Task:
        task = Task(item_id=str(next(self._ids)), name=name, due_at=due_at, status="todo")
        self.tasks[task.item_id] = task
        return task

    async def complete_task(self, item_id: str) -> None:
        self._get(item_id).status = "done"

    async def update_due(self, item_id: str, due_at: datetime) -> None:
        self._get(item_id).due_at = due_at

    async def get_status(self, item_id: str) -> str:
        return self._get(item_id).status

    def _get(self, item_id: str) -> Task:
        if item_id not in self.tasks:
            raise EurekaError(f"없는 업무: {item_id}")
        return self.tasks[item_id]


@cache
def get_eureka() -> EurekaClient | EurekaMock:
    """앱 전체에서 같은 객체를 쓴다 (mock 데이터가 요청마다 사라지지 않도록)."""
    return EurekaClient() if settings.eureka_mode == "real" else EurekaMock()
