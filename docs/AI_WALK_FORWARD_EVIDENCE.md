# AI walk-forward evidence

**Implementation baseline:** `c850786a209b3ef96cf5bdbe12630ca3e56bbf56`

`ai_learning_loop()` is an operational rolling-metrics job. It recomputes
7/30/90-day outcomes every six hours and is **not** a persisted replay or
walk-forward evaluation.

The `ai_walk_forward_runs` and `ai_walk_forward_folds` tables are the bounded
evidence scaffold. An explicit evaluator must provide:

- immutable candidate/baseline identifiers and request metadata;
- contiguous fold indexes with `train_start < train_end <= test_start < test_end`;
- train/test counts and metrics for every fold;
- dataset or artifact identifiers in `evidence` so results can be reproduced.

Creating a run only records the contract and starts it as `running`; it does
not claim that a model was evaluated. A run is evidence only after every fold
has results, the evaluator records its artifacts, and the run is completed.
Failures must be persisted as `failed`. AI remains PAPER-only until a separate
review approves the resulting report and its minimum sample, calibration,
abstention, and baseline-comparison gates.
