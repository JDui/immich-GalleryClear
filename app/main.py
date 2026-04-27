import math
import os
import random
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .immich_client import ImmichClient
from .models import SeenRepository
from .schemas import (
    AssetItem,
    DebugDBInfo,
    DebugLogEntry,
    DebugLogResponse,
    DebugStatus,
    DeleteRequest,
    DeleteResponse,
    NextRequest,
    NextResponse,
    RandomResponse,
    RoundAsset,
    RoundSnapshotRequest,
    UISettingsRequest,
)


def get_env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, default))
    except ValueError:
        return default


IMMICH_BASE_URL = os.getenv("IMMICH_BASE_URL", "").rstrip("/")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")

# UI defaults (persisted in DB once user changes)
DEFAULT_GRID_ROWS = 3
DEFAULT_GRID_COLS = 2
DEFAULT_GRID_COUNT = DEFAULT_GRID_ROWS * DEFAULT_GRID_COLS
DEFAULT_FILTER_MODE = "exclude-prescreen"
DEFAULT_THEME = "rainbow"
THEME_OPTIONS = {"rainbow", "gradient", "blur"}
PAGE_SIZE = 200

SEARCH_MAX_PAGES = get_env_int("SEARCH_MAX_PAGES", 30)

# Album names
SEEN_ALBUM_NAME = "immichClearSeen"
FAV_ALBUM_NAME = "iCCollection"

# API pacing / log size controls
IMMICH_LOG_SIZE = get_env_int("IMMICH_LOG_SIZE", 50)
IMMICH_MIN_INTERVAL_MS = get_env_int("IMMICH_MIN_INTERVAL_MS", 10)
IMMICH_MAX_CONCURRENCY = get_env_int("IMMICH_MAX_CONCURRENCY", 10)
ALBUM_CACHE_TTL_SEC = get_env_int("ALBUM_CACHE_TTL_SEC", 0)
ALBUM_ID_CACHE_TTL_SEC = get_env_int("ALBUM_ID_CACHE_TTL_SEC", 0)
ALL_ALBUM_CACHE_TTL_SEC = get_env_int("ALL_ALBUM_CACHE_TTL_SEC", 0)
PAGES_CACHE_TTL_SEC = get_env_int("PAGES_CACHE_TTL_SEC", 0)

# If DB_PATH is set to empty string, fall back to default to avoid sqlite errors
_db_env = os.getenv("DB_PATH")
DB_PATH_RAW = _db_env or "/app/data/random_cleaner.db"
if DB_PATH_RAW.endswith("/"):
    DB_PATH_RAW = os.path.join(DB_PATH_RAW.rstrip("/"), "random_cleaner.db")
DB_PATH = DB_PATH_RAW
ACTIVE_DB_PATH = DB_PATH
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"

# Ensure database directory exists when running locally (outside container)
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
# Ensure file exists (sqlite will create if missing). If permission denied, we handle below.
try:
    Path(DB_PATH).touch(exist_ok=True)
except Exception:
    pass


def init_seen_repository() -> SeenRepository:
    """
    Try configured DB_PATH; if it fails and ALLOW_DB_FALLBACK=true, fall back to /tmp.
    """
    allow_fallback = os.getenv("ALLOW_DB_FALLBACK", "true").lower() == "true"
    try:
        global ACTIVE_DB_PATH
        repo = SeenRepository(DB_PATH)
        ACTIVE_DB_PATH = DB_PATH
        return repo
    except (sqlite3.OperationalError, PermissionError) as exc:
        if not allow_fallback:
            raise
        fallback = "/tmp/random_cleaner.db"
        Path(fallback).parent.mkdir(parents=True, exist_ok=True)
        print(f"[warn] DB path not writable: {DB_PATH}, fallback to {fallback}. Error: {exc}")
        repo = SeenRepository(fallback)
        ACTIVE_DB_PATH = fallback
        return repo


seen_repo = init_seen_repository()


def load_ui_settings() -> tuple[int, str, str]:
    return seen_repo.get_ui_settings(DEFAULT_GRID_COUNT, DEFAULT_FILTER_MODE, DEFAULT_THEME)


immich_client = ImmichClient(
    IMMICH_BASE_URL,
    IMMICH_API_KEY,
    log_size=IMMICH_LOG_SIZE,
    min_interval_ms=IMMICH_MIN_INTERVAL_MS,
    max_concurrency=IMMICH_MAX_CONCURRENCY,
)

_album_asset_cache: Dict[str, Tuple[Set[str], float]] = {}
_album_id_cache: Dict[str, Tuple[str, float]] = {}
_all_album_asset_cache: Optional[Tuple[Set[str], float]] = None
_page_cache: Dict[int, Tuple[int, Optional[int], float]] = {}

def _is_cache_valid(ts: float, ttl: int) -> bool:
    if ttl <= 0:
        return True
    return (time.monotonic() - ts) <= ttl

