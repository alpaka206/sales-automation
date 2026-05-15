"""Entry point for PyInstaller .exe — starts the FastAPI server via uvicorn."""

from __future__ import annotations

import os
import shutil
import sys


def _ensure_env() -> None:
    """Copy .env.example to .env if missing, and warn the user."""
    env_path = os.path.join(os.getcwd(), ".env")
    example_path = os.path.join(os.getcwd(), ".env.example")
    if os.path.exists(env_path):
        return
    if os.path.exists(example_path):
        shutil.copy2(example_path, env_path)
        print("[Sales Automation] .env 파일이 없어서 .env.example 을 복사했습니다.")
        print("[Sales Automation] .env 파일을 열어 필요한 값을 설정한 후 다시 실행하세요.")
    else:
        print("[Sales Automation] .env 파일이 없습니다. .env.example 을 참고하여 생성하세요.")
    print()
    input("Enter 키를 누르면 종료합니다...")
    sys.exit(1)


def main() -> None:
    """Start the uvicorn server."""
    _ensure_env()

    import uvicorn

    from .common.config import settings

    print(f"[Sales Automation] 서버 시작: http://{settings.APP_HOST}:{settings.APP_PORT}")
    print("[Sales Automation] 종료하려면 Ctrl+C 를 누르세요.")
    uvicorn.run(
        "src.api.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
