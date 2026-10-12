import json
import os
import time
from dotenv import load_dotenv
from services.ai import analyze

# .env 파일 로드
load_dotenv()

# 테스트용 가상의 업무 맥락 (현재 시각 및 미완료 업무 예시)
context = {
    "now": "2026-10-13T09:00:00+09:00",
    "tasks": [
        {"item_id": "t1", "name": "보고서 검토", "due_at": "2026-10-14T15:00:00+09:00", "status": "pending"},
        {"item_id": "t2", "name": "팀 회의 준비", "due_at": "2026-10-13T18:00:00+09:00", "status": "pending"},
        {"item_id": "t3", "name": "코드 리팩토링", "due_at": "2026-10-15T12:00:00+09:00", "status": "pending"}
    ]
}

# 테스트할 발화 5개 샘플
sample_utterances = [
    "내일 오후 3시까지 보고서 검토 추가해줘",
    "오늘 할 일 알려줘",
    "보고서 검토 완료했어",
    "보고서 마감 두 시간 미뤄줘",
    "보고서 알림 설정해줘"
]

def run_quick_test():
    print("🚀 Gemini Flash-Lite 5개 발화 테스트 시작...\n")
    latencies = []
    success_count = 0

    for i, text in enumerate(sample_utterances, 1):
        print(f"[{i}/5] 입력: \"{text}\"")
        
        start_time = time.time()
        try:
            decision = analyze(text, context)
            elapsed = time.time() - start_time
            latencies.append(elapsed)
            success_count += 1
            
            print(f"  👉 [성공] 액션: {decision.action} (소요 시간: {elapsed:.2f}초)")
            print(f"     인자: {decision.args.model_dump()}")
            if decision.question:
                print(f"     재질문: {decision.question}")
        except Exception as e:
            print(f"  ❌ [실패] 에러 발생: {e}")
        print("-" * 50)

    if latencies:
        median_latency = sorted(latencies)[len(latencies) // 2]
        print(f"\n📊 [테스트 결과 요약]")
        print(f"- 성공 건수: {success_count}/5")
        print(f"- 응답 시간(지연) 중앙값: {median_latency:.2f}초")

if __name__ == "__main__":
    run_quick_test()