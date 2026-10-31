"""Full-dataset storage.

Firestore only keeps dataset metadata; the full table lives behind this interface.
`LocalStorage` writes Parquet files to disk. A Firebase Storage or S3 backend can
implement the same three methods later without touching the endpoints.
"""

import os
import re
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class DatasetNotFound(LookupError):
    pass


def validate_id(value: str, kind: str = "id") -> str:
    """Only allow ids that are safe to use as a file/object name (no path traversal)."""
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
        raise ValueError(f"Invalid {kind}.")
    return value


class DatasetStorage(ABC):
    @abstractmethod
    def save(self, uid: str, dataset_id: str, df: pd.DataFrame) -> str:
        """Persist the full table and return its storage path."""

    @abstractmethod
    def load(self, uid: str, dataset_id: str) -> pd.DataFrame:
        """Return the full table, or raise DatasetNotFound."""

    @abstractmethod
    def delete(self, uid: str, dataset_id: str) -> bool:
        """Remove the table; True if something was deleted."""


class LocalStorage(DatasetStorage):
    """Parquet files at <data_dir>/<uid>/<dataset_id>.parquet."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir).resolve()

    def _path(self, uid: str, dataset_id: str) -> Path:
        validate_id(uid, "uid")
        validate_id(dataset_id, "dataset id")
        return self.base_dir / uid / f"{dataset_id}.parquet"

    def save(self, uid: str, dataset_id: str, df: pd.DataFrame) -> str:
        path = self._path(uid, dataset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)  # atomic, so readers never see a half-written file
        return f"{self.base_dir.name}/{uid}/{dataset_id}.parquet"

    def load(self, uid: str, dataset_id: str) -> pd.DataFrame:
        path = self._path(uid, dataset_id)
        if not path.is_file():
            raise DatasetNotFound(dataset_id)
        return pd.read_parquet(path)

    def delete(self, uid: str, dataset_id: str) -> bool:
        path = self._path(uid, dataset_id)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
