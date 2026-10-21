"""Run the web server and deadline worker as one supervised Railway service."""

import signal
import subprocess
import sys
import time


def supervise(commands):
    processes = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        for command in commands:
            processes.append(subprocess.Popen(command))
        while not stopping:
            if any(process.poll() is not None for process in processes):
                return 1
            time.sleep(0.2)
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    port = int(sys.argv[1])
    web = [
        sys.executable,
        "-m",
        "gunicorn",
        "config.wsgi:application",
        "--bind",
        f"0.0.0.0:{port}",
        "--workers",
        "2",
        "--threads",
        "2",
        "--timeout",
        "30",
        "--access-logfile",
        "-",
        "--access-logformat",
        "%(h)s %(m)s %(U)s %(s)s %(L)s",
        "--error-logfile",
        "-",
    ]
    worker = [sys.executable, "-u", "manage.py", "run_deadlines"]
    sys.exit(supervise([web, worker]))
