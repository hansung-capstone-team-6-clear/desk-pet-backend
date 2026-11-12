"""평가셋을 services/ai.py의 analyze()로 돌려 정확도·지연을 출력한다 (tests/eval/README.md).

실행: python tests/eval/run_eval.py   (레포 루트, .env에 LLM_API_KEY 필요)
"""

import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from desk_pet_backend.schemas import Task
from desk_pet_backend.services.ai import analyze

UTTERANCES = Path(__file__).with_name("utterances.jsonl")
MIN_INTERVAL = 4.5  # 무료 등급 분당 15회 제한


def _matches(expected: dict, args: dict) -> bool:
    for key, want in expected.items():
        got = args.get(key)
        if key == "due_at" and want and got:
            if datetime.fromisoformat(want) != datetime.fromisoformat(got):
                return False
        elif got != want:
            return False
    return True


async def main() -> None:
    cases = [json.loads(line) for line in UTTERANCES.read_text(encoding="utf-8").splitlines() if line]
    latencies, passed = [], 0
    for i, case in enumerate(cases):
        if i:
            await asyncio.sleep(MIN_INTERVAL)
        start = time.perf_counter()
        try:
            decision = await analyze(
                audio=None,
                text=case["text"],
                now=datetime.fromisoformat(case["now"]),
                open_tasks=[Task(**t) for t in case["tasks"]],
                pending=case.get("pending"),
            )
        except Exception as e:  # noqa: BLE001
            print(f"[ERR ] {case['id']}: {e}")
            continue
        latencies.append(time.perf_counter() - start)
        args = decision.args.model_dump(mode="json", exclude_none=True)
        exp = case["expected"]
        ok = (
            decision.action == exp["action"]
            and _matches(exp.get("args", {}), args)
            and ("transcript" not in exp or decision.transcript == exp["transcript"])
        )
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {case['id']}: {decision.action} {args}"
              + (f" / T: {decision.transcript}" if "transcript" in exp else "")
              + (f" / Q: {decision.question}" if decision.question else ""))
        if not ok:
            print(f"        expected: {exp}")

    print(f"\n정답 {passed}/{len(cases)}")
    if latencies:
        print(f"지연 중앙값 {statistics.median(latencies):.2f}s, 최대 {max(latencies):.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
