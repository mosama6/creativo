import os
from io import BytesIO
from pathlib import Path

BASE_REPO = "black-forest-labs/FLUX.1-schnell"
GGUF_REPO = "city96/FLUX.1-schnell-gguf"
GGUF_FILE = "flux1-schnell-Q4_K_S.gguf"
RESIDENT_VRAM_GB = 36


def batch_limit_for_vram(vram_gb: int) -> int:
    """How many different prompts can share one pass once the transformer stays on the GPU."""
    if vram_gb >= 70:
        return 5
    if vram_gb >= RESIDENT_VRAM_GB:
        return 4
    return 1


def chunk_size(*, limit: int, width: int, height: int, resident: bool) -> int:
    if not resident:
        return 1
    cap = max(1, limit)
    if width * height >= 1024 * 1024 and cap < 5:
        return min(cap, 2)
    return cap


def _keep_gguf_quant_type() -> None:
    """Sequential offload rebuilds parameters without the GGUF quant type."""
    from diffusers.quantizers.gguf.utils import GGUFParameter

    original = GGUFParameter.__new__

    def wrapped(cls, data, requires_grad=False, quant_type=None):
        if quant_type is None:
            quant_type = getattr(data, "quant_type", None)
        return original(cls, data, requires_grad, quant_type)

    GGUFParameter.__new__ = wrapped


def prepare_cache() -> None:
    root = Path.cwd()
    for candidate in [root, *root.parents]:
        if (candidate / "apps").is_dir() and (candidate / "packages").is_dir():
            os.environ.setdefault("HF_HUB_CACHE", str(candidate / "data" / "hf" / "hub"))
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            return


class FluxEngine:
    def __init__(self) -> None:
        self.pipe = None
        self.img2img = None
        self.gpu_name = "cuda"
        self.vram_gb = 6
        self.max_batch_size = 1
        self.resident = False

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
        from diffusers import FluxImg2ImgPipeline, FluxPipeline

        if not torch.cuda.is_available():
            raise RuntimeError("FLUX.1 schnell needs a CUDA GPU.")
        if self.vram_gb >= RESIDENT_VRAM_GB:
            pipe = self._load_resident(torch, FluxPipeline)
        else:
            pipe = self._load_gguf(torch, FluxPipeline)
        pipe.set_progress_bar_config(disable=True)
        pipe.vae.enable_tiling()
        pipe.vae.enable_slicing()
        self.pipe = pipe
        self.img2img = FluxImg2ImgPipeline(
            scheduler=pipe.scheduler,
            vae=pipe.vae,
            text_encoder=pipe.text_encoder,
            tokenizer=pipe.tokenizer,
            text_encoder_2=pipe.text_encoder_2,
            tokenizer_2=pipe.tokenizer_2,
            transformer=pipe.transformer,
        )

    def _load_resident(self, torch, pipeline_cls):
        pipe = pipeline_cls.from_pretrained(BASE_REPO, torch_dtype=torch.bfloat16)
        pipe.enable_model_cpu_offload()
        self.resident = True
        return pipe

    def _load_gguf(self, torch, pipeline_cls):
        from diffusers import FluxTransformer2DModel, GGUFQuantizationConfig
        from huggingface_hub import hf_hub_download

        weights = hf_hub_download(GGUF_REPO, GGUF_FILE)
        dtype = torch.float16
        transformer = FluxTransformer2DModel.from_single_file(
            weights,
            quantization_config=GGUFQuantizationConfig(compute_dtype=dtype),
            config=BASE_REPO,
            subfolder="transformer",
            torch_dtype=dtype,
        )
        pipe = pipeline_cls.from_pretrained(BASE_REPO, transformer=transformer, torch_dtype=dtype)
        _keep_gguf_quant_type()
        pipe.enable_sequential_cpu_offload()
        self.resident = False
        self.max_batch_size = 1
        return pipe

    def generate_prompts(
        self,
        prompts: list[str],
        width: int,
        height: int,
        steps: int,
        guidance: float,
        max_sequence_length: int,
        strength: float,
        references: list[bytes | None],
    ) -> list[bytes]:
        """One forward pass per group of prompts that share a size. Order is preserved."""
        import torch
        from PIL import Image

        if self.pipe is None or self.img2img is None:
            raise RuntimeError("Flux weights are not loaded.")
        if len(prompts) != len(references) or not prompts:
            raise RuntimeError("Each prompt needs one reference slot.")
        encoded: list[bytes | None] = [None] * len(prompts)
        text_indexes = [index for index, reference in enumerate(references) if reference is None]
        image_indexes = [index for index, reference in enumerate(references) if reference is not None]
        step_count = max(1, min(steps, 4))
        size = chunk_size(
            limit=self.max_batch_size, width=width, height=height, resident=self.resident
        )
        for indexes in (text_indexes, image_indexes):
            for start in range(0, len(indexes), size):
                piece = indexes[start : start + size]
                generator = torch.Generator(device="cpu")
                shared = {
                    "prompt": [prompts[index] for index in piece],
                    "guidance_scale": guidance,
                    "num_inference_steps": step_count,
                    "max_sequence_length": max_sequence_length,
                    "generator": generator,
                }
                if references[piece[0]] is None:
                    images = self.pipe(width=width, height=height, **shared).images
                else:
                    inits = [
                        Image.open(BytesIO(references[index] or b""))
                        .convert("RGB")
                        .resize((width, height))
                        for index in piece
                    ]
                    images = self.img2img(
                        image=inits, strength=strength, **shared
                    ).images
                if len(images) != len(piece):
                    raise RuntimeError("The model returned a different number of images.")
                for index, image in zip(piece, images, strict=True):
                    buffer = BytesIO()
                    image.save(buffer, format="PNG")
                    encoded[index] = buffer.getvalue()
        torch.cuda.empty_cache()
        return [item for item in encoded if item is not None]
