"""마감 선제 알림 (knowledge 레포 architecture.md § 선제 알림). 담당: backend.

Eureka는 Webhook이 없어서 settings.poll_interval_seconds(60초)마다 목록을 직접 조회한다.
"""

import asyncio
import logging

from desk_pet_backend.config import settings

log = logging.getLogger(__name__)


async def run_forever() -> None:
    """main.py가 앱 시작 시 백그라운드로 실행한다. 한 번 실패해도 루프는 계속 돈다."""
    while True:
        try:
            await check_once()
        except Exception:
            log.exception("reminder check failed")
        await asyncio.sleep(settings.poll_interval_seconds)


async def check_once() -> None:
    """한 번 조회해서 보낼 알림을 보낸다.

    1. Eureka 목록 조회 (status == done 제외)
    2. 마감 − offsetMinutes ≤ 지금 < 마감 이고, db.alerts에 (itemId, dueAt, offsetMinutes)가 없으면 대상
    3. 보내기 직전 상태 API로 다시 확인 → done이면 건너뜀
    4. 템플릿 문장: "{name} 마감이 {N분/1시간} 남았어요."
    5. api.ws.send_alert() → 결과에 따라 alerts에 sent / undelivered 기록
    """
    # TODO(backend, 11/14)
    raise NotImplementedError


async def resend_undelivered(device_id: str) -> None:
    """Pi가 다시 연결되면 undelivered 알림을 3~5단계부터 다시 처리한다 (남은 시간 재계산)."""
    # TODO(backend, 11/14)
    raise NotImplementedError
