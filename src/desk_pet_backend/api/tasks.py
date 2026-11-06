"""GET /api/tasks (contracts § 2). Pi가 마감 알람에 쓸 업무 목록을 받아 간다."""

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from desk_pet_backend.api.auth import require_device_token
from desk_pet_backend.eureka import EurekaError
from desk_pet_backend.schemas import DeviceTaskList
from desk_pet_backend.services import tasks

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_device_token)])


@router.get("/tasks", response_model=DeviceTaskList)
async def list_tasks() -> DeviceTaskList:
    """목록을 못 받으면 503을 돌려준다. Pi는 저장해 둔 목록을 계속 쓰면 된다."""
    try:
        return await tasks.list_device_tasks()
    except (EurekaError, sqlite3.Error) as exc:
        log.exception("device task list failed")
        raise HTTPException(status_code=503, detail="업무 목록을 가져오지 못했습니다") from exc
