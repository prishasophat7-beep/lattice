"""Start the backend: python -m oasis_engine  (the Tauri shell runs this)."""
import os

import uvicorn

from .api import create_app
from .watchdog import watch_parent


def main() -> None:
    watch_parent()
    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("OASIS_PORT", "8765")),
                log_level=os.environ.get("OASIS_LOG_LEVEL", "info"))


if __name__ == "__main__":
    main()
