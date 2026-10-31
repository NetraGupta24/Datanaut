from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ColumnType = Literal["number", "date", "string"]


class DatasetSchema(BaseModel):
    headers: list[str]
    types: list[ColumnType]


class DatasetMetadata(BaseModel):
    """Shape of users/{uid}/datasets/{id} (camelCase to match the frontend store)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    sourceType: str
    schema_: DatasetSchema = Field(alias="schema")
    rowsPreview: list[dict[str, Any]]
    originalRowCount: int
    storagePath: str
    profile: dict[str, Any]
    createdAt: str
    updatedAt: str


class DeleteResult(BaseModel):
    deleted: bool
    chartsDeleted: int
