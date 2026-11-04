"""POST /api/voice, POST /api/text (contracts § 1).

입구 역할만 한다: 입력 받기 → services.tasks.handle() 호출 → 응답 돌려주기.
업무 규칙을 여기에 쓰지 않는다.
"""

import secrets
import wave
from datetime import datetime
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from desk_pet_backend.api.auth import require_device_token
from desk_pet_backend.eureka import KST
from desk_pet_backend.schemas import TextRequest, VoiceResponse
from desk_pet_backend.services import tasks

router = APIRouter(prefix="/api", dependencies=[Depends(require_device_token)])
MAX_UPLOAD_BYTES = 1_000_000
MAX_FRAMES = 16_000 * 15
DEFAULT_USER_ID = "default"  # 1차는 로그인 없이 계정 하나 (contracts § 0-4)


@router.post("/text", response_model=VoiceResponse)
async def text(req: TextRequest) -> VoiceResponse:
    """개발·평가·비상 시연용. 응답은 /api/voice와 같다."""
    return await tasks.handle(
        user_id=req.user_id or DEFAULT_USER_ID,
        request_id=req.request_id or new_request_id(),
        text=req.text,
    )


@router.post("/voice", response_model=VoiceResponse)
async def voice(
    audio: Annotated[UploadFile, File()],
    user_id: Annotated[str | None, Form()] = None,
    request_id: Annotated[str | None, Form()] = None,
) -> VoiceResponse:
    """WAV(16kHz, mono, 16bit, 최대 15초)를 받아 처리한다. 9초 안에 응답해야 한다 (Pi는 10초 대기)."""
    data = await audio.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="WAV 파일이 너무 큽니다")
    _validate_wav(data)
    return await tasks.handle(
        user_id=user_id or DEFAULT_USER_ID,
        request_id=request_id or new_request_id(),
        audio=data,
    )


def new_request_id() -> str:
    """Pi가 request_id를 보내지 않았을 때 쓸 id를 만든다 (contracts § 0-5). 예: req_20261029_153000_a1b2"""
    return f"req_{datetime.now(KST):%Y%m%d_%H%M%S}_{secrets.token_hex(2)}"


def _validate_wav(data: bytes) -> None:
    try:
        with wave.open(BytesIO(data), "rb") as recording:
            if (
                recording.getnchannels() != 1
                or recording.getsampwidth() != 2
                or recording.getframerate() != 16_000
                or recording.getcomptype() != "NONE"
            ):
                raise HTTPException(
                    status_code=422, detail="16kHz 모노 16비트 PCM WAV가 필요합니다"
                )
            frames = recording.getnframes()
            if frames > MAX_FRAMES:
                raise HTTPException(status_code=413, detail="음성은 최대 15초까지 가능합니다")
            if frames == 0 or len(recording.readframes(frames)) != frames * 2:
                raise HTTPException(status_code=422, detail="WAV 음성 데이터가 비어 있거나 잘렸습니다")
    except (wave.Error, EOFError) as exc:
        raise HTTPException(status_code=422, detail="올바른 WAV 파일이 아닙니다") from exc
