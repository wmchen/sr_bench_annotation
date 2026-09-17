"""Authorized snapshot aggregation with bounded per-sample memoization."""

import json
from collections import OrderedDict
from threading import Lock
from typing import Any

from ..domain.rules import VARIANTS
from ..domain.statistics import sample_statistics


class StatisticsCache:
    """Cache counts only; versions fence concurrent snapshot reads.

    Args:
        capacity: Maximum sample-version summaries retained in memory.
    """

    def __init__(self, capacity: int = 4096) -> None:
        self.capacity = capacity
        self._values: OrderedDict[tuple, dict] = OrderedDict()
        self._lock = Lock()

    def aggregate(self, db: Any, dataset: Any, sample: str | None) -> dict:
        """Aggregate only rows authorized by the caller's read snapshot.

        Args:
            db: Connection inside the caller's consistent read transaction.
            dataset: Dataset row from that same authorized snapshot.
            sample: Authorized sample restriction, or None for the dataset.

        Returns:
            Fresh aggregate counters, without exposing cached mutable values.
        """
        where = "dataset=?"
        args = [dataset["id"]]
        if sample is not None:
            where += " AND id=?"
            args.append(sample)
        rows = db.execute(
            "SELECT id,revision,complete FROM samples WHERE " + where, args
        ).fetchall()
        total = sample_statistics({v: [] for v in VARIANTS}, None, 0)
        total.update(
            sample_groups=len(rows), image_files=0, complete_samples=0
        )
        for row in rows:
            key = (
                dataset["id"],
                dataset["import_version"],
                row["id"],
                row["revision"],
            )
            with self._lock:
                counts = self._values.get(key)
                if counts is not None:
                    self._values.move_to_end(key)
            if counts is None:
                source = db.execute(
                    "SELECT draft,formal,images FROM samples "
                    "WHERE dataset=? AND id=?",
                    (dataset["id"], row["id"]),
                ).fetchone()
                counts = sample_statistics(
                    json.loads(source["draft"]),
                    json.loads(source["formal"]) if source["formal"] else None,
                    row["revision"],
                )
                counts["image_files"] = len(json.loads(source["images"]))
                with self._lock:
                    self._values[key] = counts
                    self._values.move_to_end(key)
                    while len(self._values) > self.capacity:
                        self._values.popitem(last=False)
            total["complete_samples"] += row["complete"]
            for name, value in counts.items():
                if name == "by_variant":
                    for variant, distribution in value.items():
                        for field, count in distribution.items():
                            total[name][variant][field] += count
                else:
                    total[name] += value
        return total
