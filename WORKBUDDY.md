# 给 WorkBuddy 的部署指令

请把本项目部署为用户独立拥有的 WHOOP 私人健康网站。用户不懂代码，请自主完成可执行步骤，最终只交付 HTTPS 网址、私下交付的登录密码及每日更新时间，并提醒其到网站设置页连接自己的 WHOOP。先读完本文和 AGENTS.md，不要把本地演示或静态分享当成上线。

## 目标体验

用户把压缩包交给你 → 你部署带登录保护的网站 → 用户在网页填写自己的 WHOOP Client ID / Client Secret 并授权 → 每日云端同步 → 手机访问网站。DeepSeek API Key 可不填；填了也只在用户主动点击 AI 分析时收费调用。

## 第二项交付：WorkBuddy 每周周报

网站之外，还必须按 WEEKLY_AUTOMATION.md 在使用者自己的 WorkBuddy 创建每周一15:00（UTC+8）云端任务，获取上一完整周周报并将结果送到使用者自己的WorkBuddy任务结果/已启用的原生移动端通知渠道。不是在网页里加一个按钮就算完成；需要给出自动化名称、时区、下次执行时间和推送位置，并手动执行一次验收。

## 成品入口

- `cloud_server.py`：在线版，Python 3.10+ 标准库，HTTP 服务、密码登录、网页设置、WHOOP OAuth、每日调度、DeepSeek 汇总分析。
- `report.py` / `explain.py`：现有完整中文工作台，保留睡眠/恢复/运动/趋势。
- `whoop.py`：WHOOP API v2 客户端，分页、续期、增量历史。
- `Dockerfile` / `compose.yaml`：可托管容器的云端环境直接运行。
- `weekly.py`：无需模型的上一完整周统计；`WEEKLY_AUTOMATION.md`：每周任务部署说明。
- `server.py`：本地命令行版；不要用它替代在线版。
- `fixtures/` 与 `演示工作台.html`：纯虚构数据，仅用于预览。

## 部署决策（由你处理）

1. 优先使用当前 WorkBuddy 网站应用的域名、HTTPS、身份认证、后端和持久化能力。检查其真实运行环境，而非假设可以托管任意 Python 进程。
2. 若支持 Python/容器长期运行及持久化卷，直接启动 `python3 cloud_server.py`，单实例运行，将 DATA_ROOT 指向持久化目录，配合本项目每日调度。不要把 DATA_ROOT 放临时文件系统。
3. 若平台使用云函数而不是常驻后端，需适配为平台实际支持的函数与云数据库：保留 WHOOP API 和分析逻辑，后端保存凭据/token/历史/同步状态，数据库使用原子锁避免 token 并发刷新，平台定时函数每日调用同步。不能用网页 setInterval 或本机自动化替代云端调度；不能只给一段让用户再写代码的方案。
4. 前端可用 WorkBuddy 分配的域名，受保护后端通过同源反向代理或平台服务路由接入，设置 PUBLIC_URL 为最终用户访问的 HTTPS origin。
5. 如当前套餐或平台缺少必要能力，明确报告缺少哪一项，完成可验证的适配或向用户说明需要的云资源及费用。不得声称“只上传就一定成功”，不得偷偷改成公开静态站。

## 部署配置

- `PUBLIC_URL`：最终 HTTPS 网站 origin（不带路径和末尾斜杠），例如 `https://your-app.example.com`。以真实分配域名替换，不使用示例域名上线。
- `ADMIN_PASSWORD`：至少 12 位强随机密码，通过平台 Secret 注入，不放源码或公开日志。私下告知用户。
- `DATA_ROOT`：可写持久化目录，默认项目内 runtime/。持久化包含用户凭据、轮换 token、健康历史、页面和调度状态，服务器只允许运行账号读取。
- `PORT`：平台提供的 HTTP 端口，使用服务实际支持的默认值；监听 0.0.0.0，由平台 HTTPS 入口代理。
- `CRON_SECRET`：若使用外部云调度，生成独立随机密钥，通过 Secret 注入，调用 `POST /internal/sync` 并携带 `Authorization: Bearer ...`。禁止把密钥放 URL。
- `AUTOMATION_SECRET`：独立随机密钥，用于 WorkBuddy 周报任务 `POST /internal/weekly` 的 Bearer 鉴权；保存在网站与自动化运行环境的 Secret，不能写入任务提示词或回显。
- WHOOP / DeepSeek 凭据由用户部署后在网页填写，不需要向用户索要密钥后写进部署包。

WHOOP 回调为 `PUBLIC_URL/oauth/callback`，设置页显示此地址让用户复制。必须精确匹配 WHOOP Developer Dashboard 注册值，变更域名后需同步修改。state 必须校验，token 永远只在服务端。

## 资源与运行方式

项目不运行本地模型、不转码、不下载媒体；后台主要进行HTTP请求、JSON合并和HTML/SVG生成。21天合成数据的本机单次渲染实测约0.08秒、最大常驻内存约26 MiB；这不是完整服务或长期历史数据的资源保证。部署后请测实际内存、执行耗时、数据库/存储用量与账户配额。优先用每日一次云端调度减少空闲进程和调用费用；平台可休眠时不要依赖进程内计时器。

## 真正完成的验收

逐项检查后再报完成：

1. 新域名 HTTPS 可访问，未登录用户看不到健康数据或设置；手机宽度没有整页横向溢出。
2. 登录进入设置页，看到 WHOOP 凭据表单、可复制回调地址、WHOOP 官方入口、可跳过的 DeepSeek 设置。
3. 保存设置后刷新页面，密钥不回显；重启后设置仍保留；网站登录密码不会出现在网页源代码。
4. 由用户本人在 WHOOP 完成授权，state 验证成功，首次全量同步，真实图表可见。未获用户授权时不要用假数据冒充真实同步。
5. 数据和轮换 token 持久化，重启不会丢；只跑一个同步实例。
6. 每日任务由云端执行，关闭个人电脑和网页也不影响；手动触发同一同步入口，核对“最后成功同步”确实更新。平台休眠会暂停常驻调度时，必须配置真正的云端调度并核验回执。
7. 未配置 DeepSeek 也能使用全部基础图表；配置后主动点击才调用官方接口，需先提示健康汇总会发送到 DeepSeek并产生费用。
8. 凭据、runtime/、data/、publish/、日志不上传 Git；不把配置接口、token文件或健康页面开放匿名访问。

最终给用户一句操作路径：“打开此网址 → 登录 → 设置 → 填 WHOOP 两个值 → 连接 WHOOP → 手机添加到主屏幕”。日常使用只访问网址，不需 Codex、不需终端。

## 平台依据与已知边界

[WorkBuddy 网站应用](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/App-Publishing/Web-App)支持网站发布；[云服务](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/App-Publishing/Cloud-Services)支持数据库、身份认证等；[云上定时任务](https://www.workbuddy.cn/docs/workbuddymini/use-cases/Cases-Three)不依赖个人电脑开机。具体账号能否将这些能力用于本项目，要在部署现场验证。项目作者未登录收件人的 WorkBuddy 账号，也未验证其云域名部署，不应将这些未验证项写成已经完成。
