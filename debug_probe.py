import argparse
import json
import sys

import httpx


def pretty(obj: object) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def main() -> int:
    parser = argparse.ArgumentParser(description="Immich 局域网联通性与资产获取快速测试")
    parser.add_argument("--base-url", required=True, help="例如 http://192.168.31.173:8181")
    parser.add_argument("--api-key", required=True, help="Immich API Key")
    parser.add_argument("--take", type=int, default=9, help="拉取数量，默认 9")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    headers = {"x-api-key": args.api_key, "Content-Type": "application/json"}

    with httpx.Client(timeout=10) as client:
        # 健康检查：兼容旧版 /server-info
        health_paths = ["/api/server-info", "/server-info"]
        health_result = None
        for path in health_paths:
            try:
                r = client.get(f"{base}{path}", headers=headers)
                health_result = (path, r.status_code, r.text[:200])
                if r.status_code == 200:
                    break
            except Exception as exc:
                health_result = (path, None, str(exc))
        print("健康检查：", health_result)

        # 资产搜索：兼容 skip/take
        payload = {
            "take": args.take,
            "skip": 0,
            "isArchived": False,
            "isTrashed": False,
        }
        try:
            r = client.post(f"{base}/api/search/metadata", headers=headers, json=payload)
            print("search/metadata 状态：", r.status_code)
            data = r.json()
            items_field = data.get("items") or data.get("assets") or data.get("data") or []
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
            items = [itm for itm in items_field if isinstance(itm, dict)]
            print(f"返回 items 数量：{len(items)}")
            if items:
                sample = items[: min(3, len(items))]
                print("示例：", pretty(sample))
            else:
                print("返回数据：", pretty(data))
        except Exception as exc:
            print("search/metadata 调用失败：", exc)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
