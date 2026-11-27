"""A high-resource replication of the short live probe, in a separate directory."""
from importlib.util import module_from_spec,spec_from_file_location
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TOP=ROOT.parent
spec=spec_from_file_location("short_measure",TOP/"resource-live-mini/src/run.py")
probe=module_from_spec(spec)
spec.loader.exec_module(probe)
probe.ROOT=ROOT
probe.PROTOCOL=TOP/"protocols/29-resource-live-hi.md"
probe.ARMS={"A":{},"R":{"reproducible-resource-limit":"256000"}}
old=TOP/"resource-live-mini/inputs/manifest.json"


def freeze():
    path=ROOT/"inputs/manifest.json"
    if not path.is_file():
        full=probe.base.read(old)
        jobs=[{**j,"order":[a for a in j["order"] if a in probe.ARMS]} for j in full["jobs"]]
        probe.base.write(path,dict(at=probe.base.now(),protocol=probe.base.sha(probe.PROTOCOL),
                         source=probe.base.sha(old),projects=full["projects"],jobs=jobs,options=probe.ARMS))
    p=probe.base.read(path)
    assert p["source"]==probe.base.sha(old) and p["protocol"]==probe.base.sha(probe.PROTOCOL)
    return p


if __name__=="__main__":
    probe.freeze=freeze
    probe.main()
