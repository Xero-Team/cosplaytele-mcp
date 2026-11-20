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

视频下载依赖 `cryptography`（已在依赖中）；`download_video` 用 `ffmpeg` 把 HLS 分片转封装成 MP4，系统需安装 `ffmpeg`（`brew install ffmpeg`），未安装时保留 `.ts`。

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
| `cosplaytele` | https://cosplaytele.com | 页脚「Popular Cosplay」组件 | HTML 归档 `/page/N/` | HTML `?s=` |
| `hentaicosplay` | https://hentai-cosplay-xxx.com | `/ranking/` 及 like/bookmark 等 | `/search/` | `/search/keyword/` |
| `everia` | https://everia.club | 无 | Cosplay 等分类 REST | WP REST |
| `misskon` | https://misskon.com/tag/cosplay/ | 无 | WP REST `tags=cosplay` | WP REST |
| `fourkhd` | https://www.4khd.com | 分类 `popular` | `orderby=date` | WP REST |
| `kiutaku` | https://kiutaku.com | `/hot` | `/?start=` | `?search=` |
| `cup2d` | https://cup2d.com | 无 | WP REST | WP REST |
| `beauty3600000` | https://3600000.xyz | HTML `/category/cosplay/` | 无 | 无（`?s=` 与 WP REST 均触发 PHP 内存错误） |
| `foamgirl` | https://foamgirl.net/cosplay | `/cosplay` | 无 | `?s=` |
| `ososedki` | https://ososedki.com | `/api/albums?type=top` | `/api/albums` | `type=search` |
| `mitaku` | https://mitaku.net | `/category/ero-cosplay/` | 无 | `?s=` |
| `lovecutes` | https://www.lovecutes.com/type/6/ | Cosplay 分类（`type/6`） | 无 | 全站搜索后仅保留 Cosplay 分类 |
| `simplycosplay` | https://www.simply-cosplay.com | `/sets?sort=hot` | `/sets?page=N` | `/search?q=&type=set` |
| `jjcos` | https://jjcos.com/tag/HSQ2151O0wZ/ | Cosplay 标签归档 | 无 | `/api/index.html` 标题过滤 |
| `buondua` | https://buondua.com/tag/cosplay-10688 | Cosplay 标签归档 | 无 | `?search=` |
| `xasiat` | https://www.xasiat.com/albums/categories/cosplay/ | `sort_by=album_viewed_week` | `sort_by=post_date` | `/search/search/` |
| `baobua` | https://baobua.net/category/Cosplay | Cosplay 分类归档 | 无 | 全站 `?s=` |
| `coswives` | https://coswives.com | `/popular` 按 `view_count` 排行（固定 24 条，无分页） | `/?page=N` 归档 | `/?q=` |

## 工具

- `list_sources`：源目录
- `open_url(url, offset=0, limit=20)`：从完整帖子 URL 按域名选源并打开图集
- `search(query, source=all, page=1, category?, exclude_ai=true)`：按站点真实接口搜索。`source=all` 时并行查全部源，结果按源交错排列
- `browse(source, sort=popular|latest, page=1, query?, category?, period?, exclude_ai=true)`：排行 / 最新；只带 `category` 时按该分类列出，不走关键词搜索；带 `query` 时走该源搜索。`period` 仅部分源的热门有效：CosplayTele 为 `last24hours|last7days|last30days|all`，Hentai Cosplay 为 `day|week|month|year`。CosplayTele 的 WordPress 排行 REST 接口当前整站 500，源改为抓站内 HTML；所有 `period` 都回落到同一个「Popular Cosplay」精选组件，不再区分时间窗
- `browse_tag(source, tag, page=1, exclude_ai=true)`：按标签列出
- `related(source, path, page=1, exclude_ai=true)`：相近套图。CosplayTele 走 Contextual Related Posts；其余源用图集第一个标签
- `get_gallery(source, path, offset=0, limit=20)`：详情和图片 URL。默认只回前 20 张加 `image_count`；`limit=0` 只回元数据。`path` 用列表或搜索结果里的 `path`，也接受完整帖子 URL。`image_assets` 为每个 `image_urls` 条目附带源站要求的请求头；可能带 `download_urls`、`has_video`、`video_assets`（CosplayTele 的 cossora.stream 播放器地址）
- `fetch_image(source, path, index)`：按需取图集中的一张，按 `image_assets` 的请求头请求源站，并返回 MCP `ImageContent`。只拉取源站 allowlist 上的 URL；CosWives 的 `coswives-cdn.b-cdn.net`、4KHD 的 `i0.wp.com`、Simply Cosplay 的 `images.sh-cdn.com` 已加入 allowlist，配合 `Referer` 可直接取图；JJCOS（`i1.wp.com`）、BaoBua（`blogger.googleusercontent.com`）会被拒绝，请改用 `image_assets` 自行请求。不写本地文件，不下载整套图集；单图上限 20 MiB，超限在缓冲前拒绝，较大的原图同样用 `image_assets`。
- `download_video(source, path, index=0, output_dir?)`：下载图集中的视频到本地文件（默认当前目录）。仅 CosplayTele 的 cossora.stream 播放器有效：服务端解密其 AES-256-CBC 加密的 HLS `index.m3u8`，流式拼接 `.ts` 分片，并用 `ffmpeg` 转封装为 MP4（未安装 ffmpeg 时保留 `.ts`）。只返回本地文件路径、大小、mime 和分片数，不返回视频二进制；`output_dir` 可指定输出目录。

