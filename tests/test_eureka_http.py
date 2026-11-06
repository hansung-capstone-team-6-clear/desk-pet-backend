"""실제 EurekaClient의 HTTP 계약과 업무 변환을 네트워크 없이 검증한다."""

from datetime import datetime

import httpx
import pytest

from desk_pet_backend.config import settings
from desk_pet_backend.eureka import KST, EurekaClient, EurekaError, to_epoch_ms


@pytest.fixture(autouse=True)
def eureka_settings(monkeypatch):
    monkeypatch.setattr(settings, "eureka_api_key", "test-key")
    monkeypatch.setattr(settings, "eureka_process_id", "test-process")
    monkeypatch.setattr(settings, "eureka_base_url", "https://example.test/flw-v1")


async def test_create_and_list_convert_real_http_shapes():
    due = datetime(2026, 10, 29, 15, tzinfo=KST)
    requests = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-api-key"] == "test-key"
        if request.method == "POST":
            assert request.url.path == "/flw-v1/_api_/items/0/start"
            assert request.read() == (
                b'{"processId":"test-process","name":"report","dueAt":1793253600000,'
                b'"priority":"high"}'
            )
            return httpx.Response(201, json={"id": 1000013, "name": "report", "stageIds": [1]})
        assert request.method == "GET"
        assert request.url.path == "/flw-v1/_api_/items/0/list"
        assert request.url.query == b"sites&detail=true&limit=100"
        return httpx.Response(200, json={"list": [
            {"id": 1000013, "name": "report", "dueAt": to_epoch_ms(due),
             "stage$$": [{"status": "todo"}]},
            {"id": 1000012, "name": "finished", "dueAt": to_epoch_ms(due),
             "stage$$": [{"status": "done"}]},
        ]})

    client = EurekaClient(transport=httpx.MockTransport(respond))
    created = await client.create_task("report", due)
    listed = await client.list_open_tasks()
    assert created.item_id == "1000013"
    assert created.status == "todo"
    assert len(listed) == 1
    assert listed[0].item_id == created.item_id
    assert listed[0].status == "todo"
    assert listed[0].due_at == due
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("stages", "expected"),
    [(["todo", "todo"], "todo"), (["done", "todo"], "doing"),
     (["doing", "todo"], "doing"), (["hold", "todo"], "hold"),
     (["done", "done"], "done")],
)
def test_stage_status_conversion(stages, expected):
    assert EurekaClient._task_status({"stage$$": [{"status": s} for s in stages]}) == expected


async def test_create_rejects_mismatched_response():
    client = EurekaClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"id": 3, "name": "other"})
    ))
    with pytest.raises(EurekaError, match="id 또는 name"):
        await client.create_task("report", datetime(2026, 10, 29, tzinfo=KST))


async def test_list_rejects_invalid_status_and_full_page():
    due_ms = to_epoch_ms(datetime(2026, 10, 29, tzinfo=KST))
    item = {"id": 1, "name": "report", "dueAt": due_ms, "stage$$": []}
    client = EurekaClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"list": [item]})
    ))
    with pytest.raises(EurekaError, match="상태"):
        await client.list_open_tasks()

    client = EurekaClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"list": [item] * 100})
    ))
    with pytest.raises(EurekaError, match="페이지"):
        await client.list_open_tasks()


async def test_http_failure_is_not_treated_as_success():
    client = EurekaClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"message": "unauthorized"})
    ))
    with pytest.raises(EurekaError, match="HTTP 401"):
        await client.list_open_tasks()


async def test_complete_posts_to_item_and_checks_response_id():
    seen = {}

    def respond(request):
        seen["method"], seen["path"], seen["body"] = request.method, request.url.path, request.content
        return httpx.Response(200, json={"id": 1000013})

    client = EurekaClient(transport=httpx.MockTransport(respond))
    await client.complete_task("1000013")
    assert seen == {"method": "POST", "path": "/flw-v1/_api_/items/1000013/complete", "body": b"{}"}


async def test_complete_rejects_mismatched_response():
    client = EurekaClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"id": 999})
    ))
    with pytest.raises(EurekaError, match="완료 응답"):
        await client.complete_task("1000013")


async def test_update_due_puts_epoch_ms_and_checks_response():
    due = datetime(2026, 10, 29, 15, tzinfo=KST)
    due_ms = to_epoch_ms(due)
    seen = {}

    def respond(request):
        seen["method"], seen["path"], seen["body"] = request.method, request.url.path, request.content
        return httpx.Response(200, json={"id": 1000013, "dueAt": due_ms})

    client = EurekaClient(transport=httpx.MockTransport(respond))
    await client.update_due("1000013", due)
    assert seen == {
        "method": "PUT",
        "path": "/flw-v1/_api_/items/1000013",
        "body": f'{{"dueAt":{due_ms}}}'.encode(),
    }


@pytest.mark.parametrize("body, message", [
    ({"id": 999}, "응답의 id"),
    ({"id": 1000013, "dueAt": 1}, "응답의 dueAt"),
])
async def test_update_due_rejects_mismatched_response(body, message):
    client = EurekaClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=body)
    ))
    with pytest.raises(EurekaError, match=message):
        await client.update_due("1000013", datetime(2026, 10, 29, tzinfo=KST))
