import os
import sys
from pathlib import Path


REQUIRED_IN_PROD = [
    "ENV",
    "TELEGRAM_BOT_TOKEN",
    "BOT_USERNAME",
    "PUBLIC_BASE_URL",
    "ASAAS_WEBHOOK_TOKEN",
    "OPENAI_API_KEY",
]


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env = os.getenv("ENV", "dev").strip().lower()

    missing = []
    if env in {"prod", "production"}:
        for key in REQUIRED_IN_PROD:
            if not os.getenv(key, "").strip():
                missing.append(key)

    expected_dirs = [
        root / "logs",
        root / "data",
        root / "data" / "audio",
    ]

    for directory in expected_dirs:
        directory.mkdir(parents=True, exist_ok=True)

    print("preflight.env", env)
    print("preflight.root", root)
    print("preflight.dirs", ", ".join(str(item) for item in expected_dirs))

    if missing:
        print("preflight.missing", ", ".join(missing))
        return 2

    print("preflight.status OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
