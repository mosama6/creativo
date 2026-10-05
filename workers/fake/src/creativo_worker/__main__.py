import uvicorn

from creativo_common.settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "creativo_worker.main:app",
        factory=True,
        host="0.0.0.0",
        port=settings.worker_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
