"""Desktop opening priorities adapted to the online annotation state."""

from collections.abc import Iterable
from typing import Any

from .rules import VARIANTS


def has_pending_draft(draft: dict, formal: dict | None, revision: int) -> bool:
    """Identify saved work without treating every online draft as an edit."""
    if formal is not None:
        return draft != formal
    return revision > 0 or any(draft.get(v) for v in VARIANTS)


def opening_selection(samples: Iterable[dict[str, Any]]) -> dict:
    """Choose changed work, then unfinished work, then the first sample.

    Args:
        samples: Authorized samples in dataset order, with parsed groups.

    Returns:
        The sample ID, its accessible index, and whether to preview its draft.
    """
    first = incomplete = None
    for index, sample in enumerate(samples):
        pending = has_pending_draft(
            sample["draft"], sample["formal"], sample["revision"]
        )
        selection = {
            "sample": sample["id"],
            "index": index,
            "pending_draft": pending,
        }
        if pending:
            return selection
        if first is None:
            first = selection
        if incomplete is None and not sample["complete"]:
            incomplete = selection
    return (
        incomplete
        or first
        or {
            "sample": None,
            "index": None,
            "pending_draft": False,
        }
    )