async def get_album_id_cached(name: str, *, create_if_missing: bool = False) -> Optional[str]:
    entry = _album_id_cache.get(name)
    if entry and _is_cache_valid(entry[1], ALBUM_ID_CACHE_TTL_SEC):
        return entry[0]
    album_id = None
    try:
        if create_if_missing:
            album_id = await immich_client.ensure_album(name)
        else:
            album_id = await immich_client.find_album_by_name(name)
    except Exception:
        album_id = None
    if album_id:
        _album_id_cache[name] = (album_id, time.monotonic())
    return album_id

async def get_album_asset_ids_cached(name: str) -> Set[str]:
    entry = _album_asset_cache.get(name)
    if entry and _is_cache_valid(entry[1], ALBUM_CACHE_TTL_SEC):
        return set(entry[0])
    album_id = await get_album_id_cached(name, create_if_missing=False)
    if not album_id:
        _album_asset_cache[name] = (set(), time.monotonic())
        return set()
    ids = set(await immich_client.list_album_asset_ids(album_id))
    _album_asset_cache[name] = (ids, time.monotonic())
    return set(ids)

async def get_all_album_asset_ids_cached() -> Set[str]:
    global _all_album_asset_cache
    if _all_album_asset_cache and _is_cache_valid(_all_album_asset_cache[1], ALL_ALBUM_CACHE_TTL_SEC):
        return set(_all_album_asset_cache[0])
    ids = set(await immich_client.list_all_album_asset_ids())
    _all_album_asset_cache = (ids, time.monotonic())
    return set(ids)

def cache_add_album_assets(name: str, ids: List[str]) -> None:
    global _all_album_asset_cache
    if not ids:
        return
    entry = _album_asset_cache.get(name)
    if entry:
        entry[0].update(ids)
        _album_asset_cache[name] = (entry[0], time.monotonic())
    if _all_album_asset_cache:
        _all_album_asset_cache[0].update(ids)
        _all_album_asset_cache = (_all_album_asset_cache[0], time.monotonic())

def cache_remove_album_assets(name: str, ids: List[str]) -> None:
    global _all_album_asset_cache
    if not ids:
        return
    entry = _album_asset_cache.get(name)
    if entry:
        entry[0].difference_update(ids)
        _album_asset_cache[name] = (entry[0], time.monotonic())
    if _all_album_asset_cache:
        _all_album_asset_cache[0].difference_update(ids)
        _all_album_asset_cache = (_all_album_asset_cache[0], time.monotonic())

def clear_album_cache(name: str) -> None:
    global _all_album_asset_cache
    _album_asset_cache.pop(name, None)
    _album_id_cache.pop(name, None)
    _all_album_asset_cache = None


