"""쌓인 시험 업무를 Eureka에서 정리한다. 기본은 미리보기이고 아무것도 지우지 않는다.

Eureka 삭제는 복구 가능 여부를 확인하지 못했다. 아래 조건을 모두 만족하는 업무만 지운다.
- 상태가 done (미완료 업무는 어떤 경우에도 지우지 않는다)
- 이름이 TEST_NAME_PREFIXES 중 하나로 시작

실행 (실제 Eureka 키가 있는 환경):
    python tests/integration/cleanup_test_tasks.py                  미리보기
    python tests/integration/cleanup_test_tasks.py --delete --limit 1   1건만 삭제
    python tests/integration/cleanup_test_tasks.py --delete --limit 50  최대 50건 삭제
    python tests/integration/cleanup_test_tasks.py --delete --ids 1000025 1000026  완료 업무를 ID로 삭제

--ids는 이름 규칙과 상관없이 지정한 업무를 지운다. 지정한 ID 중 하나라도 완료 상태가 아니면 아무것도 지우지 않는다.
"""

import argparse
import asyncio
import sys

from desk_pet_backend.config import settings
from desk_pet_backend.eureka import EurekaClient, EurekaError

LIST_PATH = "/_api_/items/0/list?sites&detail=true&limit=100"
LIST_SETTLE_SECONDS = 5  # 삭제 직후 목록에는 지운 업무가 잠시 남아 있을 수 있다
TEST_NAME_PREFIXES = (
    "실측테스트 ",
    "실측재질문 ",
    "이름검증테스트 ",
    "검증 ",
    "테스트 마감변경 ",
    "삭제검증테스트 ",
)


def classify(items: list[dict]) -> dict[str, list[dict]]:
    """업무를 delete(삭제 대상), review(완료됐지만 시험 이름이 아님), open(미완료)으로 나눈다."""
    groups: dict[str, list[dict]] = {"delete": [], "review": [], "open": []}
    for item in items:
        try:
            done = EurekaClient._task_status(item) == "done"
        except EurekaError:
            done = False  # 상태를 모르면 지우지 않는다
        if not done:
            groups["open"].append(item)
        elif str(item.get("name", "")).startswith(TEST_NAME_PREFIXES):
            groups["delete"].append(item)
        else:
            groups["review"].append(item)
    return groups


def pick_by_ids(groups: dict[str, list[dict]], ids: list[str]) -> list[dict]:
    """지정한 ID의 완료 업무를 고른다. 없거나 완료가 아닌 ID가 있으면 ValueError를 낸다."""
    done = {str(item["id"]): item for item in groups["delete"] + groups["review"]}
    invalid = [item_id for item_id in ids if item_id not in done]
    if invalid:
        raise ValueError(f"완료 업무가 아니거나 없는 ID: {invalid}")
    return [done[item_id] for item_id in dict.fromkeys(ids)]


def show(title: str, items: list[dict]) -> None:
    print(f"\n[{title}] {len(items)}건")
    for item in items:
        print(f"  {item.get('id')}  {item.get('name')}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete", action="store_true", help="삭제 대상을 실제로 지운다")
    parser.add_argument("--limit", type=int, default=0, help="--delete 때 지울 최대 건수 (필수, 1 이상)")
    parser.add_argument("--ids", nargs="+", default=[], help="--delete 때 지울 완료 업무 ID")
    args = parser.parse_args()
    if args.ids and (not args.delete or args.limit):
        parser.error("--ids는 --delete와 함께 쓰고 --limit과는 같이 쓰지 않습니다")
    if args.delete and not args.ids and args.limit < 1:
        parser.error("--delete에는 --limit 1 이상 또는 --ids가 필요합니다")
    if settings.eureka_mode != "real":
        print("EUREKA_MODE=real 환경에서만 실행합니다.")
        return 1

    client = EurekaClient()
    items = (await client._request("GET", LIST_PATH)).get("list", [])
    groups = classify(items)
    print(f"전체 {len(items)}건")
    show("삭제 대상: 완료 + 시험 이름", groups["delete"])
    show("직접 확인: 완료 + 다른 이름 (지우지 않음)", groups["review"])
    show("보호: 미완료 (지우지 않음)", groups["open"])

    if not args.delete:
        print("\n미리보기입니다. 아무것도 지우지 않았습니다.")
        return 0

    try:
        targets = pick_by_ids(groups, args.ids) if args.ids else groups["delete"][: args.limit]
    except ValueError as exc:
        print(f"\n아무것도 지우지 않았습니다. {exc}")
        return 1

    deleted = 0
    for item in targets:
        item_id = str(item["id"])
        data = await client._request("DELETE", f"/_api_/items/{item_id}")
        if str(data.get("id")) != item_id:
            print(f"응답 id가 달라 중단합니다: {item_id} {data}")
            return 1
        deleted += 1
        print(f"삭제: {item_id}  {item.get('name')}")

    await asyncio.sleep(LIST_SETTLE_SECONDS)
    remaining = (await client._request("GET", LIST_PATH)).get("list", [])
    print(f"\n{deleted}건 삭제, 전체 {len(items)}건 → {len(remaining)}건")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
