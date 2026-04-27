import asyncio
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

# Immich API reference:
# - deleteAssets: https://api.immich.app/endpoints/assets/deleteAssets
# - searchMetadata: https://api.immich.app/endpoints/search/searchMetadata
# - server-info: https://api.immich.app/endpoints/server-info


@dataclass
class APILogEntry:
    timestamp: str
    method: str
    path: str
    status: Optional[int]
    error: Optional[str] = None
    correlation_id: Optional[str] = None
    raw_error: Optional[Dict[str, Any]] = None


class ImmichClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        log_size: int = 20,
        min_interval_ms: int = 0,
        max_concurrency: int = 6,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None
        self.logs: deque[APILogEntry] = deque(maxlen=log_size)
        self._min_interval = max(0, min_interval_ms) / 1000.0
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))

    @staticmethod
    def _truncate(text: Optional[str], max_len: int = 240) -> Optional[str]:
        if text is None:
            return None
        if len(text) <= max_len:
            return text
        return text[: max_len - 3] + "..."

    @staticmethod
    def _sanitize_error_obj(error_obj: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not isinstance(error_obj, dict):
            return None
        keep_keys = ("message", "error", "statusCode", "correlationId")
        sanitized = {k: error_obj.get(k) for k in keep_keys if k in error_obj}
        return sanitized or None

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._rate_lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15, follow_redirects=True)
        return self._client

    async def _log(
        self,
        *,
        method: str,
        path: str,
        status: Optional[int],
        error: Optional[str],
        correlation_id: Optional[str],
        raw_error: Optional[Dict[str, Any]] = None,
    ) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_error = self._truncate(error, 240)
        safe_raw = self._sanitize_error_obj(raw_error)
        self.logs.append(
            APILogEntry(
                timestamp=ts,
                method=method,
                path=path,
                status=status,
                error=safe_error,
                correlation_id=correlation_id,
                raw_error=safe_raw,
            )
        )

    async def _request(
        self, method: str, path: str, *, stream: bool = False, **kwargs: Any
    ) -> httpx.Response:
        client = await self._get_client()
        url = f"{self.base_url}{path}"
        headers = kwargs.pop("headers", {}) or {}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        headers.setdefault("Accept", "*/*")
        try:
            async with self._semaphore:
                await self._throttle()
                if stream:
                    response = await client.stream(method, url, headers=headers, **kwargs)
                else:
                    response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.RequestError as exc:  # Network or DNS error
            await self._log(
                method=method,
                path=path,
                status=None,
                error=str(exc),
                correlation_id=None,
                raw_error=None,
            )
            raise

        correlation_id = None
        error_message = None
        error_obj: Optional[Dict[str, Any]] = None
        if response.status_code >= 400:
            try:
                error_obj = response.json()
                correlation_id = error_obj.get("correlationId")
                message = error_obj.get("message") or error_obj.get("error")
                if isinstance(message, list):
                    message = "; ".join(map(str, message))
                error_message = (
                    f"{message or 'Immich API error'} (status {response.status_code})"
                )
            except Exception:
                error_message = f"Immich API error (status {response.status_code})"
                error_obj = None

        await self._log(
            method=method,
            path=path,
            status=response.status_code,
            error=error_message,
            correlation_id=correlation_id,
            raw_error=error_obj,
        )

        if response.status_code >= 400 and not stream:
            response.raise_for_status()

        return response

    async def get_server_info(self) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Hit Immich server-info endpoint for health check.
        Try `/api/server-info` first (newer path), then fallback to `/server-info` for older deployments.
        Returns: (ok, status_code, error_message)
        """
        paths = ["/api/server-info", "/server-info"]
        last_error: Optional[str] = None
        last_status: Optional[int] = None
        for path in paths:
            try:
                response = await self._request("GET", path)
                return response.is_success, response.status_code, None
            except httpx.HTTPStatusError as exc:
                last_status = exc.response.status_code if exc.response else None
                last_error = str(exc)
                # try next path
            except Exception as exc:
                last_error = str(exc)
        return False, last_status, last_error

    async def get_asset_statistics(self) -> Tuple[Optional[int], Optional[str]]:
        """
        Fetch asset statistics to get total asset count.
        Endpoint: /api/asset/statistics or /api/assets/statistics (permission: asset.statistics)
        Returns (total asset count, debug message) or (None, debug)
        """
        paths = ["/api/asset/statistics", "/asset/statistics", "/api/assets/statistics", "/assets/statistics"]
        for path in paths:
            try:
                resp = await self._request("GET", path)
                data = resp.json()
                if isinstance(data, dict):
                    total = data.get("total")
                    if total is not None:
                        return int(total), f"{path} -> total={total}"
                    return None, f"{path} -> no total, keys={list(data.keys())}"
            except httpx.HTTPStatusError:
                continue
            except Exception:
                continue
        return None, None

    async def search_assets(
        self, page: int = 1, size: int = 200, skip_override: Optional[int] = None
    ) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        """
        Use the documented searchMetadata endpoint to fetch assets.
        Reference: https://api.immich.app/endpoints/search/searchMetadata
        """
        skip = skip_override if skip_override is not None else (page - 1) * size
        # Send both page/size and skip/take to be compatible with older/newer Immich versions
        body = {
            "page": page,
            "size": size,
            "skip": skip,
            "take": size,
            "isArchived": False,
            "isTrashed": False,
        }
        response = await self._request("POST", "/api/search/metadata", json=body)
        data = response.json()

        # Handle legacy list shapes like [count, count, [items]]
        items_field: Any
        total_count: Optional[int] = None
        if isinstance(data, list):
            if len(data) >= 1 and isinstance(data[0], (int, float)):
                total_count = int(data[0])
            items_field = data[-1] if data else []
        else:
            items_field = data.get("items") or data.get("assets") or data.get("data") or []
            total_count = (
                data.get("totalCount")
                or data.get("count")
                or data.get("total")
                or total_count
            )

        if isinstance(items_field, dict):
            nested = (
                items_field.get("items")
                or items_field.get("assets")
                or items_field.get("data")
            )
            if nested is not None:
                items_field = nested
            else:
                items_field = list(items_field.values())
        if not isinstance(items_field, list):
            items_field = [items_field]

        normalized: List[Dict[str, Any]] = []
        for item in items_field:
            if not isinstance(item, dict):
                continue
            normalized.append(item)

        try:
            total_count = int(total_count) if total_count is not None else None
        except Exception:
            total_count = None
        if total_count is None:
            total_count = len(normalized) if normalized else None

        return normalized, total_count

    async def delete_assets(self, ids: List[str]) -> Dict[str, Any]:
        """
        Move assets to Immich trash via deleteAssets.
        Reference: https://api.immich.app/endpoints/assets/deleteAssets
        """
        payload = {"ids": ids, "force": False}
        response = await self._request("DELETE", "/api/assets", json=payload)
        return response.json() if response.content else {"status": "ok"}

    async def restore_assets(self, ids: List[str]) -> Tuple[int, List[str]]:
        """
        Restore assets from trash.
        Tries multiple endpoints for compatibility.
        """
        if not ids:
            return 0, []
        payloads = [{"ids": ids}, {"assetIds": ids}]
        paths = [
            "/api/assets/restore",
            "/api/assets/restoreAssets",
            "/api/assets/restore",
            "/api/assets/untrash",
        ]
        for path in paths:
            for body in payloads:
                try:
                    resp = await self._request("POST", path, json=body)
                    data = resp.json() if resp.content else {}
                    failed = []
                    if isinstance(data, dict):
                        failed = (
                            data.get("failedIds")
                            or data.get("failedAssetIds")
                            or data.get("failed")
                            or []
                        )
                    return len(ids) - len(failed), [str(x) for x in failed]
                except httpx.HTTPStatusError:
                    continue
                except Exception:
                    continue
        raise RuntimeError("Failed to restore assets")

    async def ensure_album(self, name: str) -> str:
        """
        Ensure album exists, return albumId.
        Create if missing.
        Reference: https://api.immich.app/endpoints/albums/createAlbum
        """
        # Try to list albums and find by name
        try:
            r = await self._request("GET", "/api/albums")
            data = r.json()
            if isinstance(data, list):
                garbled_id: Optional[str] = None
                for album in data:
                    if not isinstance(album, dict):
                        continue
                    if album.get("albumName") == name:
                        return album.get("id")
        except Exception:
            pass

        # Create
        body = {"albumName": name, "assetIds": []}
        r = await self._request("POST", "/api/albums", json=body)
        data = r.json()
        album_id = data.get("id")
        if not album_id:
            raise RuntimeError("Failed to create album")
        return album_id

    async def add_assets_to_album(self, album_id: str, ids: List[str]) -> None:
        """
        Add assets to album.
        Reference: https://api.immich.app/endpoints/albums/addAssetsToAlbum
        """
        if not ids:
            return
        body = {"ids": ids}
        paths = [
            f"/api/albums/{album_id}/assets",
            f"/api/album/{album_id}/assets",
            f"/api/albums/{album_id}/asset",
        ]
        for path in paths:
            try:
                await self._request("PUT", path, json=body)
                return
            except Exception:
                continue
        raise RuntimeError("Failed to add assets to album")

    async def remove_assets_from_album(self, album_id: str, ids: List[str]) -> List[str]:
        """
        Remove assets from album.
        """
        if not ids:
            return []
        body = {"ids": ids}
        paths = [
            f"/api/albums/{album_id}/assets",
            f"/api/album/{album_id}/assets",
            f"/api/albums/{album_id}/asset",
        ]
        for path in paths:
            try:
                resp = await self._request("DELETE", path, json=body)
                data = resp.json() if resp.content else {}
                failed = []
                if isinstance(data, dict):
                    failed = (
                        data.get("failedIds")
                        or data.get("failedAssetIds")
                        or data.get("failed")
                        or []
                    )
                return [str(x) for x in failed]
            except httpx.HTTPStatusError:
                continue
            except Exception:
                continue
        raise RuntimeError("Failed to remove assets from album")

    async def list_albums(self) -> List[Dict[str, Any]]:
        response = await self._request("GET", "/api/albums")
        data = response.json()
        if isinstance(data, list):
            return [a for a in data if isinstance(a, dict)]
        return []

    async def find_album_by_name(self, name: str) -> Optional[str]:
        albums = await self.list_albums()
        for album in albums:
            if album.get("albumName") == name:
                return album.get("id")
        return None

    async def rename_album(self, album_id: str, new_name: str) -> None:
        """
        Rename album to the correct name to fix garbled titles.
        """
        body = {"albumName": new_name}
        paths = [f"/api/albums/{album_id}", f"/api/album/{album_id}"]
        last_error: Optional[Exception] = None
        for path in paths:
            try:
                await self._request("PUT", path, json=body)
                return
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error

    async def delete_album_by_id(self, album_id: str) -> None:
        """
        Delete an album (does not delete assets).
        """
        paths = [f"/api/albums/{album_id}", f"/api/album/{album_id}"]
        last_error: Optional[Exception] = None
        for path in paths:
            try:
                await self._request("DELETE", path)
                return
            except Exception as exc:
                last_error = exc
                continue
        if last_error:
            raise last_error

    async def delete_album_by_name(self, name: str) -> bool:
        """
        Delete album by name if exists. Returns True if deleted or not found.
        """
        album_id = await self.find_album_by_name(name)
        if not album_id:
            return True
        try:
            await self.delete_album_by_id(album_id)
            return True
        except Exception:
            return False

    async def list_album_asset_ids(self, album_id: str) -> List[str]:
        """
        Get asset ids in an album.
        """
        paths = [f"/api/albums/{album_id}", f"/api/album/{album_id}"]
        for path in paths:
            try:
                resp = await self._request("GET", path)
                data = resp.json()
                items = data.get("assets") or data.get("albumAssets") or []
                ids: List[str] = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    aid = it.get("id") or (it.get("asset") or {}).get("id")
                    if aid:
                        ids.append(str(aid))
                return ids
            except Exception:
                continue
        return []

    async def list_all_album_asset_ids(self) -> List[str]:
        ids: List[str] = []
        albums = await self.list_albums()
        for alb in albums:
            aid = alb.get("id")
            if not aid:
                continue
            try:
                ids.extend(await self.list_album_asset_ids(aid))
            except Exception:
                continue
        return ids

    async def list_assets(self, skip: int = 0, take: int = 500) -> List[Dict[str, Any]]:
        """
        Fallback asset list when searchMetadata returns empty.
        Uses GET /api/assets (stable list endpoint).
        Reference: https://api.immich.app/endpoints/assets/getAssets
        """
        params = {
            "isArchived": "false",
            "isTrashed": "false",
            "skip": str(skip),
            "take": str(take),
        }
        try:
            response = await self._request("GET", "/api/assets", params=params)
            data = response.json()
            if isinstance(data, dict):
                items = data.get("assets") or data.get("items") or data.get("data") or []
            else:
                items = data
            normalized: List[Dict[str, Any]] = []
            for item in items:
                if isinstance(item, dict):
                    normalized.append(item)
            return normalized
        except httpx.HTTPStatusError as exc:
            # Some deployments may not expose GET /api/assets; fall back to searchMetadata semantics
            if exc.response is not None and exc.response.status_code == 404:
                assets, _ = await self.search_assets(page=1, size=take)
                return assets
            raise

    async def stream_original(
        self, asset_id: str, *, headers: Optional[Dict[str, str]] = None
    ) -> httpx.Response:
        """
        Stream original asset (for gif/video preview when requested).
        Reference: https://api.immich.app/endpoints/assets/downloadFile
        """
        hdrs = headers or {}
        hdrs.setdefault("Accept", "image/gif,image/*,video/*,*/*")
        paths = [
            f"/api/assets/{asset_id}/original",
            f"/api/assets/{asset_id}/original?download=true",
            f"/api/assets/{asset_id}/download",
            f"/api/asset/download/{asset_id}",
            f"/api/asset/original/{asset_id}",
            f"/api/asset/file/{asset_id}",
        ]
        last_exc: Optional[Exception] = None
        for path in paths:
            try:
                resp = await self._request("GET", path, stream=True, headers=hdrs)
                if resp.status_code < 400:
                    return resp
                await resp.aclose()
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc:
            raise last_exc
        raise RuntimeError("Unable to fetch original asset")

    async def stream_thumbnail(self, asset_id: str) -> httpx.Response:
        """
        Proxy thumbnail from Immich cache.
        Reference: https://api.immich.app/endpoints/assets/getAssetThumbnail
        Tries multiple query variants to improve compatibility.
        """
        paths = [
            f"/api/assets/{asset_id}/thumbnail?size=preview&format=webp",
            f"/api/assets/{asset_id}/thumbnail?size=preview&format=jpeg",
            f"/api/assets/{asset_id}/thumbnail?size=thumbnail&format=webp",
            f"/api/assets/{asset_id}/thumbnail?size=thumbnail&format=jpeg",
            f"/api/assets/{asset_id}/thumbnail?isWebp=false",
            f"/api/assets/{asset_id}/thumbnail",
            # Legacy paths (older Immich):
            f"/api/asset/thumbnail/{asset_id}",
            f"/api/asset/thumbnail/{asset_id}?isWebp=false",
        ]
        headers = {"Accept": "image/*"}
        last_exc: Optional[Exception] = None
        for path in paths:
            try:
                resp = await self._request("GET", path, stream=True, headers=headers)
                if resp.status_code < 400:
                    return resp
                await resp.aclose()
            except Exception as exc:
                last_exc = exc
                continue
        if last_exc:
            raise last_exc
        raise RuntimeError("Unable to fetch thumbnail")

    async def fetch_thumbnail_bytes(self, asset_id: str) -> Tuple[bytes, str]:
        """
        Fetch thumbnail as bytes (non-stream) to avoid client disconnect issues.
        Returns (content, content_type)
        """
        paths = [
            f"/api/assets/{asset_id}/thumbnail?size=preview&format=jpeg",
            f"/api/assets/{asset_id}/thumbnail?size=preview&format=webp",
            f"/api/assets/{asset_id}/thumbnail?size=thumbnail&format=jpeg",
            f"/api/assets/{asset_id}/thumbnail?size=thumbnail&format=webp",
            f"/api/assets/{asset_id}/thumbnail?isWebp=false",
            f"/api/assets/{asset_id}/thumbnail",
            f"/api/asset/thumbnail/{asset_id}",
            f"/api/asset/thumbnail/{asset_id}?isWebp=false",
        ]
        headers = {"Accept": "image/*"}
        last_error: Optional[str] = None
        for path in paths:
            try:
                resp = await self._request("GET", path, headers=headers)
            except Exception as exc:
                last_error = str(exc)
                continue
            if resp.status_code < 400 and resp.content:
                return resp.content, resp.headers.get("content-type", "image/jpeg")
            last_error = f"HTTP {resp.status_code}"
        raise RuntimeError(f"Thumbnail fetch failed: {last_error or 'unknown error'}")

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