app = FastAPI(title="Immich Random Cleaner", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def normalize_asset(item: dict) -> Optional[AssetItem]:
    asset_id = item.get("id") or item.get("assetId")
    if not asset_id:
        return None

    file_name = (
        item.get("originalFileName")
        or item.get("fileName")
        or item.get("displayName")
    )
    mime_type = (
        item.get("originalMimeType")
        or item.get("mimeType")
        or (item.get("exifInfo") or {}).get("mimeType")
    )
    lower_name = (file_name or "").lower()
    is_gif = False
    if mime_type and "gif" in str(mime_type).lower():
        is_gif = True
    if lower_name.endswith(".gif"):
        is_gif = True
    if item.get("isAnimated") is True or item.get("isGif") is True:
        is_gif = True

    return AssetItem(
        id=str(asset_id),
        fileName=file_name,
        createdAt=item.get("createdAt") or item.get("updatedAt"),
        type=item.get("type"),
        mimeType=mime_type,
        isGif=is_gif,
    )


def build_round_snapshot(
    count: int, filter_mode: str, assets: List[RoundAsset]
) -> dict:
    safe_assets: List[dict] = []
    for asset in assets:
        try:
            parsed = RoundAsset(**asset) if isinstance(asset, dict) else asset
        except Exception:
            continue
        mark = parsed.mark if parsed.mark in {"delete", "album", "none"} else "none"
        safe_assets.append(
            {
                "id": parsed.id,
                "fileName": parsed.fileName,
                "createdAt": parsed.createdAt,
                "type": parsed.type,
                "mimeType": parsed.mimeType,
                "isGif": bool(parsed.isGif),
                "mark": mark,
            }
        )
    return {
        "count": int(count),
        "filter_mode": filter_mode,
        "assets": safe_assets,
    }


def store_prev_round(
    count: int, filter_mode: str, assets: List[RoundAsset], ready: bool
) -> None:
    payload = build_round_snapshot(count, filter_mode, assets)
    seen_repo.set_prev_round(payload)
    if ready:
        seen_repo.set_prev_ready(True)


def store_current_round(
    count: int, filter_mode: str, assets: List[RoundAsset], ready: bool
) -> None:
    payload = build_round_snapshot(count, filter_mode, assets)
    seen_repo.set_current_round(payload)
    if ready:
        seen_repo.set_current_ready(True)


def snapshot_assets_to_items(raw_assets: list) -> List[AssetItem]:
    assets: List[AssetItem] = []
    for item in raw_assets:
        try:
            parsed = RoundAsset(**item)
        except Exception:
            continue
        assets.append(
            AssetItem(
                id=parsed.id,
                fileName=parsed.fileName,
                createdAt=parsed.createdAt,
                type=parsed.type,
                mimeType=parsed.mimeType,
                isGif=parsed.isGif,
                mark=parsed.mark,
                missing=False,
            )
        )
    return assets


def snapshot_count(snapshot: dict, fallback: int) -> int:
    try:
        count_val = snapshot.get("count")
        if count_val is not None:
            return max(1, int(count_val))
    except Exception:
        pass
    try:
        rows = snapshot.get("rows")
        cols = snapshot.get("cols")
        if rows is not None and cols is not None:
            return max(1, int(rows) * int(cols))
    except Exception:
        pass
    return max(1, int(fallback))


def store_current_round_from_items(
    count: int, filter_mode: str, items: List[AssetItem]
) -> None:
    assets: List[RoundAsset] = []
    for item in items:
        assets.append(
            RoundAsset(
                id=item.id,
                fileName=item.fileName,
                createdAt=item.createdAt,
                type=item.type,
                mimeType=item.mimeType,
                isGif=item.isGif,
                mark=item.mark or "none",
            )
        )
    store_current_round(count, filter_mode, assets, True)


def adjust_counter(key: str, delta: int) -> None:
    current = seen_repo.get_counter(key)
    seen_repo.set_meta(key, str(max(0, current + delta)))


async def fetch_random_assets(
    count: int,
    filter_mode: str = "all",
    force_validate_pages: bool = False,
) -> RandomResponse:
    if not IMMICH_BASE_URL or not IMMICH_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="IMMICH_BASE_URL 或 IMMICH_API_KEY 未配置",
        )

    page_size = PAGE_SIZE
    all_assets: List[AssetItem] = []
    pages_used: List[int] = []
    total_pages_calculated: Optional[int] = None
    total_assets: Optional[int] = None
    stats_debug: Optional[str] = None
    message: Optional[str] = None
    previous_available = seen_repo.is_prev_ready()

    pages_cache: Dict[int, List[AssetItem]] = {}
    parsed_first: Optional[List[AssetItem]] = None

    try:
        cache_entry = _page_cache.get(page_size)
        cache_valid = False
        last_page_cache: Optional[int] = None
        if cache_entry and _is_cache_valid(cache_entry[2], PAGES_CACHE_TTL_SEC):
            last_page_cache, cached_total_assets, _ = cache_entry
            total_assets = cached_total_assets
            cache_valid = True

        if force_validate_pages:
            cache_valid = False
            last_page_cache = None

        if not cache_valid:
            first_items, total_count = await immich_client.search_assets(
                page=1, size=page_size
            )
            parsed_first = [normalize_asset(item) for item in first_items]
            parsed_first = [p for p in parsed_first if p is not None]
            pages_cache[1] = parsed_first
            if total_count and total_count > page_size:
                total_assets = total_count

        async def detect_last_page() -> int:
            nonlocal parsed_first
            if parsed_first is None:
                items, _ = await immich_client.search_assets(page=1, size=page_size)
                parsed_first = [normalize_asset(item) for item in items]
                parsed_first = [p for p in parsed_first if p is not None]
                pages_cache[1] = parsed_first
            last_non_empty = 1 if parsed_first else 0
            step = 1
            upper_bound = 2
            while step < 2048:
                probe_page = max(2, last_non_empty + step)
                items, _ = await immich_client.search_assets(
                    page=probe_page, size=page_size
                )
                parsed = [normalize_asset(item) for item in items]
                parsed = [p for p in parsed if p is not None]
                if parsed:
                    last_non_empty = probe_page
                    pages_cache[probe_page] = parsed
                    step *= 2
                else:
                    upper_bound = probe_page
                    break
            low = last_non_empty
            high = upper_bound
            while high - low > 1:
                mid = (low + high) // 2
                parsed_mid = pages_cache.get(mid)
                if parsed_mid is None:
                    items, _ = await immich_client.search_assets(page=mid, size=page_size)
                    parsed_mid = [normalize_asset(it) for it in items]
                    parsed_mid = [p for p in parsed_mid if p is not None]
                    pages_cache[mid] = parsed_mid
                if parsed_mid:
                    low = mid
                else:
                    high = mid
            return max(1, low)

        pages_from_total = math.ceil(total_assets / page_size) if total_assets else None
        if pages_from_total and pages_from_total < 1:
            pages_from_total = 1

        recorded_total = seen_repo.get_total_pages_record()
        if recorded_total is not None and recorded_total < 1:
            recorded_total = None

        last_page: Optional[int] = None
        if force_validate_pages:
            last_page = await detect_last_page()
        elif pages_from_total:
            last_page = pages_from_total
        elif cache_valid and last_page_cache:
            last_page = last_page_cache
        elif recorded_total:
            last_page = recorded_total
        else:
            last_page = await detect_last_page()

        last_page = max(1, last_page or 1)
        _page_cache[page_size] = (last_page, total_assets, time.monotonic())

        recorded_total = seen_repo.get_total_pages_record()
        if recorded_total is not None and recorded_total != last_page:
            seen_repo.clear_exhausted_pages()
        if recorded_total != last_page:
            seen_repo.set_total_pages_record(last_page)

        exhausted_pages = seen_repo.get_exhausted_pages()
        available_pages = set(range(1, last_page + 1)) - exhausted_pages

        if not available_pages:
            return RandomResponse(
                assets=[],
                used_seen_fallback=False,
                message="已结束所有图片筛选。",
                total_returned=0,
                requested=count,
                seen_count=seen_repo.get_counter("seen_count"),
                deleted_total=seen_repo.get_counter("deleted_count"),
                favorited_total=seen_repo.get_counter("favorited_count"),
                pages_used=None,
                total_pages_considered=last_page,
                total_assets=total_assets,
                stats_debug=stats_debug,
                previous_available=previous_available,
                is_previous=False,
                round_count=count,
                round_filter=filter_mode,
            )

        all_assets = []
        pages_used = []
        while available_pages and not all_assets:
            rand_page = random.choice(list(available_pages))
            available_pages.remove(rand_page)

            parsed = pages_cache.get(rand_page)
            if parsed is None:
                items, _ = await immich_client.search_assets(
                    page=rand_page, size=page_size
                )
                parsed = [normalize_asset(item) for item in items]
                parsed = [p for p in parsed if p is not None]
                pages_cache[rand_page] = parsed

            if parsed:
                pages_used = [rand_page]
                all_assets = parsed
            else:
                seen_repo.mark_page_exhausted(rand_page)

        total_pages_calculated = last_page

    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"从 Immich 获取资源失败: {exc}")

    if not all_assets:
        return RandomResponse(
            assets=[],
            used_seen_fallback=False,
            message="已结束所有图片筛选。",
            total_returned=0,
            requested=count,
            seen_count=seen_repo.get_counter("seen_count"),
            deleted_total=seen_repo.get_counter("deleted_count"),
            favorited_total=seen_repo.get_counter("favorited_count"),
            pages_used=None,
            total_pages_considered=total_pages_calculated,
            total_assets=total_assets,
            stats_debug=stats_debug,
            previous_available=previous_available,
            is_previous=False,
            round_count=count,
            round_filter=filter_mode,
        )

    unique: dict[str, AssetItem] = {}
    for a in all_assets:
        unique[a.id] = a
    all_assets = list(unique.values())
    random.shuffle(all_assets)

    excluded_ids: Set[str] = set()
    if filter_mode == "exclude-prescreen":
        try:
            excluded_ids.update(await get_album_asset_ids_cached(SEEN_ALBUM_NAME))
            excluded_ids.update(await get_album_asset_ids_cached(FAV_ALBUM_NAME))
        except Exception:
            pass
    elif filter_mode == "exclude-albums":
        try:
            excluded_ids.update(await get_all_album_asset_ids_cached())
        except Exception:
            pass

    filtered_assets = [a for a in all_assets if a.id not in excluded_ids]

    if pages_used and not filtered_assets:
        seen_repo.mark_page_exhausted(pages_used[0])

    assets_pool = filtered_assets if filtered_assets else all_assets

    selected: List[AssetItem] = []
    used_seen_fallback = False
    time_start = None
    time_end = None

    if len(assets_pool) >= count:
        selected = random.sample(assets_pool, count)
    else:
        selected.extend(assets_pool)
        remaining = count - len(selected)
        remaining_pool = [a for a in all_assets if a.id not in {x.id for x in selected}]
        if remaining_pool:
            extras = random.sample(remaining_pool, min(remaining, len(remaining_pool)))
            selected.extend(extras)
            if extras:
                used_seen_fallback = True

    if not selected and all_assets:
        selected = random.sample(all_assets, min(count, len(all_assets)))
        used_seen_fallback = True
        message = "全部已看，返回已看图片以填充。"

    try:
        if selected:
            seen_album_id = await get_album_id_cached(SEEN_ALBUM_NAME, create_if_missing=True)
            if seen_album_id:
                await immich_client.add_assets_to_album(
                    seen_album_id, [a.id for a in selected]
                )
                seen_repo.incr_counter("seen_count", len(selected))
                cache_add_album_assets(SEEN_ALBUM_NAME, [a.id for a in selected])
    except Exception:
        pass

    if not assets_pool:
        message = message or "未见图片不足，补充了部分已见图片。"
    elif used_seen_fallback:
        message = message or "已无新的未见图片，使用部分已见图片填充。"

    pages_used_dedup = list(dict.fromkeys(pages_used)) if pages_used else None

    return RandomResponse(
        assets=selected,
        used_seen_fallback=used_seen_fallback,
        message=message,
        total_returned=len(selected),
        requested=count,
        seen_count=seen_repo.get_counter("seen_count"),
        deleted_total=seen_repo.get_counter("deleted_count"),
        favorited_total=seen_repo.get_counter("favorited_count"),
        time_start=time_start,
        time_end=time_end,
        pages_used=pages_used_dedup,
        total_pages_considered=total_pages_calculated,
        total_assets=total_assets,
        stats_debug=stats_debug,
        previous_available=previous_available,
        is_previous=False,
        round_count=count,
        round_filter=filter_mode,
    )


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await immich_client.close()


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    count, filter_mode, theme = load_ui_settings()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "grid_count": count,
            "filter_mode": filter_mode,
            "theme": theme,
            "debug_mode": DEBUG_MODE,
            "immich_base_url": IMMICH_BASE_URL,
            "api_key_configured": bool(IMMICH_API_KEY),
            "db_path": DB_PATH,
            "search_max_pages": SEARCH_MAX_PAGES,
        },
    )


