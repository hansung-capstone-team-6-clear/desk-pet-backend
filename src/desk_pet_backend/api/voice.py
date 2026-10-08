"""POST /voice, POST /text (contracts § 1).

입구 역할만 한다: 입력 받기 → services.tasks.handle() 호출 → 응답 돌려주기.
업무 규칙을 여기에 쓰지 않는다.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from desk_pet_backend.api.auth import require_device_token
from desk_pet_backend.schemas import TextRequest, VoiceResponse

router = APIRouter(dependencies=[Depends(require_device_token)])


@router.post("/text", response_model=VoiceResponse)
async def text(req: TextRequest) -> VoiceResponse:
    """개발·평가·비상 시연용. 응답은 /voice와 같다."""
    # TODO(backend, 10/18): return await tasks.handle(device_id=req.device_id, text=req.text)
    raise HTTPException(status_code=501, detail="not implemented")


@router.post("/voice", response_model=VoiceResponse)
async def voice(
    audio: Annotated[UploadFile, File()],
    device_id: Annotated[str, Form(alias="deviceId")],
) -> VoiceResponse:
    """WAV(16kHz, mono, 16bit, 최대 15초)를 받아 처리한다."""
    # TODO(backend, 10/31): return await tasks.handle(device_id=device_id, audio=await audio.read())
    raise HTTPException(status_code=501, detail="not implemented")
