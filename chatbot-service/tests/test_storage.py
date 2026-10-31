import pandas as pd
import pytest

from app.storage import DatasetNotFound, LocalStorage


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
