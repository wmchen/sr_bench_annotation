"""Application-facing infrastructure contracts."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol


class Store(Protocol):
    """Short transaction boundary used by application use cases."""

    def transaction(self) -> AbstractContextManager:
        """Open a serialized write-capable transaction."""
        ...

    def read(self) -> AbstractContextManager:
        """Open an independent consistent read snapshot."""
        ...


class Predictor(Protocol):
    """Qt-free image array to ordinary result data contract."""

    def predict(self, image: object, regions: list[dict] | None) -> list[dict]:
        """Detect regions or recognize only the supplied regions."""
        ...
