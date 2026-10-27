# 部署手册（给执行部署的 WorkBuddy / AI 助手）

你要把本项目部署成用户**独享的 WHOOP 健康网站**：手机可打开的 HTTPS 网址，第一次打开就是初始化页（设登录密码 + 填 WHOOP Client ID/Secret，DeepSeek 选填），之后每天北京时间 09:00、12:00 自动抓取，尽量不依赖用户电脑。

## 先选路线

| 路线 | 适用环境 | 状态存放 | 定时抓取 | 需要用户提供 |
|---|---|---|---|---|
| **A. 发布应用（默认，推荐）** | 任何 WorkBuddy 环境 | 沙箱磁盘 `/tmp/whoop-data` | 进程内调度器（容器存活期间有效） | 无 |
| B. CloudBase 云托管 | 用户有 CloudBase 环境 + 服务端 API Key | PostgreSQL `whoop_state` 表 | 定时云函数 | 环境 ID、API Key |

**默认走路线 A。** 它是纯 Python 标准库 + 单端口 HTTP 服务，无需任何外部服务即可发布。
路线 B 只有在用户明确要求跨容器重建保留数据、且能提供 CloudBase 凭据时才使用。

---

## 路线 A：发布应用（默认）

### A1. 发布

用内置的发布工具，参数如下：

| 参数 | 值 |
|---|---|
| `directory` | 项目根目录的绝对路径 |
| `language` | `python` |
| `port` | `8080` |
| `startCmd` | `sh start.sh` |
| `appName` | 简短中文名，如「WHOOP健康」 |
| `domainPrefix` | 英文短横杠 slug，如 `whoop-health` |

`start.sh` 做了两件必要的事，**不要绕过它直接运行 `cloud_server.py`**：

1. 把数据目录放到源码树之外。每次重新部署都会替换源码目录，状态若留在源码旁边会被清空。
   （实测：沙箱会复用同一个容器，跨重新部署 hostname 不变，`/tmp` 下的数据保留。）
2. 公网地址由运行时从网关请求头推导，无需预先写死域名。

本项目无第三方依赖，不需要 `requirements.txt`，依赖安装步骤会被自动跳过。

### A2. 验收

1. `https://<域名>/health` 返回 `ok`
2. `https://<域名>/` 跳转到 `/setup`
3. **关键**：`/setup` 页面显示的「WHOOP 回调地址」必须是用户实际访问的 https 域名，
   形如 `https://<域名>/oauth/callback`。
   应用按以下优先级推导：`PUBLIC_URL` 环境变量 → 网关 `X-Wb-Base-Url` 请求头 → `X-Forwarded-Host`/`Host`。
   若显示的是 `*.e2b.*.sandbox.cloudstudio.club` 这类沙箱内网地址，说明当前环境没有注入
   `X-Wb-Base-Url`，此时需设置 `PUBLIC_URL` 环境变量为公网域名后重新发布。
4. `https://<域名>/privacy` 能打开（WHOOP 要求填隐私政策地址）
5. `https://<域名>/guide` 能打开（建 App 的分步图文流程，与 PDF 内容一致）
6. 登录后首页顶部应有「配置进度」清单；全部配置完成后显示「全部完成 🎉」

### A3. 交付给用户

1. 完整的分享链接（**包含 `?sharecode=...` 参数**，缺了会返回 403「链接不完整」）
2. 一句话操作指引：**「用电脑打开这个网址 → 第一页就是分步引导，跟着它走：设置登录密码 → 照着页面上的 7 步在 WHOOP 开发者后台建 App（回调和隐私地址页面上能一键复制）→ 把 Client ID / Secret 填回页面底部保存 → 回到首页点『连接 WHOOP』授权 → 用手机打开网址并添加到主屏幕」**
3. 说明两点，避免用户绕路：**DeepSeek 可以留空**（之后在设置里补）；**建 App 的流程站内就有**（`/guide`），PDF 只是同一套流程的截图版

### A4. 数据备份与恢复

沙箱磁盘是唯一存储，所以项目自带了导出/恢复接口，**默认关闭**：

| 环境变量 | 作用 | 不设时 |
|---|---|---|
| `BACKUP_SECRET` | 开启 `/internal/export` 与 `/internal/restore` | 两个接口一律 403 |

导出（返回 JSON 快照，内容就是 `store.py` 会同步到云端的那批文件）：

