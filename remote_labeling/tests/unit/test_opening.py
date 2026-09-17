"""Opening priorities and the documented desktop compatibility boundary."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from remote_labeling.backend.domain.opening import (
    has_pending_draft,
    opening_selection,
)
from remote_labeling.backend.domain.rules import VARIANTS

EMPTY = {v: [] for v in VARIANTS}
GROUP = {v: [{"region_id": "r", "recoverable": 1}] for v in VARIANTS}


@pytest.mark.parametrize(
    "draft,formal,revision,expected",
    [
        (
            EMPTY,
            None,
            0,
            False,
        ),  # Includes indistinguishable imported empties.
        (EMPTY, None, 1, True),
        (GROUP, None, 0, True),
        (GROUP, GROUP, 5, False),
        (EMPTY, GROUP, 0, True),
        (GROUP, EMPTY, 0, True),
        (EMPTY, EMPTY, 4, False),
    ],
)
def test_pending_draft(
    draft: dict, formal: dict | None, revision: int, expected: bool
) -> None:
    """Saved work is distinct from initialized and redundant snapshots."""
    assert has_pending_draft(draft, formal, revision) is expected


def test_priorities_match_desktop() -> None:
    """Run the desktop selection methods without importing desktop packages."""
    source = Path(__file__).parents[3] / (
        "anylabeling/views/labeling/realisr_dataset.py"
    )
    if not source.exists():
        pytest.skip("desktop parity requires the repository checkout")
    tree = ast.parse(source.read_text())
    cls = next(
        n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == "RealISRDataset"
    )
    names = {"classify_draft_samples", "opening_selection", "is_committed"}
    methods = [
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    namespace = {"VARIANTS": VARIANTS}
    exec(
        compile(
            ast.Module(body=methods, type_ignores=[]), str(source), "exec"
        ),
        namespace,
    )
    ids = ["1.png", "2.png", "10.png"]
    desktop = SimpleNamespace(
        samples=ids,
        formal_json_samples=set(ids[:2]),
        formal={v: {name: GROUP[v] for name in ids[:2]} for v in VARIANTS},
        _sample_stats_cache={
            name: {"committed_samples": int(i < 2)}
            for i, name in enumerate(ids)
        },
    )
    desktop.classify_draft_samples = lambda: namespace[
        "classify_draft_samples"
    ](desktop)
    desktop.is_committed = lambda name: namespace["is_committed"](
        desktop, name
    )
    changed = copy.deepcopy(GROUP)
    changed["LR4"][0]["recoverable"] = 2
    for drafts in (
        {"2.png": GROUP, "10.png": changed},
        {"2.png": changed},
        {"2.png": GROUP},
        {},
    ):
        desktop.drafts = drafts
        samples = [
            {
                "id": name,
                "draft": drafts.get(name, GROUP if i < 2 else EMPTY),
                "formal": GROUP if i < 2 else None,
                "revision": 0,
                "complete": bool(desktop.is_committed(name)),
            }
            for i, name in enumerate(ids)
        ]
        selected = opening_selection(iter(samples))
        assert selected["sample"] == namespace["opening_selection"](desktop)[0]
        assert selected["index"] == ids.index(selected["sample"])
    for sample in samples:
        sample.update(formal=sample["draft"], complete=True)
    assert opening_selection(samples) == {
        "sample": "1.png",
        "index": 0,
        "pending_draft": False,
    }
    assert opening_selection([]) == {
        "sample": None,
        "index": None,
        "pending_draft": False,
    }
