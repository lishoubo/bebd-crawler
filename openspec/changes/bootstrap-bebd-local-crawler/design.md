# BEBD 本地爬虫最小闭环 - Design

## 1. 技术选择

采用 Python 3.11+ 与 DrissionPage，参考 `rms-rpa-worker` 的浏览器生命周期和 cookie 处理方式，
但保留为一个轻量的本地 CLI 工具。

建议命令：

```bash
bebd-crawler login
bebd-crawler crawl --brand 欧莱雅
bebd-crawler search-all --brand 欧莱雅
bebd-crawler product-detail --mid <商品MID>
```

第一阶段两个命令均使用可见浏览器；后续稳定后再评估 headless。

浏览器按单次命令管理生命周期：命令启动时创建独立的可见浏览器，运行期间保持开启，
命令成功、失败、用户中断或发生异常时均在 `finally` 中关闭浏览器。不使用后台常驻浏览器，
也不复用用户日常 Chrome 配置。

## 2. 模块边界

```text
CLI
├── login       打开浏览器 → 用户手工登录 → 校验登录成功 → 保存 cookie
├── crawl       加载品牌清单 → 恢复 cookie → 校验会话 → 搜索品牌 → 翻页采集
└── product-detail
                接收 MID → 恢复 cookie → 打开详情页 → 拦截详情/成分响应
                             ├── 页面/接口适配器
                             └── 本地文件输出
```

建议目录：

```text
bebd_crawler/
  cli.py
  config.py
  browser.py
  auth.py
  adapters/bebd.py
  adapters/product_detail.py
  models.py
  output.py
config/brands.example.yaml
tests/
.local/                 # cookie、运行截图和临时诊断，仅本地存在
data/                   # 采集结果，是否提交由后续规则决定
```

## 3. 登录态

默认 cookie 文件为 `.local/auth/bebd-cookies.json`，路径可由配置覆盖。

登录流程：

1. 启动独立的可见浏览器并打开目标地址。
2. 用户自行输入账号、密码及验证码；程序不接触明文密码。
3. 程序等待用户完成登录，并通过“目标路由可访问 + 登录页特征消失”判断成功。
4. 使用 CDP 获取完整 cookie 信息，保留 `name`、`value`、`domain`、`path`、
   `expires`、`httpOnly`、`secure`、`sameSite` 等还原所需属性。
5. 原子写入本地文件，并将 `.local/` 加入 `.gitignore`。

采集启动时先恢复 cookie，再访问目标页验证登录态。`crawl` 使用可见浏览器，因此 cookie
失效并落到登录页时不立即退出，而是每
2 秒轮询一次，保持窗口等待用户手工登录。检测到搜索页输入框后自动继续；默认无限等待，用户
可用 `Ctrl-C` 取消，也可通过 `--login-wait` 设置超时。独立的 `session-check` 仍保持快速检查。

平台可能在正常访问中续期或轮换 cookie。`crawl` 获取数据后、关闭浏览器前必须重新执行一次
完整 cookie 导出，并原子覆盖本地文件。采集中途失败但已确认会话有效时也应尽量回刷；若已跳回
登录页则禁止覆盖，避免把匿名 cookie 写回。`session-check` 成功后同样回刷。

## 4. 页面探查与采集策略

登录闭环完成后进行一次人工辅助探查：用户负责登录，程序记录目标页面的 DOM、分页行为及
网络请求元数据。优先级如下：

1. 若页面存在稳定、账号有权访问的 JSON/XHR 接口，浏览器负责会话，采集器复用该会话请求接口；
2. 若接口不稳定或存在必要的页面交互，则使用 DOM 采集；
3. 不绕过验证码、签名或权限控制。

首个搜索词固定为“欧莱雅”。品牌清单使用 YAML 或逐行文本保存，但第一阶段按页面顶部的
“查商品”关键词搜索执行，而不是使用“所属品牌”高级筛选：输入关键词、搜索、选择美修指数
降序、遍历分页、去重并写出结果。

