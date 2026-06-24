# chatgpt2api-reg

这是一个适合 Docker 部署的独立注册器版本，提供 Web 控制台。

## 云端部署

推荐服务器直接使用 GitHub Container Registry 镜像，不在服务器本地构建：

```bash
git clone https://github.com/FlyLjx/regs.git
cd regs
docker compose pull
docker compose up -d
```

更新版本：

```bash
git pull
docker compose pull
docker compose up -d
```

## 开发模式

开发模式会挂载整个项目源码，并启用 `uvicorn --reload`。

首次启动需要构建镜像：

```bash
docker compose up --build
```

后续如果你修改了下面这些文件，通常不需要重新构建镜像：

- Python 代码
- `web/*.js`
- `web/*.css`
- `web/*.html`

保存后容器会自动热重载。

如果偶尔没有自动刷新，可以执行：

```bash
docker compose restart
```

## 什么时候需要重新 build

只有这些情况通常需要重新构建：

- 改了 `Dockerfile`
- 改了 `pyproject.toml`
- 新增或升级 Python 依赖
- 改了镜像里的系统层

重新构建命令：

```bash
docker compose build
docker compose up -d
```

## 访问地址

- `http://localhost:8080`
- `FlareSolverr: http://localhost:8191`
- `WARP SOCKS5: socks5://localhost:1080`

## 持久化目录

以下内容位于项目目录下，并会随着 `./:/app` 一起映射到宿主机：

- `reg/register.json`
- `reg/settings.json`
- `reg/.env`
- `reg/config.json`
- `reg/output`
- `reg/data`

## 首次启动

首次启动时，如果下面这些文件不存在，程序会自动生成默认模板：

- `reg/register.json`
- `reg/settings.json`
- `reg/.env`
- `reg/config.json`

可以参考这些示例文件：

- `reg/register.example.json`
- `reg/settings.example.json`
- `reg/.env.example`
- `reg/config.example.json`

## 说明

- Web 页面支持保存配置、开始注册、检查补号、开启/停止监控、导出日志。
- `docker compose up -d` 会同时拉起 `chatgpt2api-reg`、`flaresolverr` 和 `warp` 三个容器。
- 开启“WARP 注册”后，注册流会优先走 `socks5://warp:1080` 出口。
- Docker 环境下建议将 FlareSolverr 地址填写为 `http://flaresolverr:8191`。
- 实际注册是否成功仍取决于邮箱供应商、代理、FlareSolverr 和上游环境是否可用。
