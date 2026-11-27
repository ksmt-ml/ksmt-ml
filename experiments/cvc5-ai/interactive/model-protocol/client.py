"""Request training measurements, history or the search clock."""
import json
from pathlib import Path
import secrets
import sys
import time


def request(spec, timeout=30):
    filename = secrets.token_hex(10) + ".json"
    work = Path("/work")
    path = work / "requests" / filename
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(spec))
    temp.rename(path)
    response = work / "responses" / filename
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(response.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.1)
    raise TimeoutError("No broker response within the client timeout")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "eval":
        spec = {"config": json.loads(sys.argv[2])}
    elif sys.argv[1:] == ["clock"]:
        spec = {"clock": True}
    elif len(sys.argv) == 3 and sys.argv[1] == "history":
        spec = {"history": int(sys.argv[2])}
    else:
        raise SystemExit("usage: client.py eval CONFIG_JSON | client.py clock | client.py history FIRST_ID")
    print(json.dumps(request(spec), sort_keys=True))
