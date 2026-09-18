"""One fresh process per candidate run isolates RUSAGE_CHILDREN peak RSS."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

try:
    import resource
except ImportError:
    resource = None


def main():
    script, wav, rttm, report, timeout_s = sys.argv[1:]
    timeout = float(timeout_s)
    started = time.perf_counter()
    try:
        with subprocess.Popen([script, wav, rttm], start_new_session=os.name == "posix") as child:
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill the candidate's process group, including model/shell children.
                if os.name == "posix":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    child.kill()
                child.wait()
                print(f"candidate timed out after {timeout:g} s", file=sys.stderr)
                code = 2
    except OSError as error:
        print(error, file=sys.stderr)
        code = 2
    elapsed = time.perf_counter() - started
    peak = None
    if resource is not None:
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        peak /= 1024 * 1024 if sys.platform == "darwin" else 1024
    Path(report).write_text(json.dumps({"returncode": code, "cand_s": elapsed, "peak_rss_mb": peak}))


if __name__ == "__main__":
    main()
