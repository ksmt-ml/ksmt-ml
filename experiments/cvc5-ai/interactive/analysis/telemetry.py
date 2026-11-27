"""Recompute interactive time, measurement counts and feedback from raw logs."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import statistics

TOOLS = {"command_execution", "mcp_tool_call", "web_search", "file_change", "tool_call"}


def rows(path):
    if path.exists():
        with path.open() as stream:
            yield from map(json.loads, stream)


def load(path):
    return json.loads(path.read_text())


def total(intervals):
    """Union length so overlapping commands and evaluator events count once."""
    end = 0
    elapsed = 0
    for start, stop in sorted(intervals):
        elapsed += max(0, stop - max(end, start))
        end = max(end, stop)
    return elapsed


def summaries(values):
    if not values:
        return {}
    return {"n": len(values), "sum_s": sum(values), "min_s": min(values),
            "median_s": statistics.median(values), "max_s": max(values)}


def signals(output):
    return {int(x) for x in re.findall(r'(?:"candidate_id"\s*:\s*|\bID\s+|\bid=)(\d+)', output)}


def run(directory):
    trace = list(rows(directory / "model-events.jsonl"))
    starts = {r["turn"]: r for r in trace if r["event"] == "start"}
    ends = {r["turn"]: r for r in trace if r["event"] == "end"}
    deadline = load(directory / "run.json")["search_s"]
    lock = load(directory / "locked.json")
    broker = list(rows(directory / "broker.jsonl"))
    candidates = list(rows(directory / "candidates.jsonl"))
    sessions = list(rows(directory / "train-sessions.jsonl"))
    configs = {}
    for event in broker:
        result = event["response"]
        trial = result.get("trial")
        if trial and not trial.get("cached_observation"):
            configs[trial["candidate_id"]] = event
    measured_by_turn = Counter(e["turn"] for e in configs.values())
    tool_intervals = []
    api_intervals = []
    feedback_in_outputs = set()
    clock_in_outputs = 0
    tool_counts = Counter()
    turns = []
    first_shell = []
    for idx, end in ends.items():
        start = starts[idx]
        start_s = start["start_s"]
        stop_s = min(start_s + end["elapsed_s"], deadline)
        api_intervals.append((start_s, stop_s))
        prefix = directory / "model" / f"turn-{idx:03}"
        opened = {}
        local_tools = []
        first = None
        messages = []
        for entry in rows(prefix.with_suffix(".timeline.jsonl")):
            timestamp = min(entry["elapsed_s"], deadline)
            event = entry["event"]
            item = event.get("item", {})
            if event["type"] != "item.completed" and (event["type"] != "item.started" or not item):
                continue
            if event["type"] == "item.completed" and item.get("type") == "agent_message":
                messages.append(item["text"])
            if item.get("type") not in TOOLS:
                continue
            if event["type"] == "item.started":
                opened[item["id"]] = timestamp
                if first is None:
                    first = timestamp
                continue
            tool_counts[item["type"]] += 1
            if item["id"] in opened:
                local_tools.append((opened.pop(item["id"]), timestamp))
            output = item.get("aggregated_output", "")
            feedback_in_outputs.update(signals(output))
            clock_in_outputs += "search_remaining_s" in output
        local_tools.extend((timestamp, stop_s) for timestamp in opened.values())
        tool_intervals.extend(local_tools)
        if first is not None:
            first_shell.append(first - start_s)
        turns.append({
            "index": idx, "elapsed_s_at_start": start["clock_at_prompt"]["elapsed_wall_s"],
            "remaining_s_at_start": start["clock_at_prompt"]["search_remaining_s"],
            "cli_wall_s": end["elapsed_s"], "observed_tool_wall_s": total(local_tools),
            "completed_trials": end["completed_trials"], "measured_responses": measured_by_turn[idx],
            "resumed": bool(start["resume_thread_id"]), "completed": end["completed"],
            "killed": end["deadline_killed"], "returncode": end["returncode"],
            "output_tokens": end.get("usage", {}).get("output_tokens"),
            "last_message": messages[-1] if messages else None,
        })
    solver_broker = [(e["started_s"], min(e["finished_s"], deadline)) for e in configs.values()]
    api = total(api_intervals)
    tool_or_eval = total(tool_intervals + solver_broker)
    eligible = [c for c in candidates if c["eligible"]]
    eligible.sort(key=lambda c: (c["finished_s"], c["candidate_id"]))
    improvements = []
    for c in eligible:
        if not improvements or c["par2"] < improvements[-1]["par2"]:
            improvements.append({k: c[k] for k in ("candidate_id", "config", "par2", "finished_s")})
    return {
        "seed": lock["seed"], "lock_status": lock["status"],
        "search_s": deadline, "wall_s_locked": lock["wall_s"], "full_wall_budget_s": lock["budget_s"],
        "validation_wall_from_cutoff_s": lock["wall_s"] - deadline,
        "cli_wall_s": api, "command_and_other_tool_wall_s": total(tool_intervals),
        "training_eval_broker_wall_s": total(solver_broker), "tool_or_training_eval_wall_s": tool_or_eval,
        "cli_wall_outside_observed_tools_and_eval_s": api - tool_or_eval,
        "search_wall_outside_cli_s": deadline - api,
        "solver_process_wall_s": sum(s["wall_s"] for s in sessions),
        "solver_cpu_train_s": sum(s["solver_cpu_s"] for s in sessions),
        "solver_cpu_total_s": lock["solver_cpu_s"], "tool_first_dispatch_latency": summaries(first_shell),
        "eval_request_latency": summaries([stop - start for start, stop in solver_broker]),
        "item_counts": dict(tool_counts), "model_usage": lock["model_usage"],
        "completed_unique_candidates": lock["candidate_count"], "eligible_candidates": lock["eligible_count"],
        "new_evaluations_in_turns": len(configs), "cached_evals": sum(
            e["response"].get("trial", {}).get("cached_observation", False) for e in broker),
        "error_responses": sum("error" in e["response"] for e in broker),
        "tool_outputs_with_live_clock": clock_in_outputs,
        "candidate_ids_visible_in_captured_tool_output": sorted(feedback_in_outputs & configs.keys()),
        "turns": turns, "training_incumbent_path": improvements,
        "training_outcomes": dict(Counter(s["status"] for s in sessions)),
        "ite_simp_unsat_core_errors": sum(s["status"] == "error" and "unsat core" in (
            s.get("stdout", "") + s.get("stderr", "")).lower()
            and candidates[s["candidate_id"]]["config"].get("ite-simp") == "on" for s in sessions),
        "selected_config": lock["config"], "validation": lock["validation"],
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("runs", type=Path, nargs="+")
    args = p.parse_args()
    artifact = {path.name: run(path) for path in args.runs}
    args.output.write_text(json.dumps(artifact, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
