<p align="center">
  <img src="docs/assets/musicpilot-icon.png" alt="MusicPilot" width="420">
</p>

# MusicPilot Scraper

**把一堆音频文件刮削成整齐的音乐库。**

自托管的音频刮削与曲库整理工具：递归扫描源目录 → 读标签、缺失的从文件名推断 → 查 MusicBrainz / 网易云 / QQ音乐 / 酷狗 / 酷我 补全 → 写回标题 / 歌手 / 专辑 / 专辑艺术家 / 年份 / 轨号 / 封面 / 歌词 → 按命名模板生成目标路径 → 用复制、移动、硬链接、软链接四种方式之一放进音乐库。落盘前先出**可视化预览**：逐字段「原值 → 新值」对照、全部在线候选与匹配度，点选即改，确认后才写。

Docker 一键部署，自带 Miuix 风格 Web 界面与登录，另有盯住源目录的自动刮削。

## 1. 能力一览

### 1.1 刮削与整理（主线）

- **扫描**：递归遍历源目录，读取内嵌标签，读不出来的从文件名与目录名推断。
- **在线候选**：五个音源并发查询，按 1–10 打匹配度（10 = 歌名、歌手、专辑全对得上；7 分以上才建议写入）。
- **只补空字段**：文件里已有的标签一律保留，**绝不覆盖**；置信度低于阈值的结果标「需人工确认」。
- **写回**：标题、歌手、专辑、专辑艺术家、年份、专辑轨号、碟号、封面，可选歌词；封面内嵌进文件。
- **命名模板**：`{album}/{album_artist}/{track:02d} - {title}` 形式，字段可自由组合，预览实时显示结果。
- **冲突策略**：同名同路径时改名 / 跳过 / 覆盖，可选。
- **四种转移方式**：复制 / 移动 / 硬链接 / 软链接，**只写标签、不动文件位置**的纯补全模式另算一种。

### 1.2 音乐库与全屏媒体编辑器

音乐库页是高密度表格：统计卡（总曲目、有封面、有歌词、待补全、完整率）、状态与来源筛选、专辑 / 歌手 / 年份分组、分页、表格 ⇄ 网格视图切换、批量补全、只补这一首。

点行内「编辑」进**全屏媒体编辑器**：左侧是这一首的完整写照（大图封面、曲名与歌手 · 专辑 · 年份、信息完整度进度、待写字段、物理属性），右侧四个页签：

| 页签 | 内容 |
| --- | --- |
| 核心数据 | 已有值的字段只读（后端只补空字段），缺失字段可填；保存前先 dry-run 预览「将写入：…」 |
| 智能刮削 | 按平台搜索候选 → 候选「预览」打开完整详情窗（封面、逐字段对照表、将写入清单）→ 写入标签 |
| 歌词 | 左右双栏：左「同步渲染预览」逐行渲染，右「歌词编辑区 LRC / TXT」带行号编辑；支持本地搜词（高亮 + 命中计数 + 上/下一处）、格式化结构、清空、保存 |
| 操作历史 | 当前会话里真实做过的写入（时间 + 操作 + 字段），刷新即清空 |

### 1.3 歌词

- 歌词页与编辑器同页，左右 1:1 等宽两栏，各自独立滚动。
- **本地搜词**：输入即高亮全部命中行，右栏行号同步标出命中行与当前行，回车 = 下一处。
- **从音源搜词**：复用音源搜索接口拿到歌词正文，填进编辑区（只填前端，不落盘），点「保存歌词」才写文件。
- 取不到词时给出**具体原因**（该来源不支持按 id 取词 / 接口无返回 / 该歌曲没有歌词），不编造歌词。

### 1.4 五个在线音源

| 音源 | 强项 | 能给什么 |
| --- | --- | --- |
| MusicBrainz | 国际曲库 / 古典（中文覆盖低） | 标题、歌手、专辑、专辑艺术家、年份、轨号、封面（**不提供歌词**） |
| 网易云音乐 | 中文曲库 | 标题、歌手、专辑、专辑艺术家、年份、封面、歌词 |
| QQ音乐 | 中文 / 华语 | 标题、歌手、专辑、年份、轨号、封面、歌词 |
| 酷狗音乐 | 中文 / 网络歌曲 | 标题、歌手、专辑、歌词 |
| 酷我音乐 | 中文 / 流行 | 标题、歌手、专辑、专辑艺术家、封面、歌词 |

界面为每个源标出「能不能搜 / 能不能按 id 取词 / 限制说明」，不可用的源置灰并写明原因 —— **不允许出现「chip 显示可用、实际搜不出东西」的源**。单个音源挂掉只是少一份候选，不会让任务失败。

### 1.5 单曲与专辑曲目

同一级目录里只有这一首歌时按**单曲**处理：单曲本来就没有专辑与专辑轨号，模板里的 `{track}` 自动省掉，轨号仍写进标签；判定依据（目录内音频数、专辑类型等）在界面上写明。专辑目录里的曲目照旧要求专辑、专辑艺术家、年份与轨号。

### 1.6 目录、转移方式与运行环境

