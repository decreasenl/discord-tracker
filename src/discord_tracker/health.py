import json
import os
import time
from pathlib import Path


def main():
    try:
        status = json.loads((Path(os.environ['SQLITE_PATH']).parent / 'health.json').read_text())
        healthy = time.time() - status['at'] < 45 and status['connected']
    except (OSError, ValueError, KeyError):
        healthy = False
    raise SystemExit(0 if healthy else 1)


if __name__ == '__main__':
    main()
