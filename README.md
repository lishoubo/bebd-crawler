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
- 接收商品 `mid`，拦截产品详情和成分响应并按品牌落盘。

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

### 搜索全集与断点续跑

将一个搜索词的账号可访问结果持续翻页，并只保存后续详情采集需要的商品 `mid`：

```bash
.venv/bin/bebd-crawler search-all --brand 欧莱雅
```

输出及进度文件：

```text
data/欧莱雅/search-result-all.json
data/欧莱雅/search-result-mids-all.json
data/欧莱雅/state.md
```

`search-result-all.json` 是早期已经采集的完整搜索响应快照，继续保留但不再更新。
`search-result-mids-all.json` 是当前使用的轻量文件，采用原始 MID 一行一个；程序每完成一页
只向该文件追加尚未出现的 MID，并更新 `state.md`。再次执行同一命令时，会读取 MID 和进度，在完成搜索及
美修指数降序排序后，在页面下方输入“下一次开始页”，再点击顶部搜索框使跳页输入框失焦并触发
跳转，不会从第一页重新翻。
结果始终按 `mid` 去重，页码断点以 `state.md` 为准。

## 产品详情采集

传入一个商品 `mid`：

```bash
.venv/bin/bebd-crawler product-detail --mid c140d305e626346df8a8b8c6640d3fb4
```

程序会打开可见浏览器、恢复 cookie，并且只打开一次目标产品详情地址。若当前 URL 不是携带对应
MID 的产品详情路由，程序只轮询等待，不刷新或主动改路由。详情页就绪后随机向下滚动一小段并
停留，再拦截页面正常发出的以下响应：

```text
POST /auth/goods/detail/data
POST /auth/goods/detail/ingredient
```

结果按接口响应中的品牌名称和命令传入的 `mid` 保存：

```text
data/欧莱雅/c140d305e626346df8a8b8c6640d3fb4/data.json
data/欧莱雅/c140d305e626346df8a8b8c6640d3fb4/ingredient.json
```

同一页面可能请求两次 `ingredient`。程序按完整 JSON 内容去重：内容相同只保存
`ingredient.json`；内容确实不同时继续保存为 `ingredient-2.json`、`ingredient-3.json`。
例如实测示例商品的两份响应分别是完整成分视图，以及“常规成分 + 微量成分”的拆分视图，
因此会保留两份。完成后程序回刷 cookie，并无论成功、失败或中断都关闭浏览器。

### 批量产品详情与断点续跑

从品牌目录下的 `search-result-mids-all.json` 顺序读取 MID，本次新采集 10 个：

```bash
.venv/bin/bebd-crawler product-details --brand 欧莱雅 --limit 10
```

已有完整详情目录的 MID 自动跳过，不计入本次新增数量。真正发起详情请求的两个商品之间随机
等待 1–10 秒。每成功一个商品都会更新 `data/欧莱雅/detail-state.md`，其中记录 MID 总数、
完整详情数、下一行、下一 MID 和最近完成 MID。再次运行同一命令即可从断点继续。

批量流程共用一个可见浏览器。当前 URL 不是对应 MID 的产品详情页时只等待；进入目标详情页后
才拦截接口。每个商品成功后立即回刷 Cookie。

详情接口返回 `retCode=30019` 时，程序不会退出或推进 MID，而是将进度标记为“遇到图片验证码，
已暂停；完成验证后自动恢复”，并保持浏览器和网络监听。人工完成验证码后，页面自动重发请求；
若未自动重发，可手工刷新当前详情页。捕获到同一 MID 的正常响应后程序自动继续。

### Cookie 回刷

平台可能在正常访问过程中更新或续期 cookie。`crawl` 完成数据请求后，会从当前已登录浏览器
重新导出完整 cookie，并原子覆盖 `.local/auth/bebd-cookies.json`，让下一次运行使用最新登录态。

采集中途失败时，只要程序已经确认页面仍处于登录状态，也会在关闭浏览器前尽量回刷。若启动时
已经跳转到登录页，则不会用匿名 cookie 覆盖原文件，而是提示重新执行 `bebd-crawler login`。
`session-check` 验证成功后也会回刷一次；使用 `--keep-open` 时，按 Enter 关闭前会再次回刷。
`product-detail` 同样在详情响应获取完成后回刷。

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
33 passed
All checks passed!
```

## 相关文档

- `openspec/changes/bootstrap-bebd-local-crawler/`：需求、设计和任务清单；
- `docs/踩点/搜索01.md`：搜索页面、排序控件及 `goodsSearch` 接口踩点记录；
- `docs/踩点/产品详情.md`：产品详情页及详情、成分接口踩点记录；
- `docs/爬取逻辑.md`：品牌和商品覆盖顺序。
