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
  tsbyj/musicpilot-scraper:latest
```

打开 `http://<NAS_IP>:8000`，首次访问注册管理员。

## 3. 自己构建一份

```bash
# 单架构，本机用
docker build -t musicpilot-scraper:local .

# 多架构，推到自己的仓库
docker buildx build --platform linux/amd64,linux/arm64 \
  -t <你的 Docker Hub 用户名>/musicpilot-scraper:latest --push .
```

构建阶段两个可调参数：`PYTHON_VERSION`（默认 3.12）、`PIP_INDEX_URL`（换国内 PyPI 镜像）。

## 4. 发布链路（仓库已配好，开箱即用）

根目录 `.cnb.yml` 只做入口，把三套流水线 include 进来，互不干扰：

| 平台 | 文件 | 触发 | 产物 |
| --- | --- | --- | --- |
| CNB | `.cnb/docker-hub.yml` | main 推送 / tag 推送 | `docker.io/<DOCKER_HUB_NAMESPACE>/<仓库名>`（缺凭据自动跳过） |
| CNB | `.cnb/release.yml` | tag 推送 | CNB Release + GitHub Release（校验版本号后双写） |
| CNB | `.cnb/github-sync.yml` | main 推送 | 同步代码到 GitHub |
| GitHub | `.github/workflows/docker.yml` | main 推送 / tag / PR / Release 发布 | `ghcr.io/<owner>/<repo>` |

```yaml title=".cnb.yml"
include:
  - .cnb/github-sync.yml
  - .cnb/docker-hub.yml
  - .cnb/release.yml
```

同一分支下的多段 pipeline **并发执行**，某个文件写坏了不会连带另一个；同步 GitHub 用的密钥只在
`.cnb/github-sync.yml` 里 import，镜像流水线自带 `services: docker` 与 buildx，两边不互相依赖。

产物标签（语义化标签，tag 写成 `v1.2.3` 或 `1.2.3` 都认）：

| 触发 | 标签 |
| --- | --- |
| main 推送 | `latest`、`edge`、`sha-<短 SHA>` |
| tag `v1.2.3`（正式版） | `v1.2.3`、`1.2.3`、`v1.2`、`1.2`、`v1`、`1`、`latest`、`sha-<短 SHA>` |
| tag `v1.2.3-rc.1`（预发布） | `v1.2.3-rc.1`、`1.2.3-rc.1`、`sha-<短 SHA>`，**不动** `latest` |
| 其它 tag（如 `nightly-2025`） | 原样推，不带 `latest` |
| PR | 不接，避免 PR 里就能改写 Docker Hub 标签 |

主次标签的语义：`:1` 永远指 1.x 线最新正式版，`:1.2` 指 1.2.x 线最新补丁，`:latest` 指最新正式版。
想「锁大版本、自动收小版本」就写 `:1`，想完全固定就写 `:1.2.3`。

- 手工重发某个版本：构建时选 **tag 推送**（`tag_push`）事件并填 tag 名，不用重新打 tag；Release 那边也是幂等的，重跑就是更新说明。
- 镜像构建加了 `release` 锁，同一分支/tag 连续推送时旧构建不会和新构建抢着推同一批标签。
- 构建缓存以 `:buildcache` 标签存回制品库，第二次构建只重跑改动的层。

## 5. 版本发布（Release）

`.cnb/release.yml` 挂在 `tag_push` 上，打 tag 就自动发版，三步：

1. **校验版本号**：tag 去掉 `v` 后必须与 `pyproject.toml` 的 `version`、`musicpilot/__init__.py` 的 `__version__` 完全一致，不一致直接失败——避免镜像上是 1.0.0、Release 说明里却写 1.0.1。
2. **生成说明**：优先取 `CHANGELOG.md` 里 `## [版本号]` 那段；没有就按 `git log` 自动生成（从上一个 tag 到本次）。末尾自动附镜像拉取命令。
3. **两边都发**：用内置任务 `git:release` 建 / 更新 **CNB Release**（`overlying: true`，重跑不会先删再建），再用 GitHub API 写一份 **GitHub Release**（已存在就 PATCH 更新）。版本号里带 `-` 的自动标 Pre-release。

发版流程：

```bash
# 1. 改版本号（两处一起改）+ 补 CHANGELOG
# 2. 提交
git commit -am "chore(release): 1.1.0"
# 3. 打 tag 并推送，剩下的交给流水线
git tag v1.1.0 && git push origin v1.1.0
```

GitHub Release 需要密钥仓库里已有 `GITHUB_USERNAME` / `GITHUB_ACCESS_TOKEN`（token 给 `repo` 权限）；
缺 token 时只发 CNB Release 并打一行警告，流水线不会变红。

## 6. 推到 Docker Hub

`.cnb/docker-hub.yml` 负责把镜像推到 Docker Hub：同一份 Dockerfile、同一组架构、同一套语义化标签规则。

凭据放密钥仓库 `tsbyj.666/github-sync-secrets` 的 `docker-hub.yml`：

```yaml title="docker-hub.yml"
DOCKER_HUB_NAMESPACE: your-dockerhub-name  # 镜像名前缀，用户或组织
DOCKER_HUB_USERNAME: your-dockerhub-name   # 登录名
DOCKER_HUB_TOKEN: dckr_pat_xxx             # Access Token，不是账号密码
allow_slugs: tsbyj.666/musicpilot-scraper
allow_events: [push, tag_push, api_trigger]
allow_branches: [main]
```

- `DOCKER_HUB_TOKEN` 在 Docker Hub 的 Account settings → Personal access tokens 里建，权限给 Read & Write。
- `allow_*` 三行必须写：密钥仓库文件默认只允许仓库负责人/管理员触发的流水线引用，
  `push` / `tag_push` 自动触发时要靠 `allow_*` 放行（规则一旦声明就完全按规则校验，不再看触发者角色）。
  `.cnb/release.yml` 引用的是同一密钥仓库里的 `github-sync.yml`，那边同样要带上 `allow_events: [tag_push, api_trigger]`。
- 文件不存在 → 整段流水线跳过（`imports` 带 `ignoreError`），其它流水线照跑；
  文件在但缺某一项 → 只跳过登录与推送，仍做一次本地构建验证，不会把流水线刷成红的。
- 不接 `pull_request`：PR 里就能改写 Docker Hub 上的标签，没必要冒这个险。

## 7. 权限怎么调

容器默认以 `root` 启动，入口脚本按 `PUID:PGID` 建号、把 `/data` 与挂载的媒体目录 chown 后 `gosu` 降权。

- 写不进 `/data` 或建不了目标目录：`id <宿主用户名>` 查出 uid/gid，同步改 `PUID` / `PGID`。
- NAS 上多人共享媒体目录：`UMASK=000`（或 `002`），否则别人只读。
- 想彻底不用 root：`docker run --user 1000:1000 ...`，此时入口脚本跳过 chown，直接运行。

## 8. 别忘了的坑

- **硬链接**：源与库必须在同一挂载点（一个 `/media` 挂进去），跨挂载点会失败，界面「写入自检」可实测。
- **时区**：`TZ` 只影响容器时区，日志与任务时间随之变化。
- **升级**：镜像升级不碰 `/data`（SQLite 与 `session.secret` 都在里面），换 tag 重启即可。
- **反向代理**：容器内是 HTTP，公网部署请在前面套 HTTPS。
