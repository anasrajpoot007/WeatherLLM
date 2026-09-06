"""
Pipeline runner
---------------
Runs every stage in order from the project root, whatever directory the
caller was in. This is what the scheduler invokes.

    python src/pipeline.py
    python src/pipeline.py --refresh-reference   # re-resolve grids/stations
    python src/pipeline.py --skip-ingest         # rebuild from existing captures

Output streams live to the console AND is appended to
logs/pipeline_<YYYYmmdd>.log, so a long stage never looks frozen and a
scheduled run still leaves a trace. Exits non-zero on the first failure.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.chdir(ROOT)

LOG_DIR = os.path.join(ROOT, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

LOG_PATH = os.path.join(LOG_DIR, f"pipeline_{datetime.now():%Y%m%d}.log")

parser = argparse.ArgumentParser(description="Run the full weather pipeline.")
parser.add_argument("--skip-ingest", action="store_true",
                    help="Reuse existing raw captures instead of calling the API.")
parser.add_argument("--refresh-reference", action="store_true",
                    help="Re-resolve city -> office/grid/station during ingest.")
arguments = parser.parse_args()

ingest_args = ["--refresh-reference"] if arguments.refresh_reference else []

STEPS = [
    ("Create database",   ["src/create_db.py"]),
    ("Ingest live data",  ["src/ingest.py"] + ingest_args),
    ("Load into SQLite",  ["src/load_data.py"]),
    ("Validate",          ["src/validate.py"]),
    ("Chunk text",        ["src/prepare_text.py"]),
    ("Embed chunks",      ["src/embed.py"]),
    ("Build index",       ["src/vector_search.py"]),
]

if arguments.skip_ingest:
    STEPS = [step for step in STEPS if step[0] != "Ingest live data"]


log_file = open(LOG_PATH, "a", encoding="utf-8")


def log(message):
    stamped = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}"
    print(stamped, flush=True)
    log_file.write(stamped + "\n")
    log_file.flush()


log("=" * 62)
log("PIPELINE RUN STARTED")
log("=" * 62)

started = datetime.now()

for title, command in STEPS:

    log(f"--- {title}  ({' '.join(command)})")

    step_started = datetime.now()

    # Stream line by line: the caller sees progress immediately AND the
    # log captures it. capture_output would buffer everything until the
    # stage ends, which makes a 5-minute fetch look like a hang.
    process = subprocess.Popen(
        [sys.executable, "-u"] + command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    for line in process.stdout:
        line = line.rstrip("\n")
        print(line, flush=True)
        log_file.write(line + "\n")

    log_file.flush()
    process.wait()

    elapsed = (datetime.now() - step_started).total_seconds()

    if process.returncode != 0:
        log(f"FAILED: {title} (exit {process.returncode}) after {elapsed:.1f}s")
        log("PIPELINE RUN ABORTED")
        log_file.close()
        sys.exit(process.returncode)

    log(f"OK: {title}  ({elapsed:.1f}s)")

total = (datetime.now() - started).total_seconds()

log("=" * 62)
log(f"PIPELINE RUN COMPLETED in {total:.1f}s")
log(f"Log: {os.path.relpath(LOG_PATH, ROOT)}")
log("=" * 62)

log_file.close()
