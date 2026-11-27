"""Request a training-only trial from the experiment broker."""
import json
import os
from pathlib import Path
import secrets
import sys
import time


def request(spec):
    ident = secrets.token_hex(10)
    work = Path("/work")
    file = work / "requests" / (ident + ".json")
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(spec))
    tmp.rename(file)
    response = work / "responses" / file.name
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        if response.is_file():
            try:
                return json.loads(response.read_text())
            except json.JSONDecodeError:
                pass
        time.sleep(0.10)
    raise TimeoutError("training broker did not respond within 150 seconds")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "smac":
        spec = {"smac": int(sys.argv[2])}
    elif len(sys.argv) == 3 and sys.argv[1] == "eval":
        spec = {"config": json.loads(sys.argv[2])}
    else:
        raise SystemExit("usage: client.py eval CONFIG_JSON | client.py smac 1..4")
    print(json.dumps(request(spec), sort_keys=True))
