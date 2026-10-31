import copy

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_uid
from app.main import app, get_metadata_store, get_storage
from app.metadata import MetadataStore
from app.storage import LocalStorage

SALES_CSV = (
    "Month,Date,Revenue,Units,Region\n"
    "Jan,2024-01-31,12000,320,North\n"
    "Feb,2024-02-29,13500,340,North\n"
    "Mar,2024-03-31,14250,355,South\n"
    "Apr,2024-04-30,15100,360,South\n"
    "May,2024-05-31,16000,380,East\n"
    "Jun,2024-06-30,17250,395,West\n"
)


class FakeMetadata(MetadataStore):
    """In-memory stand-in for Firestore: {uid: {"datasets": {...}, "charts": {...}}}."""

    def __init__(self):
        self.users: dict[str, dict[str, dict]] = {}

    def _user(self, uid):
        return self.users.setdefault(uid, {"datasets": {}, "charts": {}})

    def create_dataset(self, uid, dataset_id, doc):
        self._user(uid)["datasets"][dataset_id] = copy.deepcopy(doc)

    def get_dataset(self, uid, dataset_id):
        return copy.deepcopy(self._user(uid)["datasets"].get(dataset_id))

    def delete_dataset(self, uid, dataset_id):
        user = self._user(uid)
        existed = user["datasets"].pop(dataset_id, None) is not None
        linked = [cid for cid, c in user["charts"].items() if c.get("datasetId") == dataset_id]
        for cid in linked:
            del user["charts"][cid]
        return existed, len(linked)


@pytest.fixture
def fake_metadata():
    return FakeMetadata()


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "data")


@pytest.fixture
def current_uid():
    """Mutable holder so a test can switch users mid-test."""
    return {"uid": "user-a"}


@pytest.fixture
def client(fake_metadata, storage, current_uid):
    app.dependency_overrides[get_current_uid] = lambda: current_uid["uid"]
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_metadata_store] = lambda: fake_metadata
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def unauthenticated_client(fake_metadata, storage):
    """Real auth dependency; storage and Firestore still faked."""
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_metadata_store] = lambda: fake_metadata
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def upload(client, content: str | bytes, filename="sales.csv", name="Sales", **form):
    data = {"name": name, **{k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()}}
    body = content.encode() if isinstance(content, str) else content
    return client.post("/datasets", files={"file": (filename, body)}, data=data)
