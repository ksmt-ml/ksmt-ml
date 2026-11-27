"""Lightweight resource log for the overnight limit and host-noise diagnostics."""
from pathlib import Path
import os
import shutil
import subprocess
import time

from replay import CORES, ROOT, STOP, append, log, now
from report import write_json

def optional(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None

def snapshot():
    cpu_times={}
    cpu_names={f"cpu{n}" for n in CORES}
    for line in Path("/proc/stat").read_text().splitlines():
        fields=line.split()
        if fields[0] in cpu_names:
            cpu_times[fields[0]]=[int(x) for x in fields[1:]]
    return {"at":now(),"unix":time.time(),"loadavg":list(os.getloadavg()),"cpus":cpu_times,
            "pressure_cpu":optional("/proc/pressure/cpu"),
            "frequency_khz":{cpu:optional(f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq") for cpu in CORES},
            "disk_bytes":int(subprocess.check_output(["du","-sb",ROOT],text=True).split()[0]),
            "file_system_available_bytes":shutil.disk_usage(ROOT).free}

def main():
    os.nice(10)
    topo={}
    for cpu in CORES:
        paths=Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        topo[str(cpu)]={name:optional(paths/name) for name in ("physical_package_id","core_id","thread_siblings_list")}
    assert len({(d["physical_package_id"],d["thread_siblings_list"]) for d in topo.values()})==len(CORES)
    write_json(ROOT/"protocols/resource-limits.json",{"created":now(),"workers":list(CORES),"physical":topo,
               "max_workers":6,"disk_guard_bytes":13_500_000_000,"max_new_disk_bytes":15_000_000_000,
               "measurements_until":"2026-11-22T10:48:00+03:00","final_until":"2026-11-22T10:55:00+03:00"})
    out=ROOT/"resource-samples.jsonl"
    while time.time()<STOP:
        append(out,snapshot())
        time.sleep(min(45,max(0,STOP-time.time())))
    log("Resource monitor stopped at measurement deadline")
if __name__=="__main__":
    main()
