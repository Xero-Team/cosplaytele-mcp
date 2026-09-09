# CosplayTele MCP

面向 CosplayTele 及同类图库站点的 MCP 2（Model Context Protocol，规范 2026-07-28）服务器。

需要 Python 3.10+。已发布：[cosplaytele-mcp](https://pypi.org/project/cosplaytele-mcp/)。

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
uvx --from cosplaytele-mcp python -c "from cosplaytele_mcp.server import mcp; mcp.run(transport='streamable-http', port=8000)"
```

## 源

| id | 站点 | 热门 | 最新 | 搜索 |
| --- | --- | --- | --- | --- |
| `cosplaytele` | https://cosplaytele.com | popular-posts | HTML | WP REST `search=` |
| `hentaicosplay` | https://hentai-cosplay-xxx.com | `/ranking/` | `/search/` | `/search/keyword/` |
| `everia` | https://everia.club | Cosplay 分类 REST | 同左 | WP REST |
| `misskon` | https://misskon.com/tag/cosplay/ | `/tag/cosplay/` | `/tag/cosplay/` | `?s=` |
| `fourkhd` | https://www.4khd.com | WP REST `orderby=modified` | `orderby=date` | WP REST |
| `kiutaku` | https://kiutaku.com | `/hot` | `/?start=` | `?search=` |
| `cup2d` | https://cup2d.com | WP REST | WP REST | WP REST |
| `beauty3600000` | https://3600000.xyz | WP REST | 无 | WP REST |
| `foamgirl` | https://foamgirl.net/cosplay | `/cosplay` | 无 | `?s=` |
| `ososedki` | https://ososedki.com | `/api/albums?type=top` | `/api/albums` | `type=search` |
| `mitaku` | https://mitaku.net | `/category/ero-cosplay/` | 无 | `?s=` |

## 工具

- `list_sources`：源目录
- `search(query, source=all, page=1, category?, exclude_ai=true)`：按站点真实接口搜索。`source=all` 时并行查全部源
- `browse(source, sort=popular|latest, page=1, query?, category?, exclude_ai=true)`：排行 / 最新；带 `query` 时走该源搜索
- `get_gallery(source, path)`：详情和图片 URL；`path` 用列表或搜索结果里的 `path`，也接受完整帖子 URL

`exclude_ai` 默认开启。只认明确 AI 标记，避免误伤 `Ai Yamada`、`Ai Hoshino` 这类名字：

| 源 | AI 标记 |
| --- | --- |
| CosplayTele | 分类 `ai-art`（id 589），标题 `AI Art – ...`；搜索用 `categories_exclude` |
| Hentai Cosplay | 标题 `(AI Generated)` / `(AI Enhanced)`，路径 `*-ai-generated*`，标签 `ai-generated` / `ai-enhanced` |
| OSOSEDKI | 标题里的 `ai-generated`，无独立分类 |
| Mitaku | 无 AI 标签页，只能靠标题/路径 |
| 其余新源 | 标题/路径/标签命中 `ai-art`、`ai-generated`、`ai-enhanced` |

结果里带 `is_ai`。`get_gallery` 仍会返回 AI 图集，只打标不拦截。

搜索实现：

| 源 | 接口 |
| --- | --- |
| CosplayTele / Everia / Cup2D / 3600000 | `/wp-json/wp/v2/posts?search=` |
| 4KHD | `/index.php?rest_route=/wp/v2/posts` |
| Hentai Cosplay | `/search/keyword/<kw>/` |
| MissKon | `/?s=`，默认浏览 `/tag/cosplay/` |
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
