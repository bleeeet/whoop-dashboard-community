# WHOOP 健康工作台 · 分享版

**把这个压缩包交给 WorkBuddy，部署一个属于自己的健康网站。** 上线并连接 WHOOP 后，手机打开网址即可查看睡眠、恢复、运动和趋势；后台每天更新，不需要电脑开机。另附 **WorkBuddy 每周周报自动化**：默认每周一 15:00（北京时间）汇总上一完整周并送到自己的 WorkBuddy。DeepSeek 可选，不填也能看图表、基础分析和周报。

## 1. 交给 WorkBuddy

把完整压缩包发给 WorkBuddy，再复制发送这句话：

> 请解压并阅读 WORKBUDDY.md，按其中要求部署我的私人 WHOOP 健康网站，开启云端持久化和每日自动更新，再按 WEEKLY_AUTOMATION.md 建立每周周报云端自动化，给我一个手机能打开的 HTTPS 网址。请完成后台部署，不要只发布静态页面。部署需要的云服务确认请直接告诉我。

WorkBuddy 应交付：**网站网址、登录密码、每天更新时间，以及每周周报自动化的名称和推送位置**。部署步骤由它处理。云服务是否可用取决于你的 WorkBuddy 账号和套餐；若其环境不支持本项目后台，WorkBuddy 需按部署说明完成云端适配后才能算上线。仅有预览链接不代表自动更新已启用。

## 2. 找到 WHOOP API，填进网站

1. 打开你的网站，登录，进入「设置」，复制页面显示的 **WHOOP 回调地址**。
2. 打开 **https://developer-dashboard.whoop.com/**，用自己的 WHOOP 账号登录。
3. 如果提示，先创建一个 **Team**，再创建 **App**。名字可填“我的健康工作台”。
4. **Redirect URI / 回调地址**：粘贴第 1 步复制的完整地址。勾选六项读取权限：`read:recovery`、`read:cycles`、`read:sleep`、`read:workout`、`read:profile`、`read:body_measurement`。项目授权时会请求 `offline`，用于自动续期；门户如显示该选项，也勾选。
5. 创建后找到 **Client ID** 和 **Client Secret**，分别复制到自己网站「设置」的同名框，保存。
6. 点击 **「连接 WHOOP」**，按提示登录并同意授权。返回网站，等待首次同步完成。

**以后不用每天填写。** 网站会自动续期和抓取。这里的 API 配置是两个值加一次登录授权，不是单个 API Key。凭据只填到自己的 HTTPS 网站，不要发进聊天或给别人。

## 3. 手机每天查看

用手机浏览器打开网址并登录，查看页面的「最后成功同步」时间。iPhone：Safari → 分享 → 添加到主屏幕；安卓：浏览器菜单 → 添加到主屏幕。关闭网页也会更新，前提是云端后台和调度持续有效。

## 4. DeepSeek（可以跳过）

想要 AI 分析时，打开 **https://platform.deepseek.com/**，在 **API keys** 中创建密钥，复制到网站「设置 → DeepSeek API Key」，保存即可。模型默认 `deepseek-flash`。不填时，WHOOP 抓取和图表照常使用。

点击 AI 分析前会提示：将汇总健康数据发给 DeepSeek，并按它的规则收费。只有你主动点击才会调用。

## 常见问题

- **授权失败**：核对 WHOOP 后台回调地址与网站显示的地址完全相同；六项 read 权限已开启。
- **没有数据 / 更新时间没变**：先在 WHOOP App 同步手环，再在网站点「立即同步」。仍失败，把网站错误提示交给 WorkBuddy，勿附密钥。
- **换域名**：先让 WorkBuddy 更新网站地址，再更新 WHOOP App 的回调地址并重新连接。

每人独立部署、使用自己的 WHOOP 应用和数据；分享包不含作者的健康数据或凭据。抓取内容以 WHOOP 官方开放 API 为准，分析不能替代医生判断。

给 WorkBuddy 的部署细节见 [WORKBUDDY.md](WORKBUDDY.md)，技术人员本地运行见 [LOCAL.md](LOCAL.md)。

参考：[WHOOP 官方入门](https://developer.whoop.com/docs/developing/getting-started/)、[WorkBuddy 网站发布](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/App-Publishing/Web-App)、[DeepSeek API](https://api-docs.deepseek.com/)。

开发联系：bleetchenxuanling@gmail.com