- **系统设置**：源目录与音乐库目录（可「浏览…」点选）、转移方式（四种卡片 + 当前使用标记）、转移方式可用性自检、账户管理。
- **刮削设置**：命名模板与实时示例、冲突策略、元数据来源开关、写入标签 / 封面 / 歌词开关、置信度阈值、自动刮削。
- **写入自检**：直接探测当前挂载点能否支持硬链接 / 软链接，并把四种方式标成可用或不可用。

### 1.7 自动刮削与任务记录

盯住源目录，出现新文件就自动排一个刮削任务（间隔与稳定期可调）。任务记录页可看进度、取消、看明细（右侧抽屉）、导出 CSV 报告。

### 1.8 界面

Miuix 风格的深色单页，**原生 HTML / CSS / JS，无 Node 构建步骤**；支持夜间 / 日间 / 跟随系统三档主题；首次访问创建管理员账号，PBKDF2 加盐 + HMAC 会话 Cookie + 登录限速。

## 2. 不做什么

不搜索、不解析、不下载任何种子或 PT 站点资源，不连 qBittorrent / Transmission；不管理歌单、不刷 Navidrome 等媒体服务器、不发 Telegram；不提供 / 存储 / 分发任何音乐 —— 只处理你已有的本地文件。

## 3. 工作流程

```
源目录扫描 → 读标签 + 文件名推断 → 在线候选查询与评分 → 合并（只补缺失字段）
   → 生成目标路径（模板 + 冲突策略）→ 写标签 / 封面 / 歌词 → 按四种方式之一转移 → 记录结果
```

两个约定：① **标签写入发生在转移前**，让硬链接 / 软链接的目标也带完整标签（源文件标签同时被补全，不想改源请关闭「写入标签」或用复制模式）；② **在线结果只填空**，已有标签优先。

## 4. 快速开始（Docker）

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

### 4.1 用现成的通用镜像

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
- 容器内只有两个约定：数据落 `/data`，媒体在 `/media`（源 `/media/downloads`、库 `/media/music`）。

常用环境变量：`PUID`/`PGID`/`UMASK`（容器运行身份）、`MP_HOST_MEDIA_PATH`（媒体父目录）、`MP_SOURCE_DIR`/`MP_LIBRARY_DIR`（源与库目录）、`MP_TRANSFER_MODE`（copy/move/hardlink/symlink，默认 copy）、`MP_NAMING_TEMPLATE`、`MP_CONFLICT_POLICY`、`MP_WRITE_TAGS`/`MP_EMBED_COVER`/`MP_WRITE_LYRICS`、`MP_METADATA_SOURCES`（默认 `local,musicbrainz,netease,qq,kugou,kuwo`）、`MP_CONFIDENCE_THRESHOLD`（默认 0.62）、`MP_BROWSABLE_ROOTS`（目录选择器根，默认 `/media`）、`MP_WATCH_ENABLED`（自动刮削，默认 false）。完整见 `.env.example`。

## 5. 四种转移方式

| 方式 | 源文件之后 | 额外空间 | 跨挂载点 | 要点 |
| --- | --- | --- | --- | --- |
| 复制 copy | 保留 | 双份 | ✅ | 最稳妥默认 |
| 移动 move | 删除 | 无 | ✅ | 清空暂存用 |
| 硬链接 hardlink | 保留（同一份数据） | 无 | ❌ 需同一挂载点 | 源与目标共享 inode，改一边另一边跟着变 |
| 软链接 symlink | 保留（指针） | 无 | ✅ | 源文件删了目标即失效 |

界面里可以跑一次**写入自检**，直接看当前挂载点支持哪几种。

## 6. API 一览

35 个端点，FastAPI + OpenAPI 文档。主要分组：

- `GET /api/library` 曲库与统计、`GET /api/library/detail` 单曲详情、`GET /api/library/cover` 封面
- `POST /api/library/search` 在线候选、`POST /api/library/apply` 写入（支持 dry-run 预览）
- `POST /api/library/lyrics/fetch` 按候选 id 取歌词正文（只读）、`PUT /api/library/lyrics` 写歌词
- `POST /api/library/scan` 查漏补缺扫描、`POST /api/library/fill` 批量补全
- `GET /api/settings`、`PUT /api/settings`、`GET /api/system/capabilities`（转移方式可用性）、`GET /api/transfer-modes`
- `GET/PUT /api/watch` 自动刮削、`GET/POST/DELETE /api/jobs` 任务记录与明细

## 7. 开发与测试

后端按域分层：`api/`（路由）、`services/`（业务）、`domain/`（模型与匹配）、`adapters/`（扫描、标签、音源）；SQLite 用标准库，无额外依赖。测试用 pytest：

```bash
pip install -e ".[dev]"
python -m pytest -q
python -m ruff check musicpilot tests
```

前端在 `web/` 下，无构建步骤；改动直接改这三个文件即可。

## 8. 致谢与许可

- 思路受 [lzcer/MusicPilot](https://github.com/lzcer/MusicPilot) 启发，本项目只保留「刮削 + 整理」这条主线，并把实现重写成分层模块 + 原生前端。
- 许可：[GPL-3.0-or-later](LICENSE)。
