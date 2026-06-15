"""Versioning for the tournament colour-assignment policy.

Lives in the shared kernel so both the schedule generator (which applies the policy) and the run
artifact contract (which records it in the schedule hash) can reference one source of truth without
a layering cycle. Bump the version whenever the colour-assignment logic changes so a schedule
generated under a different policy is treated as a distinct, non-resume-compatible schedule.
"""

from __future__ import annotations

COLOR_POLICY_VERSION = 1

__all__ = ["COLOR_POLICY_VERSION"]
