"""정리 스크립트가 미완료·실제 업무를 삭제 대상으로 고르지 않는지 확인한다. Eureka는 호출하지 않는다."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "integration" / "cleanup_test_tasks.py"
spec = importlib.util.spec_from_file_location("cleanup_test_tasks", SCRIPT)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def item(name, *stages):
    return {"id": 1, "name": name, "stage$$": [{"status": stage} for stage in stages]}


@pytest.mark.parametrize("name", [
    "실측테스트 01번 과제", "실측재질문 02번 자료", "이름검증테스트 보고서 작성",
    "검증 코드 리뷰", "테스트 마감변경 1106 업무", "삭제검증테스트 1106",
])
def test_done_test_task_is_delete_target(name):
    assert cleanup.classify([item(name, "done")])["delete"]


@pytest.mark.parametrize("stages", [("todo",), ("doing",), ("hold",), ("done", "todo")])
def test_open_task_is_protected_even_with_test_name(stages):
    groups = cleanup.classify([item("검증 코드 리뷰", *stages)])
    assert not groups["delete"]
    assert groups["open"]


@pytest.mark.parametrize("name", [
    "회의 준비", "보고서 작성", "할 일", "내일 오후 3시",
    "Desk Pet Eureka 연동 검증 20261028-134838", "데스크 펫 Eureka 연동 테스트", "검증", "검증회의",
])
def test_done_task_with_other_name_needs_review(name):
    groups = cleanup.classify([item(name, "done")])
    assert not groups["delete"]
    assert groups["review"]


def groups_with_ids():
    done_test = {**item("검증 코드 리뷰", "done"), "id": 1}
    done_other = {**item("할 일", "done"), "id": 2}
    still_open = {**item("회의 준비", "todo"), "id": 3}
    return cleanup.classify([done_test, done_other, still_open])


def test_ids_can_pick_done_task_with_any_name():
    picked = cleanup.pick_by_ids(groups_with_ids(), ["2", "1", "2"])
    assert [entry["id"] for entry in picked] == [2, 1]


@pytest.mark.parametrize("ids", [["3"], ["2", "3"], ["999"], ["1", "999"]])
def test_ids_refuse_open_or_unknown_task(ids):
    with pytest.raises(ValueError, match="완료 업무가 아니거나"):
        cleanup.pick_by_ids(groups_with_ids(), ids)


def test_unknown_status_is_not_deleted():
    groups = cleanup.classify([{"id": 1, "name": "검증 코드 리뷰", "stage$$": []}])
    assert not groups["delete"]
    assert groups["open"]
