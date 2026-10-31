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


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    dataset_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    message: str = Field(min_length=1, max_length=2000)
    # The client sends the last 10 turns; anything older is ignored
    history: list[ChatTurn] = Field(default_factory=list, max_length=50)


class ResultTable(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    note: str | None = None


class Evidence(BaseModel):
    tool: str
    args: dict[str, Any]
    result_table: ResultTable


class ChatResponse(BaseModel):
    answer: str
    evidence: list[Evidence]
