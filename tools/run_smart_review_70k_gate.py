"""Run the Smart Review 70k gate with a bounded, owned child process."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from queue import Empty, Queue

_TIMEOUT_SECONDS = 60


def _kill_owned_process_tree(pid: int) -> None:
    """Terminate only the pytest process tree created by this runner."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(pid, 15)
    except ProcessLookupError:
        pass


def main() -> int:
    """Execute the true-data performance test and enforce the hard gate."""
    repo = Path(__file__).resolve().parents[1]
    started = time.perf_counter()
    env = os.environ.copy()
    env["SMART_REVIEW_70K_GATE"] = "1"
    data_directory = tempfile.TemporaryDirectory(prefix="smart-review-70k-gate-")
    env["SMART_REVIEW_70K_ROOT"] = data_directory.name
    command = [
        sys.executable,
        "tools/run_smart_review_70k_case.py",
    ]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=repo,
        env=env,
        bufsize=1,
        creationflags=creationflags,
        stderr=subprocess.STDOUT,
        start_new_session=os.name != "nt",
        stdout=subprocess.PIPE,
        text=True,
    )
    print("70k gate started", flush=True)
    output_queue: Queue[str | None] = Queue()

    def forward_output() -> None:
        """Forward child output while letting the parent enforce a timeout."""
        assert process.stdout is not None
        for line in process.stdout:
            output_queue.put(line)
        output_queue.put(None)

    threading.Thread(target=forward_output, daemon=True).start()
    deadline = time.monotonic() + _TIMEOUT_SECONDS

    def cleanup() -> None:
        """Stop only the owned child and wait before removing its fixture tree."""
        if process.poll() is None:
            _kill_owned_process_tree(process.pid)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_owned_process_tree(process.pid)
        data_directory.cleanup()

    def report(status: str, code: int) -> int:
        """Print a machine-readable status with full-command wall time."""
        elapsed = time.perf_counter() - started
        print(f"70k gate wall_time_s={elapsed:.3f}", flush=True)
        print(status, flush=True)
        return code

    try:
        next_heartbeat = time.monotonic() + 1
        completed_code: int | None = None
        output_closed = False
        while True:
            try:
                line = output_queue.get(timeout=0.2)
            except Empty:
                line = ""
            if line is None:
                output_closed = True
            elif line:
                print(line, end="", flush=True)
                if line.startswith("70k case complete exit="):
                    completed_code = int(line.rsplit("=", 1)[1].strip())
            if completed_code is not None:
                cleanup()
                if completed_code == 0:
                    return report("STATUS: PASS", 0)
                return report("STATUS: STOP_NO_COMMIT", completed_code)
            returncode = process.poll()
            if returncode is not None and output_closed:
                cleanup()
                return report("STATUS: STOP_NO_COMMIT", returncode)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, _TIMEOUT_SECONDS)
            if time.monotonic() >= next_heartbeat:
                print("70k gate still running", flush=True)
                next_heartbeat = time.monotonic() + 1
            time.sleep(min(0.2, remaining))
    except subprocess.TimeoutExpired:
        cleanup()
        print(f"70k gate wall_time_s={time.perf_counter() - started:.3f}")
        print("STATUS: STOP_NO_COMMIT")
        print(f"70k gate exceeded {_TIMEOUT_SECONDS} seconds; commit is forbidden.")
        return 124
    except KeyboardInterrupt:
        cleanup()
        print(f"70k gate wall_time_s={time.perf_counter() - started:.3f}")
        print("STATUS: STOP_NO_COMMIT")
        print("70k gate interrupted; commit is forbidden.")
        return 130
    finally:
        if process.poll() is None:
            cleanup()
        else:
            data_directory.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
