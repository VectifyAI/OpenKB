"""Frozen entry point for the OpenKB API sidecar.

Mirrors the `openkb-api = openkb.api:main` console script. The Tauri shell
spawns the frozen binary as `openkb-api-sidecar --host 127.0.0.1 --port <PORT>`;
argparse in `openkb.api.main` reads those args from sys.argv unchanged.
"""

from openkb.api import main

if __name__ == "__main__":
    main()
