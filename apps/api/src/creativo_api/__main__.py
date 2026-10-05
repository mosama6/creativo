import uvicorn

from creativo_common.settings import get_settings
from creativo_db.migrate import upgrade


def main() -> None:
    settings = get_settings()
    if settings.app_env in {"development", "test"}:
        upgrade()
    uvicorn.run(
        "creativo_api.app:create_app", factory=True, host="0.0.0.0", port=8000, reload=False
    )


if __name__ == "__main__":
    main()
