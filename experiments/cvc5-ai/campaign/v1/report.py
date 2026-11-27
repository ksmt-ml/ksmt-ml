"""Render the current campaign checkpoint as Markdown."""
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

from evaluator import ROOT
from phase import DATASET


def load(p, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def num(x, precision=3):
    return f"{x:.{precision}f}" if x is not None else "—"


def summary(run):
    status = load(ROOT / "current.json", {})
    train = load(DATASET / "train/manifest.json")
    val = load(DATASET / "validation/manifest.json")
    test = load(run / "final/test/manifest.json")
    comparison = load(run / "final/comparison/comparison.json")
    cal = load(ROOT / "logs/usvm-train-calibration/summary.json")
    lines = ["# cvc5 tuning campaign", "", f"**Status** · {status.get('current', 'Preparing')}",
             "", f"Last controller update · {status.get('updated', 'pending')}", "",
             "## Corpus and evaluator", "",
             "Fresh generated Java source programs analyzed by USVM at "
             "`814a963354209421d36af758edbcec4295b081c4`, with KSMT 0.5.26 and Z3 as the live answer oracle. "
             "This estimates performance on unseen programs from the same generator. The cohort is small and "
             "consists of bounded integer Java methods.", "",
             "| Split | Programs | Sessions | Oracle checks | Rejected traces |", "|---|---:|---:|---:|---:|"]
    for label, manifest in (("Train", train), ("Validation", val), ("Final test", test)):
        if manifest:
            lines.append(f"| {label} | {manifest['group_count']} | {len(manifest['sessions'])} | "
                         f"{sum(len(x['oracle']) for x in manifest['sessions'])} | {len(manifest['rejected'])} |")
        else:
            lines.append(f"| {label} | — | — | — | — |")
    lines += ["", "Operations retain their order, including push/pop, assertions, assumption checks, model "
              "requests and unsat assumption requests. KSMT context declarations are hoisted. The trace uses "
              "the driver’s successful method end and balanced scopes as the completion boundary. The replay "
              "checks satisfiability responses against the oracle. It exercises model and core retrieval without "
              "comparing the particular model or core. KSMT-side computation on detached models is outside "
              "the SMT-LIB replay. Wall-clock cutoffs during live exploration can change a regenerated trace. "
              "Frozen SMT-LIB and raw transcripts are the reproducibility artifacts.", "",
              "Split by generating Java source program, with distinct seeds, exact source and session checks, "
              "and pairwise source-operation Jaccard similarity below 0.72. Traces with unknown live oracle "
              "answers are excluded. The test seed was committed before tuning. The test sources and sessions "
              "are materialized after the full lock file exists. Sol receives only training data.", "",
              "All scores use fresh real cvc5 1.4.1 processes, incremental mode, solver seed 18473, a 2 GiB "
              "address-space limit, fixed CPU affinity and a 1.50 s wall-clock cap per complete session. PAR2 "
              "uses elapsed wall time for a fully correct, solved session and 3.00 s for timeouts, unknowns, "
              "errors and incorrect sessions. Incorrect answers and errors disqualify a candidate.", ""]
    if cal:
        rows = [json.loads(line) for line in
                (ROOT / "logs/usvm-train-calibration/sessions.jsonl").read_text().splitlines()]
        c = Counter(row["status"] for row in rows)
        solved = [r["wall_s"] for r in rows if r["status"] == "solved"]
        noise = [x["relative_range_solved"] for x in cal["noise"] if x["relative_range_solved"] is not None]
        changes = sum(len(set(x["statuses"])) > 1 for x in cal["noise"])
        probes = [json.loads(line) for line in
                  (ROOT / "logs/usvm-train-calibration/option-probes.jsonl").read_text().splitlines()]
        lines += ["## Training calibration", "",
                  f"Across {cal['repeats']} default repeats, {c['solved']}/{len(rows)} sessions solved "
                  f"and {c['timeout']} timed out. Median solved latency was {num(statistics.median(solved))} s. "
                  f"The median within-program relative range was {num(100*statistics.median(noise),1)}% "
                  f"over {len(noise)} always-solved programs. {changes} programs changed outcome across repeats. "
                  f"{sum(x['status']=='solved' for x in probes)}/{len(probes)} option probes solved the short "
                  "training session. Pilot results excluded eager bit-blasting with model generation and "
                  "an incompatible minisat simplification setting from the shared search space.", ""]
    lines += ["## Runs", "",
              "Untuned cvc5 defaults are the reference. Random samples the shared option space independently. "
              "Native SMAC 2.4.0 uses its random forest and ask/tell interface, with default plus four Sobol "
              "configurations. Sol warm-start SMAC uses default plus four actual Sol proposals before the same "
              "SMAC algorithm. Autonomous Sol decides which training configurations to evaluate and can call the "
              "same native SMAC. It may supply a Python policy that continues in the training-only sandbox.", "",
              "Each tuned arm receives one pinned solver worker. Up to six workers run concurrently on separate "
              "physical cores. The host can still schedule unrelated work on those cores. Wall-clock accounting "
              "begins before each driver starts and includes model CLI startup, search, candidate evaluations "
              "and validation. Full budget is 3,600 s per arm and seed, with 180 s reserved for validation. "
              "Validation reruns the three best eligible training configurations and default in paired order, "
              "with two repeats. The winner minimizes validation PAR2. Validation outcomes are withheld from "
              "model turns. Smoke uses 210 s and one validation repeat.", ""]
    for stage in ("smoke", "full"):
        progress = load(run / stage / "progress.json", {})
        lock = load(run / stage / "locks.json")
        records = lock["runs"] if lock else progress.get("runs", [])
        if not records:
            continue
        lines += [f"### {stage.capitalize()}", "",
                  "| Seed | Arm | Status | Candidate count | Wall s | Solver CPU s | Sol calls | Sol wall s | Sol input / output tokens |",
                  "|---:|---|---|---:|---:|---:|---:|---:|---:|"]
        for row in records:
            usage = row.get("model_usage", {})
            lines.append(f"| {row['seed']} | {row['arm']} | {row['status']} | "
                         f"{row.get('candidate_count',row.get('candidates','—'))} | "
                         f"{num(row.get('wall_s',row.get('elapsed_s')),1)} | "
                         f"{num(row.get('solver_cpu_s'),1)} | "
                         f"{row.get('model_calls','—')} | {num(row.get('model_wall_s'),1)} | "
                         f"{usage.get('input_tokens',0)} / {usage.get('output_tokens',0)} |")
        lines.append("")
        for row in records:
            if row.get("error"):
                lines.append(f"- Seed {row['seed']} / {row['arm']} · {row['error']}")
        lines.append("")
    if comparison:
        lines += ["## Final held-out outcomes", "",
                  "Means weight Java programs equally. Each seed uses three repeat measurements in random paired "
                  "arm order inside each session. The final intervals resample programs, and additionally seeds "
                  "for the crossed interval. A seed count of three limits inference across tuning seeds.", "",
                  "| Arm | Paired seeds | PAR2 s | Ratio to default | 95% program CI | 95% program + seed CI | Solved | Correct / total answers | Timeout / unknown / error / incorrect sessions |",
                  "|---|---:|---:|---:|---|---|---:|---:|---:|"]
        for arm, r in comparison["arms"].items():
            c = r["counts"]
            pg = " to ".join(num(v) for v in r["group_bootstrap"]["ratio_95"])
            crossed = " to ".join(num(v) for v in r["group_and_seed_bootstrap"]["ratio_95"])
            lines.append(f"| {arm} | {r['paired_seed_count']} | {num(r['group_weighted_par2'])} | "
                         f"{num(r['ratio']) if r['eligible'] else 'ineligible'} | {pg} | {crossed} | "
                         f"{c['solved']} | {r['correct_answers']} / {r['queries']} | "
                         f"{c['timeout']} / {c['unknown']} / {c['error']} / {c['incorrect']} |")
        lines += [""]
        for arm, r in comparison["arms"].items():
            if arm != "default":
                per_seed = ", ".join(f"{x['seed']} {num(x['ratio'])}" for x in r["per_seed"])
                lines.append(f"- {arm} seed-level PAR2 ratios · {per_seed}.")
        if comparison["missing_arms"]:
            lines.append("- Missing full-run arms · " + ", ".join(comparison["missing_arms"]) + ".")
        lines.append("")
    lines += ["## Artifacts", "",
              "- `dataset-v1/frozen.json` records hashes, seeds, hardware assignments and the evaluation contract.",
              "- `logs/` preserves pilot and calibration traces, including the rejected synthetic fallback pilot.",
              "- Each run directory preserves solver-session rows, candidate/config records, checkpoints, native SMAC "
              "state, model prompts and transcripts, CPU seconds and model token telemetry.",
              "- ChatGPT login was used for Sol. Total monetary usage and server-side compute are unavailable from "
              "the local CLI. No separately metered paid API key was used.",
              "- Primary sources · [cvc5 1.4.1](https://github.com/cvc5/cvc5/releases/tag/cvc5-1.4.1), "
              "[SMAC ask/tell](https://automl.github.io/SMAC3/latest/examples/1%20Basics/3_ask_and_tell/), "
              "[Codex CLI](https://learn.chatgpt.com/docs/cli/reference).", ""]
    dest = ROOT / "RESULTS.md"
    tmp = dest.with_suffix(".md.new")
    tmp.write_text("\n".join(lines))
    tmp.replace(dest)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    summary(p.parse_args().directory.resolve())
