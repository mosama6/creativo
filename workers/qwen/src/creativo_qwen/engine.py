import os
from io import BytesIO
from pathlib import Path

REPO = "Qwen/Qwen-Image-2512"
LORA_REPO = "lightx2v/Qwen-Image-2512-Lightning"
LORA_FILE = "Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors"
STEPS = 4


def batch_limit_for_vram(vram_gb: int) -> int:
    """How many Qwen prompts may share one worker visit. The fill window uses this."""
    if vram_gb >= 70:
        return 4
    if vram_gb >= 40:
        return 2
    return 1


def chunk_size(*, limit: int, width: int, height: int) -> int:
    """How many of those prompts share one forward. 1024 on an 80 GB GPU is two."""
    cap = max(1, limit)
    if width * height >= 1024 * 1024 and cap < 4:
        return 1
    if width * height >= 1024 * 1024:
        return min(cap, 2)
    return cap


def prepare_cache() -> None:
    root = Path.cwd()
    for candidate in [root, *root.parents]:
        if (candidate / "apps").is_dir() and (candidate / "packages").is_dir():
            os.environ.setdefault("HF_HUB_CACHE", str(candidate / "data" / "hf" / "hub"))
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            return


class QwenEngine:
    """Qwen-Image-2512 plus the 4-step Lightning LoRA used by the fast ComfyUI graph."""

    def __init__(self) -> None:
        self.pipe = None
        self.gpu_name = "cuda"
        self.vram_gb = 80
        self.max_batch_size = 1

    def probe(self) -> None:
        prepare_cache()
        import torch

        if not torch.cuda.is_available():
            self.max_batch_size = 1
            return
        self.gpu_name = torch.cuda.get_device_name(0)
        self.vram_gb = max(1, round(torch.cuda.get_device_properties(0).total_memory / (1024**3)))
        self.max_batch_size = batch_limit_for_vram(self.vram_gb)

    def load(self) -> None:
        self.probe()
        import torch
        from diffusers import DiffusionPipeline

        if not torch.cuda.is_available():
            raise RuntimeError("Qwen-Image-2512 needs a CUDA GPU.")
        pipe = DiffusionPipeline.from_pretrained(REPO, torch_dtype=torch.bfloat16)
        pipe.load_lora_weights(LORA_REPO, weight_name=LORA_FILE)
        pipe.fuse_lora()
        pipe.unload_lora_weights()
        pipe.enable_model_cpu_offload()
        self.pipe = pipe

    def unload(self) -> None:
        self.pipe = None
        import gc

        gc.collect()
        try:
            import torch
        except ImportError:
            return
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def generate_prompts(
        self,
        prompts: list[str],
        width: int,
        height: int,
        steps: int,
        true_cfg: float,
    ) -> list[bytes]:
        if self.pipe is None:
            raise RuntimeError("Qwen weights are not loaded.")
        if not prompts:
            raise RuntimeError("A Qwen pass needs at least one prompt.")
        import torch

        step_count = max(1, min(steps, STEPS))
        size = chunk_size(limit=self.max_batch_size, width=width, height=height)
        encoded: list[bytes] = []
        for start in range(0, len(prompts), size):
            piece = prompts[start : start + size]
            generator = torch.Generator(device="cpu")
            kwargs = {
                "prompt": piece,
                "width": width,
                "height": height,
                "num_inference_steps": step_count,
                "true_cfg_scale": true_cfg,
                "generator": generator,
            }
            if true_cfg > 1:
                kwargs["negative_prompt"] = " "
            images = self.pipe(**kwargs).images
            if len(images) != len(piece):
                raise RuntimeError("The model returned a different number of images.")
            for image in images:
                buffer = BytesIO()
                image.save(buffer, format="PNG")
                encoded.append(buffer.getvalue())
        torch.cuda.empty_cache()
        return encoded
