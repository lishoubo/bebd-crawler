# bebd-crawler

BEBD 数据中心的本地浏览器采集工具。

项目使用 Python 和 DrissionPage 驱动可见的 Chrome 浏览器，登录由用户手工完成；采集时由
浏览器正常生成请求，程序拦截 `goodsSearch` 接口的 JSON 响应。当前不依赖数据库、Redis、
消息队列或后台服务。

## 当前状态

已经可用：

- 打开浏览器并手工登录 BEBD；
- 将完整 cookie 保存到本地安全目录；
- 在新的浏览器中恢复 cookie 并验证登录态；
- 按一个或多个搜索词采集商品；
- 自动切换美修指数降序、翻页并拦截 API 响应；
- 按页写入 JSONL，并生成运行摘要。

## 环境要求

- macOS；
- Google Chrome；
- Python 3.11、3.12 或 3.13；
- `uv`。

本机当前使用 Python 3.12。

## 安装

进入项目目录：

```bash
cd /Users/lishoubo/p/projects/bebd-crawler
```

安装运行及开发依赖：

```bash
UV_CACHE_DIR=.uv-cache uv sync --python 3.12 --extra dev
```

安装完成后，命令位于：

```text
.venv/bin/bebd-crawler
```

## 登录并保存 cookie

执行：

```bash
.venv/bin/bebd-crawler login
```

运行过程：

1. 程序打开一个可见的独立 Chrome 浏览器；
2. 在浏览器中手工完成 BEBD 登录；
3. 确认已经进入数据中心页面；
4. 回到终端按 Enter；
5. 程序保存 cookie 并关闭浏览器。

cookie 默认保存到：

```text
.local/auth/bebd-cookies.json
```

## 验证登录态

执行：

```bash
.venv/bin/bebd-crawler session-check
```

程序会打开新的可见浏览器，注入本地 cookie，访问数据中心搜索页并检查是否仍然处于登录状态。
检查结束后浏览器自动关闭。

如需在验证后暂时保持窗口开启：

```bash
.venv/bin/bebd-crawler session-check --keep-open
```

检查完毕后回到终端按 Enter，程序关闭浏览器。

如果 cookie 已失效，重新运行：

```bash
.venv/bin/bebd-crawler login
```

## 数据采集

采集“欧莱雅”前 90 条：

```bash
.venv/bin/bebd-crawler crawl --brand 欧莱雅
```

`--limit` 默认就是 90，也可以明确指定：

```bash
.venv/bin/bebd-crawler crawl --brand 欧莱雅 --limit 90
```

如果本地 cookie 已失效，`crawl` 会停留在可见的登录页面，每 2 秒检查一次。直接在该浏览器中
手工登录即可，程序检测到搜索页面后会自动继续。默认一直等待，可按 `Ctrl-C` 取消。

如需限制等待时间，例如最多等待 10 分钟：

```bash
.venv/bin/bebd-crawler crawl --brand 欧莱雅 --login-wait 600
```

一次采集多个搜索词：

```bash
.venv/bin/bebd-crawler crawl \
  --brand 欧莱雅 \
  --brand 雅诗兰黛
```

从清单文件读取：

```bash
.venv/bin/bebd-crawler crawl --brands-file config/brands.example.yaml
```

不传 `--brand` 和 `--brands-file` 时，优先读取 `config/brands.yaml`；如果该文件不存在，
使用示例文件 `config/brands.example.yaml`。

采集流程设计如下：

1. 启动一个可见的独立浏览器；
2. 恢复 `.local/auth/bebd-cookies.json`；
3. 打开 BEBD 数据中心搜索页；若进入登录页，则轮询等待用户手工登录；
4. 输入搜索词，例如“欧莱雅”；
5. 选择“美修指数”从高到低；
6. 监听页面发出的 `POST /auth/goods/goodsSearch` 请求；
7. 从响应的 `data.data` 中获取商品数据；
8. 自动翻页并持续拦截响应；
9. 翻页前随机滚动、悬停并等待，页面返回后再次随机停顿；
10. 将结果按页写为本地 JSONL 和运行摘要；
11. 从当前页面重新导出 cookie，原子覆盖本地 cookie 文件；
12. 无论成功、失败或用户中断，均关闭浏览器。

页面请求含动态生成的 `currDate`、`str` 和 `sign`。工具不会硬编码或自行伪造签名，
而是让已登录页面正常发出请求后拦截响应。

每次运行会创建独立目录：

```text
data/<运行时间>/欧莱雅.jsonl
data/<运行时间>/summary.json
```

JSONL 按商品 `mid` 去重，并附加 `_searchKeyword`、`_page` 和 `_capturedAt` 采集元数据。
每页完成后立即刷新到磁盘，异常中断时会保留已经写入的部分结果。

### Cookie 回刷

平台可能在正常访问过程中更新或续期 cookie。`crawl` 完成数据请求后，会从当前已登录浏览器
重新导出完整 cookie，并原子覆盖 `.local/auth/bebd-cookies.json`，让下一次运行使用最新登录态。

采集中途失败时，只要程序已经确认页面仍处于登录状态，也会在关闭浏览器前尽量回刷。若启动时
已经跳转到登录页，则不会用匿名 cookie 覆盖原文件，而是提示重新执行 `bebd-crawler login`。
`session-check` 验证成功后也会回刷一次。

## 本地文件与安全

以下目录已写入 `.gitignore`：

- `.local/`：cookie、浏览器诊断材料和临时踩点结果；
- `data/`：正式采集结果；
- `.venv/`、`.uv-cache/`：本地 Python 环境和缓存。

注意：

- 不要提交或分享 `.local/auth/bebd-cookies.json`；
- 不要把 `token`、`sessid`、Cookie 等凭证复制到代码、文档、Issue 或测试夹具；
- 日志只记录 cookie 数量，不记录 cookie 值；
- cookie 文件创建后权限为 `0600`，仅当前用户可读写；
- 仅采集当前账号正常可见且有权限访问的数据。

## 测试

运行登录态相关测试：

```bash
.venv/bin/pytest tests/test_auth.py -q
```

运行静态检查：

```bash
.venv/bin/ruff check bebd_crawler tests
```

当前验证基线：

```text
18 passed
All checks passed!
```

## 相关文档

- `openspec/changes/bootstrap-bebd-local-crawler/`：需求、设计和任务清单；
- `docs/踩点/搜索01.md`：搜索页面、排序控件及 `goodsSearch` 接口踩点记录；
- `docs/爬取逻辑.md`：品牌和商品覆盖顺序。
