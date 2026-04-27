from typing import List, Optional

from pydantic import BaseModel, Field


class AssetItem(BaseModel):
    id: str = Field(..., description="Asset ID from Immich")
    fileName: Optional[str] = None
    createdAt: Optional[str] = None
    type: Optional[str] = None
    mimeType: Optional[str] = None
    isGif: bool = False
    mark: str = "none"
    missing: bool = False


class RoundAsset(BaseModel):
    id: str
    fileName: Optional[str] = None
    createdAt: Optional[str] = None
    type: Optional[str] = None
    mimeType: Optional[str] = None
    isGif: bool = False
    mark: str = "none"


class RandomResponse(BaseModel):
    assets: List[AssetItem]
    used_seen_fallback: bool = False
    message: Optional[str] = None
    total_returned: int
    requested: int
    seen_count: int
    deleted_total: int = 0
    favorited_total: int = 0
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    pages_used: Optional[List[int]] = None
    total_pages_considered: Optional[int] = None
    total_assets: Optional[int] = None
    stats_debug: Optional[str] = None
    previous_available: bool = False
    is_previous: bool = False
    round_count: Optional[int] = None
    round_filter: Optional[str] = None


class DeleteRequest(BaseModel):
    ids: List[str]


class DeleteResponse(BaseModel):
    success: bool
    deleted: int
    failed: List[str] = []
    detail: Optional[str] = None


class NextRequest(BaseModel):
    delete_ids: List[str] = []
    album_ids: List[str] = []
    count: Optional[int] = None
    filter_mode: Optional[str] = None
    current_count: Optional[int] = None
    current_filter: Optional[str] = None
    current_assets: List[RoundAsset] = []


class NextResponse(RandomResponse):
    deleted: int = 0
    album_added: int = 0
    failed_delete: List[str] = []
    failed_album: List[str] = []
    album_error: Optional[str] = None


class UISettingsRequest(BaseModel):
    count: int
    filter_mode: str
    theme: Optional[str] = None


class RoundSnapshotRequest(BaseModel):
    count: int
    filter_mode: str
    assets: List[RoundAsset] = []


class DebugStatus(BaseModel):
    immich_base_url: str
    api_key_configured: bool
    grid_count: int
    db_path: str
    ok: bool
    http_status: Optional[int] = None
    error: Optional[str] = None


class DebugLogEntry(BaseModel):
    timestamp: str
    method: str
    path: str
    status: Optional[int]
    error: Optional[str] = None
    correlation_id: Optional[str] = None
    raw_error: Optional[dict] = None


class DebugLogResponse(BaseModel):
    logs: List[DebugLogEntry]


class DebugDBInfo(BaseModel):
    db_path: str
    active_db_path: str
    exists_active: bool
    exists_config: bool
    size_bytes: Optional[int] = None
    mtime: Optional[str] = None
    error: Optional[str] = None
