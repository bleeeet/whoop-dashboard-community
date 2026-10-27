# WHOOP 通用分享版

> **部署方式：直接发布这个目录即可，不需要指定启动命令、语言、端口或环境变量。**
> 项目是零第三方依赖的 Python 服务，入口有三层且都指向同一个云端版：
> `app.py`（标准探测名）→ `cloud_server.py`（初始化向导 + 每日调度）→ `start.sh`（等价兜底）。
> 数据目录由 `cloud_server.default_data_root()` 自动选在源码树之外（`/tmp/whoop-app-data`），
> 重新部署不会丢数据，所以即使平台直接用 `python app.py` 启动也完全正确。
> 唯一不要用的入口是 `python server.py`：那是本地单机版，没有初始化向导、不读 PORT。
> 需要显式指定时，启动命令写 `sh start.sh`。

先读 README.md（用户操作）、WORKBUDDY.md（WorkBuddy/CloudBase 部署手册）和 WEEKLY_AUTOMATION.md（可选的每周周报）。目标：用户自己的在线网站，首次打开即初始化向导（登录密码 + WHOOP Client ID/Secret 必填 + DeepSeek 选填），每天北京时间 09:00、12:00 云端同步，不依赖用户电脑。

- Python 3.10+ 标准库；在线入口 cloud_server.py，持久化适配 store.py（CloudBase PostgreSQL REST），本地命令行入口 server.py。
- 线上形态：默认「WorkBuddy 发布应用」，入口 `app.py` 或 `start.sh` 均可（见上方部署说明）。
  备选路线是 CloudBase 容器型云托管（最小 0 / 最大 1 实例）+ whoop_state 表 +
  cloudbase/functions/whoop-cron 定时云函数；走这条路线时最大实例必须为 1。
- 数据目录必须放在源码树之外，否则重新部署会清空状态；该规则已由 `default_data_root()` 兜底，
  改动时不要退回「写在代码旁边」。
- 公网地址由运行时从网关 `X-Wb-Base-Url` 请求头推导，不要写死域名；确实需要覆盖时才设 `PUBLIC_URL`。
- 静态发布不算完成；不得用网页 setInterval 或用户电脑代替云端调度。
- 站内已有用户引导：`/setup` 是分步向导，`/guide` 是建 WHOOP App 的图文流程，首页有配置进度清单。
  改这些页面时保持「用户不需要读 PDF 也能配完」。
- 不向版本控制或分享包添加 .env、token、runtime/、健康数据、生成的个人页面和日志。
- DeepSeek 只在用户填写 key 且保留自动分析勾选，或主动点击时调用；只发送汇总统计。未填写时完整保留基础图表。
- 修改 WHOOP App 相关流程后同步更新站内 `/guide` 与《WHOOP API 获取教程.pdf》（源 HTML 不随包分发）。
- 验证：python3 -m unittest discover -s tests -v；python3 server.py --demo --once。线上按 WORKBUDDY.md 的 A2 验收。
- 用户重置诉求：设置页「恢复出厂设置」（需登录密码）或把 start.sh 的数据目录换个名字后重新发布。