@app.get("/api/random", response_model=RandomResponse)
async def api_random(
    count: int = Query(None),
    filter_mode: str = Query(DEFAULT_FILTER_MODE),
) -> RandomResponse:
    default_count, _, current_theme = load_ui_settings()
    target = count or default_count
    if target <= 0:
        raise HTTPException(status_code=400, detail="count å¿é¡»å¤§äº 0")
    seen_repo.set_ui_settings(target, filter_mode, current_theme)
    if seen_repo.is_current_ready():
        snapshot = seen_repo.get_current_round()
        if snapshot:
            snap_count = snapshot_count(snapshot, target)
            snap_filter = snapshot.get("filter_mode") or filter_mode
            snap_assets_raw = snapshot.get("assets") or []
            snap_assets = snapshot_assets_to_items(snap_assets_raw)
            if PAGE_SIZE not in _page_cache:
                recorded_total = seen_repo.get_total_pages_record()
                if recorded_total:
                    _page_cache[PAGE_SIZE] = (recorded_total, None, time.monotonic())
            seen_repo.set_meta("current_restored", "1")
            return RandomResponse(
                assets=snap_assets,
                used_seen_fallback=False,
                message="已恢复当前缓存。",
                total_returned=len(snap_assets),
                requested=snap_count,
                seen_count=seen_repo.get_counter("seen_count"),
                deleted_total=seen_repo.get_counter("deleted_count"),
                favorited_total=seen_repo.get_counter("favorited_count"),
                pages_used=None,
                total_pages_considered=None,
                total_assets=None,
                stats_debug=None,
                previous_available=seen_repo.is_prev_ready(),
                is_previous=False,
                round_count=snap_count,
                round_filter=snap_filter,
            )
        seen_repo.clear_current_round()
    result = await fetch_random_assets(target, filter_mode)
    store_current_round_from_items(target, filter_mode, result.assets)
    seen_repo.set_meta("current_restored", "0")
    return result