```sh
curl -H "Authorization: Bearer $BACKUP_SECRET" https://<域名>/internal/export -o whoop-backup.json
```

恢复（把快照原样 POST 回去）：

```sh
curl -X POST -H "Authorization: Bearer $BACKUP_SECRET" \
     -H "Content-Type: application/json" \
     --data-binary @whoop-backup.json https://<域名>/internal/restore
```

快照**包含敏感信息**（登录密码散列、WHOOP Client Secret、refresh token），这正是它必须靠
`BACKUP_SECRET` 把关、且默认关闭的原因。请让用户自己保管备份文件，不要上传公开位置。

恢复接口只接受 `whoop-state/1` 格式，且会逐个校验文件名在白名单内（含 `..` 的一律拒绝），
避免写入状态目录之外的文件。若容器被彻底回收，重新发布后用它即可恢复，无需重新授权。

### A5. 填错了 / 忘了密码：怎么重来

三种情况，按这个顺序处理：

| 情况 | 做法 |
|---|---|
| 能登录，只是凭据填错了 | 登录 → 设置页直接改 Client ID / Secret、补 DeepSeek，不用重来 |
| 能登录，想彻底换一套 | 设置页最下方「恢复出厂设置」，输入登录密码确认，回到初始化向导 |
| 登不进去（密码忘了） | 把 `start.sh` 里的默认数据目录改个名字（如 `/tmp/whoop-app-data2`）后重新发布；旧目录仍在沙箱里，改回去即可找回 |

别用「删源码目录里的文件」来重置：重新部署会替换整个源码树，只有 `DATA_ROOT` 指向的目录才是持久的。

### A6. 已知边界

- 数据在沙箱磁盘上。重新部署已实测保留；若容器被完全回收则可能丢失，届时需用户重新授权 WHOOP
  （历史数据需要重新抓取）。想要万无一失请用路线 B。
- 定时抓取靠进程内调度器，容器存活时有效；若容器缩容停止，需要外部定时唤醒
  （设置 `CRON_SECRET` 后用定时任务请求 `POST /internal/sync`，请求头 `Authorization: Bearer <CRON_SECRET>`）。
- `/internal/*` 未设置密钥时一律返回 403，这是安全默认值。

---

## 路线 B：CloudBase 云托管（可选，需要用户凭据）

以下内容仅在走路线 B 时执行。

用户不懂代码。可执行的步骤你自己完成；只有云服务开通、费用确认这类事项需要问用户。**不要向用户索要 WHOOP 或 DeepSeek 密钥**，用户会在网页上自己填。

## 架构

```
手机浏览器 ──HTTPS──▶ CloudBase 云托管「whoop」（容器，Dockerfile，最小 0 / 最大 1 实例）
                          │ 每次操作后备份 / 冷启动时恢复
                          ▼
                     CloudBase PostgreSQL 表 whoop_state（设置、token、健康历史）

CloudBase 云函数「whoop-cron」（定时 09:00、12:00）──POST /internal/sync──▶ 云托管
WorkBuddy 云端自动化（每周一 15:00，可选）──POST /internal/weekly──▶ 云托管
```

为什么这样搭：容器缩容到 0 实例时不收计算费，但本地磁盘会清空，所以数据必须放 PostgreSQL；缩容后进程内计时器也不会运行，所以每天的抓取由定时云函数唤醒。代码已经处理好这两点，你只需按下面配置。

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 云托管镜像，Python 3.12 标准库，无第三方依赖 |
| `cloud_server.py` | 网站与后台（登录、初始化页、WHOOP OAuth、同步、DeepSeek 分析） |
| `store.py` | 设置了 `CLOUDBASE_ENV_ID` + `CLOUDBASE_APIKEY` 时，把状态读写到 PostgreSQL |
| `cloudbase/schema.sql` | 建表语句 |
| `cloudbase/functions/whoop-cron/` | 定时云函数（Node.js 18，无依赖） |
| `cloudbase/cloudbaserc.json` | 云函数配置参考（CLI 部署时用） |
| `WEEKLY_AUTOMATION.md` | 可选的每周周报自动化 |
| `server.py` | 本地命令行版，**不要**用于线上 |

## 第 0 步：先告诉用户（一句话，请用户确认）

> 我会在你的 WorkBuddy 里开启云服务：一个云托管容器（网站）、一张 PostgreSQL 表（存你的 WHOOP 数据和设置）、一个定时云函数（每天 9 点和 12 点抓数据）。闲时容器自动停止，预计每月消耗约 1,000～3,000 资源点，体验版每个应用每月有 5,000 点免费额度。数据只存在你自己的云环境里。

