# Training-only cvc5 protocol

The read-only training bundle is mounted at `/train`. Its manifest lists full
SMT-LIB sessions, their observed satisfiability answers, and source program IDs.
The source directory contains the Java programs. The raw instrumentation traces
are also available when present. Each real cvc5 1.4.1 CLI process receives one
complete session. The fixed process timeout is 1.50 s for a whole session.
PAR2 equals wall time for a correct, complete session or 3 s for a session that
times out, produces unknown, or errors. Incorrect answers and solver errors
make a candidate ineligible. The attached evaluator.py is the host evaluator
source for inspection. Only the broker can run the actual evaluator.

`options.json` lists allowed tuning options. An omitted option or value
`"auto"` means to omit that CLI flag. Booleans "on" and "off" map to the
positive and negative CLI flags. Options outside the file are rejected.

The autonomous arm can use standard library Python and the training broker:

```sh
python3 /protocol/client.py eval '{"bv-abstraction":"off"}'
python3 /protocol/client.py smac 2
```

A policy saved to `/work/policy.py` may be run after the model turn in the
same training-only filesystem with the same broker. Its environment includes
`SEARCH_SEED`. Reuse `/protocol/client.py` by adding `/protocol` to
`sys.path` and importing `request`. Each request takes a JSON object with
one key, either "config" or "smac". Request "smac" with an integer from 1 to
4. Duplicate configurations return prior training observations. A "budget"
session represents a truncated measurement and cannot win.