@app.post("/api/refresh", response_model=RandomResponse)
async def api_refresh(
    count: int = Query(None),
    filter_mode: str = Query(DEFAULT_FILTER_MODE),
) -> RandomResponse:
    default_count, _, current_theme = load_ui_settings()
    target = count or default_count
    if target <= 0:
        raise HTTPException(status_code=400, detail="count å¿é¡»å¤§äº 0")
    seen_repo.set_ui_settings(target, filter_mode, current_theme)

    snapshot = seen_repo.get_current_round() if seen_repo.is_current_ready() else None
    if snapshot:
        ids = [item.get("id") for item in (snapshot.get("assets") or []) if item.get("id")]
        if ids:
            try:
                album_id = await get_album_id_cached(SEEN_ALBUM_NAME, create_if_missing=False)
                if album_id:
                    failed_ids = await immich_client.remove_assets_from_album(album_id, ids)
                    removed_ids = [x for x in ids if x not in (failed_ids or [])]
                    if removed_ids:
                        adjust_counter("seen_count", -len(removed_ids))
                        cache_remove_album_assets(SEEN_ALBUM_NAME, removed_ids)
            except Exception:
                pass

    seen_repo.clear_current_round()
    seen_repo.clear_prev_round()
    seen_repo.clear_forward_round()
    seen_repo.set_meta("current_restored", "0")

    result = await fetch_random_assets(target, filter_mode)
    store_current_round_from_items(target, filter_mode, result.assets)
    seen_repo.set_meta("current_restored", "0")
    return result


