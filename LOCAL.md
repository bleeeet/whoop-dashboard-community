# WHOOP 健康工作台 · 通用版

下载代码，配置自己的 WHOOP API 应用，首次登录授权后，启动后台即可定期抓取并生成中文健康工作台。每位使用者独立部署、独立保存自己的数据。

保留今日概览、睡眠分期与需求、恢复、HRV、静息心率、血氧、体温、运动、周/月趋势和关联分析。分析由本地规则和统计生成，不需要大模型 API，也不会把数据发给模型服务。非 WHOOP 官方产品。

## 三步开始

要求 Python 3.10+，仅标准库，无需安装依赖。支持 macOS、Windows、Linux。

1. 下载解压本项目，在 WHOOP 开发者后台 https://developer-dashboard.whoop.com/ 创建自己的 App。回调地址填写 `http://localhost:8080/callback`，启用 recovery、cycles、sleep、workout、profile、body_measurement 对应六项 read 权限，以及 offline 授权范围。复制 `.env.example` 为 `.env`，填写 Client ID、Client Secret。也可通过同名环境变量注入，环境变量优先。
2. 在项目目录执行 `python3 whoop.py auth`，浏览器登录自己的 WHOOP 账号并同意授权。Windows 可将 `python3` 换成 `py -3`。如果 WHOOP 门户拒绝 localhost 回调，请使用其允许且自己控制的回调 URL，并用下文手动授权。
3. 执行 `python3 server.py`，访问 http://127.0.0.1:8788/ 。Mac 可双击 `start.command`；Windows 可双击 `start.bat`。

首次启动全量抓取，之后每小时同步最近 7 天并合并历史；重启不会丢失已有历史。网页刷新可读取最新生成页面。后台终端需保持运行，电脑休眠、关机或进程退出期间无法抓取，恢复运行后继续。Ctrl+C 停止，无自启、无系统定时任务、无额外常驻安装。

WHOOP 不是只填一个永久 API Key 的服务：首次必须用自己的应用凭据完成 OAuth 授权；之后 refresh token 自动续期。授权被撤销或无法续期时需重新授权。同一份 token 只让一个后台使用，不复制给另一台同时运行。

## 无 API 先看效果

```sh
python3 server.py --demo
```

使用纯程序构造的虚拟数据，绝不连接 WHOOP。后台输出的地址可浏览演示工作台。`--demo --once` 只生成演示 HTML 后退出。

## 常用命令

```sh
python3 server.py --once               # 同步并生成一次后退出
python3 server.py --interval 1800      # 运行期间每 30 分钟更新
python3 whoop.py auth --manual         # 无本地回调监听时，粘贴完整回调 URL
python3 whoop.py sync                  # 手动补全量历史；先停止后台
python3 report.py --data data --out publish/index.html
python3 -m unittest discover -s tests -v
```

手动授权时，回调地址须与 WHOOP App 注册地址完全一致；浏览器授权后复制含 code 和 state 的完整回调地址到终端。不要把授权 URL 或 token 发给别人。API 权限、网络和 WHOOP 服务可用性由使用者自行配置。

## WorkBuddy 使用

请将完整项目交给 WorkBuddy，并按 README.md 操作。本文只介绍本地命令行用法，在线部署以 WORKBUDDY.md 为准。

## 云端在线版

在线入口已改为 `cloud_server.py`，带网页登录、设置和每日更新。Dockerfile 与 compose.yaml 对应在线版，请阅读 WORKBUDDY.md，不要用本地版无登录的 server.py 直接公网部署。

## 隐私与数据位置

分享包只包含代码、空白配置模板和标明 Demo 的虚构数据。不含原作者的健康记录、姓名、邮箱、API 密钥、token、服务器配置、推送账号或原仓库 Git 历史。运行之后生成的 `.env`、`.tokens.json`、`data/`、`publish/` 均保留在自己的项目内且被 Git 忽略；再次分享时请重新下载干净源码，不直接压缩自己用过的文件夹。

服务只开放生成的工作台和脱敏 `/status`，不提供文件目录浏览。不内置第三方统计、企微/TG 推送或上传功能。健康工作台本身含个人健康数据，默认仅本机可访问；公网使用需自行部署认证与 HTTPS。保留上次成功页面不代表数据最新，可查看 `/status` 和页面生成时间。图表解释与相关性分析不能代替医疗诊断。

## 开发与来源

基于原作者的 WHOOP 工作台代码重新整理，作为独立分享项目维护，不影响原作者正在使用的实例。Python 标准库代码可直接阅读修改；合成演示数据由 `fixtures/make.py` 生成。

官方文档：[WHOOP OAuth](https://developer.whoop.com/docs/developing/oauth/)、[WorkBuddy 网站应用发布](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/App-Publishing/Web-App)。
