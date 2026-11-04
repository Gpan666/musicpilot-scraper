<p align="center">  
  <img src="docs/assets/musicpilot-icon.png" alt="MusicPilot" width="360">  
</p>

# MusicPilot Scraper

**只做一件事：把一堆音频文件刮削成整齐的音乐库。**

扫描源目录 → 读标签、缺了就从文件名推断 → 查 MusicBrainz / 网易云补全 → 写回标签与封面 → 按命名模板转移进库（复制 / 移动 / 硬链接 / 软链接任选）。整理前先出**可视化预览**：每文件一张卡片，封面、标签「原值 → 新值」、全部在线候选，点选即改，确认后才落盘。Docker 一键部署，自带 Miuix 风格 Web 界面与登录。

> [lzcer/MusicPilot](https://github.com/lzcer/MusicPilot) 的**重构精简版**：砍掉 PT 索引、下载器、歌单同步、媒体服务器刷新和 Telegram 机器人，只留「刮削 + 整理」，并把单文件巨石拆成分层模块。

## 它做什么 / 不做什么

- **做**：递归扫描音频 → 读/推断标签 → 查 MusicBrainz、网易云按匹配置信度补全缺失字段 → 写回标签/封面/歌词 → 按模板转移（四选一）→ 先出 dry-run 可视化计划，确认后落盘，可导出 CSV 报告。
- **不做**：不下载/解析任何种子或 PT 资源，不连 qBittorrent/Transmission；不管理歌单、不刷媒体服务器、不发 Telegram；不提供/存储/分发任何音乐。

## 快速开始（Docker）

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

> 硬链接能否用，取决于 `downloads` 与 `music` 是否在同一挂载点：挂共同父目录 `/volume1/media:/media` 最稳妥（分别挂载两个子目录会失败）。

常用环境变量：`MP_HOST_MEDIA_PATH`（媒体父目录）、`MP_TRANSFER_MODE`（copy/move/hardlink/symlink，默认 copy）、`MP_CONFIDENCE_THRESHOLD`（默认 0.62）、`MP_BROWSABLE_ROOTS`（目录选择器根，默认 `/media`）。完整见 `.env.example`。

## 四种转移方式

| 方式           | 源文件之后 | 额外空间 | 跨挂载点    | 要点                  |
| ------------ | ----- | ---- | ------- | ------------------- |
| 复制 copy      | 保留    | 双份   | ✅       | 最稳妥默认               |
| 移动 move      | 删除    | 无    | ✅       | 清空暂存用，别用于做种         |
| 硬链接 hardlink | 保留    | ≈0   | ❌ 需同挂载点 | 与源共享 inode，改一边另一边也变 |
| 软链接 symlink  | 保留    | ≈0   | ✅       | 源删则链接失效；默认相对路径      |

拿不准用「复制」；同挂载点想省空间用「硬链接」；用完即清用「移动」；只想建目录视图用「软链接」。

## Web 界面与登录

六个页面：概览、源文件、整理计划、任务记录、刮削设置、转移方式说明。

- **可视化整理计划**：卡片墙，封面 + 标签原值/新值对比 + 全部在线候选，点选即改；顶部按「全部/需确认/已匹配/异常」筛选。
- **界面**：Miuix 配色（亮 `#3482FF` / 暗 `#277AF7`）+ 小米 MiSans 可变字体（`clamp()` 自适应），默认夜间模式，可跟随系统。
- **目录选择器**：源/库目录「浏览…」点选，后端限定在 `MP_BROWSABLE_ROOTS` 内，拒绝越界与逃逸。
- **登录**：认证常开，首次注册管理员（PBKDF2 加盐 + HMAC 会话 Cookie + 登录限速），之后为登录页；公网请套 HTTPS 反代。

## API 一览

| 方法       | 路径                                      | 说明               |
| -------- | --------------------------------------- | ---------------- |
| GET      | `/api/health`                           | 健康检查             |
| GET      | `/api/auth/status`                      | 登录状态、是否需注册       |
| POST     | `/api/auth/register` `/login` `/logout` | 注册/登录/退出         |
| GET      | `/api/about` `/api/dashboard`           | 版本、目录状态、能力       |
| GET      | `/api/transfer-modes`                   | 四种方式说明           |
| GET/POST | `/api/system/capabilities`              | 只读检查 / 写入自检      |
| GET/PUT  | `/api/settings`                         | 运行时设置            |
| GET      | `/api/files`                            | 扫描源目录            |
| GET      | `/api/files/directories`                | 目录选择子目录列表        |
| POST     | `/api/files/detail`                     | 单文件标签/推断/预览路径    |
| POST     | `/api/jobs`                             | 创建预览/刮削任务（同时仅一个） |
| GET      | `/api/jobs` `/{id}` `/{id}/items`       | 任务与明细            |
| POST     | `/api/jobs/{id}/cancel`                 | 取消任务             |
| GET      | `/api/jobs/{id}/report`                 | 导出 CSV           |
| GET/PUT  | `/api/watch`                            | 自动刮削开关与状态        |
| POST     | `/api/watch/reset`                      | 标记现有文件已处理        |

交互式文档：`/docs`（FastAPI 自带）。

## 致谢与许可

源自 [lzcer/MusicPilot](https://github.com/lzcer/MusicPilot)（GPL-3.0）；界面参考 [Miuix](https://github.com/compose-miuix-ui/miuix)，字体用小米 [MiSans](https://hyperos.mi.com/font/)。本仓库以 GPL-3.0-or-later 发布，保留原 `LICENSE` 与版权声明。