@app.post("/api/ui-settings")
async def api_ui_settings(payload: UISettingsRequest) -> dict:
    count = int(payload.count)
    filter_mode = payload.filter_mode or DEFAULT_FILTER_MODE
    theme = (payload.theme or DEFAULT_THEME).strip()
    if count <= 0:
        raise HTTPException(status_code=400, detail="count å¿é¡»å¤§äº 0")
    if filter_mode not in {"all", "exclude-prescreen", "exclude-albums"}:
        raise HTTPException(status_code=400, detail="filter_mode æ æ")
    if theme not in THEME_OPTIONS:
        raise HTTPException(status_code=400, detail="theme æ æ")
    seen_repo.set_ui_settings(count, filter_mode, theme)
    return {"success": True}


@app.post("/api/remember-forward")
async def api_remember_forward(payload: RoundSnapshotRequest) -> dict:
    count = int(payload.count)
    filter_mode = payload.filter_mode or DEFAULT_FILTER_MODE
    if count <= 0:
        raise HTTPException(status_code=400, detail="count å¿é¡»å¤§äº 0")
    if filter_mode not in {"all", "exclude-prescreen", "exclude-albums"}:
        raise HTTPException(status_code=400, detail="filter_mode æ æ")
    payload_dict = build_round_snapshot(count, filter_mode, payload.assets)
    seen_repo.set_forward_round(payload_dict)
    seen_repo.set_forward_state(1)
    return {"success": True}

@app.post("/api/remember-round")
async def api_remember_round(payload: RoundSnapshotRequest) -> dict:
    count = int(payload.count)
    filter_mode = payload.filter_mode or DEFAULT_FILTER_MODE
    if count <= 0:
        raise HTTPException(status_code=400, detail="count å¿é¡»å¤§äº 0")
    if filter_mode not in {"all", "exclude-prescreen", "exclude-albums"}:
        raise HTTPException(status_code=400, detail="filter_mode æ æ")
    store_prev_round(count, filter_mode, payload.assets, True)
    return {"success": True}


@app.post("/api/delete", response_model=DeleteResponse)
async def api_delete(payload: DeleteRequest) -> DeleteResponse:
    if not payload.ids:
        return DeleteResponse(success=True, deleted=0, failed=[], detail="空列表，无需删除")
    try:
        result = await immich_client.delete_assets(payload.ids)
        deleted_count = len(payload.ids)
        detail_text = None
        if isinstance(result, dict) and "failedIds" in result:
            failed_ids = result.get("failedIds") or []
            deleted_count = deleted_count - len(failed_ids)
            return DeleteResponse(
                success=len(failed_ids) == 0,
                deleted=deleted_count,
                failed=failed_ids,
                detail="部分删除失败" if failed_ids else None,
            )
        return DeleteResponse(success=True, deleted=deleted_count, failed=[], detail=detail_text)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/next", response_model=NextResponse)
