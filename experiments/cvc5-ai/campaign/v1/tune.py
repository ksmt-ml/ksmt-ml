"""Wall-clock-limited random, SMAC, warm-start SMAC, and autonomous Sol."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import re
import resource
import subprocess
import time

from evaluator import (CHECKPOINTS, SPACE, TUNING_TIMEOUT, aggregate, atomic_json,
                       child_cpu, config, json_line, measure, verify)


ARMS = ("random", "smac", "sol-smac", "sol-auto")
SOURCE = Path(__file__).resolve().parent
REQUEST_NAME = re.compile(r"[0-9a-f]{20}\.json\Z")


def utc():
    return datetime.now(timezone.utc).isoformat()


def key(params):
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def args_for_cvc5_space():
    return {name: list(options) for name, options in SPACE.items()}


def never_evaluate(config, seed):
    raise RuntimeError("The immutable real cvc5 evaluator is called through ask/tell.")


class NativeSmac:
    def __init__(self, output, seed, initial=None):
        from ConfigSpace import Categorical, Configuration, ConfigurationSpace
        from smac import HyperparameterOptimizationFacade as Facade, Scenario

        self.config_class = Configuration
        self.space = ConfigurationSpace(seed=seed)
        self.space.add([Categorical(name, values, default="auto") for name, values in SPACE.items()])
        scenario = Scenario(self.space, output_directory=output, seed=seed,
                            n_trials=10**6, deterministic=True, n_workers=1, crash_cost=10.0)
        if initial is None:
            sobol = Facade.get_initial_design(scenario, n_configs=4)
            extra = sobol.select_configurations()
        else:
            extra = [self.to_native(p) for p in initial]
        design = Facade.get_initial_design(scenario, n_configs=0,
                                           additional_configs=[self.space.get_default_configuration(), *extra])
        intensifier = Facade.get_intensifier(scenario, max_config_calls=1, max_incumbents=1)
        self.facade = Facade(scenario, never_evaluate, initial_design=design, intensifier=intensifier,
                             logging_level=40, overwrite=False)

    def to_native(self, sparse):
        return self.config_class(self.space, values={name: sparse.get(name, "auto") for name in SPACE})

    def ask(self):
        trial = self.facade.ask()
        return trial, config(dict(trial.config))

    def tell(self, trial, result):
        from smac.runhistory.dataclasses import TrialValue
        from smac.runhistory.enumerations import StatusType

        failed = not result["eligible"]
        value = TrialValue(cost=10.0 if failed else result["par2"],
                           time=result["elapsed_measurement_s"],
                           cpu_time=result["solver_cpu_s"],
                           status=StatusType.CRASHED if failed else StatusType.SUCCESS)
        self.facade.tell(trial, value, save=True)


class TuningRun:
    def __init__(self, args):
        self.begin = args.begin if args.begin is not None else time.monotonic()
        self.wall_begin = utc()
        self.args = args
        self.output = args.output
        self.output.mkdir(exist_ok=False, parents=True)
        self.search_end = self.begin + args.budget - args.validation_seconds
        self.end = self.begin + args.budget
        self.rng = random.Random(args.seed)
        self.train = verify(args.train)
        # Identical order across arms at every candidate for a given search seed.
        self.rng.shuffle(self.train)
        self.results = []
        self.seen = {}
        self.calls = []
        self.checks = set()
        self.sm = None
        self.error = None
        self.launched_code = False
        self.partials = 0
        self.solver_cpu = 0.0
        self.usage = {}
        self.started_solver_cpu = child_cpu()
        os.sched_setaffinity(0, {args.core})
        atomic_json(self.output / "run.json", {
            "arm": args.arm, "search_seed": args.seed, "core": args.core, "workers": 1,
            "budget_s": args.budget, "search_s": args.budget - args.validation_seconds,
            "validation_reserve_s": args.validation_seconds, "session_timeout_s": args.timeout,
            "started": self.wall_begin, "train_groups_in_order": [r["group"] for r in self.train],
            "max_model_call_s": args.call_timeout, "validation_repeats": args.validation_repeats,
        })

    def elapsed(self):
        return time.monotonic() - self.begin

    def ranked(self, through=float("inf")):
        return sorted((r for r in self.results if r["eligible"] and r["finished_s"] <= through),
                      key=lambda r: (r["par2"], r["candidate_id"]))

    def tick(self):
        search_seconds = self.args.budget - self.args.validation_seconds
        for fraction in CHECKPOINTS:
            elapsed = fraction * search_seconds
            if fraction not in self.checks and self.elapsed() >= elapsed:
                self.checkpoint(fraction, elapsed)
        atomic_json(self.output / "progress.json", {
            "elapsed_s": self.elapsed(), "candidates": len(self.results),
            "partial_candidates": self.partials, "calls": len(self.calls),
            "incumbent": next(iter(self.ranked()), None), "updated": utc()})

    def checkpoint(self, fraction, elapsed):
        self.checks.add(fraction)
        ranked = self.ranked(elapsed)
        json_line(self.output / "checkpoints.jsonl", {"fraction_of_search": fraction, "at_s": elapsed,
                  "candidates": sum(r["finished_s"] <= elapsed for r in self.results),
                  "incumbent": ranked[0] if ranked else None})

    def trial(self, params, origin, call_deadline=None):
        params = config(params)
        digest = key(params)
        if digest in self.seen:
            return {**self.seen[digest], "cached_observation": True}
        deadline = self.search_end if call_deadline is None else min(call_deadline, self.search_end)
        if time.monotonic() >= deadline:
            return None
        candidate_id = len(self.results) + self.partials
        start = time.monotonic()
        rows = []
        for item in self.train:
            row = measure(self.args.train, item, params, self.args.timeout, deadline)
            rows.append(row)
            self.solver_cpu += row["solver_cpu_s"]
            json_line(self.output / "train-sessions.jsonl",
                      {"candidate_id": candidate_id, "origin": origin, **row})
            if row["status"] == "budget":
                break
            # A satisfiability mismatch or CLI error makes a configuration ineligible.
            if row["status"] in ("incorrect", "error"):
                break
        summary = aggregate(rows)
        summary.update({"candidate_id": candidate_id, "config": params, "origin": origin,
                        "finished_s": self.elapsed(), "elapsed_measurement_s": time.monotonic() - start})
        if len(rows) != len(self.train) and summary["eligible"]:
            raise RuntimeError("incomplete candidate incorrectly classified as eligible")
        json_line(self.output / "candidates.jsonl", summary)
        if summary["counts"]["budget"]:
            self.partials += 1
        else:
            self.results.append(summary)
            self.seen[digest] = summary
        self.tick()
        return summary

    def smac_trial(self, sm, origin, deadline=None):
        trial, params = sm.ask()
        result = self.trial(params, origin, deadline)
        if result is not None and result["counts"]["budget"] == 0:
            sm.tell(trial, result)
        return result

    def run_random(self):
        self.trial({}, "default")
        while time.monotonic() < self.search_end:
            params = {name: self.rng.choice(values) for name, values in SPACE.items()}
            self.trial(params, "random")

    def run_native(self, initial=None):
        sm = NativeSmac(self.output / "smac", self.args.seed, initial)
        while time.monotonic() < self.search_end:
            result = self.smac_trial(sm, "smac" if initial is None else "sol-smac")
            if result is None or result["counts"]["budget"]:
                break

    def model_prompt(self, kind):
        top = [{"config": r["config"], "par2": r["par2"]} for r in self.ranked()[:5]]
        seed = self.args.seed
        base = (
            "You are the blind candidate-search model for cvc5 1.4.1 incremental sessions. "
            "Read /protocol/options.json and /protocol/README.md, and optionally the training "
            "manifest and files in /train. Only training data are available. "
            f"This independent run has search seed {seed}. "
            "Use cvc5 options from that file. Omitted entries use the exact cvc5 default. "
            "Do not spawn other agents, read credentials, inspect anything outside /work, /train "
            "and /protocol, or send training material to public websites. "
        )
        if kind == "warm":
            return (base + "Propose exactly four distinct, nonempty candidate configurations for a "
                    "native SMAC warm start. Do not run trials. Read the training file styles first. "
                    'Return a JSON object with "configs" containing four strings. Each string is a JSON object of solver options.')
        budget_left = int(self.search_end - time.monotonic())
        return (base + f"Search time left is at most {budget_left} seconds. Current training leaderboard: {json.dumps(top)}. "
                "You control candidate generation. Use local Python code and training measurements. "
                "Run eval via python3 /protocol/client.py eval '{...}', or ask the callable native "
                "SMAC to evaluate 1 to 4 more candidates via python3 /protocol/client.py smac N. "
                "Derive, try and improve a strategy. Write your open-ended, seeded training "
                "search policy to /work/policy.py early, so the host can keep running your original code "
                "after your bounded model call, on the same broker and remaining budget. It should "
                "use repeated training feedback (or your deliberate fixed search), avoid unnecessary "
                "duplicates, run until stopped, and need only stdlib. The host will rank eligible measured "
                "configs by training PAR2. Live web search for public solver documentation is optional. "
                "No validation or test feedback is available.")

    def prepare_broker(self):
        self.call_root = self.output / "model"
        self.work = self.call_root / "work"
        for path in (self.work, self.call_root / "tmp", self.call_root / "state"):
            path.mkdir(parents=True, exist_ok=True)
        # Private state lives outside the directory given to commands.
        for name in ("requests", "responses"):
            (self.work / name).mkdir(exist_ok=True)

    def broker_poll(self, req_fd, res_fd, deadline, seen):
        for name in sorted(os.listdir(req_fd)):
            if not REQUEST_NAME.fullmatch(name) or name in seen:
                continue
            seen.add(name)
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=req_fd)
                with os.fdopen(fd, "rb") as request_file:
                    payload = request_file.read(4097)
                if len(payload) > 4096:
                    raise ValueError("request limit is 4096 bytes")
                request = json.loads(payload)
                if set(request) == {"config"}:
                    result = {"trial": self.trial(request["config"], "sol-direct", deadline)}
                elif (set(request) == {"smac"} and type(request["smac"]) is int
                      and 1 <= request["smac"] <= 4):
                    if self.sm is None:
                        self.sm = NativeSmac(self.output / "agent-smac", self.args.seed)
                    result = {"trials": []}
                    for _ in range(request["smac"]):
                        if time.monotonic() >= deadline:
                            break
                        row = self.smac_trial(self.sm, "sol-called-smac", deadline)
                        if row is None:
                            break
                        result["trials"].append(row)
                else:
                    raise ValueError("expected config or smac integer 1..4")
            except (ValueError, OSError, TypeError) as exc:
                result = {"error": str(exc)}
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=res_fd)
                with os.fdopen(fd, "w") as file:
                    json.dump(result, file)
            except FileExistsError:
                pass
            json_line(self.output / "broker.jsonl", {"request_id": name, "finished_s": self.elapsed(),
                      "response": result})
            if time.monotonic() >= deadline:
                return

    def call(self, kind, maximum, seen):
        i = len(self.calls)
        base = self.call_root / f"{i:02d}-{kind}"
        deadline = min(self.search_end, time.monotonic() + maximum)
        command = [str(SOURCE / ("policy-sandbox.sh" if kind == "policy" else "codex-sandbox.sh")),
                   str(self.call_root), str(self.args.train.parent), str(self.args.protocol)]
        prompt = self.model_prompt(kind) if kind != "policy" else None
        if kind == "policy":
            command.append(str(self.args.seed))
        else:
            command.append("-")
            if kind == "warm":
                command[4:4] = ["--output-schema", "/protocol/warm-schema.json"]
            base.with_suffix(".prompt.txt").write_text(prompt + "\n")
        recorded = {"kind": kind, "index": i, "start_s": self.elapsed(), "maximum_s": maximum}
        json_line(self.output / "model-events.jsonl", {**recorded, "event": "start"})
        cpu0 = child_cpu()
        solver_index = len(self.results)
        with ExitStack() as stack:
            out = stack.enter_context(base.with_suffix(".jsonl").open("wb"))
            err = stack.enter_context(base.with_suffix(".stderr").open("wb"))
            req_fd = os.open(self.work / "requests", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, req_fd)
            res_fd = os.open(self.work / "responses", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, res_fd)
            p = subprocess.Popen(command, stdin=subprocess.PIPE if prompt else subprocess.DEVNULL,
                                 stdout=out, stderr=err, start_new_session=True)
            try:
                if prompt:
                    p.stdin.write(prompt.encode())
                    p.stdin.close()
                while p.poll() is None and time.monotonic() < deadline:
                    if kind in ("auto", "policy"):
                        self.broker_poll(req_fd, res_fd, deadline, seen)
                    self.tick()
                    time.sleep(min(0.12, max(0.0, deadline - time.monotonic())))
            except BrokenPipeError:
                pass
            finally:
                killed = p.poll() is None
                if killed:
                    p.kill()
                p.wait()
        usage = {}
        message = None
        tools = 0
        if kind != "policy":
            for line in base.with_suffix(".jsonl").open(errors="replace"):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get("item", {})
                if event.get("type") == "item.completed":
                    if item.get("type") == "agent_message":
                        message = item.get("text")
                    elif item.get("type") in ("command_execution", "web_search", "mcp_tool_call"):
                        tools += 1
                if isinstance(event.get("usage"), dict):
                    usage = event["usage"]
        for name, value in usage.items():
            if type(value) is int:
                self.usage[name] = self.usage.get(name, 0) + value
        cpu_including_solver = child_cpu() - cpu0
        entry = {**recorded, "event": "end", "elapsed_s": self.elapsed() - recorded["start_s"],
                 "returncode": p.returncode, "deadline_killed": killed, "usage": usage,
                 "tool_items": tools, "child_cpu_including_concurrent_solver_s": cpu_including_solver,
                 "candidate_count_during_call": len(self.results) - solver_index}
        self.calls.append(entry)
        json_line(self.output / "model-events.jsonl", entry)
        return message

    def run_warm(self):
        self.prepare_broker()
        initial = None
        for _ in range(2):
            if time.monotonic() >= self.search_end:
                break
            response = self.call("warm", self.args.call_timeout, set())
            if not response:
                continue
            try:
                payload = json.loads(response)
                parsed = [config(json.loads(c) if isinstance(c, str) else c) for c in payload["configs"]]
                if len(parsed) == 4 and all(parsed) and len({key(c) for c in parsed}) == 4:
                    initial = parsed
                    break
            except (KeyError, TypeError, ValueError):
                pass
        if initial is None:
            self.error = "No valid four-configuration Sol reply before search cutoff."
            self.trial({}, "default-after-model-block")
            return
        atomic_json(self.output / "sol-warm-configs.json", initial)
        self.run_native(initial)
        warm_keys = {key(c) for c in initial}
        if not warm_keys <= set(self.seen):
            self.error = "Search deadline before completing all four Sol warm evaluations."

    def run_auto(self):
        self.prepare_broker()
        self.trial({}, "default")
        seen = set()
        for _ in range(3):
            if time.monotonic() >= self.search_end:
                break
            self.call("auto", self.args.call_timeout, seen)
            script = self.work / "policy.py"
            if script.is_file() and not script.is_symlink():
                self.launched_code = True
                self.call("policy", self.search_end - time.monotonic(), seen)
                if self.elapsed() >= self.args.budget - self.args.validation_seconds - 0.25:
                    break
        if not any(r["config"] and r["eligible"] for r in self.results):
            self.error = "No eligible non-default autonomous training candidate completed."

    def validation(self):
        selections = self.ranked()[:3]
        default = self.seen.get(key({}))
        if default and default["eligible"] and all(c["config"] for c in selections):
            selections.append(default)
        if not selections:
            return None, []
        if not self.args.validation:
            return selections[0]["config"], []
        items = verify(self.args.validation)
        results = {r["candidate_id"]: [] for r in selections}
        rng = random.Random(self.args.seed ^ 0xB206214)
        for repeat in range(self.args.validation_repeats):
            rng.shuffle(items)
            for item in items:
                order = selections.copy()
                rng.shuffle(order)
                for c in order:
                    row = measure(self.args.validation, item, c["config"], self.args.timeout, self.end)
                    results[c["candidate_id"]].append(row)
                    self.solver_cpu += row["solver_cpu_s"]
                    json_line(self.output / "validation-sessions.jsonl",
                              {"repeat": repeat, "candidate_id": c["candidate_id"], **row})
        total = len(items) * self.args.validation_repeats
        summaries = []
        for c in selections:
            rows = results[c["candidate_id"]]
            row = {"config": c["config"], "candidate_id": c["candidate_id"], **aggregate(rows)}
            if len(rows) != total:
                row["eligible"] = False
                row["par2"] = None
            summaries.append(row)
        eligible = [r for r in summaries if r["eligible"]]
        if not eligible:
            self.error = self.error or "No eligible fully evaluated validation candidate."
            return None, summaries
        winner = min(eligible, key=lambda r: (r["par2"], bool(r["config"]), r["candidate_id"]))
        return winner["config"], summaries

    def run(self):
        arms = {"random": self.run_random, "smac": self.run_native,
                "sol-smac": self.run_warm, "sol-auto": self.run_auto,
                "default": lambda: self.trial({}, "default")}
        try:
            arms[self.args.arm]()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            (self.output / "exception.txt").write_text(__import__("traceback").format_exc())
        for fraction in CHECKPOINTS:
            if fraction not in self.checks:
                self.checkpoint(fraction, min(self.elapsed(), fraction * (
                    self.args.budget - self.args.validation_seconds)))
        winner, validation = self.validation()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        locked = {"arm": self.args.arm, "seed": self.args.seed, "config": winner,
                  "status": "ok" if self.error is None and winner is not None else "partial",
                  "error": self.error, "locked": utc(),
                  "budget_s": self.args.budget, "wall_s": self.elapsed(),
                  "solver_cpu_s": self.solver_cpu,
                  "child_cpu_total_s": child_cpu() - self.started_solver_cpu,
                  "controller_cpu_s": usage.ru_utime + usage.ru_stime,
                  "candidate_count": len(self.results), "partial_candidate_count": self.partials,
                  "eligible_count": len(self.ranked()), "validation": validation,
                  "model_calls": sum(c["kind"] in ("warm", "auto") for c in self.calls),
                  "model_usage": self.usage, "policy_executed": self.launched_code,
                  "model_wall_s": sum(c["elapsed_s"] for c in self.calls if c["kind"] != "policy"),
                  "model_billing": "ChatGPT login; token telemetry, no separately metered API calls"}
        atomic_json(self.output / "locked.json", locked)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("arm", choices=(*ARMS, "default"))
    p.add_argument("output", type=Path)
    p.add_argument("--train", required=True, type=Path)
    p.add_argument("--validation", type=Path)
    p.add_argument("--protocol", required=True, type=Path)
    p.add_argument("--seed", required=True, type=int)
    p.add_argument("--core", required=True, type=int)
    p.add_argument("--budget", required=True, type=float)
    p.add_argument("--validation-seconds", required=True, type=float)
    p.add_argument("--validation-repeats", default=2, type=int)
    p.add_argument("--timeout", default=TUNING_TIMEOUT, type=float)
    p.add_argument("--call-timeout", default=300.0, type=float)
    p.add_argument("--begin", type=float)
    a = p.parse_args()
    if not 0 <= a.validation_seconds < a.budget:
        p.error("validation reserve must be smaller than the tuning budget")
    TuningRun(a).run()


if __name__ == "__main__":
    main()