列表项在源站提供时带 `tags`、`published_at`、`image_count`、`has_video`。没有的字段为 `null` / 空列表，不会为凑字段再打详情。年龄语义容易被误判的制服主题词（如 `JK`、`校服`、`制服`、`school girl`、`school uniform`、`after school`）会在标题和标签中追加 `(18+)`；路径、搜索词和源站原始标识不变。

CosplayTele 的 `category` 除表内 slug 外，也接受模特/作品分类 slug（如 `byoru`）。Hentai Cosplay 热门 `category` 为排行种类：`like`、`bookmark`、`download`、`tag`、`keyword`、`images`。OSOSEDKI `category=cosplays` 列出角色目录，再把名字交给 `browse_tag`。Simply Cosplay 改版后没有分类页，带 `category` 会直接报错，请改用 `browse_tag`。

`exclude_ai` 默认开启。只认明确 AI 标记，避免误伤 `Ai Yamada`、`Ai Hoshino` 这类名字：

| 源 | AI 标记 |
| --- | --- |
| CosplayTele | 标题/路径/标签命中 `ai-art`、`ai-generated`、`ai-enhanced`（原 `ai-art` 分类 REST 已失效，改由 HTML 结果打标） |
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
| CosplayTele | HTML `?s=`；分类走 `/category/<slug>/`，标签走 `/tag/<slug>/`（WP REST 整站 500 后不再依赖） |
| Everia / Cup2D | `/wp-json/wp/v2/posts?search=` |
| 4KHD | `/index.php?rest_route=/wp/v2/posts` |
| Hentai Cosplay | `/search/keyword/<kw>/` |
| MissKon | WP REST `search=`，默认浏览 tag `cosplay` |
| Kiutaku | `?search=` |
| FoamGirl | `/?s=`，默认浏览 `/cosplay` |
| OSOSEDKI | `/api/albums?type=search` |
| Mitaku | `/?s=` |
| Simply Cosplay | `/search?q=&type=set`（全站搜索按名称匹配，再只看 set 结果） |
| JJCOS | `/api/index.html` 缓存后按标题/链接/标签过滤 Cosplay；默认浏览 Cosplay 标签 |
| Buon Dua | `?search=`；默认浏览 `/tag/cosplay-10688` |
| Xasiat | `/search/search/` XHR block；`category=cosplay` 时按标题/路径过滤；默认浏览 Cosplay 相册分类 |
| BaoBua | 全站 `/?s=`（无法锁到 Cosplay 分类）；默认浏览 `/category/Cosplay` |
| CosWives | `/?q=`；分类 `/?category=Sinful|Lewd|Innocent`；标签 `/?tag=<标签>`；列表与详情数据来自 Next.js RSC flight 内嵌 JSON |

资源：`sources://catalog`、`gallery://{source}/{+path}`。提示词：`find_gallery`。

源适配修复：MissKon 详情改走 WP REST `content.rendered`，不再翻 `<!--nextpage-->` 分页（分页页会混入「Go to page 1」横幅并重复图片，导致 `image_count`/索引错乱）；4KHD 全尺寸图恢复为原始 `i0.wp.com/pic.4khd.com` URL（`img.uuss.uk` 已失效、404 且不在 allowlist），并加入 allowlist，`fetch_image` 可直接取图；Xasiat 单图常超 20 MiB，取图改为流式读并在缓冲前按上限拒绝，避免整张大图进内存；Simply Cosplay 站点已改版为 Next.js 服务端渲染，旧 API（`api.simply-porn.com`，域名已无 A 记录）与 `/gallery/new/<slug>`、`/image/<slug>` 路径全部失效，源改为抓 HTML：列表走 `/sets`（热门 `?sort=hot`）、搜索走 `/search?q=&type=set`、标签走 `/tags/<slug>`（单页，`?page=2` 返回 404）、详情走 `/sets/<slug>`（`.film` 胶片条取全尺寸图，JSON-LD `ImageGallery` 兜底），图片 CDN 变为 `images.sh-cdn.com` 并加入 allowlist。

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