async def api_next(payload: NextRequest) -> NextResponse:
    default_count, _, current_theme = load_ui_settings()
    count = payload.count or default_count
    filter_mode = payload.filter_mode or DEFAULT_FILTER_MODE
    if count <= 0:
        raise HTTPException(status_code=400, detail="count å¿é¡»å¤§äº 0")
    seen_repo.set_ui_settings(count, filter_mode, current_theme)

    forward_state = seen_repo.get_forward_state()
    if forward_state == 1:
        snapshot = seen_repo.get_forward_round()
        seen_repo.set_forward_state(2)
        if snapshot:
            snap_count = snapshot_count(snapshot, count)
            snap_filter = snapshot.get("filter_mode") or filter_mode
            snap_assets_raw = snapshot.get("assets") or []
            snap_assets: List[AssetItem] = []
            snap_round_assets: List[RoundAsset] = []
            for item in snap_assets_raw:
                try:
                    parsed = RoundAsset(**item)
                except Exception:
                    continue
                snap_round_assets.append(parsed)
                snap_assets.append(
                    AssetItem(
                        id=parsed.id,
                        fileName=parsed.fileName,
                        createdAt=parsed.createdAt,
                        type=parsed.type,
                        mimeType=parsed.mimeType,
                        isGif=parsed.isGif,
                        mark=parsed.mark,
                        missing=False,
                    )
                )
            if snap_round_assets:
                store_current_round(snap_count, snap_filter, snap_round_assets, True)
            return NextResponse(
                assets=snap_assets,
                used_seen_fallback=False,
                message="已返回到上一轮的下一页。",
                total_returned=len(snap_assets),
                requested=snap_count,
                seen_count=seen_repo.get_counter("seen_count"),
                deleted_total=seen_repo.get_counter("deleted_count"),
                favorited_total=seen_repo.get_counter("favorited_count"),
                pages_used=None,
                total_pages_considered=None,
                total_assets=None,
                stats_debug=None,
                previous_available=False,
                is_previous=False,
                round_count=snap_count,
                round_filter=snap_filter,
                deleted=0,
                album_added=0,
                failed_delete=[],
                failed_album=[],
                album_error=None,
            )
        # if snapshot missing, fall through to normal behavior
    elif forward_state == 2:
        seen_repo.clear_forward_round()
        forward_state = 0

    snapshot_count_val = payload.current_count or count
    snapshot_filter = payload.current_filter or filter_mode
    if payload.current_assets and forward_state == 0:
        store_prev_round(
            snapshot_count_val, snapshot_filter, payload.current_assets, True
        )

    deleted_count = 0
    failed_delete: List[str] = []
    album_added = 0
    failed_album: List[str] = []
    album_error: Optional[str] = None
    target_album_name = FAV_ALBUM_NAME
    target_album_id: Optional[str] = None

    if payload.album_ids:
        try:
            target_album_id = await get_album_id_cached(target_album_name, create_if_missing=True)
            if target_album_id:
                await immich_client.add_assets_to_album(target_album_id, payload.album_ids)
                album_added = len(payload.album_ids)
                if album_added:
                    seen_repo.incr_counter("favorited_count", album_added)
                    cache_add_album_assets(target_album_name, payload.album_ids)
            else:
                failed_album = payload.album_ids
                album_error = "相册不可用"
        except Exception as exc:
            failed_album = payload.album_ids
            album_error = str(exc)

    if payload.delete_ids:
        try:
            result = await immich_client.delete_assets(payload.delete_ids)
            failed_ids = []
            if isinstance(result, dict) and "failedIds" in result:
                failed_ids = result.get("failedIds") or []
            deleted_count = len(payload.delete_ids) - len(failed_ids)
            failed_delete = failed_ids
            if deleted_count > 0:
                seen_repo.incr_counter("deleted_count", deleted_count)
        except Exception as exc:
            failed_delete = payload.delete_ids
            album_error = album_error or str(exc)

    force_validate = seen_repo.get_meta("current_restored") == "1"
    if force_validate:
        seen_repo.set_meta("current_restored", "0")
    random_result = await fetch_random_assets(count, filter_mode, force_validate_pages=force_validate)
    store_current_round_from_items(count, filter_mode, random_result.assets)

    return NextResponse(
        **random_result.dict(),
        deleted=deleted_count,
        album_added=album_added,
        failed_delete=failed_delete,
        failed_album=failed_album,
        album_error=album_error,
    )


@app.get("/api/previous", response_model=RandomResponse)
async def api_previous() -> RandomResponse:
    if not seen_repo.is_prev_ready():
        raise HTTPException(status_code=404, detail="暂无上一轮记录")
    snapshot = seen_repo.get_prev_round()
    if not snapshot:
        seen_repo.set_prev_ready(False)
        raise HTTPException(status_code=404, detail="暂无上一轮记录")
    count = snapshot_count(snapshot, DEFAULT_GRID_COUNT)
    filter_mode = snapshot.get("filter_mode") or DEFAULT_FILTER_MODE
    raw_assets = snapshot.get("assets") or []
    assets: List[AssetItem] = []
    restore_ids: List[str] = []
    album_ids: List[str] = []
    for item in raw_assets:
        try:
            parsed = RoundAsset(**item)
        except Exception:
            continue
        if parsed.mark == "delete":
            restore_ids.append(parsed.id)
        if parsed.mark == "album":
            album_ids.append(parsed.id)

    if restore_ids:
        try:
            restored_count, failed_ids = await immich_client.restore_assets(restore_ids)
            if restored_count:
                adjust_counter("deleted_count", -restored_count)
        except Exception:
            pass

    if album_ids:
        try:
            album_id = await immich_client.find_album_by_name(FAV_ALBUM_NAME)
            if album_id:
                failed_ids = await immich_client.remove_assets_from_album(
                    album_id, album_ids
                )
                removed_ids = [x for x in album_ids if x not in (failed_ids or [])]
                removed_count = len(removed_ids)
                if removed_count:
                    adjust_counter("favorited_count", -removed_count)
                    cache_remove_album_assets(FAV_ALBUM_NAME, removed_ids)
        except Exception:
            pass

    assets = snapshot_assets_to_items(raw_assets)
    if assets:
        store_current_round_from_items(count, filter_mode, assets)
    seen_repo.set_prev_ready(False)
    return RandomResponse(
        assets=assets,
        used_seen_fallback=False,
        message=None,
        total_returned=len(assets),
        requested=count,
        seen_count=seen_repo.get_counter("seen_count"),
        deleted_total=seen_repo.get_counter("deleted_count"),
        favorited_total=seen_repo.get_counter("favorited_count"),
        pages_used=None,
        total_pages_considered=None,
        total_assets=None,
        stats_debug=None,
        previous_available=False,
        is_previous=True,
        round_count=count,
        round_filter=filter_mode,
    )


