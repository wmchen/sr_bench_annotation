"""Evidence counts match region identity and saved draft semantics."""

import copy

import pytest

from remote_labeling.backend.domain.rules import VARIANTS, edit_group
from remote_labeling.backend.domain.statistics import sample_statistics


@pytest.mark.parametrize("attribute", ["text", "face"])
def test_evidence_distribution_and_region_identity(attribute: str) -> None:
    """Each HR instance is counted once, even when variant order differs."""
    hr = [
        {
            "region_id": name,
            "label": attribute,
            "shape_type": "rectangle",
            "points": [[1, 1], [8, 8]],
            "recoverable": value,
        }
        for name, value in (("one", 0), ("two", None))
    ]
    group = edit_group(
        hr,
        {},
        {v: [10, 10] for v in VARIANTS},
        attribute,
        {"LR2": {"one": 2}, "LR3": {"one": 1}, "LR4": {"one": 2}},
    )
    group["LR2"].reverse()
    original = copy.deepcopy(group)
    stats = sample_statistics(group, None, 1)
    assert stats["instances"] == 2
    assert stats["completed_instances"] == 1
    assert stats["recoverability_assigned"] == 4
    assert stats["recoverability_total"] == 8
    assert stats["pending_samples"] == 1
    assert stats["by_variant"]["LR2"] == {
        "sufficient": 0,
        "ambiguous": 0,
        "insufficient": 1,
        "unset": 1,
        "assigned": 1,
        "total": 2,
    }
    assert group == original
    # Monotonicity is advisory and does not make assigned evidence incomplete.
    group["LR3"][0]["region_id"] = "unmatched"
    assert sample_statistics(group, None, 1)["completed_instances"] == 0


def test_empty_and_semantic_formal_equality() -> None:
    """Untouched empty groups are not drafts; saved empty edits can be pending."""
    empty = {v: [] for v in VARIANTS}
    assert sample_statistics(empty, None, 0)["pending_samples"] == 0
    assert sample_statistics(empty, None, 1)["pending_samples"] == 1
    assert (
        sample_statistics(empty, dict(reversed(list(empty.items()))), 9)[
            "pending_samples"
        ]
        == 0
    )
    stats = sample_statistics(empty, empty, 9)
    assert stats["completed_instances"] == stats["recoverability_total"] == 0
