# Experiments

`registry.csv` is the durable index of planned, running, completed, and aborted
experiments. Add a row before a costly run and update the same row when its
status changes.

Large raw outputs belong under `experiments/runs/<experiment_id>/` and are
ignored by Git. A completed experiment should retain its configuration,
checksums, aggregate report, and a pointer to external raw storage when
applicable.

Experiment IDs use `E` followed by four digits. IDs are never reused.
