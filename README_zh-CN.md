# Immich 随机图片清理器

一个基于 Immich API 的随机图库筛选工具。它会在浏览器里随机展示 Immich 中的照片/图片，你可以快速标记“删除”或“收藏到相册”，点击下一轮后批量执行，适合给图库做轻量清理和预筛。

本工具不会硬删除原始文件。“删除”动作调用的是 Immich 的移入回收站接口，最终是否永久删除由 Immich 自身的回收站策略决定。

## 功能特性

- 随机抽取 Immich 图片，默认展示 6 张，可在页面中切换 3 / 4 / 6 / 9 / 12 / 25 或自定义数量。
- 每轮先等概率随机一个页码，再在该页内随机选图；仅当单页候选不足时才继续扫描其他随机页，全部候选页都不足时才使用已看图片补位。
- 点击图片循环切换标记：待删除 -> 收藏到相册 -> 无标记。
- 点击“下一轮（执行标记）”后，待删除图片会移入 Immich 回收站，收藏图片会加入 `iCCollection` 相册。
- 自动维护预筛记录相册 `immichClearSeen`，默认过滤已经预筛过的图片，减少重复出现。
- 支持过滤模式：所有图片、预筛相册除外、所有相册照片除外。
- 页面耗尽状态按过滤模式隔离；删除、恢复或相册缓存变化时会自动失效，避免跳过仍可筛选的图片。
- 支持返回上一轮，并尽量回滚上一轮已经执行的删除/收藏动作。
- 支持 GIF 原图预览，缩略图失败时会回退显示占位提示。
- 内置调试视窗，可查看 Immich 连接状态、最近 API 调用日志、数据库文件状态，并尝试修复数据库。
- 使用 SQLite 保存 UI 设置、当前轮次、上一轮记录、计数器和页面扫描状态。

## 部署前准备

1. 已有可访问的 Immich 服务。
2. 在 Immich Web 界面中为当前用户生成 API Key。
3. 确认本容器能访问到 Immich 地址，例如同一 Docker 网络内的 `http://immich-server:2283`，或局域网地址 `http://192.168.1.10:2283`。
4. 准备一个可写的数据目录挂载到容器的 `/app/data`，用于持久化 SQLite 数据库。

## Docker Compose 范例

将下面内容保存为 `docker-compose.yml`，按实际环境修改 `IMMICH_BASE_URL`、`IMMICH_API_KEY` 和数据目录。

```yaml
version: "3.8"

services:
  immich-random-cleaner:
    image: immich-random-cleaner:0.7.29
    container_name: immich-random-cleaner
    restart: unless-stopped
    ports:
      - "8787:8000"
    environment:
      IMMICH_BASE_URL: "http://immich-server:2283"
      IMMICH_API_KEY: "请替换为你的 Immich API Key"
      DB_PATH: "/app/data/random_cleaner.db"
      ALLOW_DB_FALLBACK: "true"
      IMMICH_LOG_SIZE: "50"
      IMMICH_MIN_INTERVAL_MS: "10"
      IMMICH_MAX_CONCURRENCY: "10"
    volumes:
      - "./data:/app/data"
```

启动：

```bash
docker compose up -d
```

访问：

```text
http://你的服务器IP:8787/
```

如果本工具和 Immich 不在同一个 Docker 网络里，`IMMICH_BASE_URL` 不要写容器名，请改成宿主机或反向代理可访问的地址。

## 使用本仓库构建镜像

```bash
docker build -t immich-random-cleaner:0.7.29 .
```

如需导出镜像到其他机器：

```bash
docker save immich-random-cleaner:0.7.29 -o immich-random-cleaner-0.7.29.tar
```

导入：

```bash
docker load -i immich-random-cleaner-0.7.29.tar
```

本仓库也提供了示例文件 [docker-compose.example.jks.yml](./docker-compose.example.jks.yml)，可直接复制后修改。