## 第 1 步：开通云服务

为本应用开启云服务，需要：云数据库（PostgreSQL）、云函数、云托管。记下 **CloudBase 环境 ID**（形如 `xxx-1a2b3c`）。

## 第 2 步：建表

在该环境的 PostgreSQL 中执行 `cloudbase/schema.sql`：

```sql
CREATE TABLE IF NOT EXISTS whoop_state (
    name       text        PRIMARY KEY,
    body       text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
```

表权限设为**仅管理端可读写**，不对匿名用户或登录用户开放（表里有 WHOOP token 和健康数据）。

## 第 3 步：准备密钥（只放进平台的环境变量/Secret，不写进代码、对话或日志）

1. 在 CloudBase 身份认证 → API Key 管理中创建一个**服务端 API Key**（管理员权限，`keyType=api_key`），作为 `CLOUDBASE_APIKEY`。
2. 生成两个随机串，分别作为 `CRON_SECRET`、`AUTOMATION_SECRET`：
   ```sh
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

## 第 4 步：部署云托管服务

- 类型：**容器型云托管**，用项目根目录的 `Dockerfile` 构建，服务名 `whoop`
- 端口：应用读取平台注入的 `PORT`；如平台要求填写，填它的默认端口（CloudBase 默认 80），镜像内默认 8080
- **最小实例 0，最大实例 1**。最大实例必须是 1：OAuth token 每次刷新都会轮换，多实例会互相作废
- 规格：0.25 核 / 0.5 GB 足够
- 访问：公网 HTTPS（PUBLIC）；健康检查路径 `/health`
- 环境变量：

| 变量 | 值 | 必填 |
|---|---|---|
| `CLOUDBASE_ENV_ID` | 第 1 步的环境 ID | 是 |
| `CLOUDBASE_APIKEY` | 第 3 步的服务端 API Key | 是 |
| `CRON_SECRET` | 第 3 步生成的随机串 | 是 |
| `AUTOMATION_SECRET` | 第 3 步生成的随机串 | 做周报时必填 |
| `DATA_ROOT` | `/data`（只是缓存，可丢） | 镜像已默认 |
| `PUBLIC_URL` | **默认不设**，网站自动按访问域名生成回调地址。只有当设置页显示的回调地址不是用户实际访问的 https 域名时，才设为该域名（如 `https://whoop-xxx.sh.run.tcloudbase.com`，不带末尾斜杠） | 否 |
| `ADMIN_PASSWORD` | **默认不设**，用户第一次打开网页时自己设登录密码。设置后（≥12 位）会覆盖网页设的密码，可用于忘记密码时重置 | 否 |
| `SETUP_CODE` | 可选。设了以后，初始化页要先输入这个码，防止别人抢先初始化。设了就要私下告诉用户 | 否 |
| `CLOUDBASE_API_BASE` | 仅国际站环境需要：`https://<环境ID>.api.intl.tcloudbasegateway.com` | 否 |

部署后检查：

1. 日志出现 `WHOOP private service ready（CloudBase 持久化已启用）`。若出现「服务启动失败」，按提示核对 `CLOUDBASE_ENV_ID`、`CLOUDBASE_APIKEY`、表是否已建。数据库连不上时服务会**拒绝启动**，这是为了防止空数据覆盖云端备份
2. `https://<域名>/health` 返回 `ok`
3. `https://<域名>/` 跳转到 `/setup`，页面上显示的回调地址是 `https://<域名>/oauth/callback`
4. `https://<域名>/privacy` 能打开（WHOOP 要求填隐私政策地址，用户会填这个）

## 第 5 步：部署定时云函数

- 目录：`cloudbase/functions/whoop-cron/`，函数名 `whoop-cron`，运行时 Node.js 18 或更高，入口 `index.main`，**超时 300 秒**，不需要安装依赖
- 环境变量：`WHOOP_SITE_URL` = 第 4 步的网站地址（`https://...`，不带末尾斜杠），`CRON_SECRET` = 与云托管相同
- 定时触发器：`0 0 9,12 * * * *`（腾讯云 7 段 cron：秒 分 时 日 月 周 年），北京时间。如果控制台显示触发器按 UTC 计时，改为 `0 0 1,4 * * * *`
- 立即手动运行一次：用户还没连接 WHOOP 时，预期报错 `同步未成功：HTTP 502`，说明链路已通。返回 `HTTP 403` 说明两边 `CRON_SECRET` 不一致

