import os
from io import BytesIO
from pathlib import Path

REPO = "Qwen/Qwen-Image-2512"


def prepare_cache() -> None:
    root = Path.cwd()
    for candidate in [root, *root.parents]:
        if (candidate / "apps").is_dir() and (candidate / "packages").is_dir():
            os.environ.setdefault("HF_HUB_CACHE", str(candidate / "data" / "hf" / "hub"))
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            return


class QwenEngine:
    """Qwen-Image-2512. Weights load for one job and can be dropped afterward."""

    def __init__(self) -> None:
        self.pipe = None
        self.gpu_name = "cuda"
        self.vram_gb = 80

    def probe(self) -> None:
        prepare_cache()
        import torch

        if not torch.cuda.is_available():
            return
        self.gpu_name = torch.cuda.get_device_name(0)
        self.vram_gb = max(1, round(torch.cuda.get_device_properties(0).total_memory / (1024**3)))

    def load(self) -> None:
        self.probe()
        import torch
        from diffusers import DiffusionPipeline

        if not torch.cuda.is_available():
            raise RuntimeError("Qwen-Image-2512 needs a CUDA GPU.")
        pipe = DiffusionPipeline.from_pretrained(REPO, torch_dtype=torch.bfloat16)
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

    def generate(self, prompt: str, width: int, height: int, steps: int, true_cfg: float) -> bytes:
        if self.pipe is None:
            raise RuntimeError("Qwen weights are not loaded.")
        image = self.pipe(
            prompt=prompt,
            negative_prompt=" ",
            width=width,
            height=height,
            num_inference_steps=max(1, min(steps, 50)),
            true_cfg_scale=true_cfg,
        ).images[0]
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
