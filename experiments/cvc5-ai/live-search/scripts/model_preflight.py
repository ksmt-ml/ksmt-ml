from base import *

setup_limits()
p=ROOT/"raw/sol-preflight"
cmd=["taskset","-c","12",str(ROOT/"scripts/isolated_sol.sh"),str(p),
     str(ROOT/"model/preflight-train"),str(ROOT/"model/protocol"),
     "--output-schema","/protocol/schema.json","--output-last-message","/work/answer.json","-"]
write(p/"invocation.json",cmd)
print(run_process(p,cmd,os.environ,85,stdin=(p/"prompt.txt").read_bytes(),rss_limit=0),flush=True)
