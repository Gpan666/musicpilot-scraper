from __future__ import annotations

import uvicorn

from musicpilot.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "musicpilot.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
