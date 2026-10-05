import pytest

from creativo_api.adapters import adapter_for, pixel_size
from creativo_api.policy import Decision, decide
from creativo_api.prompts import PassthroughEnhancer, prepare_prompt
from creativo_common.storage import FileStorage, StorageError
from creativo_common.worker_auth import WorkerAuthError, sign, verify
from creativo_contracts.enums import is_retryable
from creativo_contracts.generations import CreateGenerationRequest
from creativo_contracts.worker import WorkerHeartbeat
from creativo_flux.engine import batch_limit_for_vram, chunk_size
from creativo_orchestrator.scheduler import (
    choose_worker,
    desired_replicas,
    pack_limit,
    step_towards,
)
from creativo_qwen.engine import batch_limit_for_vram as qwen_batch_limit
from creativo_qwen.engine import chunk_size as qwen_chunk_size


def _worker(worker_id: str, active: int, status: str = "READY", cost: int = 0) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        worker_id=worker_id,
        model="fixture-image",
        model_version="v1",
        gpu="cpu",
        vram_gb=0,
        status=status,  # type: ignore[arg-type]
        active_jobs=active,
        max_concurrent_jobs=1,
        queue_depth=active,
        advertise_url=f"http://127.0.0.1/{worker_id}",
        hourly_micro_usd=cost,
    )


def test_choose_worker_prefers_shorter_queue_then_cost() -> None:
    chosen = choose_worker(
        [
            _worker("b", 0, cost=30),
            _worker("a", 0, cost=10),
            _worker("c", 4, cost=1),
            _worker("d", 0, status="STARTING", cost=0),
        ],
        "fixture-image",
    )
    assert chosen is not None
    assert chosen.worker_id == "a"


def test_choose_worker_skips_full_workers() -> None:
    assert choose_worker([_worker("a", 1)], "fixture-image") is None


def test_pack_limit_is_how_many_prompts_share_a_pass() -> None:
    small = _worker("laptop", 0)
    small.max_batch_size = 1
    large = _worker("a100", 0, cost=50)
    large.max_batch_size = 5
    assert pack_limit(small) == 1
    assert pack_limit(large) == 5
    chosen = choose_worker([small, large], "fixture-image")
    assert chosen is not None
    assert chosen.worker_id == "laptop"


def test_capacity_steps_instead_of_jumping() -> None:
    assert desired_replicas(queue_depth=20, in_flight=1, minimum=1, maximum=8) == 8
    assert step_towards(1, 8) == 2
    assert step_towards(2, 8) == 4
    assert step_towards(4, 8) == 8
    assert step_towards(4, 1) == 3
    assert desired_replicas(queue_depth=0, in_flight=0, minimum=0, maximum=4) == 0
    assert desired_replicas(queue_depth=0, in_flight=0, minimum=1, maximum=4) == 1


def test_retry_classification() -> None:
    assert is_retryable("invalid_parameters", True) is False
    assert is_retryable("transient", True) is True
    assert is_retryable("transient", False) is False
    assert is_retryable("worker_timeout", False) is True
    assert is_retryable("made_up", True) is False


@pytest.mark.asyncio
async def test_safety_blocks_before_enhancement() -> None:
    calls = 0

    class Spy(PassthroughEnhancer):
        async def enhance(self, prompt: str):
            nonlocal calls
            calls += 1
            return await super().enhance(prompt)

    blocked = await decide("a nude portrait in a studio", sfw_only=True)
    prepared = await prepare_prompt(blocked, Spy(), "a nude portrait in a studio")
    assert blocked.action == "block"
    assert blocked.category == "sexual_content"
    assert prepared.final is None
    assert calls == 0

    allowed = await decide("a quiet workshop at dusk", sfw_only=True)
    prepared = await prepare_prompt(allowed, Spy(), "a quiet workshop at dusk")
    assert allowed.action == "allow"
    assert prepared.final == "a quiet workshop at dusk"
    assert calls == 1


@pytest.mark.asyncio
async def test_child_sexual_content_is_always_blocked() -> None:
    decision = await decide("explicit photo of a child", sfw_only=False)
    assert decision.action == "block"
    assert decision.category == "child_sexual"


