"""The AI day manager (shadow): day plans that may only narrow deterministic trading.

``constants`` pins every limit; ``plan`` holds the DayPlan schema and the
narrow-only gate; ``context`` builds the model's input from the database
alone; ``planner`` writes plans on schedule within the spend ledger;
``store`` reads and writes the manager's tables (migration 16); ``switch``
is the owner's append-only autopilot switch and its per-session latch;
``managed`` runs managed MOMO (AP_MOMO_HOD_V1) against the plan, falling back
to neutral mode (V2's behaviour) on any plan failure.

Nothing here holds a broker connection or can create, change or cancel an
order. Submodules are imported on use so a strategy session never pays for
the manager unless it runs managed.
"""
