# CosplayTele MCP

面向 CosplayTele 及同类图库站点的 MCP 2（Model Context Protocol，规范 2026-07-28）服务器。

需要 Python 3.14+。已发布：[cosplaytele-mcp](https://pypi.org/project/cosplaytele-mcp/)。

## 安装

```bash
uvx cosplaytele-mcp
```

`uvx` 会临时拉取包并走 stdio 启动，适合直接接到宿主。持久安装：

```bash
uv tool install cosplaytele-mcp
# 或
pip install cosplaytele-mcp
```

装完后命令是 `cosplaytele-mcp`。

## 接入宿主

推荐用 `uvx`，不必克隆仓库。若宿主找不到 `uvx`，把 `command` 换成 `uvx` 的绝对路径（常见是 `~/.local/bin/uvx`）。

Claude Desktop / Cursor（`mcpServers`）：

```json
{
  "mcpServers": {
    "cosplaytele": {
      "command": "uvx",
      "args": ["cosplaytele-mcp"]
    }
  }
}
```

已用 `uv tool install` 或 `pip install` 时：

```json
{
  "mcpServers": {
    "cosplaytele": {
      "command": "cosplaytele-mcp"
    }
  }
}
```

VS Code `.vscode/mcp.json`：

```json
{
  "servers": {
    "cosplaytele": {
      "type": "stdio",
      "command": "uvx",
      "args": ["cosplaytele-mcp"]
    }
  }
}
```

## 运行

stdio（给宿主用）：

```bash
uvx cosplaytele-mcp
# 或已安装后
cosplaytele-mcp
```

HTTP：

```bash
uvx cosplaytele-mcp --transport streamable-http --port 8000
# 或
cosplaytele-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

对外绑定时必须显式列出可信的浏览器 Origin：

```bash
cosplaytele-mcp --transport streamable-http --host 0.0.0.0 --port 8000 \
  --allowed-host mcp.example.com \
  --allowed-origin https://mcp.example.com
```

`--host` 只是本地监听地址；`--allowed-host` 必须填写反向代理转发的 Host（可重复指定，
也可使用 `example.com:*` 匹配端口）。非 localhost 监听时，`--allowed-host` 和
`--allowed-origin` 都是必填项。生产环境还应在反向代理或网关启用 HTTPS、认证、限流和审计。
本项目只公开现代 Streamable HTTP；旧版 HTTP+SSE 已从 CLI 移除。

`popular_kind=archive` 表示源站的分类或默认归档，不表示按统计热度排名。`ranking_kinds`
与内容 `categories` 分开列出。

## 源

| id | 站点 | 热门 | 最新 | 搜索 |
| --- | --- | --- | --- | --- |
| `cosplaytele` | https://cosplaytele.com | popular-posts（`period`） | WP REST | WP REST `search=` |
| `hentaicosplay` | https://hentai-cosplay-xxx.com | `/ranking/` 及 like/bookmark 等 | `/search/` | `/search/keyword/` |
| `everia` | https://everia.club | 无 | Cosplay 等分类 REST | WP REST |
| `misskon` | https://misskon.com/tag/cosplay/ | 无 | WP REST `tags=cosplay` | WP REST |
| `fourkhd` | https://www.4khd.com | 分类 `popular` | `orderby=date` | WP REST |
| `kiutaku` | https://kiutaku.com | `/hot` | `/?start=` | `?search=` |
| `cup2d` | https://cup2d.com | 无 | WP REST | WP REST |
| `beauty3600000` | https://3600000.xyz | HTML `/category/cosplay/` | 无 | `?s=` |
| `foamgirl` | https://foamgirl.net/cosplay | `/cosplay` | 无 | `?s=` |
| `ososedki` | https://ososedki.com | `/api/albums?type=top` | `/api/albums` | `type=search` |
| `mitaku` | https://mitaku.net | `/category/ero-cosplay/` | 无 | `?s=` |
| `lovecutes` | https://www.lovecutes.com/type/6/ | Cosplay 分类（`type/6`） | 无 | 全站搜索后仅保留 Cosplay 分类 |

## 工具

- `list_sources`：源目录
- `open_url(url, offset=0, limit=20)`：从完整帖子 URL 按域名选源并打开图集
- `search(query, source=all, page=1, category?, exclude_ai=true)`：按站点真实接口搜索。`source=all` 时并行查全部源，结果按源交错排列
- `browse(source, sort=popular|latest, page=1, query?, category?, period?, exclude_ai=true)`：排行 / 最新；只带 `category` 时按该分类列出，不走关键词搜索；带 `query` 时走该源搜索。`period` 仅部分源的热门有效：CosplayTele 为 `last24hours|last7days|last30days|all`，Hentai Cosplay 为 `day|week|month|year`
- `browse_tag(source, tag, page=1, exclude_ai=true)`：按标签列出
- `related(source, path, page=1, exclude_ai=true)`：相近套图。CosplayTele 走 Contextual Related Posts；其余源用图集第一个标签
- `get_gallery(source, path, offset=0, limit=20)`：详情和图片 URL。默认只回前 20 张加 `image_count`；`limit=0` 只回元数据。`path` 用列表或搜索结果里的 `path`，也接受完整帖子 URL。`image_assets` 为每个 `image_urls` 条目附带源站要求的请求头；可能带 `download_urls`、`has_video`（有视频时只打标，不返回可播放流）
- `fetch_image(source, path, index)`：按需取图集中的一张，按 `image_assets` 的请求头请求源站，并返回 MCP `ImageContent`。不写本地文件，不下载整套图集；单图上限 20 MiB，较大的原图请用 `image_assets` 自行请求。

列表项在源站提供时带 `tags`、`published_at`、`image_count`、`has_video`。没有的字段为 `null` / 空列表，不会为凑字段再打详情。年龄语义容易被误判的制服主题词（如 `JK`、`校服`、`制服`、`school girl`、`school uniform`、`after school`）会在标题和标签中追加 `(18+)`；路径、搜索词和源站原始标识不变。

CosplayTele 的 `category` 除表内 slug 外，也接受模特/作品分类 slug（如 `byoru`）。Hentai Cosplay 热门 `category` 为排行种类：`like`、`bookmark`、`download`、`tag`、`keyword`、`images`。OSOSEDKI `category=cosplays` 列出角色目录，再把名字交给 `browse_tag`。

`exclude_ai` 默认开启。只认明确 AI 标记，避免误伤 `Ai Yamada`、`Ai Hoshino` 这类名字：

| 源 | AI 标记 |
| --- | --- |
| CosplayTele | 分类 `ai-art`（id 589），标题 `AI Art – ...`；搜索/标签用 `categories_exclude` |
| Hentai Cosplay | 标题 `(AI Generated)` / `(AI Enhanced)`，路径 `*-ai-generated*`，标签 `ai-generated` / `ai-enhanced` |
| MissKon | 标签 `ai-generated`，搜索用 `tags_exclude` |
| Cup2D | 分类 `ai-art` / `aimodel`，搜索用 `categories_exclude` |
| OSOSEDKI | 标题里的 `ai-generated`，无独立分类 |
| Mitaku | 无 AI 标签页，只能靠标题/路径 |
| 其余新源 | 标题/路径/标签命中 `ai-art`、`ai-generated`、`ai-enhanced` |

结果里带 `is_ai`。`get_gallery` 仍会返回 AI 图集，只打标不拦截。

搜索实现：

| 源 | 接口 |
| --- | --- |
| CosplayTele / Everia / Cup2D | `/wp-json/wp/v2/posts?search=` |
| 4KHD | `/index.php?rest_route=/wp/v2/posts` |
| Hentai Cosplay | `/search/keyword/<kw>/` |
| MissKon | WP REST `search=`，默认浏览 tag `cosplay` |
| Kiutaku | `?search=` |
| FoamGirl | `/?s=`，默认浏览 `/cosplay` |
| OSOSEDKI | `/api/albums?type=search` |
| Mitaku | `/?s=` |

资源：`sources://catalog`、`gallery://{source}/{+path}`。提示词：`find_gallery`。

## 开发

克隆仓库后用 [uv](https://docs.astral.sh/uv/)：

```bash
uv sync --dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
uv run mcp dev src/cosplaytele_mcp/server.py
```

格式化：

```bash
uv run ruff format src tests
uv run ruff check --fix src tests
```
