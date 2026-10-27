# 交付验证记录

## 2026-10-27：WorkBuddy / CloudBase 部署改造

改动：

- 新增 `store.py`：设置 `CLOUDBASE_ENV_ID` + `CLOUDBASE_APIKEY` 后，把设置、WHOOP token、6 类健康数据、同步状态、AI 结果读写到 CloudBase PostgreSQL（REST 网关，标准库实现）。启动时恢复，每次操作后只上传有变化的文件；同步失败时也会备份已轮换的 token；数据库不可达时拒绝启动，避免空数据覆盖云端。
- 登录会话改为 HMAC 签名，容器重启或缩容到 0 后仍有效（30 天）；退出登录让所有设备失效。
- 不设 `ADMIN_PASSWORD` 时，首次打开进入 `/setup`：设登录密码（PBKDF2 存储）+ WHOOP Client ID/Secret 必填 + DeepSeek 选填；可选 `SETUP_CODE`。
- `PUBLIC_URL` 可不设，按访问域名自动生成回调地址（公网一律 https）。
- 调度改为每天北京时间 09:00、12:00；`POST /internal/sync` 等同步完成后返回（200 成功 / 502 失败），供 CloudBase 定时云函数 `cloudbase/functions/whoop-cron` 调用。
- 填了 DeepSeek 且保留勾选时，每次同步后自动生成 AI 分析。
- 首页新增「今天早上 / 昨天全天」摘要；新增公开 `/privacy` 页（WHOOP 创建 App 时必填 Privacy Policy URL）。
- 新增《WHOOP API 获取教程.pdf》（8 页，截图来自真实 WHOOP 开发者后台，Client ID、邮箱、姓名均已替换为示例）。

已验证：

- `python3 -m unittest discover -s tests`：73 项通过（原 59 项按新行为更新 4 项，新增 14 项：初始化流程、初始化码、隐私页、会话跨重启与吊销、域名自动识别、自动 AI、首页摘要、CloudBase 持久化 7 项）。
- CloudBase 持久化用本地模拟的 PostgREST 服务测试（接口格式按 CloudBase 官方文档：`/v1/rdb/rest/:table`、`Prefer: resolution=merge-duplicates`、`Authorization: Bearer`）。
- 进程级端到端：真实启动 `cloud_server.py` → 初始化 → 定时接口 → 杀进程，换一块空磁盘重启（模拟缩容到 0）→ 同一 Cookie 仍处于登录状态，设置、token、6 类数据、工作台页面全部恢复。
- 定时云函数用模拟 fetch 测了成功、失败、缺少配置三条分支。
- DeepSeek：`/models` 返回 `deepseek-flash`、`deepseek-v4-pro`；`deepseek-flash` 对话返回 HTTP 200（只发送 “Reply only OK.”）。
- 浏览器 390px 手机宽度：初始化页、设置页、首页、工作台都没有横向溢出。
- WHOOP 开发者后台创建 App 的表单：只填写和勾选，没有提交；没有显示或复制 Client Secret。

没有验证（需要在用户的 WorkBuddy 部署时验收，见 WORKBUDDY.md 第 4、5、7 步）：

- 真实的 CloudBase 环境：建表、服务端 API Key 权限、云托管冷启动时长、定时触发器的时区、实际资源点消耗。本机没有腾讯云凭据。
- 新公网域名上的 WHOOP OAuth 全流程。授权回调逻辑由自动化测试覆盖；2026-10-23 已用真实 WHOOP API 验证过抓取和渲染链路（见下）。

## 2026-10-23：首轮交付

- 自动化测试 59 项全部通过（本地运行、云端认证/授权/安全、定时同步、每周报告窗口与接口）。
- 使用通用版 whoop.py 真实读取 WHOOP 六类 API（cycle/recovery/sleep/workout/profile/body）成功，并用 report.py 生成有效的健康工作台 HTML；在线版 cloud_server.App 的设置保存与 sync 完整路径实测成功。测试用的凭据和 token 只放在临时目录，结束后已清理。
- 浏览器验证登录、设置表单、390px 手机宽度无横向溢出。
- 资源抽样：21 天合成数据单次生成 HTML 约 0.08 秒，最大常驻内存约 26 MiB。
