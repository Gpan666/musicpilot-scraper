<p align="center">
  <img src="docs/assets/musicpilot-icon.png" alt="MusicPilot Scraper" width="420">
</p>

# MusicPilot Scraper

**把一堆音频文件刮削成整齐的音乐库。**

自托管的音频刮削与曲库整理工具：递归扫描源目录 → 读标签、缺失的从文件名与目录名推断 → 并发查询 MusicBrainz / 网易云 / QQ音乐 / 酷狗 / 酷我 补全 → 写回标题 / 歌手 / 专辑 / 专辑艺术家 / 年份 / 轨号 / 碟号 / 封面 / 歌词 → 按命名模板生成目标路径 → 用复制、移动、硬链接、软链接四种方式之一放进音乐库。

落盘前先出**整理计划与可视化预览**：源 → 目标路径映射、逐字段「原值 → 新值」对照、全部在线候选与匹配度，点选即改，确认后才写。Docker 一键部署，自带 Miuix 风格 Web 界面与登录，另有盯住源目录的自动刮削。

> 一句话：**只处理你已有的本地音频文件，不下载、不分发任何音乐。**

## 目录

- [1. 能力一览](#1-能力一览)
- [2. 支持的音频格式](#2-支持的音频格式)
- [3. 不做什么](#3-不做什么)
- [4. 工作流程](#4-工作流程)
- [5. Web 页面一览](#5-web-页面一览)
- [6. 快速开始（Docker）](#6-快速开始docker)
- [7. 配置项](#7-配置项)
- [8. 四种转移方式](#8-四种转移方式)
- [9. API 一览](#9-api-一览)
- [10. 开发与测试](#10-开发与测试)
- [11. 致谢与许可](#11-致谢与许可)

## 1. 能力一览

### 1.1 扫描与标签推断

- 递归遍历源目录，读取文件内嵌标签；读不出来的从文件名与目录名推断。
- 支持 13 种音频扩展名，可自定义扩展名、排除目录、单次扫描文件上限（默认 5000）。
- 扫描结果会带上物理属性（编码、时长、采样率等）与警告信息，超限时明确标出 `truncated`。
- 目录选择器只能浏览配置好的根目录（`MP_BROWSABLE_ROOTS`）及当前源 / 库目录的父目录，越界的软链不会被列出。

### 1.2 五个在线音源

| 音源 | 强项 | 能给什么 |
| --- | --- | --- |
| MusicBrainz | 国际曲库 / 古典（中文覆盖低） | 标题、歌手、专辑、专辑艺术家、年份、轨号、封面（**不提供歌词**） |
| 网易云音乐 | 中文曲库 | 标题、歌手、专辑、专辑艺术家、年份、封面、歌词 |
| QQ音乐 | 中文 / 华语 | 标题、歌手、专辑、年份、轨号、封面、歌词 |
| 酷狗音乐 | 中文 / 网络歌曲 | 标题、歌手、专辑、歌词 |
| 酷我音乐 | 中文 / 流行 | 标题、歌手、专辑、专辑艺术家、封面、歌词 |

- 五个音源**并发查询**，按 1–10 打匹配度（10 = 歌名、歌手、专辑全对得上；7 分以上才建议写入），可在设置页逐项开关。
- 界面为每个源标出「能不能搜 / 能不能按 id 取词 / 限制说明」，不可用的源置灰并写明原因 —— **不允许出现「chip 显示可用、实际搜不出东西」的源**。
- 单个音源挂掉只是少一份候选，不会让任务失败；可配 HTTP 代理与请求超时访问外部音源。

### 1.3 只补空字段与人工确认

- 文件里已有的标签一律保留，**绝不覆盖**；在线结果只填空缺字段。
- 置信度低于阈值（默认 0.62）的结果标「需人工确认」，不会悄悄写进去。
- 写回字段：标题、歌手、专辑、专辑艺术家、年份、专辑轨号、碟号、封面，可选歌词；封面内嵌进文件。

### 1.4 整理计划与可视化预览

- 「整理计划」页先给出**源 → 目标路径**的完整映射，确认后才执行。
- 「刮削前后对比」逐字段展示「原值 → 新值」，并把全部在线候选与匹配度摊开，点选即改。
- 候选「预览」可打开完整详情窗：封面、逐字段对照表、将写入清单。
- 保存前支持 dry-run 预览「将写入：…」，确认后才真正写标签。

### 1.5 命名模板与冲突策略

- 模板形如 `{album}/{album_artist}/{track:02d} - {title}`，字段可自由组合，界面实时显示示例结果。
- 冲突策略：同名同路径时**改名 / 跳过 / 覆盖**，可选；软链接可设相对路径。
- 缺失字段用可配置的占位符（默认「未知」）。

### 1.6 四种转移方式 + 纯补全模式

复制 / 移动 / 硬链接 / 软链接，另有一种**只写标签、不动文件位置**的纯补全模式（音乐库页的「批量补全」「只补这一首」）。详见 [8. 四种转移方式](#8-四种转移方式) 与写入自检。

### 1.7 单曲与专辑曲目

同一级目录里只有这一首歌时按**单曲**处理：单曲本来就没有专辑与专辑轨号，模板里的 `{track}` 自动省掉，轨号仍写进标签；判定依据（目录内音频数、专辑类型等）在界面上写明。专辑目录里的曲目照旧要求专辑、专辑艺术家、年份与轨号。

### 1.8 音乐库与全屏媒体编辑器

音乐库页是高密度表格：统计卡（总曲目、有封面、有歌词、待补全、完整率）、状态与来源筛选、专辑 / 歌手 / 年份分组、分页、表格 ⇄ 网格视图切换、查漏补缺扫描、批量补全、只补这一首。

点行内「编辑」进**全屏媒体编辑器**：左侧是这一首的完整写照（大图封面、曲名与歌手 · 专辑 · 年份、信息完整度进度、待写字段、物理属性），右侧四个页签：

| 页签 | 内容 |
| --- | --- |
| 核心数据 | 已有值的字段只读（后端只补空字段），缺失字段可填；保存前先 dry-run 预览「将写入：…」 |
| 智能刮削 | 按平台搜索候选 → 候选「预览」打开完整详情窗（封面、逐字段对照表、将写入清单）→ 写入标签 |
| 歌词 | 左右双栏：左「同步渲染预览」逐行渲染，右「歌词编辑区 LRC / TXT」带行号编辑；支持本地搜词（高亮 + 命中计数 + 上/下一处）、格式化结构、清空、保存 |
| 操作历史 | 当前会话里真实做过的写入（时间 + 操作 + 字段），刷新即清空 |

### 1.9 歌词工作台

- 歌词页与编辑器同页，左右 1:1 等宽两栏，各自独立滚动。
- **本地搜词**：输入即高亮全部命中行，右栏行号同步标出命中行与当前行，回车 = 下一处。
- **从音源搜词**：复用音源搜索接口拿到歌词正文，填进编辑区（只填前端，不落盘），点「保存歌词」才写文件。
- 取不到词时给出**具体原因**（该来源不支持按 id 取词 / 接口无返回 / 该歌曲没有歌词），不编造歌词。

### 1.10 自动刮削与任务记录

- 盯住源目录，出现新文件就自动排一个刮削任务；检查间隔（5–3600 秒）与文件稳定期可调。
- 任务记录页可看进度、取消任务、看明细（右侧抽屉）、删除 / 清空记录、**导出 CSV 报告**。
- 刮削参数保存在数据目录的 SQLite 中，重启容器后依然生效。

### 1.11 设置、自检与运行环境

- **系统设置**：源目录与音乐库目录（可「浏览…」点选）、转移方式（四种卡片 + 当前使用标记）、转移方式可用性自检、账户管理。
- **刮削设置**：命名模板与实时示例、冲突策略、元数据来源开关、写入标签 / 封面 / 歌词开关、置信度阈值、自动刮削。
- **写入自检**：直接探测当前挂载点能否支持硬链接 / 软链接，并把四种方式标成可用或不可用。
- 运行设置优先级：Web 界面（SQLite）→ 环境变量 → 默认值。

### 1.12 Web 界面与账户安全

- Miuix 风格的深色单页，**原生 HTML / CSS / JS，无 Node 构建步骤**；支持夜间 / 日间 / 跟随系统三档主题，侧栏可折叠。
- 首次访问创建管理员账号，PBKDF2 加盐存 SQLite + HMAC 会话 Cookie + 登录限速；会话密钥自动生成并持久化，重启不失效。
- 后端 FastAPI + SQLite（标准库，无额外数据库依赖），前端随 wheel / 镜像一起分发。

## 2. 支持的音频格式

`.mp3` `.flac` `.wav` `.m4a` `.aac` `.ogg` `.opus` `.aiff` `.aif` `.dsf` `.dff` `.wma` `.ape`

可用 `MP_SCAN_EXTENSIONS` 调整；默认排除 `@eaDir`、`.git`、`.thumbnails`、`System Volume Information` 等目录。

## 3. 不做什么

不搜索、不解析、不下载任何种子或 PT 站点资源，不连 qBittorrent / Transmission；不管理歌单、不刷 Navidrome 等媒体服务器、不发 Telegram；不提供 / 存储 / 分发任何音乐 —— 只处理你已有的本地文件。

## 4. 工作流程

```
源目录扫描 → 读标签 + 文件名/目录名推断 → 五个音源并发查询与 1–10 评分
   → 合并（只补缺失字段，低置信度标人工确认）→ 整理计划 / 可视化预览（原值 → 新值）
   → 生成目标路径（模板 + 冲突策略）→ 写标签 / 封面 / 歌词
   → 按四种方式之一转移（或纯补全不动位置）→ 记录任务与结果
```

两个约定：① **标签写入发生在转移前**，让硬链接 / 软链接的目标也带完整标签（源文件标签同时被补全，不想改源请关闭「写入标签」或用复制模式）；② **在线结果只填空**，已有标签优先。

## 5. Web 页面一览

| 页面 | 作用 |
| --- | --- |
| 概览 | 音乐库状态、统计卡、任务与最近入库 |
| 音乐库 | 浏览库内所有曲目、手动刮削、自动扫描查漏补缺（只改标签，不动文件） |
| 源文件 | 扫描源目录、选择文件并生成整理计划 |
| 整理计划 | 先预览源 → 目标的映射与逐字段对照，确认后再执行 |
| 任务记录 | 查看进度与明细、取消任务、导出 CSV 报告 |
| 刮削设置 | 命名模板、冲突策略、元数据来源与自动刮削 |
| 系统设置 | 目录、转移方式与运行环境自检、账户管理 |
| 转移方式说明 | 四种转移方式的区别与适用场景 |

## 6. 快速开始（Docker）

准备一个共同父目录放源与库，只挂载它一次：

```text
/volume1/media/
├── downloads/   ← 源目录（待刮削）
└── music/       ← 音乐库（整理后）
```

```bash
git clone <本仓库地址> MusicPilot && cd MusicPilot
mkdir -p media/downloads media/music data
docker compose up -d --build
```

打开 `http://<NAS_IP>:8000`，**首次访问创建管理员账号**后自动登录。

> 硬链接能否用取决于两目录是否同一挂载点：挂共同父目录 `/volume1/media:/media` 最稳妥（分别挂载两个子目录会失败）。

### 6.1 用现成的通用镜像

发布好的双架构镜像（linux/amd64 + linux/arm64），一份镜像跑遍 NAS 与服务器。Docker Hub 与 GHCR 同步发布，按网络可达性挑一个：

```bash
docker run -d --name musicpilot \
  -p 8000:8000 \
  -e PUID=1000 -e PGID=1000 -e TZ=Asia/Shanghai \
  -v /volume1/media:/media \
  -v /volume1/docker/musicpilot:/data \
  --restart unless-stopped \
  tsbyj/musicpilot-scraper:latest   # 或 ghcr.io/<你的 GitHub 用户名>/musicpilot-scraper:latest
```

- `PUID` / `PGID` / `UMASK`：容器内进程的运行身份与文件权限，挂载目录写不进去时改成宿主属主（`id 用户名` 可查）。
- 镜像标签按语义化版本推：`:latest`、`:1.0.0`、`:v1.0.0`、`:1.0`、`:1`、`:sha-xxxxxxxx`，规则见 [docs/docker.md](docs/docker.md)。
- 容器内只有两个约定：数据落 `/data`（SQLite、会话密钥、任务记录），媒体在 `/media`（源 `/media/downloads`、库 `/media/music`）。
- 仓库已配好 CI、双架构镜像构建与 Release 流水线，打 tag 即可发布。

## 7. 配置项

完整示例见 [.env.example](.env.example)。运行设置也可在 Web 界面修改（存 SQLite，重启不丢）。

### 目录与运行

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PUID` / `PGID` / `UMASK` / `TZ` | `1000` / `1000` / `022` / `Asia/Shanghai` | 仅容器使用：运行身份、新建文件权限、时区 |
| `MP_SOURCE_DIR` / `MP_LIBRARY_DIR` / `MP_DATA_DIR` | `/media/downloads` / `/media/music` / `/data` | 源目录、音乐库、数据目录（容器内路径） |
| `MP_BROWSABLE_ROOTS` | `/media` | 目录选择器允许浏览的根目录，逗号分隔 |
| `MP_ADMIN_USERNAME` / `MP_ADMIN_PASSWORD` / `MP_SESSION_SECRET` | `admin` / 空 / 自动生成 | 全新部署留空，在网页上注册管理员即可 |

### 刮削与写入

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MP_TRANSFER_MODE` | `copy` | `copy` / `move` / `hardlink` / `symlink` |
| `MP_NAMING_TEMPLATE` | `{album}/{album_artist}/{track:02d} - {title}` | 目标路径模板 |
| `MP_CONFLICT_POLICY` | `rename` | 改名 / 跳过 / 覆盖 |
| `MP_SYMLINK_RELATIVE` | `true` | 软链接使用相对路径 |
| `MP_WRITE_TAGS` / `MP_EMBED_COVER` / `MP_WRITE_LYRICS` | `true` / `true` / `false` | 是否写标签 / 内嵌封面 / 写歌词 |
| `MP_METADATA_SOURCES` | `local,musicbrainz,netease,qq,kugou,kuwo` | 启用的音源，`local` 始终参与 |
| `MP_CONFIDENCE_THRESHOLD` | `0.62` | 低于此值标「需人工确认」 |
| `MP_UNKNOWN_PLACEHOLDER` | `未知` | 缺失字段占位符 |
| `MP_SCAN_EXTENSIONS` | 见第 2 节 | 扫描的音频扩展名 |
| `MP_EXCLUDE_DIRS` / `MP_MAX_SCAN_FILES` | 见第 2 节 / `5000` | 排除目录、单次扫描上限 |

### 网络、自动刮削

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MP_HTTP_PROXY` / `MP_REQUEST_TIMEOUT` | 空 / `20` | 访问外部音源的代理与请求超时（秒） |
| `MP_MUSICBRAINZ_USER_AGENT` | `MusicPilot-Scraper/1.0 (self-hosted personal use)` | MusicBrainz 要求的 User-Agent |
| `MP_WATCH_ENABLED` | `false` | 是否开启源目录监控自动刮削 |
| `MP_WATCH_INTERVAL` | `30` | 检查间隔（秒，5–3600） |
| `MP_WATCH_SETTLE` | `10` | 文件最后修改距今多少秒才认为下载完成（0 = 不等待） |

## 8. 四种转移方式

| 方式 | 源文件之后 | 额外空间 | 跨挂载点 | 要点 |
| --- | --- | --- | --- | --- |
| 复制 copy | 保留 | 双份 | ✅ | 最稳妥默认 |
| 移动 move | 删除 | 无 | ✅ | 清空暂存用 |
| 硬链接 hardlink | 保留（同一份数据） | 无 | ❌ 需同一挂载点 | 源与目标共享 inode，改一边另一边跟着变 |
| 软链接 symlink | 保留（指针） | 无 | ✅ | 源文件删了目标即失效 |

界面里可以跑一次**写入自检**，直接看当前挂载点支持哪几种；另有纯补全模式，只写标签、不移动文件。

## 9. API 一览

35 个端点，FastAPI + OpenAPI 文档。主要分组：

- `GET /api/library` 曲库与统计、`GET /api/library/detail` 单曲详情、`GET /api/library/cover` 封面
- `POST /api/library/search` 在线候选、`POST /api/library/apply` 写入（支持 dry-run 预览）
- `POST /api/library/lyrics/fetch` 按候选 id 取歌词正文（只读）、`PUT /api/library/lyrics` 写歌词
- `POST /api/library/scan` 查漏补缺扫描、`POST /api/library/fill` 批量补全
- `GET /api/files`、`GET /api/files/directories`（受限目录浏览）、`POST /api/files/detail` 单文件详情与目标路径预览
- `GET /api/settings`、`PUT /api/settings`、`GET /api/system/capabilities`（转移方式可用性）、`GET /api/transfer-modes`、`GET /api/about`、`GET /api/dashboard`
- `GET/PUT /api/watch`、`POST /api/watch/reset` 自动刮削
- `GET/POST/DELETE /api/jobs` 任务记录与明细、`POST /api/jobs/{id}/cancel`、`GET /api/jobs/{id}/report` CSV 报告
- `GET /api/auth/status`、`POST /api/auth/register` / `login` / `logout` 账户与会话

## 10. 开发与测试

后端按域分层：`api/`（路由）、`services/`（业务）、`domain/`（模型与匹配）、`adapters/`（扫描、标签、音源）；Python ≥ 3.11，SQLite 用标准库，无额外数据库依赖。测试用 pytest：

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check musicpilot tests
```

前端在 `web/` 下，无构建步骤；改动直接改 `index.html` / `app.js` / `style.css` 三个文件即可，打包时由 `scripts/sync_web.py` 拷进 `musicpilot/web/` 随 wheel 分发。

## 11. 致谢与许可

- [lzcer/MusicPilot](https://github.com/lzcer/MusicPilot) —— 最初的思路来源。本项目只保留「刮削 + 整理」这条主线，并把实现重写成分层模块 + 原生前端。
- [fanszoro/music-tagger](https://github.com/fanszoro/music-tagger) —— 同样聚焦本地音乐库元数据刮削与标签管理的开源项目，感谢其在曲库管理、任务日志与 Web 交互上的实践分享，为本项目提供了很好的参考与启发。
- 许可：[GPL-3.0-or-later](LICENSE)。
