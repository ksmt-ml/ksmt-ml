"""Submit a whitelist-only training summary through the isolated ChatGPT client."""
from pathlib import Path
import json
import math
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/"recovery/scripts"))
import base

LIMIT=65
METRICS={"checks","par2_ms","check_ms","analyze_ms","jvm_ms","steps","paths","coverage",
         "failed","step_errors","unknown","solver_timeouts","same_count","n","cpu_ms",
         "complete_outcomes","known_outcomes","retrieved_outcomes","store_all"}
PROMPT="""You propose the next cvc5 settings for a measured training set. The entire
training record is JSON below. Use it directly. Do not call tools. An empty list
means baseline and is already measured. Return exactly one untried allowed list
of option keys with a brief reason. Evaluate time only on equivalent symbolic
work, prioritize unchanged coverage and reliability, and minimize the documented
objective. Project labels P1 etc. and measured statistics are anonymous.
"""

def safe_snapshot(value):
    def word(v):
        return isinstance(v,str) and 0<len(v)<=70 and re.fullmatch(r"[a-z0-9-]+",v) is not None

    def numeric(v):
        return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)

    def measures(v,allow_projects=True):
        assert isinstance(v,dict)
        for k,x in v.items():
            if k=="by_project" and allow_projects:
                assert isinstance(x,dict) and all(re.fullmatch(r"P[1-9][0-9]*",p) for p in x)
                for row in x.values():
                    measures(row,allow_projects=False)
            else:
                assert k in METRICS and (x is None or numeric(x))

    if set(value)=={"nonce"}:
        assert re.fullmatch(r"SYNTHETIC_[A-Z0-9_]+",value["nonce"])
    else:
        assert set(value)=={"allowed","option_values","baseline","trials","objective"}
        expected=value["option_values"]
        assert isinstance(expected,dict) and all(word(k) and word(v) for k,v in expected.items())
        assert isinstance(value["allowed"],list)
        for opts in value["allowed"]:
            assert isinstance(opts,list) and set(opts)<=expected.keys() and len(opts)==len(set(opts))
        assert isinstance(value["objective"],str) and 1<=len(value["objective"])<=220
        assert "/" not in value["objective"] and "\n" not in value["objective"]
        measures(value["baseline"])
        for trial in value["trials"]:
            assert set(trial)=={"options","cost","admissible","train"}
            assert trial["options"] in value["allowed"] and numeric(trial["cost"]) and isinstance(trial["admissible"],bool)
            measures(trial["train"])
    encoded=json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False,separators=(",",":")).encode()
    assert len(encoded)<=40000
    return encoded

def invoke(where,payload,budget=LIMIT,smoke=False,instruction=""):
    where.mkdir(parents=True)
    staged=where/"staged-train"
    staged.mkdir()
    data=safe_snapshot(payload)
    (staged/"training.json").write_bytes(data+b"\n")
    prefix=(("The synthetic training JSON contains a nonce. Reply with options=[] and reason equal to that nonce. "
             "Do not use tools.\n") if smoke else PROMPT)
    prompt=prefix+instruction+data.decode()+"\n"
    (where/"prompt.txt").write_text(prompt)
    cmd=["taskset","-c","12",str(ROOT/"src/run.sh"),str(where),str(staged),str(ROOT/"protocol")]
    base.write(where/"invocation.json",dict(cmd=cmd,data_sha=base.sha(staged/"training.json"),
               prompt_sha=base.sha(where/"prompt.txt"),schema_sha=base.sha(ROOT/"protocol/schema.json"),
               sandbox_sha=base.sha(ROOT/"src/run.sh"),model="gpt-6.2-sol"))
    st=base.run_process(where,cmd,base.public_env(where),min(budget,LIMIT),stdin=prompt.encode(),rss_limit=0)
    events=[]
    for line in (where/"stdout.log").read_text(errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    tools=[x for x in events if x.get("item",{}).get("type") in ("command_execution","mcp_tool_call","web_search")]
    answer=None
    final=where/"work/answer.json"
    if st["returncode"]==0 and not st["watchdog"] and final.is_file() and any(
            x.get("type")=="turn.completed" for x in events) and not tools:
        possible=base.read(final)
        if set(possible)=={"options","reason"} and isinstance(possible["options"],list) and isinstance(possible["reason"],str):
            answer=possible
    base.write(where/"result.json",dict(status=st,answer=answer,tool_events=len(tools),
                                       input_sha=base.sha(staged/"training.json")))
    return answer

if __name__=="__main__":
    where=ROOT/"evidence/synthetic-smoke"
    if (where/"result.json").exists():
        print(base.read(where/"result.json"))
    else:
        base.setup_limits()
        answer=invoke(where,{"nonce":"SYNTHETIC_TRAIN_2311"},smoke=True)
        assert answer=={"options":[],"reason":"SYNTHETIC_TRAIN_2311"},answer
        print("isolated subscription inference ready",base.now(),flush=True)
