import argparse
from pathlib import Path
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="快速测试 Immich 缩略图是否可获取")
    parser.add_argument("--base-url", required=True, help="Immich 基础地址，如 http://192.168.31.173:8181")
    parser.add_argument("--api-key", required=True, help="Immich API Key")
    parser.add_argument("--asset-id", help="指定 assetId，留空则自动取 searchMetadata 第一条")
    parser.add_argument("--out", default="thumb_test.jpg", help="输出文件名")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    headers = {"x-api-key": args.api_key, "Accept": "image/*"}

    with httpx.Client(timeout=15, follow_redirects=True) as client:
        asset_id = args.asset_id
        if not asset_id:
            payload = {"take": 1, "skip": 0, "isArchived": False, "isTrashed": False}
            r = client.post(f"{base}/api/search/metadata", headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            items = data.get("items") or data.get("assets") or data.get("data") or []
            if isinstance(items, dict):
                items = items.get("items") or list(items.values())
            if not items:
                print("searchMetadata 未返回任何资产")
                return 1
            asset_id = items[0]["id"]
            print("自动选取 assetId:", asset_id)

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
        last_status = None
        for path in paths:
            url = f"{base}{path}"
            try:
                resp = client.get(url, headers=headers)
            except Exception as exc:
                print(f"请求 {url} 失败: {exc}")
                continue
            last_status = resp.status_code
            print(f"{url} -> HTTP {resp.status_code}")
            if resp.status_code < 400 and resp.content:
                out_path = Path(args.out)
                out_path.write_bytes(resp.content)
                print(f"已保存缩略图到 {out_path.resolve()} (大小 {out_path.stat().st_size} 字节)")
                return 0
        print("未能成功获取缩略图，最后状态码：", last_status)
        return 1


if __name__ == "__main__":
    sys.exit(main())
