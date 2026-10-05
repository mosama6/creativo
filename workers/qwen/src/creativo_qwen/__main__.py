import uvicorn

from creativo_common.settings import get_settings
from creativo_qwen.engine import prepare_cache


def main() -> None:
    prepare_cache()
    settings = get_settings()
    uvicorn.run(
        "creativo_qwen.main:app",
        factory=True,
        host="0.0.0.0",
        port=settings.worker_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
