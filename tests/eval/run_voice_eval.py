"""녹음 파일(WAV)을 services/ai.py의 analyze()에 넣어 받아쓰기·판단·지연을 출력한다.

정답 비교는 하지 않는다 (사람이 눈으로 확인). 재질문 흐름은 2턴으로 확인한다:
첫 파일의 Decision을 pending으로 넘기고 answer 파일을 보낸다.

실행: python tests/eval/run_voice_eval.py <wav 폴더>   (레포 루트, .env에 LLM_API_KEY 필요)
폴더 구성 (팀 공유 deskpet-stt-wav 기준):
  *.wav                      단일 요청
  clarify/*.wav              재질문 유도 발화
  clarify/desk-pet-voice-answer.wav   재질문에 대한 대답
"""

import asyncio
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from desk_pet_backend.eureka import KST
from desk_pet_backend.schemas import Decision, Task
from desk_pet_backend.services.ai import analyze

MIN_INTERVAL = 4.5  # 무료 등급 분당 15회 제한
ANSWER = "clarify/desk-pet-voice-answer.wav"
# "보고서"가 들어간 업무가 2개라 "그거 끝났어" 같은 발화가 모호해진다
TASKS = [
    Task(item_id="t1", name="보고서 검토", due_at=datetime(2099, 1, 7, 18, 0, tzinfo=KST), status="todo"),
    Task(item_id="t2", name="보고서 작성", due_at=datetime(2099, 1, 10, 15, 0, tzinfo=KST), status="todo"),
    Task(item_id="t3", name="팀 회의", due_at=datetime(2099, 1, 5, 10, 0, tzinfo=KST), status="todo"),
]


async def run(root: Path, name: str, now: datetime, latencies: list[float],
              pending: dict | None = None) -> Decision | None:
    if latencies:
        await asyncio.sleep(MIN_INTERVAL)
    start = time.perf_counter()
    try:
        d = await analyze(audio=(root / name).read_bytes(), text=None, now=now,
                          open_tasks=TASKS, pending=pending)
    except Exception as e:  # noqa: BLE001
        print(f"\n[ERR ] {name}: {e}")
        return None
    latencies.append(time.perf_counter() - start)
    print(f"\n[{name}] {latencies[-1]:.2f}s")
    print(f"  받아쓰기: {d.transcript}")
    print(f"  판단: {d.action} {d.args.model_dump(mode='json', exclude_none=True)}")
    if d.question:
        print(f"  질문: {d.question}")
    return d


async def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("사용: python tests/eval/run_voice_eval.py <wav 폴더>")
    root = Path(sys.argv[1])
    now = datetime.now(KST).replace(second=0, microsecond=0)
    latencies: list[float] = []
    print(f"기준 시각: {now.isoformat()} / 업무: {[t.name for t in TASKS]}")

    print("\n===== 단일 요청 =====")
    decisions = {}
    for wav in sorted(root.rglob("*.wav")):
        rel = wav.relative_to(root).as_posix()
        decisions[rel] = await run(root, rel, now, latencies)

    if (root / ANSWER).exists():
        print("\n===== 2턴: 재질문 → 대답 =====")
        for first, d in decisions.items():
            if first.startswith("clarify/") and first != ANSWER and d and d.action == "clarify":
                print(f"\n--- {first} 다음에 answer ---")
                await run(root, ANSWER, now, latencies, pending=d.model_dump(mode="json"))

    if latencies:
        print(f"\n지연 중앙값 {statistics.median(latencies):.2f}s, 최대 {max(latencies):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
