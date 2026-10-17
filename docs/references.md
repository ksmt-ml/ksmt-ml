# References and scope

## Deployment context

- [KSMT](https://github.com/UnitTestBot/ksmt) supplies the solver API and the
  observation-only profile contract.
- [cvc5](https://cvc5.github.io/) is the initial target solver. Parameter domains
  are pinned to an exact version and validated on fresh instances.
- [TSA](https://github.com/ksmt-ml/tsa) owns the application launch path and
  trial evaluation. Its current Yices/Z3 composition requires a cvc5 adapter.

## Algorithm configuration

- Frank Hutter, Holger Hoos, Kevin Leyton-Brown, and Thomas Stützle, “ParamILS:
  An Automatic Algorithm Configuration Framework”
  ([paper](https://arxiv.org/abs/1401.3492)). Relevant to measured,
  budget-constrained configuration search.
- Jasper Snoek, Hugo Larochelle, and Ryan Adams, “Practical Bayesian
  Optimization of Machine Learning Algorithms”
  ([paper](https://arxiv.org/abs/1206.2944)). Relevant to expensive black-box
  objectives and surrogate-based search.

## Pre-trained workload models

- Dana Van Aken et al., “Automatic Database Management System Tuning Through
  Large-scale Machine Learning”
  ([paper](https://db.cs.cmu.edu/papers/2017/p1009-van-aken.pdf)). Relevant for
  training a reusable configuration model from earlier workload observations;
  database-specific lifecycle assumptions are not inherited automatically.

- [DACBench](https://github.com/automl/DACBench) is useful for reproducible
  observation/action/reward definitions. Its abstractions are research
  references, not inherited production APIs.
