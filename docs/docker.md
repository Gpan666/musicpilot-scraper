# 通用镜像：把 MusicPilot Scraper 做成一个可移植的容器

目标：一份镜像跑在 amd64 / arm64 / armv7 上，别人不必懂 Python，也不必自己构建。

## 1. 镜像本身为什么「通用」

| 关注点 | 做法 |
| --- | --- |
| 架构 | 发布多架构 manifest（`linux/amd64,linux/arm64,linux/arm/v7`），拉取时由 Docker 自动选择 |
| 前端 | 原生 HTML/JS，随 wheel 装进 venv（`musicpilot/web`），镜像里无需单独拷目录 |
| 依赖 | 多阶段构建，运行镜像只带 `/opt/venv` + `gosu` + `tzdata`，不含 dev 依赖 |
| 权限 | `PUID` / `PGID` / `UMASK`，NAS 上目录属主不是 1000 也能写 |
| 路径 | 全部走 `MP_*` 环境变量，挂载点变了不用重新构建 |
| 健康检查 | 内置 `HEALTHCHECK` 打 `/api/health`，编排系统可直接用 |

镜像内只有两个约定：数据在 `/data`，媒体在 `/media`（源 `/media/downloads`、库 `/media/music`）。

## 2. 用现成镜像跑

```bash
docker run -d --name musicpilot \
  -p 8000:8000 \
  -e PUID=1000 -e PGID=1000 -e TZ=Asia/Shanghai \
  -v /volume1/media:/media \
  -v /volume1/docker/musicpilot:/data \
  --restart unless-stopped \
  docker.cnb.cool/tsbyj.666/musicpilot-scraper:latest
```

打开 `http://<NAS_IP>:8000`，首次访问注册管理员。

## 3. 自己构建一份

```bash
# 单架构，本机用
docker build -t musicpilot-scraper:local .

# 多架构，推到自己的仓库
docker buildx build --platform linux/amd64,linux/arm64 \
  -t docker.cnb.cool/<你的命名空间>/musicpilot-scraper:latest --push .
```

构建阶段两个可调参数：`PYTHON_VERSION`（默认 3.12）、`PIP_INDEX_URL`（换国内 PyPI 镜像）。

## 4. 发布链路（仓库已配好，开箱即用）

根目录 `.cnb.yml` 只做入口，把两套流水线 include 进来，互不干扰：

| 平台 | 文件 | 触发 | 产物 |
| --- | --- | --- | --- |
| CNB | `.cnb/docker.yml` | main 推送 / tag 推送 / PR | `<CNB_DOCKER_REGISTRY>/<仓库路径>`（PR 只构建不推送） |
| CNB | `.cnb/github-sync.yml` | main 推送 | 同步代码到 GitHub（原有逻辑，未改动） |
| GitHub | `.github/workflows/docker.yml` | main 推送 / tag / PR / Release 发布 | `ghcr.io/<owner>/<repo>` |

```yaml title=".cnb.yml"
include:
  - .cnb/github-sync.yml
  - .cnb/docker.yml
```

同一分支下的多段 pipeline **并发执行**，某个文件写坏了不会连带另一个；同步 GitHub 用的密钥只在
`.cnb/github-sync.yml` 里 import，镜像流水线自带 `services: docker` 与 buildx，两边不互相依赖。

产物标签：

| 触发 | 标签 |
| --- | --- |
| main 推送 | `latest`、`sha-<短 SHA>` |
| tag 推送 | `<tag 名>`、`sha-<短 SHA>`；`v1.2.3` 这种正式版本同时覆盖 `latest` |
| PR | 只在 amd64 构建验证，不推送 |

- 手工重发某个版本：构建时选 **tag 推送**（`tag_push`）事件并填 tag 名，不用重新打 tag。
- 镜像构建加了 `release` 锁，同一分支/tag 连续推送时旧构建不会和新构建抢着推同一批标签。
- 构建缓存以 `:buildcache` 标签存回制品库，第二次构建只重跑改动的层。

## 5. 权限怎么调

容器默认以 `root` 启动，入口脚本按 `PUID:PGID` 建号、把 `/data` 与挂载的媒体目录 chown 后 `gosu` 降权。

- 写不进 `/data` 或建不了目标目录：`id <宿主用户名>` 查出 uid/gid，同步改 `PUID` / `PGID`。
- NAS 上多人共享媒体目录：`UMASK=000`（或 `002`），否则别人只读。
- 想彻底不用 root：`docker run --user 1000:1000 ...`，此时入口脚本跳过 chown，直接运行。

## 6. 别忘了的坑

- **硬链接**：源与库必须在同一挂载点（一个 `/media` 挂进去），跨挂载点会失败，界面「写入自检」可实测。
- **时区**：`TZ` 只影响容器时区，日志与任务时间随之变化。
- **升级**：镜像升级不碰 `/data`（SQLite 与 `session.secret` 都在里面），换 tag 重启即可。
- **反向代理**：容器内是 HTTP，公网部署请在前面套 HTTPS。
