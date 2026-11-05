<p align="center">
  <img src="docs/assets/musicpilot-icon.png" alt="MusicPilot" width="420">
</p>

# MusicPilot Scraper

**只做一件事：把一堆音频文件刮削成整齐的音乐库。**

扫描源目录 → 读标签、缺失就从文件名推断 → 查 MusicBrainz / 网易云补全 → 写回标签与封面 → 按命名模板转移进库（复制 / 移动 / 硬链接 / 软链接任选）。整理前先出**可视化预览**：每文件一张卡片，封面、标签「原值 → 新值」对比、全部在线候选，点选即改，确认后才落盘。Docker 一键部署，自带 Miuix 风格 Web 界面与登录，另有源目录监控的自动刮削。

> 这是 [lzcer/MusicPilot](https://github.com/lzcer/MusicPilot) 的**重构精简版**：砍掉 PT 索引、下载器、歌单同步、媒体服务器刷新和 Telegram 机器人，只留「刮削 + 整理」主线，并把单文件巨石拆成分层模块。详见[与上游的差异](#2-与上游的差异)。

## 1. 它做什么 / 不做什么

**做：** 递归扫描音频 → 读 / 推断标签 → 查 MusicBrainz、网易云按匹配置信度补全缺失字段 → 写回标题 / 歌手 / 专辑 / 年份 / 音轨 / 封面 / 可选歌词 → 按可配置模板生成目标路径，用四种方式之一把文件放进音乐库 → 先出 dry-run 可视化计划再执行，可导出 CSV 报告。

**不做：** 不搜索、不解析、不下载任何种子或 PT 站点资源，不连 qBittorrent / Transmission；不管理歌单、不刷 Navidrome 等媒体服务器、不发 Telegram；不提供 / 存储 / 分发任何音乐，只处理你已有的本地文件。

## 2. 与上游的差异（本次重构做了什么）

| 项目 | 上游 MusicPilot | 本重构版 |
| --- | --- | --- |
| 定位 | 搜索 → 下载 → 整理 → 歌单 → 音乐库全链路 | 只有「刮削 + 整理」 |
| PT 站点 / 下载器 / 歌单 / 媒体服务器 / 机器人 | 全部内置 | **全部移除** |
| 转移方式 | 复制 / 硬链接自动判断 | **复制、移动、硬链接、软链接四选一**，含可用性自检 |
| 后端 | `app.py` 一万多行、路由挤在一个闭包 | `api/` 按域拆多个 router + `services/` + `domain/` + `adapters/`，依赖注入 |
| 前端 | `App.vue` 九千多行单文件 | 原生单页 `web/`（约 1.3k 行），**无 Node 构建**，镜像更小 |
| 依赖 | 20+ | 6 个（FastAPI / uvicorn / httpx / mutagen / pydantic ×2），SQLite 用标准库 |
| 数据库 | PostgreSQL + SQLite 双轨、15 个迁移 | 单 SQLite，4 张表，启动即建 |
| 认证 | 默认 `admin/musicpilot` 弱口令 | 首次注册管理员；PBKDF2 加盐 + HMAC 会话 Cookie + 登录限速 |

**本次新增能力：** 四种转移方式可选、写入自检、`dry-run` 可视化计划、任务进度 / 取消、CSV 报告、置信度阈值、命名模板预设、夜间 / 日间 / 跟随系统主题、源目录「浏览…」点选器、先注册后登录的认证、源目录监控的**自动刮削**。

## 3. 工作流程

```
源目录扫描 → 读标签 + 文件名推断 → 在线候选查询与评分 → 合并（只补缺失字段）
   → 生成目标路径（模板 + 冲突策略）→ 写标签 / 封面 / 歌词 → 按四种方式之一转移 → 记录结果
```

两个约定：① **标签写入发生在转移之前**，让硬链接 / 软链接的目标也带完整标签（源文件标签同时被补全，不想改源请关闭「写入标签」或用复制模式）；② **在线结果只填空**，已有标签优先，置信度低于阈值时不采用并标「需人工确认」。

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

不想自己构建就用发布好的多架构镜像（amd64 / arm64 / armv7），一份镜像跑遍 NAS 与服务器。
CNB 制品库与 Docker Hub 同步发布，按网络可达性挑一个：

```bash
docker run -d --name musicpilot \
  -p 8000:8000 \
  -e PUID=1000 -e PGID=1000 -e TZ=Asia/Shanghai \
  -v /volume1/media:/media \
  -v /volume1/docker/musicpilot:/data \
  --restart unless-stopped \
  docker.cnb.cool/tsbyj.666/musicpilot-scraper:latest   # 或 tsbyj/musicpilot-scraper:latest
```

- `PUID` / `PGID` / `UMASK`：容器内进程的运行身份与文件权限，挂载目录写不进去时改成宿主属主（`id 用户名` 可查）。
- 容器内只有两个约定：数据落 `/data`，媒体在 `/media`（源 `/media/downloads`、库 `/media/music`）。
- 镜像由 `.cnb/docker.yml`（CNB 制品库）、`.cnb/docker-hub.yml`（Docker Hub）、`.github/workflows/docker.yml`（GHCR）三处多架构构建，均经 `.cnb.yml` include 生效；构建细节、发布链路与权限排查见 [docs/docker.md](docs/docker.md)。

常用环境变量：`PUID`/`PGID`/`UMASK`（容器运行身份）、`MP_HOST_MEDIA_PATH`（媒体父目录）、`MP_TRANSFER_MODE`（copy/move/hardlink/symlink，默认 copy）、`MP_CONFIDENCE_THRESHOLD`（默认 0.62）、`MP_BROWSABLE_ROOTS`（目录选择器根，默认 `/media`）、`MP_WATCH_ENABLED`（自动刮削，默认 false）。完整见 `.env.example`。

## 5. 四种转移方式

| 方式 | 源文件之后 | 额外空间 | 跨挂载点 | 要点 |
| --- | --- | --- | --- | --- |
| 复制 copy | 保留 | 双份 | ✅ | 最稳妥默认 |
| 移动 move | 删除 | 无 | ✅ | 清空暂存用，别用于做种 |
| 硬链接 hardlink | 保留 | ≈0 | ❌ 需同挂载点 | 与源共享 inode，改一边另一边也变 |
| 软链接 symlink | 保留 | ≈0 | ✅ | 源删则链接失效；默认相对路径 |

拿不准用「复制」；同挂载点想省空间用「硬链接」；用完即清用「移动」；只想建目录视图用「软链接」。目标已存在时按冲突策略 `rename`（默认）/ `skip` / `overwrite` 处理。

## 6. Web 界面、字体与登录

六个页面：概览、源文件、整理计划、任务记录、刮削设置、转移方式说明。

- **可视化整理计划**：卡片墙，封面 + 标签原值 / 新值对比 + 全部在线候选，点选即改；顶部按「全部 / 需确认 / 已匹配 / 异常」筛选；执行时人工选择作为覆盖项提交。
- **界面与字体**：按 [Miuix](https://github.com/compose-miuix-ui/miuix) 配色（亮 `#3482FF` / 暗 `#277AF7`）+ 小米 [MiSans](https://hyperos.mi.com/font/) 可变字体（`clamp()` 自适应），默认夜间模式。
- **目录选择器**：源 / 库目录「浏览…」点选，后端限定在 `MP_BROWSABLE_ROOTS` 内。
- **登录与注册**：认证常开，首次注册管理员（PBKDF2 加盐 + HMAC 会话 Cookie + 登录限速），之后为登录页；公网请套 HTTPS。
- **自动刮削**：开启后监控源目录，**只刮「库里还没有」的文件**——开启那一刻的存量文件只登记不刮削；已经整理进音乐库的文件（同名、文件名互为后缀、或整批顺序配对）自动过滤，不会被刮第二遍；写标签改动源文件时间戳也不会再触发；已处理记录落库，重启不重刮；下载器的 `.part` / `.!qB` / `.aria2` 临时文件不触发；轮询而非 inotify（适配 NFS / SMB / FUSE），大小或修改时间还在变的文件先等一轮再刮。

## 7. API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/auth/status` | 登录状态、是否需注册 |
| POST | `/api/auth/register` `/login` `/logout` | 注册 / 登录 / 退出 |
| GET | `/api/about` `/api/dashboard` | 版本、目录状态、能力 |
| GET | `/api/transfer-modes` | 四种方式说明 |
| GET/POST | `/api/system/capabilities` | 只读检查 / 写入自检 |
| GET/PUT | `/api/settings` | 运行时设置 |
| GET | `/api/files` | 扫描源目录 |
| GET | `/api/files/directories` | 目录选择子目录列表 |
| POST | `/api/files/detail` | 单文件标签 / 推断 / 预览路径 |
| POST | `/api/jobs` | 创建预览 / 刮削任务（同时仅一个） |
| GET | `/api/jobs` `/{id}` `/{id}/items` | 任务与明细 |
| POST | `/api/jobs/{id}/cancel` | 取消任务 |
| GET | `/api/jobs/{id}/report` | 导出 CSV |
| GET/PUT | `/api/watch` | 自动刮削开关 |
| POST | `/api/watch/reset` | 标记现有文件已处理 |

交互式文档：`/docs`。

## 8. 致谢与许可

源自 [lzcer/MusicPilot](https://github.com/lzcer/MusicPilot)（GPL-3.0）；界面参考 [Miuix](https://github.com/compose-miuix-ui/miuix)，字体用小米 [MiSans](https://hyperos.mi.com/font/)。本仓库以 GPL-3.0-or-later 发布，保留原 `LICENSE` 与版权声明。
