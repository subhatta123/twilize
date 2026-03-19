#!/usr/bin/env python
"""Launch the cwtwb Dashboard Extension server.

Usage:
    python extension/scripts/start.py          # Production (serves built frontend)
    python extension/scripts/start.py --dev    # Dev mode (frontend on 5173, backend on 8000)
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Start cwtwb Dashboard Extension")
    parser.add_argument("--dev", action="store_true", help="Run in development mode")
    parser.add_argument("--port", type=int, default=8000, help="Backend port")
    parser.add_argument("--host", default="0.0.0.0", help="Backend host")
    args = parser.parse_args()

    ext_dir = Path(__file__).resolve().parent.parent
    project_root = ext_dir.parent
    frontend_dir = ext_dir / "frontend"
    backend_dir = ext_dir / "backend"

    # Ensure project root is on sys.path so "extension.backend.app" resolves
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    if args.dev:
        print(f"Starting dev mode...")
        print(f"  Backend: http://localhost:{args.port}")
        print(f"  Frontend: http://localhost:5173")
        print(f"  Extension .trex points to: http://localhost:5173")

        # Start frontend dev server in background
        frontend_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(frontend_dir),
            shell=True,
        )

        try:
            # Start backend
            os.environ["CWTWB_EXT_PORT"] = str(args.port)
            import uvicorn
            from extension.backend.app import app

            uvicorn.run(app, host=args.host, port=args.port)
        finally:
            frontend_proc.terminate()
    else:
        # Production: build frontend if needed, serve from FastAPI
        dist_dir = frontend_dir / "dist"
        if not dist_dir.exists():
            print("Building frontend...")
            subprocess.run(
                ["npm", "install"],
                cwd=str(frontend_dir),
                check=True,
                shell=True,
            )
            subprocess.run(
                ["npm", "run", "build"],
                cwd=str(frontend_dir),
                check=True,
                shell=True,
            )

        print(f"Starting production server on http://localhost:{args.port}")
        os.environ["CWTWB_EXT_PORT"] = str(args.port)
        import uvicorn
        from extension.backend.app import app

        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
