"""Dataset metadata in Firestore at users/{uid}/datasets/{id}.

Only metadata lives here (schema, 50-row preview, profile, storage path); the full
rows are in DatasetStorage. Uses the Admin SDK, so firestore.rules don't apply.
"""

from abc import ABC, abstractmethod

from firebase_admin import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .auth import get_firebase_app
from .config import Settings


class MetadataStore(ABC):
    @abstractmethod
    def create_dataset(self, uid: str, dataset_id: str, doc: dict) -> None: ...

    @abstractmethod
    def get_dataset(self, uid: str, dataset_id: str) -> dict | None: ...

    @abstractmethod
    def delete_dataset(self, uid: str, dataset_id: str) -> tuple[bool, int]:
        """Delete the dataset doc and its charts; return (doc_existed, charts_deleted)."""


class FirestoreMetadata(MetadataStore):
    def __init__(self, settings: Settings):
        self._db = firestore.client(app=get_firebase_app(settings))

    def _user(self, uid: str):
        return self._db.collection("users").document(uid)

    def create_dataset(self, uid: str, dataset_id: str, doc: dict) -> None:
        self._user(uid).collection("datasets").document(dataset_id).set(doc)

    def get_dataset(self, uid: str, dataset_id: str) -> dict | None:
        snap = self._user(uid).collection("datasets").document(dataset_id).get()
        return snap.to_dict() if snap.exists else None

    def delete_dataset(self, uid: str, dataset_id: str) -> tuple[bool, int]:
        user = self._user(uid)
        dataset_ref = user.collection("datasets").document(dataset_id)
        existed = dataset_ref.get().exists
        charts = list(
            user.collection("charts").where(filter=FieldFilter("datasetId", "==", dataset_id)).stream()
        )
        batch = self._db.batch()
        for chart in charts:
            batch.delete(chart.reference)
        batch.delete(dataset_ref)
        batch.commit()
        return existed, len(charts)
