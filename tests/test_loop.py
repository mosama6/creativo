import asyncio

import pytest
from tests.fixtures import _ports_open

CLIENT = {"X-Creativo-Client": "web"}


pytestmark = pytest.mark.skipif(not _ports_open(), reason="Postgres and Redis are not running")


async def _login(client, email: str):
    response = await client.post(
        "/api/v1/auth/dev-login",
        json={"email": email, "name": email.split("@")[0]},
        headers=CLIENT,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _generation(prompt: str = "A quiet workshop at dusk", **extra):
    body = {
        "type": "image",
        "mode": "text_to_image",
        "model": "fixture-image",
        "prompt": prompt,
        "aspect_ratio": "1:1",
        "resolution": "512",
    }
    body.update(extra)
    return body


async def test_generation_loop_settles_credits(api, dispatcher):
    async with api() as client:
        await _login(client, "ada@creativo.dev")
        created = await client.post(
            "/api/v1/generations",
            json=_generation(),
            headers={**CLIENT, "Idempotency-Key": "loop-success-0001"},
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["status"] == "queued"
        assert body["credit_price"] == 1
        reserved = await client.get("/api/v1/credits")
        assert reserved.json() == {"available": 39, "reserved": 1}

        assert await dispatcher.dispatch_once() is True
        completed = await client.get(f"/api/v1/generations/{body['id']}")
        assert completed.json()["status"] == "completed"
        output = await client.get(f"/api/v1/generations/{body['id']}/output")
        assert output.status_code == 200
        assert output.content.startswith(b"\x89PNG")
        settled = await client.get("/api/v1/credits")
        assert settled.json() == {"available": 39, "reserved": 0}

        replay = await client.post(
            "/api/v1/generations",
            json=_generation(),
            headers={**CLIENT, "Idempotency-Key": "loop-success-0001"},
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == body["id"]
        again = await client.get("/api/v1/credits")
        assert again.json()["available"] == 39


async def test_idempotency_key_rejects_a_different_body(api):
    async with api() as client:
        await _login(client, "grace@creativo.dev")
        headers = {**CLIENT, "Idempotency-Key": "same-key-00001"}
        first = await client.post(
            "/api/v1/generations", json=_generation("first prompt here"), headers=headers
        )
        assert first.status_code == 200, first.text
        second = await client.post(
            "/api/v1/generations", json=_generation("second prompt here"), headers=headers
        )
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "idempotency_key_reused"


async def test_other_user_cannot_read_generation(api):
    async with api() as owner, api() as stranger:
        await _login(owner, "owner@creativo.dev")
        await _login(stranger, "stranger@creativo.dev")
        created = await owner.post(
            "/api/v1/generations",
            json=_generation(),
            headers={**CLIENT, "Idempotency-Key": "owner-private-01"},
        )
        assert created.status_code == 200, created.text
        hidden = await stranger.get(f"/api/v1/generations/{created.json()['id']}")
        assert hidden.status_code == 404


async def test_blocked_prompt_is_not_charged(api):
    async with api() as client:
        await _login(client, "safety@creativo.dev")
        response = await client.post(
            "/api/v1/generations",
            json=_generation("a nude portrait"),
            headers={**CLIENT, "Idempotency-Key": "blocked-prompt-01"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "content_rejected"
        credits = await client.get("/api/v1/credits")
        assert credits.json() == {"available": 40, "reserved": 0}


async def test_cancel_queued_job_refunds(api, dispatcher):
    async with api() as client:
        await _login(client, "cancel@creativo.dev")
        created = await client.post(
            "/api/v1/generations",
            json=_generation("cancel me please"),
            headers={**CLIENT, "Idempotency-Key": "cancel-queued-001"},
        )
        generation_id = created.json()["id"]
        cancelled = await client.post(f"/api/v1/generations/{generation_id}/cancel", headers=CLIENT)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        credits = await client.get("/api/v1/credits")
        assert credits.json() == {"available": 40, "reserved": 0}
        await dispatcher.reconcile()
        await dispatcher.dispatch_once()
        again = await client.get(f"/api/v1/generations/{generation_id}")
        assert again.json()["status"] == "cancelled"
        assert (await client.get("/api/v1/credits")).json()["available"] == 40


async def test_permanent_failure_refunds_once(api, dispatcher):
    async with api() as client:
        await _login(client, "fail@creativo.dev")
        created = await client.post(
            "/api/v1/generations",
            json=_generation("this fixture fails", fixture_behavior="permanent"),
            headers={**CLIENT, "Idempotency-Key": "permanent-fail-01"},
        )
        assert created.status_code == 200, created.text
        assert await dispatcher.dispatch_once() is True
        failed = await client.get(f"/api/v1/generations/{created.json()['id']}")
        assert failed.json()["status"] == "failed"
        assert (await client.get("/api/v1/credits")).json() == {"available": 40, "reserved": 0}


async def test_retryable_failure_then_succeeds(api, dispatcher):
    async with api() as client:
        await _login(client, "retry@creativo.dev")
        created = await client.post(
            "/api/v1/generations",
            json=_generation("retry this frame", fixture_behavior="retryable"),
            headers={**CLIENT, "Idempotency-Key": "retry-once-00001"},
        )
        assert created.status_code == 200, created.text
        assert await dispatcher.dispatch_once() is True
        await asyncio.sleep(0.15)
        await dispatcher.reconcile()
        assert await dispatcher.dispatch_once() is True
        completed = await client.get(f"/api/v1/generations/{created.json()['id']}")
        assert completed.json()["status"] == "completed", completed.text
        assert (await client.get("/api/v1/credits")).json() == {"available": 39, "reserved": 0}


async def test_auto_and_planned_models_are_rejected(api):
    async with api() as client:
        await _login(client, "router@creativo.dev")
        auto = await client.post(
            "/api/v1/generations",
            json=_generation(model="auto"),
            headers={**CLIENT, "Idempotency-Key": "auto-model-00001"},
        )
        assert auto.status_code == 422
        assert auto.json()["error"]["code"] == "model_selection_required"
        planned = await client.post(
            "/api/v1/generations",
            json=_generation(model="qwen-image"),
            headers={**CLIENT, "Idempotency-Key": "qwen-planned-001"},
        )
        assert planned.status_code == 422
        assert planned.json()["error"]["code"] == "model_unavailable"


async def test_five_queued_prompts_share_one_dispatch(api, dispatcher):
    async with api() as client:
        await _login(client, "burst@creativo.dev")
        ids: list[str] = []
        for index in range(5):
            created = await client.post(
                "/api/v1/generations",
                json=_generation(f"frame {index} of a quiet workshop"),
                headers={**CLIENT, "Idempotency-Key": f"burst-frame-{index:02d}"},
            )
            assert created.status_code == 200, created.text
            ids.append(created.json()["id"])
        assert await dispatcher.dispatch_once() is True
        for generation_id in ids:
            completed = await client.get(f"/api/v1/generations/{generation_id}")
            body = completed.json()
            assert body["status"] == "completed", body
            assert body["credit_price"] == 1
            output = await client.get(f"/api/v1/generations/{generation_id}/output")
            assert output.status_code == 200
            assert output.content.startswith(b"\x89PNG")
        settled = await client.get("/api/v1/credits")
        assert settled.json() == {"available": 35, "reserved": 0}
        assert await dispatcher.dispatch_once() is False


async def test_insufficient_credits(api, db_factory):
    async with api() as client:
        user = await _login(client, "broke@creativo.dev")
        async with db_factory() as session:
            from sqlalchemy import update

            from creativo_db.models import CreditAccount

            await session.execute(
                update(CreditAccount).where(CreditAccount.user_id == user["id"]).values(available=0)
            )
            await session.commit()
        response = await client.post(
            "/api/v1/generations",
            json=_generation("no credits left"),
            headers={**CLIENT, "Idempotency-Key": "no-credits-00001"},
        )
        assert response.status_code == 402
        assert (await client.get("/api/v1/credits")).json()["available"] == 0
