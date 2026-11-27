"""Sample host pressure and the current cgroup without reading process environments."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import time

FIELDS = ("cpu.stat", "cpu.pressure", "io.pressure", "memory.current",
          "memory.events", "memory.peak", "memory.pressure", "pids.current")


def sample(directory, every):
    cgroup = Path("/sys/fs/cgroup") / Path(
        Path("/proc/self/cgroup").read_text().splitlines()[0].split(":", 2)[2]).relative_to("/")
    while True:
        telemetry = {}
        for name in FIELDS:
            try:
                telemetry[name] = (cgroup / name).read_text().strip()
            except OSError as error:
                telemetry[name] = str(error)
        log = {
            "sampled": datetime.now().astimezone().isoformat(),
            "host_loadavg": Path("/proc/loadavg").read_text().strip(),
            "workspace_free_bytes": shutil.disk_usage(directory).free,
            "cgroup": str(cgroup), "cgroup_metrics": telemetry,
        }
        with (directory / "resource-samples.jsonl").open("a") as output:
            output.write(json.dumps(log, sort_keys=True) + "\n")
        if ((directory / "final/VERIFY.json").exists() or (directory / "failure.txt").exists()):
            break
        time.sleep(every)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--every", type=float, default=60)
    args = p.parse_args()
    sample(args.directory, args.every)