函数会等网站同步完成后才返回（返回 200 才算成功），所以容器缩容也不会打断抓取。

## 第 6 步：交付给用户

只需要交给用户：

1. 网站地址
2. 一句话：**「用电脑打开这个网址 → 按页面提示设置登录密码 → 照《WHOOP API 获取教程.pdf》在 WHOOP 开发者后台创建 App → 把 Client ID 和 Client Secret 填回网页 → 点『连接 WHOOP』→ 用手机打开网址并添加到主屏幕」**
3. 如果设了 `SETUP_CODE`，私下告诉用户

不要替用户填 WHOOP 凭据，也不要用假数据冒充同步成功。

## 第 7 步：用户连接 WHOOP 之后的验收

用户完成授权后（可以请用户回来告诉你），逐项确认：

1. 首页「最后成功同步」显示了时间，「健康工作台」有真实图表
2. 手动运行一次 `whoop-cron`，返回 200，首页同步时间更新
3. 重新部署或重启一次云托管：用户不用重新登录，设置和数据都还在（证明 PostgreSQL 持久化生效）
4. 第二天 09:00 之后，云函数日志里有一次成功调用
5. 未登录访问 `/`、`/settings`、`/dashboard` 都会跳到登录页；`/status` 不能匿名访问

## 第 8 步（可选）：每周周报

按 `WEEKLY_AUTOMATION.md` 在用户的 WorkBuddy 建云端定时任务（每周一 15:00）。用户没要求时可以先不做，并告诉用户随时可以加。

## 平台能力不符时

- **用不了云托管容器**：本项目也能部署到任何能跑 Docker 的地方（腾讯云轻量服务器、自己的电脑等）。挂一个持久卷到 `/data` 就不需要 CloudBase 变量，`python3 cloud_server.py` 自带每天 09:00/12:00 的调度。但需要公网 HTTPS 域名才能让手机访问并完成 WHOOP 授权，要先向用户说明。
- **建不了 PostgreSQL 表或 API Key**：可以把最小实例设为 1、不配 CloudBase 变量，用容器磁盘存数据。代价是重新部署后需要用户重新初始化、重新授权，而且容器常驻约 21,000 资源点/月，超过体验版额度。必须先征得用户同意。
- **定时云函数不可用**：改用 WorkBuddy 云端定时任务，每天 09:00、12:00 向 `POST <网站>/internal/sync` 发请求，请求头 `Authorization: Bearer <CRON_SECRET>`（密钥放在受保护的变量里，不写进提示词）。用户打开网站时，后台也会补跑错过的那一次，但这只能兜底，不能代替定时任务。

不要把网站改成静态页面，也不要在仅有预览链接时就告诉用户已经上线。

## 安全要点（已在代码中实现，部署时不要破坏）

- 除 `/health`、`/privacy`、`/setup`（仅初始化前）、`/login` 外，所有页面都要登录；`/internal/*` 只认 Bearer 密钥
- 登录 Cookie 签名、HttpOnly、SameSite、HTTPS 下带 Secure，有效期 30 天；退出登录会让所有设备失效
- 网页不回显已保存的密钥；WHOOP token 只存在服务端和用户自己的 PostgreSQL
- DeepSeek 只接收汇总统计，不发送姓名、邮箱和原始记录；只有用户填了 key 并保留「同步后自动分析」勾选时才调用
- `.env`、`runtime/`、`data/`、日志不进 Git，也不要放进公开存储

## 资源参考

本机实测：21 天合成数据生成一次页面约 0.08 秒、常驻内存约 26 MiB；状态数据每年约 1～2 MB。资源点单价：云托管 CPU 55 点/核·小时、内存 32 点/GB·小时（[CloudBase 资源点价格](https://cloud.tencent.com/document/product/876/127357)）。上线后请看一周的实际用量再告诉用户。

参考：[WorkBuddy 网站应用](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/App-Publishing/Web-App)、[WorkBuddy 云服务](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/App-Publishing/Cloud-Services)、[CloudBase 云托管开发规范](https://docs.cloudbase.net/ai/cloudbase-ai-toolkit/prompts/cloudrun-development)、[CloudBase PostgreSQL HTTP API](https://docs.cloudbase.net/en/database/postgresql/http/insert)。
