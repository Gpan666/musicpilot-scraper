<p align="center">
  <img src="docs/assets/musicpilot-icon.png" alt="MusicPilot" width="360">
</p>

# MusicPilot Scraper

**只做一件事：把一堆音频文件刮削成整齐的音乐库。**

扫描源目录 → 读标签、缺了就从文件名推断 → 查 MusicBrainz / 网易云补全 → 写回标签与封面 → 按命名模板转移进库（复制 / 移动 / 硬链接 / 软链接任选）。

整理前先出**可视化预览**：每个文件一张卡片，封面、标签「原值 → 新值」、全部在线候选，点选即改，确认后才落盘。Docker 一键部署，自带 Miuix 风格 Web 界面与登录，另有源目录监控的自动刮削。

> 这是 [lzcer/MusicPilot](https://github.com/lzcer/MusicPilot) 的**重构精简版**：砍掉 PT 索引、下载器、歌单同步、媒体服务器刷新和 Telegram 机器人，只留「刮削 + 整理」这条主线，并把单文件巨石拆成分层模块。详见 [与上游的差异](#2-与上游的差异)。

---

## 1. 它做什么 / 不做什么

**做：**

- 递归扫描源目录中的音频文件（mp3 / flac / wav / m4a / aac / ogg / opus / aiff / dsf / dff / wma / ape 等）
- 读取已有标签；标签缺失时从文件名和目录名推断标题、歌手、专辑、音轨号、年份
- 查询 MusicBrainz、网易云音乐，按**匹配置信度**决定是否采用在线结果，只补全本地缺失的字段
- 写回标题/歌手/专辑/专辑艺术家/年份/音轨号/碟号、内嵌封面、可选歌词
- 按可配置模板生成目标路径，用四种方式之一把文件放进音乐库
- 先生成**整理计划（dry-run）**再执行；整理计划是**可视化**的：每个文件一张卡片，显示封面、标签「原值 → 新值」对比、匹配度与全部在线候选，可直接点选候选或改标签，执行时按你的选择写入；冲突策略、失败原因可查，可导出 CSV 报告

**不做：**

- 不搜索、不解析、不下载任何种子或 PT 站点资源，不连接 qBittorrent / Transmission
- 不管理歌单、不刷新 Navidrome 等媒体服务器、不发 Telegram 消息
- 不提供、存储或分发任何音乐资源；它只处理你已经拥有的本地文件

---

## 2. 与上游的差异

| 项目 | 上游 MusicPilot | 本重构版 |
| --- | --- | --- |
| 定位 | 搜索 → 下载 → 整理 → 歌单 → 音乐库的全链路工具 | 只有「刮削 + 整理」 |
| PT 站点 / 种子 | NexusPHP、M-Team 等 8 个站点、Cookie/API Key、优先级 | **全部移除** |
| 下载器 | qBittorrent、Transmission、Webhook、做种监控 | **全部移除** |
| 歌单 / 媒体服务器 / 机器人 | 歌单导入同步、Navidrome、Telegram | **全部移除** |
| 转移方式 | 内部复制 / 硬链接自动判断 | **复制、移动、硬链接、软链接四种用户可选**，并附完整说明与可用性自检 |
| 后端结构 | `app.py` 12,944 行、102 个路由挤在一个闭包、无 APIRouter | `api/` 按域拆 5 个 router + `services/` + `domain/` + `adapters/`，显式依赖注入 |
| 前端结构 | `App.vue` 9,747 行单文件 | 原生单页 `web/`（约 1,300 行），**无 Node 构建阶段**，镜像更小 |
| 依赖 | 20+（SQLAlchemy、alembic、aiogram、apscheduler、bs4…） | 6 个（FastAPI / uvicorn / httpx / mutagen / pydantic / pydantic-settings），SQLite 用标准库 |
| 数据库 | PostgreSQL + SQLite 双轨、15 个 alembic 迁移 + 手写 ALTER | 单 SQLite，4 张表，启动即建，无迁移框架 |
| 认证 | 默认 `admin/musicpilot` 弱口令 | 首次启动注册管理员账号；PBKDF2 加盐存 SQLite、HMAC 会话 Cookie、登录限速；无默认口令 |
| 新增能力 | — | 转移方式四选一、写入自检、dry-run 计划、任务进度/取消、CSV 报告、置信度阈值、命名模板预设、夜间/日间/跟随系统主题 |

许可证：上游为 **GPL-3.0**，本仓库保留原 [`LICENSE`](LICENSE) 全文，并继续以 GPL-3.0-or-later 发布。原作者与上游项目的版权声明一并保留；本重构版的全部修改见本文件与 git 历史。

---

## 3. 工作流程

```
源目录扫描 → 读标签 + 文件名推断 → 在线候选查询与评分 → 合并（只补缺失字段）
     → 生成目标路径（模板 + 冲突策略） → 写标签/封面/歌词 → 按四种方式之一转移 → 记录结果
```

两个关键约定：

1. **标签写入发生在转移之前**。这是为了让硬链接 / 软链接的目标也带上完整标签；副作用是源文件的标签同样被补全。若不想改动源文件，请关闭「写入音频标签」或使用复制模式。
2. **在线结果只填空**。已有标签优先，在线候选只补本地缺失字段；置信度低于阈值时不采用，并在计划中标为「需人工确认」。

---

## 4. 快速开始（Docker）

### 4.1 目录要求（**硬链接能否用，取决于这一步**）

宿主机上准备一个**共同父目录**，下面放两个子目录：

```text
/volume1/media/
├── downloads/     ← 源目录（待刮削的文件）
└── music/         ← 音乐库（整理后的文件）
```

然后**只挂载这个父目录一次**：

```yaml
volumes:
  - /volume1/media:/media       # 容器内对应 /media/downloads 与 /media/music
  - ./data:/data                # SQLite 数据库与会话密钥
```

> ⚠️ 不要分别挂载两个子目录。硬链接能否建立，唯一判据是容器内两目录的 `st_dev` 是否相同（`os.link` 报 `EXDEV` 即不同挂载点）；挂共同父目录最稳妥，也可在 Web UI「概览 → 运行写入自检」实测。详见第 5 章。

### 4.2 启动

```bash
git clone <本仓库地址> MusicPilot
cd MusicPilot
cp .env.example .env             # 可选：按需修改，注意 compose 的用法见下
mkdir -p media/downloads media/music data
docker compose up -d --build
```

打开 `http://<NAS_IP>:8000`。**首次访问会进入「创建管理员账号」页面**：设置用户名与密码（至少 8 位）后自动登录；之后打开就是登录页。账号存在 SQLite 里，重启容器不丢失。管理员还可以在「刮削设置 → 账户」里添加其他账号。登录页与所有受保护的 API 在未登录时均不可访问，详见第 8 节。

> 从旧版本升级且设置过 `MP_ADMIN_PASSWORD` 时，启动会自动把该账号迁移成第一个管理员（用户名取 `MP_ADMIN_USERNAME`），无需重新注册。

`.env` 中 `MP_HOST_MEDIA_PATH=/volume1/media` 用于 compose 的卷映射（不设置时默认为 `./media`）。其余 `MP_*` 变量中，compose 已显式引用的会生效；**命名模板含 `{artist}` 等大括号，不做 compose 插值**，请在 Web UI「刮削设置」里修改（保存到 `/data/musicpilot.db`，重启不丢失）。源码方式直接运行时，`.env` 中的全部变量都生效。

常用环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MP_HOST_MEDIA_PATH` | `./media` | 宿主机上包含 `downloads` + `music` 的父目录 |
| `MP_HTTP_PORT` | `8000` | Web 端口 |
| `MP_ADMIN_PASSWORD` | 空 | 可选：仅用于把旧部署引导成第一个账号；全新部署请在网页上注册 |
| `MP_SESSION_SECRET` | 自动生成 | 会话签名密钥，留空时生成并保存在 `/data/session.secret` |
| `MP_BROWSABLE_ROOTS` | `/media` | 目录选择器允许浏览的容器内根目录，逗号分隔；当前源/库目录的父目录会自动加入 |
| `MP_TRANSFER_MODE` | `copy` | `copy` / `move` / `hardlink` / `symlink` |
| `MP_METADATA_SOURCES` | `local,musicbrainz,netease` | 元数据源，逗号分隔 |
| `MP_CONFIDENCE_THRESHOLD` | `0.62` | 采用在线结果的最低匹配度 |
| `MP_HTTP_PROXY` | 空 | 访问 MusicBrainz / 网易云时的代理，例如 `http://127.0.0.1:7897` |
| `MP_SOURCE_DIR` / `MP_LIBRARY_DIR` | `/media/downloads` / `/media/music` | 容器内路径 |
| `MP_WATCH_ENABLED` | `false` | 自动刮削开关（也可在「刮削设置 → 自动刮削」里改） |
| `MP_WATCH_INTERVAL` | `30` | 自动刮削检查源目录的间隔（秒，5–3600） |
| `MP_WATCH_SETTLE` | `10` | 文件最后修改距今多久才认为是「下载完成」，避免刮削半成品 |
| `PIP_INDEX_URL` | `https://pypi.org/simple` | 构建时的 PyPI 镜像 |

完整清单见 [`.env.example`](.env.example)。

### 4.3 镜像构建

```bash
docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple -t musicpilot-scraper:local .
```

镜像基于 `python:3.12-slim`，**没有 Node 构建阶段**，前端是静态文件；健康检查打 `/api/health`。

### 4.4 本地运行（不用 Docker）

```bash
uv venv .venv && uv pip install -e ".[dev]"      # 或 python -m venv + pip
uvicorn musicpilot.app:create_app --factory --host 127.0.0.1 --port 8000
```

默认源目录/音乐库是 `/media/downloads`、`/media/music`（Linux 路径）。在 Windows 或 macOS 上请用 `.env` 覆盖为本地绝对路径，例如：

```dotenv
MP_SOURCE_DIR=D:/music/inbox
MP_LIBRARY_DIR=D:/music/library
MP_DATA_DIR=D:/music/data
```

---

## 5. 四种转移方式（重点说明）

四种方式在 Web UI 的「刮削设置」里切换，也可通过 `MP_TRANSFER_MODE` 设置。「概览」页会做只读的 `st_dev` 检查，点「运行写入自检」还会在两个目录各建一个临时文件并立即删除，确认写权限以及硬链接/符号链接是否被当前文件系统支持。**「转移方式说明」页展示的是与本章同一份说明数据**（来自 `/api/transfer-modes`）。

### 5.1 速查表

| | 复制 `copy` | 移动 `move` | 硬链接 `hardlink` | 软链接 `symlink` |
| --- | --- | --- | --- | --- |
| 原理 | 复制内容到新文件 | 同设备 rename；跨设备复制后删源 | 同一 inode 的第二个目录项 | 指向源路径的文本指针 |
| 额外磁盘占用 | 完整一份 | 无（跨设备临时一份） | 几乎为零 | 几乎为零 |
| 源文件之后 | 保留 | **不存在了** | 保留 | 保留（链接依赖它） |
| 跨文件系统/挂载点 | ✅ | ✅（退化为复制+删除） | ❌ **必须同一挂载点** | ✅ |
| 删除源文件后目标 | 可用 | 可用 | 可用 | ❌ 失效（悬空链接） |
| 改目标文件会影响源吗 | 不会 | 不会（源已移走） | **会，同一份数据** | **会，写的是源文件** |
| 适合 | 稳妥默认、做种保留 | 清空暂存目录 | 同一挂载点 + 零空间 | 跨盘建立另一种目录视图 |

### 5.2 复制 `copy`

用 `copy2` 复制文件内容并保留时间戳与权限，**先写同目录的临时文件、再原子替换**为目标文件，避免中断留下半截文件。

- 优点：源文件完全不受影响，也不共享 inode；跨文件系统、跨磁盘、跨 Docker 挂载点都能用；之后重新刮削标签只改目标文件。
- 限制：占用双份空间，大文件耗时与体积成正比。
- 标签：写标签先作用于源文件，随后复制到目标，两者内容相同但**互不影响**。
- 失败条件：磁盘空间不足、目标目录不可写、源文件被独占锁定。

### 5.3 移动 `move`

同一文件系统内调用 `os.replace`，是**原子重命名，不复制数据**，大文件也是瞬间完成；跨文件系统时自动退化为「复制到临时文件 → 原子替换 → 删除源文件」。

- 优点：同设备不额外占空间，源目录被清空，不留重复文件。
- 限制：源文件不再存在，**做种 / 分享 / 需要回滚的场景不要用**；跨设备时不是单次原子操作，中断可能留下临时文件（下次运行会按冲突策略处理）；被下载器或播放器占用的文件可能移动失败。
- 标签：写标签先作用于源文件，移动后标签随文件进入音乐库。
- 失败条件：源文件被占用或只读、跨设备复制失败（此时**源文件会保留**，不会被误删）。

### 5.4 硬链接 `hardlink`

用 `os.link` 在目标目录创建**同一 inode** 的新目录项：源和目标在数据层面是同一个文件，只是有两个名字。

- 优点：零额外空间；删除任一边的名字，另一边仍可读取全部内容；内容天然同步。
- 限制（**最容易踩坑**）：
  - **必须同一文件系统且同一挂载点**。把 `/downloads` 和 `/music` 分别挂载成两个卷，即使在同一块物理盘上也会失败；Btrfs 子卷、不同分区、FAT/exFAT 与多数网络文件系统也不支持。
  - 两边共享 inode：**之后任一边被修改（重新写标签、编辑器覆盖保存、下载器续传），另一边同时变化**。它不是副本。
  - 部分网盘同步 / 备份工具不识别硬链接，可能当成两份分别上传。
- 标签：标签在建立链接**之前**写入源文件，因此两边都完整；此后修改任一边会同时影响另一边。
- 失败条件：`st_dev` 不同（`EXDEV`）、文件系统不支持、目标目录不可写。失败时任务会明确提示「请把源目录和音乐库放到同一个挂载点下，或改用复制/移动/软链接」，不会静默降级成复制。
- 适用：源与库在同一挂载点、希望零空间开销、且源文件需要保留做种。

### 5.5 软链接 `symlink`

用 `os.symlink` 写入一个路径指针，目标本身不含音频数据。

- 优点：不占空间、创建最快、可以跨文件系统和挂载点。
- 限制：
  - 源文件被移动、改名或删除后，链接**立即失效**（悬空链接），音乐库里只剩一个坏入口。
  - 播放端 / 媒体服务器必须能访问到源路径；若另一个容器没有挂载源目录，就读不到内容；部分扫描器默认不跟随符号链接。
  - Windows 上创建符号链接通常需要管理员权限或开启开发者模式；部分网络文件系统不支持。
- **相对路径选项（默认开启）**：链接写成 `../../downloads/xxx.flac` 这样的相对路径。只要源/目标目录的相对结构不变，容器内创建的链接在宿主机上同样能解析。关闭后写绝对路径（如 `/media/downloads/xxx.flac`），此时宿主机或其他容器必须能看到**完全相同**的路径，否则链接失效。
- 标签：链接没有自己的数据，**写标签实际改的是源文件**。
- 适用：只想给现有文件建立另一种目录视图，或源与库无法建立硬链接时。

### 5.6 冲突策略

目标路径已存在时（对四种方式都生效）：

- `rename`（默认）：自动改成 `标题 (2).flac`，保留两个文件。
- `skip`：跳过该文件，源文件保持不动（移动模式下也不会被删除）。
- `overwrite`：删除已有目标后写入；对硬链接/软链接会先移除旧链接。

复制/移动/硬链接/软链接在写入时都先创建 `.musicpilot-*.tmp` 临时条目再原子替换；冲突或失败时源文件不会被误删。

### 5.7 怎么选

- **拿不准就用复制**：最安全，源文件与目标完全独立。
- **NAS 上做种 + 想省空间**：把两个目录放到同一挂载点下用**硬链接**，但接受「两边是同一份数据」这个事实。
- **临时下载目录用完就清**：用**移动**。
- **只想换个目录视图、或跨盘且不想复制**：用**软链接**（默认相对路径），并确保源文件长期保留。

---

## 6. 刮削与匹配

| 元数据源 | 提供 | 说明 |
| --- | --- | --- |
| `local` | 文件内标签 + 文件名/目录名推断 | 始终参与，作为查询基准与兜底 |
| `musicbrainz` | 录音、艺术家、专辑、年份、封面 | 遵守官方 1 请求/秒限速；封面来自 Cover Art Archive |
| `netease` | 中文曲库、封面、可选歌词 | 公开搜索接口，可能被风控；失败会自动跳过 |

- 评分维度：标题 52%、艺术家 30%、专辑 13%、年份 5%，做大小写/全角半角/标点归一化，并剔除 `official`、`live`、`feat.` 等噪音词。
- 只有最高分 ≥ `MP_CONFIDENCE_THRESHOLD` 才采用在线结果；低于阈值时保留本地信息，在整理计划里标为「需人工确认」。
- 采用时执行**只补缺失字段**的合并，不会用在线结果覆盖你已有的标签。
- 在线候选结果在 SQLite 中缓存 7 天，避免重复请求。

---

## 7. 命名模板

默认：`{artist}/{album}/{track:02d} - {title}`，生成例如：

```text
Queen/A Night at the Opera/11 - Bohemian Rhapsody.flac
```

| 变量 | 含义 | 缺失时 |
| --- | --- | --- |
| `{artist}` | 歌手 | `Unknown`（可配置） |
| `{album}` | 专辑 | `Unknown` |
| `{album_artist}` | 专辑艺术家，回退到歌手 | `Unknown` |
| `{title}` | 标题 | `Unknown` |
| `{track}` / `{disc}` | 音轨号 / 碟号，支持 `{track:02d}` | `0` |
| `{year}` | 年份 | `Unknown` |
| `{ext}` | 扩展名（不含点） | 原扩展名 |
| `{filename}` | 源文件名（不含扩展名） | 回退到标题 |

Web UI 内置 5 套预设并实时显示示例。模板会自动清洗 `<>:"/\|?*` 与控制字符、处理 Windows 保留名、截断过长的路径段；**生成的目标路径必须落在音乐库目录内**，越界会直接拒绝，避免路径遍历。

---

## 8. Web 界面、字体与登录

六个页面：概览（目录状态 + 四种方式可用性自检）、源文件、整理计划、任务记录、刮削设置、转移方式说明。

- **可视化整理计划**：「整理计划」页是卡片墙——左侧封面（在线封面经 `/api/cover` 代理，取不到回退内嵌封面），右侧原值/新值并排对比（旧值加删除线）；下方列出全部在线候选（标题·歌手·专辑+匹配度），点选即切换，输入框也可直接改标签。顶部按「全部/需确认/已匹配/异常」筛选；执行时人工选择作为覆盖项提交。需老视角可点「表格视图」。
- **视觉与字体**：界面按 [Miuix](https://github.com/compose-miuix-ui/miuix) 的 `Colors.kt`/`TextStyles.kt` 重做（亮色主色 `#3482FF`、暗色 `#277AF7`，背景 `#242424`/卡片 `#2D2D2D`，大圆角分组、胶囊按钮、MIUI 开关；文字层级 32/24/20/18/17/16/14/13/11）。字体用小米 [MiSans](https://hyperos.mi.com/font/) 可变字体（`misans@5.0.0` 经 jsDelivr 加载，未打包），`clamp()` 自适应字号、`wght` 轴（150–700）自适应字重；字体不可达时回退系统字体且不阻塞加载。窄屏侧栏变横向导航、卡片自动单列。
- **目录选择器**：「刮削设置 → 目录」里源目录/音乐库各有「浏览…」按钮，打开带面包屑、上级、子目录列表的选择框，点选即回填。后端 `GET /api/files/directories` 只浏览 `MP_BROWSABLE_ROOTS`（默认 `/media`）及当前源/库父目录，拒绝相对路径、越界与逃逸软链接。
- **登录与注册**：认证始终开启。全新库首次打开显示「创建管理员账号」，注册后自动登录；之后为登录页。密码 PBKDF2-HMAC-SHA256（20 万轮+每账号随机盐）存 SQLite，会话为 HMAC 签名 HttpOnly Cookie（7 天），登录失败按 IP 限速。管理员可在「账户」里加账号；首账号创建后关闭匿名注册。公网请套 HTTPS 反代。
- **主题**：默认夜间模式，右上角一键切换，设置页可选「跟随系统」。选择存 `localStorage`，加载即应用避免闪烁。
- **任务名称**：任务列表第一列显示歌曲名/文件名（单文件直接用，多文件显示当前文件，跑完显示「自动刮削 · N 个文件」），id 仍在悬停与明细里。
- **自动刮削**：「刮削设置 → 自动刮削」开启后监控源目录，发现新文件自动排任务（类型「自动」），沿用当前转移方式/模板/标签设置；判定用「路径+大小+修改时间」去重，带「稳定等待」避免刮削未下完的文件。轮询（非 inotify，因挂载常是 NFS/SMB/FUSE）间隔与等待见环境变量表；「把现有文件标记为已处理」用于切换目录/首开时避免全量重刮。自动与手动任务共用队列，手动优先。

---

## 9. API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（公开，Docker HEALTHCHECK 用） |
| GET | `/api/auth/status` | 登录状态、`needs_setup`（是否需要先注册） |
| POST | `/api/auth/register` `/login` `/logout` | 首用户注册（之后限管理员）、登录、退出 |
| GET | `/api/about` `/api/dashboard` | 版本、目录状态、能力、最近任务 |
| GET | `/api/transfer-modes` | **四种方式的完整说明**（UI 与文档同源） |
| GET/POST | `/api/system/capabilities` | 只读检查 / 写入自检 |
| GET/PUT | `/api/settings` | 读取 / 修改运行时设置 |
| GET | `/api/files` | 扫描源目录 |
| GET | `/api/files/directories?path=...` | 目录选择器用的子目录列表（限定在允许根内） |
| POST | `/api/files/detail` | 单文件标签、推断与预览目标路径 |
| POST | `/api/jobs` | 创建预览或刮削任务（同一时刻只允许一个）；`choices` 可携带预览页的人工选择 |
| GET | `/api/cover?url=` / `?path=` | 封面代理：代理在线封面，或读取源文件内嵌封面（只允许已知图床） |
| GET | `/api/jobs` `/api/jobs/{id}` `/api/jobs/{id}/items` | 任务与明细 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| GET | `/api/jobs/{id}/report` | 导出 CSV（带 UTF-8 BOM，Excel 可直接打开） |
| GET/PUT | `/api/watch` | 自动刮削的开关、间隔与运行状态 |
| POST | `/api/watch/reset` | 把现有文件标记为已处理（清空已见列表） |

交互式文档：`/docs`（FastAPI 自带）。创建任务示例：

```bash
curl -X POST http://127.0.0.1:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"kind":"preview","paths":[],"organize":true}'
# 预览确认无误后
curl -X POST http://127.0.0.1:8000/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{"kind":"scrape","dry_run":false,"paths":[],"organize":true}'
```

---

## 10. 本地开发与测试

```bash
uv venv .venv
uv pip install -e ".[dev]"
python -m pytest tests       # 或 make test
python -m ruff check musicpilot tests
uvicorn musicpilot.app:create_app --factory --reload
```

测试覆盖：四种转移方式的真实行为（含 inode 共享、相对符号链接、冲突策略、失败不删源）、命名模板与路径遍历防护、文件名解析与置信度评分、注册/登录/会话保护、目录浏览的越界防护、API 端到端（临时目录 + 关闭在线源）。前端是静态文件，无需 `npm install`；可用 `node --check web/app.js` 检查语法。

代码结构：

```text
musicpilot/
├── app.py                  # 只负责组装：lifespan、router 注册、静态托管
├── config.py               # 环境变量 + 可运行时修改的设置模型
├── domain/                 # 纯规则：transfer / naming / matcher / models
├── adapters/               # scanner / tags / store / metadata providers
├── services/               # settings / jobs / scraper（编排）
└── api/                    # auth（注册/登录/会话）+ 4 个按域拆分的 router + context
web/                        # index.html / app.js / style.css（Miuix 风格 + MiSans VF + 双主题 + 目录选择器）
tests/
```

---

## 11. 已知限制与免责声明

- `mutagen` 无法写入的容器格式会被跳过标签写入，但文件仍可转移（任务明细会标注）。
- 在线元数据源可能限流或不可用；此时自动回退到本地标签，不会中断任务。
- 硬链接/软链接共享源文件数据，删除或改动源文件会影响音乐库；请务必理解第 5 章后再选用。
- 本项目不提供、存储、发布或分发任何音乐资源，也不提供下载渠道，只处理用户自己拥有的本地文件。用户应自行确认文件与账号的合法性并承担使用责任。
- 仅供学习和个人自托管使用；基于本项目的修改与再分发须遵守 GPL-3.0，保留许可证与版权声明。

---

## 12. 致谢

- [lzcer/MusicPilot](https://github.com/lzcer/MusicPilot) —— 原始项目，本重构版的数据模型、刮削思路与部分标签写入实践源自它。
- [MoviePilot](https://github.com/jxxghp/MoviePilot)、[musicdl](https://github.com/CharlesPikachu/musicdl)、[Jackett](https://github.com/Jackett/Jackett) —— 上游致谢的灵感来源。
- [Miuix](https://github.com/compose-miuix-ui/miuix) —— 本项目的界面配色、圆角卡片分组与文字层级参考它的 `Colors.kt` / `TextStyles.kt`（Apache-2.0）。
- **MiSans** —— 界面字体来自小米 MiSans（[字体官网](https://hyperos.mi.com/font/)），网页子集化与可变字体版本由 [dsrkafuu/misans](https://github.com/dsrkafuu/misans) 提供并经 jsDelivr 加载。按《MiSans 字体知识产权许可协议》要求，在此特别注明本项目使用了 MiSans 字体；字体文件未随本仓库分发。
- FastAPI、mutagen、MusicBrainz、Cover Art Archive、Vue 生态的上游设计参考，以及 SQLite、Python、Docker 社区。