def test_worker_signature_roundtrip() -> None:
    body = b'{"job_id":"job_1"}'
    signature = sign("secret", 1_700_000_000, "job_1", body)
    verify(
        "secret",
        timestamp="1700000000",
        job_id="job_1",
        signature=signature,
        body=body,
        now=1_700_000_000,
    )
    with pytest.raises(WorkerAuthError):
        verify(
            "secret",
            timestamp="1700000000",
            job_id="job_1",
            signature=signature,
            body=b"{}",
            now=1_700_000_000,
        )


def test_storage_rejects_traversal(tmp_path) -> None:
    storage = FileStorage(tmp_path)
    storage.put("outputs/gen_1/output.png", b"ok")
    assert storage.get("outputs/gen_1/output.png") == b"ok"
    with pytest.raises(StorageError):
        storage.path_for("../secrets")


def test_pixel_size_respects_aspect() -> None:
    assert pixel_size("1024", "1:1") == (1024, 1024)
    width, height = pixel_size("1024", "16:9")
    assert width == 1024
    assert height < width
    assert width % 8 == 0 and height % 8 == 0


def test_flux_adapter_pins_schnell_sampling() -> None:
    adapter = adapter_for("flux")
    assert adapter is not None
    body = CreateGenerationRequest(
        type="image",
        mode="text_to_image",
        model="flux",
        prompt="a quiet workshop at dusk",
        aspect_ratio="1:1",
        resolution="512",
    )
    capabilities = {
        "text_to_image": True,
        "image_to_image": True,
        "aspect_ratios": ["1:1", "16:9"],
        "resolutions": ["512", "1024"],
        "max_reference_images": 1,
    }
    adapter.validate(body, capabilities)
    assert adapter.estimate_cost(body, 2) == 2
    large = body.model_copy(update={"resolution": "1024"})
    assert adapter.estimate_cost(large, 2) == 4
    parameters = adapter.build_parameters(body)
    assert parameters["num_inference_steps"] == 4
    assert parameters["guidance_scale"] == 0.0
    assert parameters["width"] == 512
    assert "batch_size" not in parameters


def test_qwen_adapter_pins_image_2512_sampling() -> None:
    adapter = adapter_for("qwen-image")
    assert adapter is not None
    body = CreateGenerationRequest(
        type="image",
        mode="text_to_image",
        model="qwen-image",
        prompt="a ceramic cup on a wooden table",
        aspect_ratio="1:1",
        resolution="1024",
    )
    capabilities = {
        "text_to_image": True,
        "image_to_image": False,
        "aspect_ratios": ["1:1"],
        "resolutions": ["1024"],
        "max_reference_images": 0,
    }
    adapter.validate(body, capabilities)
    assert adapter.estimate_cost(body, 4) == 4
    parameters = adapter.build_parameters(body)
    assert parameters["num_inference_steps"] == 4
    assert parameters["true_cfg_scale"] == 1.0
    assert parameters["width"] == 1024
    assert "fixture_behavior" not in parameters


def test_qwen_batch_limit_follows_vram() -> None:
    assert qwen_batch_limit(24) == 1
    assert qwen_batch_limit(40) == 2
    assert qwen_batch_limit(80) == 4
    assert qwen_chunk_size(limit=2, width=1024, height=1024) == 1
    assert qwen_chunk_size(limit=4, width=1024, height=1024) == 2


def test_flux_batch_limit_follows_vram() -> None:
    assert batch_limit_for_vram(6) == 1
    assert batch_limit_for_vram(24) == 1
    assert batch_limit_for_vram(40) == 4
    assert batch_limit_for_vram(80) == 5
    assert chunk_size(limit=1, width=512, height=512, resident=False) == 1
    assert chunk_size(limit=4, width=1024, height=1024, resident=True) == 2
    assert chunk_size(limit=5, width=1024, height=1024, resident=True) == 5
    assert chunk_size(limit=5, width=512, height=512, resident=True) == 5


@pytest.mark.asyncio
async def test_dispatch_does_not_rent_a_machine() -> None:
    from creativo_orchestrator.provider import ProviderNotConfigured, RunPodProvider

    with pytest.raises(ProviderNotConfigured):
        await RunPodProvider(api_key="key", template_id="template").provision("flux", "A100")


def test_block_decision_shape() -> None:
    decision = Decision(action="block", category="sexual_content", rule_id="sexual_0")
    assert decision.source == "rules"
