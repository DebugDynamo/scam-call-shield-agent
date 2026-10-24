import sys

import uvicorn

from .config import get_settings

if __name__ == "__main__":
    s = get_settings()
    try:
        uvicorn.run("app.main:create_app", factory=True, host=s.host, port=s.port, log_level=s.log_level.lower())
    except OSError as e:
        print(f"ERROR: cannot bind {s.host}:{s.port}: {e}", file=sys.stderr)
        sys.exit(1)
