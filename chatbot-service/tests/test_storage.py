import pandas as pd
import pytest

from app.storage import CachedStorage, DatasetNotFound, LocalStorage


class CountingStorage(LocalStorage):
    def __init__(self, base_dir):
        super().__init__(base_dir)
        self.loads = 0

    def load(self, uid, dataset_id):
        self.loads += 1
        return super().load(uid, dataset_id)


def test_cache_reuses_frames_and_evicts_least_recently_used(tmp_path):
    backend = CountingStorage(tmp_path / "data")
    cache = CachedStorage(backend, max_items=10)
    for i in range(11):
        backend.save("u", f"ds{i}", pd.DataFrame({"i": [i]}))

    for i in range(10):
        cache.load("u", f"ds{i}")
    cache.load("u", "ds0")  # ds0 becomes most recent
    assert backend.loads == 10
    cache.load("u", "ds10")  # evicts ds1, the least recently used
    assert len(cache.cached_keys()) == 10 and ("u", "ds1") not in cache.cached_keys()
    cache.load("u", "ds0")
    assert backend.loads == 11
    cache.load("u", "ds1")
    assert backend.loads == 12


def test_cache_forgets_deleted_and_replaced_frames(tmp_path):
    cache = CachedStorage(LocalStorage(tmp_path / "data"), max_items=2)
    cache.save("u", "ds", pd.DataFrame({"a": [1]}))
    assert cache.load("u", "ds")["a"].tolist() == [1]
    cache.save("u", "ds", pd.DataFrame({"a": [2]}))
    assert cache.load("u", "ds")["a"].tolist() == [2]
    assert cache.delete("u", "ds") is True
    with pytest.raises(DatasetNotFound):
        cache.load("u", "ds")


def test_save_load_delete_roundtrip(tmp_path):
    storage = LocalStorage(tmp_path / "data")
    df = pd.DataFrame({"a": [1, 2], "b": ["x", None]})
    path = storage.save("user1", "ds1", df)
    assert path == "data/user1/ds1.parquet"
    assert (tmp_path / "data" / "user1" / "ds1.parquet").is_file()
    pd.testing.assert_frame_equal(storage.load("user1", "ds1"), df)
    assert storage.delete("user1", "ds1") is True
    assert storage.delete("user1", "ds1") is False
    with pytest.raises(DatasetNotFound):
        storage.load("user1", "ds1")


@pytest.mark.parametrize("uid,dataset_id", [("..", "x"), ("user1", "../other/ds"), ("a/b", "x"), ("user1", "")])
def test_rejects_path_traversal(tmp_path, uid, dataset_id):
    storage = LocalStorage(tmp_path / "data")
    with pytest.raises(ValueError):
        storage.load(uid, dataset_id)
