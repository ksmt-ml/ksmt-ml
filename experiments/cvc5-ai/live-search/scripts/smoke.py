from concurrent.futures import ThreadPoolExecutor
from base import *

def config(mask):
    return {k:v for i,(k,v) in enumerate(SWITCH.items()) if mask & (1<<i)}

def main():
    setup_limits()
    parent=ROOT/"raw/smoke"
    parent.mkdir()
    start=now()
    expected=read(EXPECTED)
    assert sha(archive.CVC5_JNI)==expected["cvc5_jni_sha256"]
    assert sha(archive.CVC5_JAR)==expected["cvc5_jar_sha256"]
    assert sha(archive.NATIVE_Z3_JAR)==expected["z3_native_jar_sha256"]
    seen=set()
    for cpu in CPUS:
        topology=Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        ident=tuple((topology/f).read_text().strip() for f in ("physical_package_id","core_id"))
        assert ident not in seen and cpu in os.sched_getaffinity(0)
        seen.add(ident)
    def lane(cpu,ids):
        results={}
        for identifier in ids:
            arm="z3" if identifier=="z3" else "default"
            opts={} if identifier=="z3" else config(identifier)
            reasons=[]
            for mode,text in (("contract","CONTRACT_PASS"),("smoke","LIVE_SMOKE_PASS")):
                where=parent/str(identifier)/mode
                try:
                    result=case(where,cpu,arm,opts,0,mode=mode,cap=52)
                    assert result["returncode"]==0 and not result["watchdog"], result
                    assert text in (where/"stdout.log").read_text()
                    validate_backend(where,read(where/"backend.json"),read(where/"invocation.json"),expected)
                except Exception as exc:
                    reasons.append(f"{mode}: {exc!r}")
            results[str(identifier)]=dict(ok=not reasons,options=opts,reasons=reasons)
            print("smoke",identifier,not reasons,reasons,flush=True)
        return results
    ids=[0,"z3",31,*range(1,31)]
    with ThreadPoolExecutor(len(CPUS)) as pool:
        pending=[pool.submit(lane,cpu,ids[i::len(CPUS)]) for i,cpu in enumerate(CPUS)]
        completed={}
        for t in pending:
            completed.update(t.result())
    write(ROOT/"inputs/space.json",dict(start_msk=start,end_msk=now(),combinations=completed))
    if not completed["0"]["ok"] or not completed["z3"]["ok"]:
        raise RuntimeError("default или Z3 не прошёл smoke")

if __name__=="__main__":
    main()
