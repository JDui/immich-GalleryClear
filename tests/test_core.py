import asyncio
import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx


_IMPORT_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("DB_PATH", str(Path(_IMPORT_TMP.name) / "import.db"))
os.environ.setdefault("IMMICH_BASE_URL", "http://immich.test")
os.environ.setdefault("IMMICH_API_KEY", "test-key")

from app import main
from app.immich_client import ImmichClient
from app.models import SeenRepository


def asset(asset_id: str) -> dict:
    return {
        "id": asset_id,
        "originalFileName": f"{asset_id}.jpg",
        "originalMimeType": "image/jpeg",
        "type": "IMAGE",
    }


class FakeImmichClient:
    def __init__(self, pages: dict[int, list[dict]], total: int | None) -> None:
        self.pages = pages
        self.total = total
        self.search_calls: list[int] = []
        self.seen_additions: list[str] = []

    async def search_assets(self, page: int = 1, size: int = 200):
        self.search_calls.append(page)
        return list(self.pages.get(page, [])), self.total

    async def add_assets_to_album(self, album_id: str, ids: list[str]):
        self.seen_additions.extend(ids)
        return [], list(ids)


class RepositoryTests(unittest.TestCase):
    def test_exhaustion_is_isolated_by_filter_and_counter_is_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SeenRepository(str(Path(tmp) / "state.db"))
            repo.mark_page_exhausted(7, "exclude-prescreen")

            self.assertEqual(repo.get_exhausted_pages("exclude-prescreen"), {7})
            self.assertEqual(repo.get_exhausted_pages("exclude-albums"), set())
            self.assertEqual(repo.incr_counter("seen_count", 5), 5)
            self.assertEqual(repo.incr_counter("seen_count", -9), 0)


class SamplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_count_larger_than_page_size_is_filled_across_pages(self):
        fake = FakeImmichClient(
            {
                1: [asset("a1"), asset("a2")],
                2: [asset("b1"), asset("b2")],
            },
            total=4,
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = SeenRepository(str(Path(tmp) / "state.db"))
            main._page_cache.clear()
            with (
                patch.object(main, "PAGE_SIZE", 2),
                patch.object(main, "seen_repo", repo),
                patch.object(main, "immich_client", fake),
                patch.object(
                    main,
                    "get_album_id_cached",
                    AsyncMock(return_value="seen-album"),
                ),
            ):
                result = await main.fetch_random_assets(3, "all")

        self.assertEqual(result.total_returned, 3)
        self.assertEqual(len({item.id for item in result.assets}), 3)
        self.assertGreaterEqual(len(set(fake.search_calls)), 2)
        self.assertFalse(result.used_seen_fallback)

    async def test_excluded_page_does_not_trigger_early_seen_fallback(self):
        fake = FakeImmichClient(
            {
                1: [asset("seen-1"), asset("seen-2")],
                2: [asset("new-1"), asset("new-2")],
            },
            total=4,
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = SeenRepository(str(Path(tmp) / "state.db"))
            main._page_cache.clear()
            with (
                patch.object(main, "PAGE_SIZE", 2),
                patch.object(main, "seen_repo", repo),
                patch.object(main, "immich_client", fake),
                patch.object(
                    main,
                    "load_excluded_ids",
                    AsyncMock(return_value={"seen-1", "seen-2"}),
                ),
                patch.object(main, "random_page_order", return_value=[1, 2]),
                patch.object(
                    main,
                    "get_album_id_cached",
                    AsyncMock(return_value="seen-album"),
                ),
            ):
                result = await main.fetch_random_assets(2, "exclude-prescreen")

        self.assertEqual({item.id for item in result.assets}, {"new-1", "new-2"})
        self.assertFalse(result.used_seen_fallback)

    async def test_page_probe_can_detect_more_than_1024_pages(self):
        fake = FakeImmichClient(
            {page: [asset(f"asset-{page}")] for page in range(1, 1101)},
            total=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = SeenRepository(str(Path(tmp) / "state.db"))
            main._page_cache.clear()
            with (
                patch.object(main, "PAGE_SIZE", 1),
                patch.object(main, "seen_repo", repo),
                patch.object(main, "immich_client", fake),
                patch.object(
                    main,
                    "get_album_id_cached",
                    AsyncMock(return_value="seen-album"),
                ),
            ):
                result = await main.fetch_random_assets(1, "all")

        self.assertEqual(result.total_pages_considered, 1100)
        self.assertEqual(result.total_returned, 1)

    async def test_filter_lookup_failure_is_not_silently_ignored(self):
        fake = FakeImmichClient({1: [asset("a1")]}, total=1)
        with tempfile.TemporaryDirectory() as tmp:
            repo = SeenRepository(str(Path(tmp) / "state.db"))
            main._page_cache.clear()
            with (
                patch.object(main, "seen_repo", repo),
                patch.object(main, "immich_client", fake),
                patch.object(
                    main,
                    "load_excluded_ids",
                    AsyncMock(side_effect=RuntimeError("album unavailable")),
                ),
            ):
                with self.assertRaises(main.HTTPException) as raised:
                    await main.fetch_random_assets(1, "exclude-prescreen")

        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("album unavailable", raised.exception.detail)

    def test_first_page_is_chosen_uniformly_without_tail_weighting(self):
        random.seed(20260807)
        last_page_first = 0
        trials = 10_000
        for _ in range(trials):
            order = main.random_page_order({1, 2})
            last_page_first += order[0] == 2

        ratio = last_page_first / trials
        self.assertGreater(ratio, 0.48)
        self.assertLess(ratio, 0.52)


class ClientConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_album_without_embedded_assets_falls_back_to_search(self):
        client = ImmichClient("http://immich.test", "test-key")
        album_response = httpx.Response(
            200,
            json={"id": "album", "albumName": "A", "assetCount": 2},
            request=httpx.Request("GET", "http://immich.test/api/albums/album"),
        )
        search_response = httpx.Response(
            200,
            json={
                "assets": {
                    "items": [asset("one"), asset("two")],
                    "count": 2,
                    "total": 2,
                    "nextPage": None,
                }
            },
            request=httpx.Request("POST", "http://immich.test/api/search/metadata"),
        )
        client._request = AsyncMock(side_effect=[album_response, search_response])

        ids = await client.list_album_asset_ids("album")

        self.assertEqual(ids, ["one", "two"])
        search_call = client._request.await_args_list[1]
        self.assertEqual(search_call.kwargs["json"]["albumIds"], ["album"])

    async def test_nested_search_response_uses_server_total(self):
        client = ImmichClient("http://immich.test", "test-key")
        response = httpx.Response(
            200,
            json={
                "assets": {
                    "items": [asset("one")],
                    "count": 1,
                    "total": 123,
                    "nextPage": "2",
                }
            },
            request=httpx.Request("POST", "http://immich.test/api/search/metadata"),
        )
        client._request = AsyncMock(return_value=response)

        items, total = await client.search_assets(page=1, size=20)

        self.assertEqual([item["id"] for item in items], ["one"])
        self.assertEqual(total, 123)

    async def test_duplicate_album_membership_is_not_reported_as_failure(self):
        client = ImmichClient("http://immich.test", "test-key")
        response = httpx.Response(
            200,
            json=[
                {"id": "existing", "success": False, "error": "duplicate"},
                {"id": "new", "success": True},
            ],
            request=httpx.Request("PUT", "http://immich.test/api/albums/1/assets"),
        )
        client._request = AsyncMock(return_value=response)

        failed, added = await client.add_assets_to_album(
            "album", ["existing", "new"]
        )

        self.assertEqual(failed, [])
        self.assertEqual(added, ["new"])

    async def test_album_assets_are_loaded_concurrently(self):
        client = ImmichClient("http://immich.test", "test-key", max_concurrency=3)
        active = 0
        peak = 0

        async def list_albums():
            return [{"id": str(index)} for index in range(4)]

        async def list_ids(album_id: str):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return [f"asset-{album_id}"]

        client.list_albums = list_albums
        client.list_album_asset_ids = list_ids
        ids = await client.list_all_album_asset_ids()

        self.assertEqual(set(ids), {f"asset-{index}" for index in range(4)})
        self.assertGreater(peak, 1)


if __name__ == "__main__":
    unittest.main()
