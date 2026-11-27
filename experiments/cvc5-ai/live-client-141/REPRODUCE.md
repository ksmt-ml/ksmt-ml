# Reproducing the live USVM experiment

Host used: Linux `butvinm-work`, directory `/home/butvinm/Dev/cvc5-ai-pilot/live-client-141`.
Read `PREREG.md` and its two pre-holdout amendments. `inputs/manifest.json` freezes installed public JAR versions, SHA-256, dependency classpaths, static candidate counts, exact held-out method signatures and the earlier manifests consulted. `inputs/schedule.json` freezes every ordered block.

The Linux x86-64 cvc5 1.4.1 Java API and static JNI library come from the original release archive already available under `../campaign/setup`. `runs/smoke/02/release.json` has their SHA-256 and the Z3 native source JAR identity. Both cvc5 arms use exactly the same Java/native files, with one `setOption` in `Live141Backend.kt`. The 0.5.26 cvc5 adapter is compiled from the isolated KSMT checkout against the official Java JAR, using `evidence/cvc5-compat.patch` and `checkout/ksmt/live-shim/io/github/cvc5/PointerAccess.java`. No old cvc5 jar enters the live classpath.

The installed USVM bytecode and its classpath manifest are read-only. `checkout/usvm` is a copy of commit 814a963354209421d36af758edbcec4295b081c4 with the existing campaign instrumentation and the local diagnostic overlay. `evidence/prior-usvm.patch` preserves previously tracked changes. `scripts/patch_usvm.py` describes the edits from the installed source to the overlay; the complete active sources, including the small added driver and native identity code, are in `checkout/usvm`. Only the designated sources are recompiled against read-only USVM module outputs through Kotlin compiler 2.1.0. Z3's native files are copied byte for byte from its installed 0.5.26 JAR to persistent resources, leaving the regular KSMT Z3 loader and binding in control. The runtime evidence files attest to the mapped copies. New files are confined to this experiment directory.

## Existing result verification

Run in the experiment directory.

```sh
sha256sum -c evidence/prereg.sha256
sha256sum -c evidence/inputs.sha256
PYTHONDONTWRITEBYTECODE=1 python scripts/analyze.py primary
PYTHONDONTWRITEBYTECODE=1 python scripts/verify.py primary
```

Analysis is deterministic given the original raw data. The verification certificate also records its execution time. The raw run includes every arm's invocation, wall interval, output and separate setup and method events. `VERIFY.json` checks and hashes those files, checks the pinned runtime configurations and project pairing, and independently recomputes the penalized ratios and project-bootstrap intervals. Verification reads the original Java and native assets again.

## New live trial

Reproduce in this directory using new attempt names. Preserve the archived `primary` and `02` smoke files and the original compiled artifacts to keep their existing verification valid. The bundled smoke fixture JAR is fixed for the original trial; running another smoke may recreate its ZIP timestamp. Use a staged directory or preserve and then restore archived build outputs before re-verifying the original run.

```sh
PYTHONDONTWRITEBYTECODE=1 python scripts/build.py adapter repro
PYTHONDONTWRITEBYTECODE=1 python scripts/build.py client repro
PYTHONDONTWRITEBYTECODE=1 python scripts/run.py smoke repro
PYTHONDONTWRITEBYTECODE=1 python scripts/run.py benchmark trial --approved-smoke repro
PYTHONDONTWRITEBYTECODE=1 python scripts/analyze.py trial
PYTHONDONTWRITEBYTECODE=1 python scripts/verify.py trial
```

For a new date, copy the run and verification scripts inside the experiment directory with the corresponding explicit deadline and save the changed runner hash. The original finite run ended in the window documented in `report.html`. To regenerate the static admission from scratch, use `scripts/ScanMethods.java` and `scripts/freeze.py` after preserving the two frozen JSON files. Filesystem and Java paths are deliberately explicit so no silent fallback to another runtime can occur. The host's 12–15 logical CPU IDs were each a separate physical core, with no SMT siblings.

## Additional interpreter-policy check

`DIAGNOSTIC-PROTOCOL.md` records the trigger, the reduced balanced schedule, and the additional step-error penalty. `runs/stock/derived-schedule.json` is derived from the original frozen schedule. The auxiliary driver changes the step exception policy to USVM's default and marks method boundaries in stdout. The primary case files are preserved. To independently check the existing auxiliary records and regenerate the Russian report, run these commands in the same directory.

```sh
PYTHONDONTWRITEBYTECODE=1 python scripts/analyze.py stock
PYTHONDONTWRITEBYTECODE=1 python scripts/stock_verify.py
PYTHONDONTWRITEBYTECODE=1 python scripts/report.py
PYTHONDONTWRITEBYTECODE=1 python scripts/check_report.py
```

`scripts/stock.py` acquires a new stock series only after the primary metrics and diagnostics exist and the trigger is met. It expects the auxiliary attempt directory to be unused. `evidence/report-verification.json` records the final HTML SHA-256 and checks for local rendering without any required external assets.
