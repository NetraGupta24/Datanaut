import json
import logging
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import Depends, FastAPI, File, Form, HTTPException, Path, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from .auth import get_current_uid
from .config import MAX_ROWS_PER_REQUEST, Settings, get_settings
from .ingest import IngestError, load_dataframe, preview_records, rows_to_json
from .llm import ChatEngine, ChatServiceError, make_groq_client
from .metadata import FirestoreMetadata, MetadataStore
from .models import ChartConfig, ChatRequest, ChatResponse, DatasetMetadata, DeleteResult
from .profiling import profile_dataframe
from .storage import CachedStorage, DatasetNotFound, DatasetStorage, LocalStorage

DATASET_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("datanaut")

app = FastAPI(title="DATANAUT chatbot service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Rate limits are per signed-in user; chat_user() puts the verified uid on request.state
limiter = Limiter(key_func=lambda request: f"uid:{request.state.uid}")
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def _rate_limited(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": f"Too many chat requests (limit {exc.detail}). Please wait a moment and try again."},
        headers={"Retry-After": "60"},
    )


@lru_cache
def get_storage() -> DatasetStorage:
    settings = get_settings()
    return CachedStorage(LocalStorage(settings.data_dir), max_items=settings.dataframe_cache_size)


@lru_cache
def get_metadata_store() -> MetadataStore:
    return FirestoreMetadata(get_settings())


def get_chat_engine() -> ChatEngine:
    settings = get_settings()
    if not settings.groq_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Chat isn't configured: set GROQ_API_KEY in chatbot-service/.env.")
    return _chat_engine(settings.groq_api_key.get_secret_value(), settings.groq_model, settings.groq_timeout_s)


@lru_cache
def _chat_engine(api_key: str, model: str, timeout_s: float) -> ChatEngine:
    return ChatEngine(make_groq_client(api_key, timeout_s), model)


def chat_user(request: Request, uid: str = Depends(get_current_uid)) -> str:
    request.state.uid = uid
    return uid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"File is larger than {limit // (1024 * 1024)} MB."
        )
    return data


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/datasets", response_model=DatasetMetadata, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    file: UploadFile = File(...),
    name: str = Form(..., max_length=200),
    source_type: str = Form("file", max_length=40),
    first_row_header: bool = Form(True),
    uid: str = Depends(get_current_uid),
    settings: Settings = Depends(get_settings),
    storage: DatasetStorage = Depends(get_storage),
    metadata: MetadataStore = Depends(get_metadata_store),
):
    name = name.strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Dataset name is required.")
    content = await _read_upload(file, settings.max_upload_bytes)
    try:
        df, types = await run_in_threadpool(load_dataframe, file.filename or "", content, first_row_header)
    except IngestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    profile = await run_in_threadpool(profile_dataframe, df, types)
    preview = await run_in_threadpool(preview_records, df, settings.preview_rows)

    dataset_id = uuid.uuid4().hex
    storage_path = await run_in_threadpool(storage.save, uid, dataset_id, df)
    now = _now()
    doc = {
        "id": dataset_id,
        "name": name,
        "sourceType": source_type,
        "schema": {"headers": list(df.columns), "types": types},
        "rowsPreview": preview,
        "originalRowCount": len(df),
        "storagePath": storage_path,
        "profile": profile,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        await run_in_threadpool(metadata.create_dataset, uid, dataset_id, doc)
    except Exception as exc:
        # Don't leave an orphaned file behind if Firestore rejects the write
        await run_in_threadpool(storage.delete, uid, dataset_id)
        logger.exception("Writing metadata for dataset %s failed", dataset_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not save the dataset metadata. Please try again.") from exc

    logger.info("Stored dataset %s for %s: %d rows x %d cols", dataset_id, uid, len(df), len(df.columns))
    return doc


@app.get("/datasets/{dataset_id}/rows")
def get_dataset_rows(
    dataset_id: str = Path(pattern=DATASET_ID_PATTERN),
    limit: int = Query(1000, ge=1, le=MAX_ROWS_PER_REQUEST),
    offset: int = Query(0, ge=0),
    uid: str = Depends(get_current_uid),
    storage: DatasetStorage = Depends(get_storage),
) -> Response:
    try:
        df = storage.load(uid, dataset_id)
    except DatasetNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found or has no stored rows.") from exc

    page = df.iloc[offset : offset + limit]
    # Build the JSON directly: pandas serialises 50k rows far faster than pydantic would
    body = (
        f'{{"datasetId":{json.dumps(dataset_id)},"total":{len(df)},'
        f'"offset":{offset},"limit":{limit},"rows":{rows_to_json(page)}}}'
    )
    return Response(content=body, media_type="application/json")


@app.delete("/datasets/{dataset_id}", response_model=DeleteResult)
def delete_dataset(
    dataset_id: str = Path(pattern=DATASET_ID_PATTERN),
    uid: str = Depends(get_current_uid),
    storage: DatasetStorage = Depends(get_storage),
    metadata: MetadataStore = Depends(get_metadata_store),
) -> DeleteResult:
    doc_existed, charts_deleted = metadata.delete_dataset(uid, dataset_id)
    file_deleted = storage.delete(uid, dataset_id)
    if not (doc_existed or file_deleted or charts_deleted):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found.")
    return DeleteResult(deleted=True, chartsDeleted=charts_deleted)


@app.post("/chat", response_model=ChatResponse)
@limiter.limit(get_settings().chat_rate_limit)
def chat(
    request: Request,
    body: ChatRequest,
    uid: str = Depends(chat_user),
    storage: DatasetStorage = Depends(get_storage),
    metadata: MetadataStore = Depends(get_metadata_store),
    engine: ChatEngine = Depends(get_chat_engine),
) -> ChatResponse:
    meta = metadata.get_dataset(uid, body.dataset_id)
    if meta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found.")
    if not meta.get("profile") or not meta.get("storagePath"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This dataset was saved before the data service existed. Re-upload it on the Data page to chat about it.",
        )
    try:
        df = storage.load(uid, body.dataset_id)
    except DatasetNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The stored rows for this dataset are missing.") from exc

    history = [turn.model_dump() for turn in body.history]
    try:
        result = engine.answer(df, meta.get("name", "dataset"), meta["profile"], history, body.message)
    except ChatServiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    chart = ChartConfig(datasetId=body.dataset_id, **result.chart) if result.chart else None
    return ChatResponse(answer=result.answer, evidence=result.evidence, chart=chart)
