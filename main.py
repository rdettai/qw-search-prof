import argparse
import datetime
import os
import requests
import shutil
import signal
import subprocess
import sys
import threading
import time
import concurrent.futures
from typing import Literal

from scenarii.scenario1 import (
    commit_timeout,
    create_indexes,
    ingest_documents,
    LogResults,
    search,
    display_statistics,
)


# from scenarii.scenario2 import (
#     commit_timeout,
#     create_indexes,
#     ingest_documents,
#     LogResults,
#     search,
#     display_statistics,
# )


DOCKER_IMAGE = "quickwit-oss/quickwit:hprof"


def start_quickwit(binary_path: str | None):
    proc_env = os.environ.copy()
    if not binary_path is None:
        args = [binary_path, "run", "--config", "quickwit.yaml"]
        proc_env["NO_COLOR"] = "true"
    else:
        args = [
            "docker",
            "run",
            "-p",
            "7280:7280",
            "-v",
            "./quickwit.yaml:/quickwit.yaml",
            "-e",
            "NO_COLOR=true",
            DOCKER_IMAGE,
            "run",
            "--config",
            "/quickwit.yaml",
        ]

    print(f"{args}")
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=".",
        env=proc_env,
    )
    return process


def wait_quickwit_ready(url: str, timeout_sec: int, interval_sec: int):
    print("Waiting for Quickwit to be ready...")
    start_time = time.time()
    last_error = "no error yet"
    while True:
        try:
            response = requests.get(f"{url}/health/readyz")
            if response.status_code == 200 and response.json():
                time.sleep(1)  # Wait a bit more to ensure Quickwit is fully ready.
                print("Quickwit is ready")
                return
            last_error = f"Error: {response.status_code}"
        except requests.RequestException as e:
            last_error = f"Error checking health: {e}"

        if time.time() - start_time > timeout_sec:
            print(last_error)
            raise TimeoutError(
                "Quickwit did not become ready within the timeout period."
            )

        time.sleep(interval_sec)


def monitor_stderr(process: subprocess.Popen, log_results: LogResults):
    """Monitor the stderr output for lines containing 'warn' and count them."""
    warn_count = 0
    current_time = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"logs/{current_time}.log"
    log_results.log_file = file_name
    with open(file_name, "w") as f:
        for line in iter(process.stdout.readline, ""):
            f.write(line)
            log_results.on_new_log_line(line)
    return warn_count


def start_mem_profiling(url, min_alloc_size, backtrace_every):
    requests.get(
        f"{url}/api/developer/heap-prof/start?min_alloc_size={min_alloc_size}&backtrace_every={backtrace_every}"
    ).raise_for_status()


def stop_mem_profiling(url):
    print("Stopping heap profiling...")
    response = requests.get(f"{url}/api/developer/heap-prof/stop")
    response.raise_for_status()


def main(
    binary_path: str | None,
    url: str,
    prof_flag: Literal["nobuild", "none", "search", "indexing", "all"],
):
    start = time.time()
    quickwit_process = start_quickwit(binary_path)

    def signal_handler(sig, frame):
        print("Received SIGINT, terminating Quickwit.")
        quickwit_process.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    log_results = LogResults(binary_path=binary_path)
    stderr_thread = threading.Thread(
        target=monitor_stderr, args=(quickwit_process, log_results)
    )
    stderr_thread.start()

    try:
        wait_quickwit_ready(url, 15, 1)
        print(f"Quickwit ready in {time.time() - start} seconds")

        create_indexes(url)

        if prof_flag in ["indexing", "all"]:
            start_mem_profiling(
                url, min_alloc_size=64 * 1024, backtrace_every=10 * 1024 * 1024
            )
        ingest_documents(url, log_results)
        print("Waiting for commit...")
        time.sleep(commit_timeout + 10)
        if prof_flag in ["indexing"]:
            stop_mem_profiling(url)

        if prof_flag in ["search"]:
            start_mem_profiling(
                url, min_alloc_size=64 * 1024, backtrace_every=10 * 1024 * 1024
            )
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            for i in range(4):
                executor.submit(search, url, i)

            executor.shutdown(wait=True)
        if prof_flag in ["search", "all"]:
            stop_mem_profiling(url)

        display_statistics()

    except Exception as e:
        print("An error occurred", e)

    print("terminating Quickwit process...")
    quickwit_process.terminate()
    try:
        stderr_thread.join()
    except:
        print("joining stderr thread failed, killing QW process")
        quickwit_process.kill()
        raise
    log_results.print()


def cleanup_datadir():
    datadir = "./qwdata"
    if os.path.exists(datadir):
        shutil.rmtree(datadir)
    os.mkdir(datadir)


def build_quickwit(quickwit_dir: str, enable_prof_build: bool):
    args = ["cargo", "build", "--release"]
    if enable_prof_build:
        args.append("--features")
        args.append("release-jemalloc-profiled")
    print(f"{args}")

    res = subprocess.run(
        args,
        cwd=f"{quickwit_dir}/quickwit",
    )
    if res.returncode != 0:
        print("Failed to build Quickwit")
        sys.exit(1)


def build_quickwit_docker(quickwit_dir: str, enable_prof_build: bool):
    args = ["docker", "build", ".", "-t", DOCKER_IMAGE]
    if enable_prof_build:
        args.append("--build-arg")
        args.append("CARGO_FEATURES=release-jemalloc-profiled")
        args.append("--build-arg")
        args.append("RUSTFLAGS=--cfg tokio_unstable --cfg tokio_taskdump")
    print(f"{args}")

    res = subprocess.run(
        args,
        cwd=quickwit_dir,
    )
    if res.returncode != 0:
        print("Failed to build Quickwit")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Quickwit search tests.")
    parser.add_argument(
        "--build",
        help="The way docker should be built",
        default="native",
        choices=["docker", "native"],
    )
    parser.add_argument(
        "--prof",
        help="The section that should be profiled (nobuild disables the feature flag at compile time)",
        default="search",
        choices=["nobuild", "none", "search", "indexing", "all"],
    )
    args = parser.parse_args()

    cleanup_datadir()
    os.makedirs("./logs", exist_ok=True)

    quickwit_dir = "/Users/remi.dettai/workspace/quickwit"
    if args.build == "docker":
        build_quickwit_docker(quickwit_dir, args.prof != "nobuild")
        binary_path = None
    elif args.build == "native":
        build_quickwit(quickwit_dir, args.prof != "nobuild")
        binary_path = f"{quickwit_dir}/quickwit/target/release/quickwit"
    else:
        print("--build must be one of [docker, native]")
        sys.exit(1)

    url = "http://localhost:7280"

    main(binary_path, url, args.prof)
