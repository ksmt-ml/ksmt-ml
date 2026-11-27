"""Check whether the installed native solver accepts the frozen CLI option vectors."""
import os
import subprocess
import sys

from arrays_options import SCOPE as ARRAY_SCOPE
from corpus import JDK
from live_client import config
from replay import ROOT, check_budget, digest, ev, guarded_popen, load, now
from report import write_json

BASE = ROOT / "runs/24-native-options"
SOURCE = ROOT / "NativeOptionsProbe.java"
CORE = 14


def settings():
    vectors = load(ARRAY_SCOPE)["configs"]
    return {label: {
        k: {"on": "true", "off": "false"}.get(v, v) if ev.SPACE[k] == ["auto", "on", "off"] else v
        for k, v in vectors[alias].items()
    } for label, alias in (("default", "default"), ("rewrite", "global_rewrite"), ("C05", "global"))}


def main():
    folder = BASE if len(sys.argv) == 1 else BASE / sys.argv[1]
    folder.mkdir(exist_ok=True)
    records = {}
    for arm, params in settings().items():
        check_budget()
        target = folder / arm
        target.mkdir()
        java = [str(JDK / "java"), "-XX:ActiveProcessorCount=1", "-XX:+UseSerialGC", "-XX:-UsePerfData",
                f"-Djava.io.tmpdir={target}", f"-Duser.home={target}", "--class-path", config()["CP"],
                str(SOURCE), "probe", *[f"{key}={value}" for key, value in params.items()]]
        with (target/"stdout.log").open("w") as stdout, (target/"stderr.log").open("w") as stderr:
            with guarded_popen(["taskset", "-c", str(CORE), *java], cwd=target, stdout=stdout, stderr=stderr,
                               stdin=subprocess.DEVNULL, env=dict(os.environ, TMPDIR=str(target))) as child:
                try:
                    rc = child.wait(15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    rc = child.wait()
        lines = [line.split("\t") for line in (target/"stdout.log").read_text().splitlines()]
        changes = {r[1]: {"before": r[2], "after": r[3]} for r in lines if len(r) == 4 and r[0] == "option"}
        records[arm] = {"returncode": rc, "version": next((r[1] for r in lines if r[0] == "version"), None),
                        "verified_option_values": changes,
                        "unsupported": [r[1] for r in lines if r[0] == "unsupported"],
                        "checked_model_core_incremental": any(r[0] == "smoke" for r in lines),
                        "exact_vector_passed": rc == 0 and all(
                            k in changes and changes[k]["after"] == value for k, value in params.items()) and
                            any(r[0] == "smoke" for r in lines)}
    write_json(folder / "VERIFY.json", {"at": now(), "cpu": CORE, "source": digest(SOURCE), "arms": records})


if __name__ == "__main__":
    main()
