"""Interactive Sol controller. Import the frozen evaluator and lock procedure."""
import argparse
from collections import Counter
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT.parent / "campaign"
sys.path.insert(0, str(CAMPAIGN / "v1"))

from evaluator import atomic_json, json_line
from tune import TuningRun, REQUEST_NAME, key


class Interactive(TuningRun):
    def __init__(self, args):
        super().__init__(args)
        self.details = {}
        self.thread_id = None
        self.last_message = ""
        self.seen_requests = set()
        self.last_pulse = 0.0

    def clock(self):
        return {
            "elapsed_wall_s": round(self.elapsed(), 2),
            "search_remaining_s": round(max(0, self.search_end - time.monotonic()), 2),
            "total_remaining_s": round(max(0, self.end - time.monotonic()), 2),
        }

    def measured(self, params, origin):
        path = self.output / "train-sessions.jsonl"
        offset = path.stat().st_size if path.exists() else 0
        trial = self.trial(params, origin)
        if trial is None:
            return {"error": "search deadline reached"}
        candidate = trial["candidate_id"]
        if candidate not in self.details:
            with path.open() as stream:
                stream.seek(offset)
                self.details[candidate] = [
                    {k: row[k] for k in ("group", "status", "wall_s", "par2", "queries",
                                         "correct_answers", "rc", "stderr", "stdout") if k in row}
                    for row in map(json.loads, stream)
                ]
        return {"trial": trial, "sessions": self.details[candidate]}

    def prompt(self):
        clock = self.clock()
        pace = {
            "elapsed": clock["elapsed_wall_s"],
            "left": clock["search_remaining_s"],
            "overall": clock["total_remaining_s"],
        }
        full = self.args.budget == 3600
        budget = "real 60-minute (3,600-second)" if full else f"real {self.args.budget:g}-second practice"
        context = (
            f"You own a {budget} wall-clock budget for this run, including the automatic final "
            f"validation reserve of {self.args.validation_seconds:g} seconds. "
            "Model inference, tools and real cvc5 runs all consume this budget. "
            f"Actual elapsed wall time: {pace['elapsed']:.2f} s. "
            f"Actual remaining search time: {pace['left']:.2f} s. "
            f"Actual remaining total time including reserve: {pace['overall']:.2f} s. "
            "Use the broker clock for live time within a turn.\n"
        )
        rank = [{"id": r["candidate_id"], "config": r["config"], "par2": r["par2"]}
                for r in self.ranked()[:5]]
        if not self.calls or not self.thread_id:
            context += (
                f"You are the blind candidate-search model for cvc5 1.4.1 incremental sessions. "
                f"This independent run has search seed {self.args.seed}. "
                "Read /protocol/README.md and /protocol/options.json. Use the actual training "
                "evaluator and allowed options. You may inspect /train, use public cvc5 docs, "
                "and write and run scripts or batches from /work. Do not send the training "
                "material to public websites, spawn agents, read credentials or inspect outside "
                "/work, /train and /protocol except the isolation check below. "
                "Only training data and outcomes are available. The harness validates the top "
                "three eligible measured candidates and includes the measured default.\n"
            )
        if not self.calls:
            context += (
                "Choose your own time allocation and briefly record your initial plan in "
                "/work/journal.md before your first candidate measurements. Check the sandbox "
                "in an actual shell tool using the following command, which tests only access "
                "metadata and never opens private files:\n"
                "python3 -c 'import importlib.util,os; "
                "assert os.access(\"/train/manifest.json\",os.R_OK); "
                "assert not any(os.access(p,os.R_OK) for p in "
                "(\"/runner/state/engineer-fixture\",\"/runner/state/auth.json\","
                "\"/validation/manifest.json\",\"/test/manifest.json\","
                "\"/prior/locked.json\")); "
                "assert not any(os.access(\"/proc/\"+pid+\"/root/runner/state/engineer-fixture\",os.R_OK) "
                "for pid in os.listdir(\"/proc\") if pid.isdecimal()); "
                "assert importlib.util.find_spec(\"smac\") is None; print(\"TRAIN_ONLY_OK\")'\n"
            )
        else:
            context += (
                f"Live continuation {len(self.calls) + 1}. "
                f"The preceding turn completed {sum(c['completed_trials'] for c in self.calls[-1:])} "
                "new candidate measurements. Revisit the journal and actual training outcomes. "
                f"Prior turn's last message excerpt: {self.last_message[-2000:]}\n"
            )
        context += (
            f"Measured training leaderboard: {json.dumps(rank, sort_keys=True)}. "
            f"Completed unique measurements: {len(self.results)}.\n"
            "Iteratively hypothesize, measure, analyze per-session timings and outcomes, and "
            "revise your choices and time allocation in light of those results. Use the entire "
            "available search time across turns. At the end of a coherent measurement and "
            "analysis cycle of your choosing, append hypotheses, candidate IDs, comparisons, "
            "decisions and your next experiment to /work/journal.md, then finish the CLI turn "
            "briefly so the harness can immediately resume you with fresh elapsed and remaining "
            "time. Turn boundaries are for feedback and replanning and do not end your search. "
            "Keep making decisions as new measurements arrive. The harness stops at the search deadline."
        )
        return context, clock

    def broker(self, req_fd, res_fd):
        for name in sorted(os.listdir(req_fd)):
            if time.monotonic() >= self.search_end:
                return
            if not REQUEST_NAME.fullmatch(name) or name in self.seen_requests:
                continue
            self.seen_requests.add(name)
            began = self.elapsed()
            spec = None
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=req_fd)
                with os.fdopen(fd, "rb") as incoming:
                    if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                        raise ValueError("request must be a regular file")
                    payload = incoming.read(4097)
                if len(payload) > 4096:
                    raise ValueError("request exceeds 4096 bytes")
                spec = json.loads(payload)
                if not isinstance(spec, dict):
                    raise ValueError("request must be an object")
                if set(spec) == {"config"}:
                    response = self.measured(spec["config"], "sol-interactive")
                elif spec == {"clock": True}:
                    response = {}
                elif set(spec) == {"history"} and type(spec["history"]) is int and spec["history"] >= 0:
                    candidates = [r for r in self.results if r["candidate_id"] >= spec["history"]]
                    response = {"history": candidates[:100],
                                "next_id": candidates[100]["candidate_id"] if len(candidates) > 100 else None}
                else:
                    raise ValueError("expected config, clock=true or history=first_candidate_id")
            except (ValueError, OSError, TypeError) as exc:
                response = {"error": str(exc)}
            response["clock"] = self.clock()
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=res_fd)
                with os.fdopen(fd, "w") as outgoing:
                    json.dump(response, outgoing)
            except OSError as exc:
                response["delivery_error"] = str(exc)
            json_line(self.output / "broker.jsonl",
                      {"request_id": name, "turn": len(self.calls), "started_s": began,
                       "finished_s": self.elapsed(), "request": spec, "response": response})

    def capture(self, pipe, raw, timeline, events):
        with pipe, raw.open("wb") as out, timeline.open("w") as timed:
            for line in pipe:
                at = self.elapsed()
                out.write(line)
                out.flush()
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                events.append((at, event))
                timed.write(json.dumps({"elapsed_s": at, "event": event}) + "\n")
                timed.flush()

    def turn(self, req_fd, res_fd):
        i = len(self.calls)
        stem = self.call_root / f"turn-{i:03}"
        prompt, clock = self.prompt()
        stem.with_suffix(".prompt.txt").write_text(prompt + "\n")
        command = [str(ROOT / "engine/model-sandbox.sh"), str(self.call_root),
                   str(self.args.train.parent), str(self.args.protocol)]
        if self.thread_id:
            command += ["resume", self.thread_id]
        command.append("-")
        call = {"turn": i, "start_s": self.elapsed(), "subcommand": command[4:],
                "kind": "auto", "clock_at_prompt": clock, "resume_thread_id": self.thread_id}
        json_line(self.output / "model-events.jsonl", {**call, "event": "start"})
        start_count = len(self.results)
        events = []
        with stem.with_suffix(".stderr").open("wb") as err:
            p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                 start_new_session=True)
            recorder = threading.Thread(target=self.capture, daemon=True, args=(
                p.stdout, stem.with_suffix(".jsonl"), stem.with_suffix(".timeline.jsonl"), events))
            recorder.start()
            try:
                with p.stdin:
                    p.stdin.write(prompt.encode())
                while p.poll() is None and time.monotonic() < self.search_end:
                    self.broker(req_fd, res_fd)
                    if self.elapsed() - self.last_pulse >= 2:
                        self.tick()
                        self.last_pulse = self.elapsed()
                    time.sleep(min(0.08, max(0, self.search_end - time.monotonic())))
            except BrokenPipeError:
                pass
            finally:
                killed = p.poll() is None
                if killed:
                    try:
                        os.killpg(p.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                p.wait()
                recorder.join(timeout=3)

        usage = {}
        thread_id = None
        item_counts = Counter()
        completed = False
        for _, event in events:
            event_type = event.get("type")
            if event_type == "thread.started":
                thread_id = event["thread_id"]
            elif event_type == "turn.completed":
                completed = True
                usage = event.get("usage", {})
            elif event_type == "item.completed":
                item = event["item"]
                item_counts[item["type"]] += 1
                if item["type"] == "agent_message":
                    self.last_message = item["text"]
        self.thread_id = thread_id if p.returncode == 0 and completed else None
        for name, value in usage.items():
            if type(value) is int:
                self.usage[name] = self.usage.get(name, 0) + value
        call.update(event="end", elapsed_s=self.elapsed() - call["start_s"],
                    returncode=p.returncode, deadline_killed=killed, usage=usage,
                    thread_id=thread_id, completed=completed, item_counts=dict(item_counts),
                    completed_trials=len(self.results) - start_count)
        self.calls.append(call)
        json_line(self.output / "model-events.jsonl", call)
        return completed

    def run_auto(self):
        self.prepare_broker()
        (self.call_root / "state/engineer-fixture").write_text("private boundary probe\n")
        self.measured({}, "default")
        with ExitStack() as stack:
            fds = []
            for name in ("requests", "responses"):
                fd = os.open(self.work / name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                stack.callback(os.close, fd)
                fds.append(fd)
            failures = 0
            while self.search_end - time.monotonic() >= 1:
                if self.turn(*fds):
                    failures = 0
                else:
                    failures += 1
                    time.sleep(min(2 ** min(failures, 4), max(0, self.search_end - time.monotonic())))
        if not any(r["eligible"] and r["config"] for r in self.results):
            self.error = "No eligible non-default interactive candidate completed."
        if len(self.calls) < 2:
            self.error = self.error or "No continuation turn recorded."
        if not any(c["resume_thread_id"] and c["completed_trials"] for c in self.calls):
            self.error = self.error or "No resumed turn evaluated a new candidate."
        if self.search_end - time.monotonic() > 2:
            raise RuntimeError("interactive loop ended before the search deadline")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--core", type=int, required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--begin", type=float, required=True)
    args = parser.parse_args()
    args.arm = "sol-auto"  # Reuse TuningRun dispatch, evaluator, accounting and validation.
    args.train = CAMPAIGN / "dataset-v1/train/manifest.json"
    args.validation = CAMPAIGN / "dataset-v1/validation/manifest.json"
    args.protocol = ROOT / "model-protocol"
    args.budget = 300 if args.smoke else 3600
    args.validation_seconds = 65 if args.smoke else 180
    args.validation_repeats = 1 if args.smoke else 2
    args.timeout = 1.5
    args.call_timeout = args.budget - args.validation_seconds
    run = Interactive(args)
    for name in ("run.json",):
        artifact = run.output / name
        record = json.loads(artifact.read_text())
        record["arm"] = "sol-interactive"
        atomic_json(artifact, record)
    run.run()
    artifact = run.output / "locked.json"
    record = json.loads(artifact.read_text())
    record["arm"] = "sol-interactive"
    atomic_json(artifact, record)
    artifact.chmod(0o444)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