@app.post("/api/reset-seen")
async def api_reset_seen() -> dict:
    # Delete seen album (do not delete assets) and reset local counters
    try:
        await immich_client.delete_album_by_name(SEEN_ALBUM_NAME)
    except Exception:
        pass
    clear_album_cache(SEEN_ALBUM_NAME)
    seen_repo.clear_current_round()
    seen_repo.clear_prev_round()
    seen_repo.clear_forward_round()
    _page_cache.clear()
    global _all_album_asset_cache
    _all_album_asset_cache = None
    seen_repo.reset_tracking()
    seen_repo.set_prev_ready(False)
    if hasattr(fetch_random_assets, "_last_page_cache"):
        setattr(fetch_random_assets, "_last_page_cache", None)
    return {"success": True}


@app.get("/thumb/{asset_id}")
async def thumb(asset_id: str):
    try:
        content, content_type = await immich_client.fetch_thumbnail_bytes(asset_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return StreamingResponse(iter([content]), media_type=content_type)


@app.get("/media/{asset_id}")
async def media(asset_id: str, request: Request):
    """
    Stream original media (used for GIF preview). Heavier than thumbnail.
    """
    try:
        range_header = request.headers.get("range")
        headers = {"Range": range_header} if range_header else {}
        resp = await immich_client.stream_original(asset_id, headers=headers)
    except Exception as exc:
        # fallback to thumbnail to avoid blank blocks on GIF failures
        try:
            content, content_type = await immich_client.fetch_thumbnail_bytes(asset_id)
            return StreamingResponse(iter([content]), media_type=content_type)
        except Exception:
            raise HTTPException(status_code=502, detail=str(exc))
    if resp.status_code >= 400:
        await resp.aclose()
        try:
            content, content_type = await immich_client.fetch_thumbnail_bytes(asset_id)
            return StreamingResponse(iter([content]), media_type=content_type)
        except Exception:
            raise HTTPException(status_code=resp.status_code, detail="原图不可用")

    async def iterator():
        async for chunk in resp.aiter_bytes():
            yield chunk
        await resp.aclose()

    media_type = resp.headers.get("content-type", "application/octet-stream")
    status_code = resp.status_code
    response = StreamingResponse(iterator(), media_type=media_type, status_code=status_code)
    for key in ["content-length", "content-range", "accept-ranges"]:
        if key in resp.headers:
            response.headers[key] = resp.headers[key]
    return response


@app.get("/api/debug/status", response_model=DebugStatus)
async def debug_status() -> DebugStatus:
    count, _, theme = load_ui_settings()
    if not IMMICH_BASE_URL:
        return DebugStatus(
            immich_base_url="(未配置)",
            api_key_configured=bool(IMMICH_API_KEY),
            grid_count=count,
            db_path=DB_PATH,
            ok=False,
            http_status=None,
            error="IMMICH_BASE_URL 未配置",
        )
    ok, status, error = await immich_client.get_server_info()
    return DebugStatus(
        immich_base_url=IMMICH_BASE_URL or "(未配置)",
        api_key_configured=bool(IMMICH_API_KEY),
        grid_count=count,
        db_path=DB_PATH,
        ok=ok,
        http_status=status,
        error=error,
    )


@app.get("/api/debug/logs", response_model=DebugLogResponse)
async def debug_logs() -> DebugLogResponse:
    logs = [
        DebugLogEntry(
            timestamp=log.timestamp,
            method=log.method,
            path=log.path,
            status=log.status,
            error=log.error,
            correlation_id=log.correlation_id,
            raw_error=log.raw_error,
        )
        for log in list(immich_client.logs)[::-1]
    ]
    return DebugLogResponse(logs=logs)


@app.get("/api/debug/db", response_model=DebugDBInfo)
async def debug_db() -> DebugDBInfo:
    err: Optional[str] = None
    try:
        path = ACTIVE_DB_PATH
        exists_active = os.path.exists(path)
        size = os.path.getsize(path) if exists_active else None
        mtime = (
            datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
            if exists_active
            else None
        )
    except Exception as exc:
        exists_active = False
        size = None
        mtime = None
        err = str(exc)
    config_exists = os.path.exists(DB_PATH)
    return DebugDBInfo(
        db_path=DB_PATH,
        active_db_path=ACTIVE_DB_PATH,
        exists_active=exists_active,
        exists_config=config_exists,
        size_bytes=size,
        mtime=mtime,
        error=err,
    )


@app.post("/api/debug/fix-db")
async def debug_fix_db() -> dict:
    """
    Try to create/touch DB file and init schema.
    """
    try:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(DB_PATH).touch(exist_ok=True)
        _repo = SeenRepository(DB_PATH)
        _repo.get_counter("seen_count")
        return {"success": True, "db_path": DB_PATH}
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(exc), "db_path": DB_PATH},
        )
