# Training interface

Read-only `/train` contains the frozen manifest, complete incremental SMT-LIB sessions, raw traces and Java programs. The evaluator starts real cvc5 1.4.1 once per session with a 1.50 s wall cap, incremental mode, fixed solver seed and 2 GiB address-space cap. PAR2 is elapsed time for a fully correct session, otherwise 3.00 s. Solver errors and incorrect answers make the candidate ineligible and stop its measurement. Budget-truncated measurements are ineligible. The attached evaluator source shows the scoring rules.

Use only `options.json` for configuration. Omission or "auto" uses the solver's existing setting. All accepted configurations use identical training order within this seed. A duplicate returns the cached observations.

```sh
python3 /protocol/client.py eval '{"bv-abstraction":"off"}'
python3 /protocol/client.py history 0
python3 /protocol/client.py clock
```

An evaluation returns its trial summary and per-session timing/status, plus the current clock. History returns up to 100 summaries from the supplied candidate ID and a continuation cursor if needed. To recover per-session timings for a previously evaluated configuration, submit the same configuration. Use `sys.path.insert(0, "/protocol")` and `from client import request` in Python. Submit one evaluation at a time and collect the response before submitting the next. There is one solver worker. Training scripts, data analyses and `/work/journal.md` can persist across turns. Public cvc5 documentation is available through web search. Validation and test data and feedback are unavailable.
