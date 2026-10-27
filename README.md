# WHOOP 通用版 · Workbuddy 一键部署

把压缩包交给 Workbuddy，说一句 **“一键部署”**，就可以开始部署自己的 WHOOP 健康网站。

## 使用方法

1. **下载压缩包**：[点击下载最新版 WHOOP通用版_WorkBuddy部署包.zip](https://github.com/bleeeet/whoop-dashboard-community/releases/latest/download/WHOOP_WorkBuddy.zip)。
2. **把整个压缩包直接发给 Workbuddy**，不用自己解压，然后告诉它：

   > 一键部署

3. **打开 Workbuddy 部署后提供的网站链接**。
4. **后续配置按照包内《WHOOP API 获取教程.pdf》操作即可**：[在线查看 PDF](WHOOP%20API%20获取教程.pdf)。按教程创建自己的 WHOOP 应用，将 Client ID、Client Secret 填入自己的网站，设置登录密码，再点击“连接 WHOOP”完成授权。
5. **用手机打开网站**，可添加到主屏幕，随时查看健康数据。

**DeepSeek 是选填项**：不填也能抓取 WHOOP 数据、查看图表和周报；需要 AI 分析时，再按 PDF 最后一部分的方法配置。

## 网站功能

- 查看睡眠、恢复、HRV、静息心率、运动负荷及周/月趋势。
- 每天北京时间 09:00、12:00 同步 WHOOP 数据；部署时由 Workbuddy 核对云端调度，容器休眠的环境需配置外部定时唤醒。
- 配置 DeepSeek 后可生成 AI 健康分析。
- 查看每周健康汇总；如需定时发送周报，可让 Workbuddy 按 [每周周报说明](WEEKLY_AUTOMATION.md) 配置。

## 常见问题

- **不知道后续怎么设置**：直接看包内《WHOOP API 获取教程.pdf》，网站初始化页和设置页也有引导。
- **连接 WHOOP 失败**：核对 WHOOP 应用的回调地址是否与网站显示的完全一致，详见 PDF。
- **数据未更新**：先在 WHOOP 手机 App 同步手环，再点击网站的“立即同步”。仍有问题，把错误提示交给 Workbuddy 排查，不要发送密钥。
- **想修改配置**：登录网站后打开设置页修改即可。

## 隐私与部署说明

每个人独立部署，使用自己的 WHOOP 账号、应用和数据。包内仅含演示数据，不含作者的健康数据或密钥。Client Secret 和 DeepSeek Key 只填入自己的网站，不要发给 Workbuddy 或公开分享。AI 分析不能替代医生判断。

网站运行和可选 AI 分析的费用以各自平台实际计费为准。数据保存、重新部署后的保留情况及云端调度，由 Workbuddy 按部署手册验收；沙箱容器被完全回收时，本地状态可能丢失。

给 Workbuddy 的部署细节：[WORKBUDDY.md](WORKBUDDY.md) · 本地运行：[LOCAL.md](LOCAL.md)

开发联系：bleetchenxuanling@gmail.com
