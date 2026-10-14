"""앱 진입점: FastAPI 앱을 만들고 라우터를 등록한다.

실행: uvicorn desk_pet_backend.main:app --reload
"""

from fastapi import FastAPI

from desk_pet_backend.api import voice, ws

app = FastAPI(title="Desk Pet Backend")
app.include_router(voice.router)
app.include_router(ws.router)

# TODO(backend, 11/14): 앱 시작 시 services.reminder.run_forever()를 백그라운드로 실행 (FastAPI lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
