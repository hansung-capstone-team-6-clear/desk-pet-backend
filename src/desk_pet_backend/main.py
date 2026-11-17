"""앱 진입점: FastAPI 앱을 만들고 라우터를 등록한다.

실행: uvicorn desk_pet_backend.main:app --reload
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from desk_pet_backend.api import tasks, voice, ws
from desk_pet_backend.eureka import get_eureka
from desk_pet_backend.services import task_cache

# uvicorn은 자기 로거만 설정하므로, 이 앱의 INFO 로그(요청별 소요 시간)는 여기서 켠다.
logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("desk_pet_backend").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    refresher = asyncio.create_task(task_cache.run_forever(get_eureka()))
    yield
    refresher.cancel()
    await asyncio.gather(refresher, return_exceptions=True)
    await task_cache.stop()


app = FastAPI(title="Desk Pet Backend", lifespan=lifespan)
app.include_router(voice.router)
app.include_router(tasks.router)
app.include_router(ws.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
