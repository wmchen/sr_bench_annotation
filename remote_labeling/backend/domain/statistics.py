"""Small, source-independent summaries of saved annotation groups."""

from .opening import has_pending_draft
from .rules import VARIANTS, Group


def sample_statistics(
    draft: Group, formal: Group | None, revision: int
) -> dict:
    """Count regions once and evidence entries separately for each variant.

    Args:
        draft: The server's saved, normalized four-variant group.
        formal: Last imported or published formal snapshot, if present.
        revision: Saved sample revision used to distinguish empty edits.

    Returns:
        Additive region, draft and per-variant evidence counters. Completion
        of a region depends on IDs and assigned values, not monotonicity.
    """
    by_variant = {}
    assigned_ids = {}
    for variant in VARIANTS:
        records = draft.get(variant, [])
        counts = {
            "sufficient": 0,
            "ambiguous": 0,
            "insufficient": 0,
            "unset": 0,
        }
        assigned_ids[variant] = set()
        for record in records:
            value = record.get("recoverable")
            if type(value) is int and value in (0, 1, 2):
                counts[("sufficient", "ambiguous", "insufficient")[value]] += 1
                assigned_ids[variant].add(record["region_id"])
            else:
                counts["unset"] += 1
        by_variant[variant] = {
            **counts,
            "total": len(records),
            "assigned": len(records) - counts["unset"],
        }
    return {
        "instances": len(draft.get("HR", [])),
        "completed_instances": sum(
            all(record["region_id"] in assigned_ids[v] for v in VARIANTS)
            for record in draft.get("HR", [])
        ),
        "recoverability_assigned": sum(
            v["assigned"] for v in by_variant.values()
        ),
        "recoverability_total": sum(v["total"] for v in by_variant.values()),
        "pending_samples": int(has_pending_draft(draft, formal, revision)),
        "by_variant": by_variant,
    }