每次 `crawl` 都应自行打开可见浏览器、恢复 cookie、执行上述交互和响应监听；所有页面完成或
任一步失败后关闭该浏览器。

2026-09-18 实测确认页面调用：

```text
POST https://saas2-api.bevol.com/auth/goods/goodsSearch
keyword = 欧莱雅
orders = [{"asc": false, "column": "exponent"}]
current = 1
size = 10
```

请求还包含页面动态生成的 `currDate`、`str` 和 `sign`。第一阶段不复制签名算法，也不硬编码
请求头里的 `token` / `sessid`；浏览器负责生成合法请求，采集器监听 `goodsSearch` 响应并提取
`data.data`。实测响应 `retCode=200`、`total=5533`、首屏 10 条，美修指数严格降序。

产品详情使用 `product-detail --mid <商品MID>`。监听在打开详情页前启动，页面加载后随机向下
滚动 220–620px 并等待 0.8–1.8 秒，然后收集浏览器正常发出的
`/auth/goods/detail/data` 与 `/auth/goods/detail/ingredient` 响应。接口路径必须精确匹配，避免将
`ingredientResemble` 误当成成分详情。页面可能发出两个参数不同的 `ingredient` 请求：完整 JSON
相同时只保存一次，不同时全部保留。请求仍由页面负责动态签名，采集器不直接调用接口。

## 5. 输出与可恢复性

第一阶段以 JSONL 为主，每行一条记录；同时写一个运行摘要 JSON，包含品牌、开始/结束时间、
页数、原始条数、去重后条数、错误和来源信息。必要时再导出 CSV 供人工查看。

建议路径：

```text
data/<run-id>/欧莱雅.jsonl
data/<run-id>/summary.json
data/<品牌名称>/<mid>/data.json
data/<品牌名称>/<mid>/ingredient.json
data/<品牌名称>/<mid>/ingredient-2.json  # 仅存在第二份不同响应时生成
data/<品牌名称>/search-result-all.json
data/<品牌名称>/search-result-mids-all.json
data/<品牌名称>/state.md
data/<品牌名称>/detail-state.md
.local/artifacts/<run-id>/     # 失败截图、有限的脱敏诊断信息
```

记录使用页面稳定主键或业务字段组合去重。每完成一页即落盘或 checkpoint，避免中断后丢失整次结果。
早期完整搜索结果 `search-result-all.json` 作为快照保留但不再更新。`search-all` 后续只将新 MID
逐行追加到 `search-result-mids-all.json`，并更新 Markdown 进度；续跑时恢复已有 MID，完成搜索
和排序后在分页跳转框输入下一页，并点击顶部搜索框触发失焦和跳转。页码断点以 `state.md` 为准。

批量详情从 MID 文件按行处理，已有完整目录直接跳过，每两个真实详情请求之间随机等待 1–10 秒。
每成功一个 MID 即更新 `detail-state.md`。详情采集不复用搜索页登录判断：每个 MID 只打开一次
详情 URL，此后只轮询当前 URL；不是携带对应 MID 的详情路由时保持页面不动并等待用户处理。

## 6. 配置

非敏感配置使用文件或 CLI 参数：目标 URL、品牌清单、输出目录、页面等待时间、请求间隔。
cookie 路径等本地配置可使用环境变量。账号密码不进入配置文件。

默认加入小幅随机等待并串行采集；遇到 401/403、登录页跳转、验证码或连续限流时立即暂停，
不自动高频重试。

## 7. 待页面探查后确认

- 翻页控件的稳定选择器、最大可访问页数，以及响应 `limitPage=true` 的具体限制。
- 首期只取既定的 TOP 90，还是遍历账号允许访问的全部结果。
- 如何从品牌搜索结果批量调度产品详情采集，并支持断点续跑。
- 结果文件是否需要长期保留，以及后续是否再引入数据库。
