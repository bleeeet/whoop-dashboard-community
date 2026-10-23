# WHOOP 通用分享版

先读 README.md（用户操作）、WORKBUDDY.md（网站部署契约）和 WEEKLY_AUTOMATION.md（每周云端自动化）。目标是用户自己的在线网站，浏览器配置 WHOOP，云端每日更新，DeepSeek 可选，不依赖 Codex。

- Python 3.10+ 标准库；在线入口 cloud_server.py，本地命令行入口 server.py。
- 上线必须有认证、HTTPS、后端持久化、单实例刷新 token 及真正的云端每日调度；静态发布不算完成。
- 不向版本控制添加 .env、token、runtime/、健康数据、生成的个人页面和日志。
- 不读取项目外账号或机器配置；每位使用者配置自己的 WHOOP OAuth 应用。
- DeepSeek 只在用户主动发起时调用，向用户说明发送汇总健康数据并产生费用；未设置时完整保留基础图表。
- WorkBuddy 原生运行时不支持 Python/容器时按 WORKBUDDY.md适配后端与云数据库，不能假装部署成功。
- 部署前向使用者说明云端运行、数据保存位置、调度及潜在套餐费用；不在作者电脑安装常驻任务。
- 必须交付第二项功能：WorkBuddy云端每周一15:00（UTC+8）自动获取上一完整周周报并将结果送到使用者自己的WorkBuddy；不以网页周报按钮替代云端自动化。
- 验证：python3 -m unittest discover -s tests -v；python3 server.py --demo --once。部署还需按 WORKBUDDY.md 的线上验收执行。
