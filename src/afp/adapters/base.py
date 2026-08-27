"""Adapter interface.

A source adapter's only job is to parse its raw files and populate the
common normalized schema (afp.schema.NormalizedData). This is the only
layer permitted to know about source-specific column names, file formats,
or quirks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..schema import NormalizedData


class SourceAdapter(ABC):
    @abstractmethod
    def parse(self) -> NormalizedData:
        """Parse this adapter's configured raw files into normalized data."""
        raise NotImplementedError
