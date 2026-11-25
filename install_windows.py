"""Idempotent native Windows setup, also used by start-windows.cmd."""

import argparse
import importlib.util
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

from process_control import windows_project_server_pids, signal_process_group, process_group_exists
from indicator_process import restore_system_cursors


def install_optional_features():
    """Browser mode and proof images need these. A failure must not block the dashboard."""
    for module, package in (("PIL", "pillow"), ("playwright", "playwright")):
        if importlib.util.find_spec(module) is None:
            result = subprocess.run([sys.executable, "-m", "pip", "install", package], check=False)
            if result.returncode:
                print(f"Could not install {package}; the features that need it stay unavailable.")
    if importlib.util.find_spec("playwright") is None:
        return
    browsers = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
                    or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright")
    if not list(browsers.glob("firefox-*")):
        print("Downloading the Firefox used by Browser mode...")
        result = subprocess.run([sys.executable, "-m", "playwright", "install", "firefox"], check=False)
        if result.returncode:
            print("Could not download Firefox for Browser mode. Run: python -m playwright install firefox")


def initialize_files(root):
    """Seed missing files only; preserve API keys, presets and runtime state."""
    for name in ("state", "sessions", "logs", "rt_library/gauntlets/runs",
                 "rt_library/artifacts"):
        (root / name).mkdir(parents=True, exist_ok=True)
    seed = root / "setup" / "starter_rt_library"
    for source in seed.rglob("*.yml"):
        destination = root / "rt_library" / source.relative_to(seed)
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    template = root / "setup" / ".env.example"
    if not (root / ".env").exists() and template.is_file():
        shutil.copy2(template, root / ".env")
        print("Created .env. Add your provider keys before using API targets.")


def restart_existing_server(root):
    """Stop only verified server processes from this checkout before launch."""
    if os.name != "nt":
        raise OSError("Automatic restart is only supported on Windows")
    for pid in sorted(windows_project_server_pids(root)):
        print(f"Stopping previous Libertarium server (PID {pid})...", flush=True)
        try:
            signal_process_group(pid, force=True)
            deadline = time.monotonic() + 10
            while process_group_exists(pid):
                if time.monotonic() >= deadline:
                    raise OSError(f"Previous server PID {pid} did not stop; new server was not started")
                time.sleep(0.1)
        finally:
            restore_system_cursors()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="start the server after setup")
    parser.add_argument("--restart", action="store_true", help="stop this project's previous server before --run")
    args = parser.parse_args(argv)
    if args.restart and not args.run:
        parser.error("--restart requires --run")
    if sys.version_info < (3, 11):
        parser.exit(1, "Python 3.11 or newer is required.\n")
    root = Path(__file__).resolve().parent
    if importlib.util.find_spec("yaml") is None:
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "pyyaml"], check=True)
        except (OSError, subprocess.CalledProcessError):
            parser.exit(1, "Install PyYAML in this Python environment, then run setup again.\n")
    initialize_files(root)
    install_optional_features()
    port = (os.environ.get("PLINY_PORT") or "8888").strip() or "8888"
    print(f"Windows setup complete. Dashboard: http://localhost:{port}/", flush=True)
    if args.run:
        os.chdir(root)
        os.environ["PYTHONUTF8"] = "1"
        if args.restart:
            try:
                restart_existing_server(root)
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                parser.exit(1, f"Could not restart KAIKEN: {error}\n")
        sys.argv = [str(root / "server.py")]
        runpy.run_path(str(root / "server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