## 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IMMICH_BASE_URL` | 空 | 必填。Immich 服务基础地址，不要以 `/` 结尾。 |
| `IMMICH_API_KEY` | 空 | 必填。Immich API Key。 |
| `DB_PATH` | `/app/data/random_cleaner.db` | SQLite 数据库路径。若值以 `/` 结尾，会自动追加 `random_cleaner.db`。 |
| `ALLOW_DB_FALLBACK` | `true` | 当 `DB_PATH` 不可写时，是否临时回退到 `/tmp/random_cleaner.db`。回退路径不适合长期持久化。 |
| `IMMICH_LOG_SIZE` | `50` | 调试面板保留的最近 Immich API 调用日志数量。 |
| `IMMICH_MIN_INTERVAL_MS` | `10` | 调用 Immich API 的最小间隔，单位毫秒。 |
| `IMMICH_MAX_CONCURRENCY` | `10` | 调用 Immich API 的最大并发数。 |
| `ALBUM_CACHE_TTL_SEC` | `60` | 单个相册资源缓存时间。`0` 表示进程内长期有效。 |
| `ALBUM_ID_CACHE_TTL_SEC` | `300` | 相册 ID 缓存时间。 |
| `ALL_ALBUM_CACHE_TTL_SEC` | `60` | “所有相册照片除外”模式下的全相册资源缓存时间。 |
| `PAGES_CACHE_TTL_SEC` | `300` | Immich 资源页数缓存时间。 |
| `MAX_GRID_COUNT` | `500` | 单轮允许请求的最大图片数量；超过单页大小时会自动跨页补齐。 |
| `PAGE_PROBE_LIMIT` | `1048576` | Immich 未返回总数时，页数探测的安全上限。 |
| `SEARCH_MAX_PAGES` | `30` | 兼容旧配置，目前页面仅展示该配置值。 |
| `DEBUG_MODE` | `false` | 调试模式标记，会显示在页面配置中。 |

## 页面操作说明

- 页面加载后会自动抽取一轮随机图片。
- 点击单张图片会依次切换为“待删除”“收藏到相册”“无标记”。
- 鼠标滚轮停在图片上可放大/缩小预览，支持 1.5x / 2x / 3x。
- “下一轮（执行标记）”会执行当前标记，并抽取下一轮。
- “重新抽一组”会放弃当前轮次并重新抽取，不执行当前标记。
- “返回上一轮”会恢复上一轮界面，并尽量撤回上一轮的删除/收藏动作。
- “重置已看记录”会删除 `immichClearSeen` 相册并清空本地筛选进度，不会删除任何照片。
- 数量、过滤模式和主题会保存到 SQLite，容器重启后仍会保留。

## 相册与过滤逻辑

- `immichClearSeen`：本工具自动维护的预筛记录相册。图片被抽到后会加入该相册，用来判断“预筛过图片除外”。
- `iCCollection`：点击图片标记为收藏后，执行下一轮时会加入该相册。
- “所有图片”：不排除已预筛或相册内图片。
- “预筛相册除外”：排除 `immichClearSeen` 和 `iCCollection` 中的图片。
- “所有相册照片除外”：排除任意 Immich 相册中已经存在的图片。

## 数据库与权限

建议始终挂载 `/app/data`：

```yaml
volumes:
  - "./data:/app/data"
```

容器内进程使用非 root 用户运行。若使用宿主机绝对路径挂载，请确保该目录对容器内用户可写。数据库异常时可打开页面右上角 `Debug`，查看“数据库文件”状态并点击“修复数据库”。

如果 `DB_PATH` 不可写且 `ALLOW_DB_FALLBACK=true`，程序会回退到 `/tmp/random_cleaner.db` 保持可用，但容器重建后这部分数据可能丢失。

## 常见问题

**页面提示 `IMMICH_BASE_URL 或 IMMICH_API_KEY 未配置`**

检查 compose 中两个变量是否已填写，并重建/重启容器。

**API Key 无效或返回 401**

在 Immich 中重新生成 API Key，确认使用的是拥有目标照片访问权限的用户。

**网络错误、连接超时或 502**

确认本容器可以访问 `IMMICH_BASE_URL`。同一 Docker 网络可使用 Immich 容器名；跨主机或跨网络时请使用 IP、域名或反向代理地址。

**抽不到图片或提示已经结束筛选**

可能当前过滤模式下已经没有可选图片。可以切换到“所有图片”，或点击“重置已看记录”重新开始。

**缩略图或 GIF 打不开**

打开 `Debug` 查看最近 API 调用日志，确认 Immich 的缩略图、原图下载接口是否可访问。

**误点删除怎么办**

本工具只是移入 Immich 回收站。可在 Immich 回收站中恢复；若刚刚执行的是上一轮，也可以先尝试点击“返回上一轮”。

## 注意事项

- 建议先用少量图片测试 API Key、网络和回收站行为，再进行大批量筛选。
- 不要把 `IMMICH_API_KEY` 提交到公开仓库。
- 如果 Immich API 在未来版本中变更，可能需要同步更新本工具的接口兼容逻辑。
