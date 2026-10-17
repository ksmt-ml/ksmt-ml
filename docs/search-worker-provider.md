# Search worker provider contract

## Ownership

`ksmt-ml-tuner` owns the public JVM search API and the canonical
`cvc5-search-worker-v0` message schema. `ksmt-ml` may publish a Python worker
that conforms to that protocol. TSA or another application implements the JVM
trial evaluator and owns the workload, correctness oracle, and produced
artifacts.

No Python class or module in this repository is a cross-repository API.

## Process boundary

The tuner host starts one local worker process per search session. Standard
input and output carry newline-delimited JSON protocol messages only. Worker
diagnostics use standard error. The host validates every envelope, sequence,
proposal, configuration patch, observation, and checkpoint descriptor.

The worker owns algorithm-native proposal selection and serializable optimizer
state. It must not execute TSA, construct a solver, decide correctness, extend
the budget, or treat local files as host-approved artifacts.

## Algorithms

The first worker is expected to expose the tuner-owned identifiers
`random-search-v0`, `evolution-search-v0`, and `smac3-v0`. Algorithm-specific
configuration arrives in the `INITIALIZE` payload; adding a new algorithm or
changing configuration meaning requires a protocol-compatible extension or a
new protocol version.

## Packaging and compatibility

A released worker must include an executable entry point, dependency lock,
supported protocol versions, build identity, license inventory, and protocol
conformance results. Host and worker negotiate an exact protocol version during
`HELLO`; an unsupported version fails closed before any trial is launched.

Checkpoint payloads remain backend-owned. The host stores only the descriptor
and checksum and may resume it only with a compatible worker build and
algorithm configuration.

## Current state

This branch defines provider responsibilities only. Experimental RS, ES, and
SMAC code is preserved on `dev/litvinovmitch11/demo` and is not a production
worker or a compatibility promise.
