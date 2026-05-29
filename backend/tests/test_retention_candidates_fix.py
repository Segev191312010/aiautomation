"""W1 regression: the direct_candidates retention policy must target the REAL
terminal statuses. The pre-fix policy filtered status IN ('completed','rejected',
'expired') — statuses this table never produces — so 'applied'/'failed' rows
leaked forever and were never garbage-collected.
"""
from db.retention import RetentionConfig


def test_direct_candidates_retention_targets_real_terminal_statuses():
    policy = next(
        p for p in RetentionConfig().policies if p.table == "direct_candidates"
    )
    assert policy.extra_where is not None
    # Real terminal statuses are deleted past the window…
    assert "applied" in policy.extra_where
    assert "failed" in policy.extra_where
    # …and the phantom statuses from the bug are gone.
    assert "completed" not in policy.extra_where
    assert "rejected" not in policy.extra_where
