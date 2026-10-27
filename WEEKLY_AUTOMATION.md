# 给 WorkBuddy：每周周报自动化

这是网站之外的第二项交付。请在使用者自己的 WorkBuddy 云上建立自动化，不建立作者电脑任务，也不依赖使用者电脑开机。

## 默认安排

- 名称：我的 WHOOP 每周健康汇总。
- 时间：每周一 15:00，Asia/Shanghai / UTC+8。
- 数据范围：上一完整周周一至周日，按日历日期筛选；缺失记录不填0，不用周外记录凑7天。
- 结果：返回完整中文周报作为 WorkBuddy 任务结果；为使用者开启其账号支持的原生小程序/移动端推送。若账号不支持主动通知，明确告知结果从哪里查看，不能声称已推送。
- 格式：恢复、主睡眠、HRV、静息心率、已完成日负荷、运动次数、与前一周对比及缺失覆盖。
- 无需 DeepSeek，不自动调用付费模型 API。WorkBuddy 执行任务本身可能消耗其套餐额度。

## 后端接口

网站上线后，服务端环境 Secret 配置独立的强随机 `AUTOMATION_SECRET`，自动化云环境通过受保护变量注入同值。`WHOOP_SITE_URL` 指向使用者自己的 HTTPS 域名。不要将密钥写进任务提示词、源码、URL查询参数或结果。

请求：`POST ${WHOOP_SITE_URL}/internal/weekly`

请求头：`Authorization: Bearer ${AUTOMATION_SECRET}`

响应：UTF-8 Markdown周报。接口只读已抓取历史数据，不做付费AI请求。网站每天09:00和12:00同步，周报15:00获取，报告须展示最后成功同步时间。若没有成功同步或报告缺数据，应把实情写进结果，不编造完整数据。

供自动化执行器参考（由 WorkBuddy 配置好云环境后运行，不要求用户手写）：

```python
import os, urllib.request
site = os.environ['WHOOP_SITE_URL'].rstrip('/')
key = os.environ['AUTOMATION_SECRET']
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
request = urllib.request.Request(site + '/internal/weekly', data=b'', method='POST',
    headers={'Authorization': 'Bearer ' + key})
with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
    digest = response.read(200_000).decode('utf-8')
print(digest)
```

如果平台云函数原生服务身份可替代 Bearer secret，可适配为同等受保护的服务器端调用。Secret注入或云端HTTP执行不可用时，要完成平台实际能力适配，不能把真实密钥放到提示词、不能改为公开周报链接。

## 建立任务时使用的提示词

> 每周一北京时间15:00，在云端从我已部署的WHOOP网站获取上一完整周中文周报。使用受保护的WHOOP_SITE_URL与AUTOMATION_SECRET变量调用POST /internal/weekly，只读取响应，不输出密钥或请求头。核对响应成功及最后成功同步时间，将完整周报作为本次任务结果，并通过我已启用的WorkBuddy原生移动端通知渠道提醒我。缺失数据和同步失败要如实标明。失败时报告状态，不生成虚构健康数据，不重复发送同一周结果。

## 验收与停止

1. 任务必须显示为云端执行；本地客户端自动化不等于云端自动化。
2. 立即手动运行一次，确认访问到的是用户自己的部署，返回正确周一至周日日期、覆盖数和最后同步时间。
3. 在使用者自己的 WorkBuddy 结果中看到周报，核对原生通知是否实际到达。
4. 记录任务名称、下次执行时间、时区和结果位置。平台若支持幂等运行状态，按周保存成功状态避免重试重复通知；发送状态不明先核实。
5. 用户随时可在 WorkBuddy 自动化页面暂停或删除这条任务；停止周报不影响每天更新的网站。

官方参考：[云上定时任务](https://www.workbuddy.cn/docs/workbuddymini/use-cases/Cases-Three)。本项目提供接口和部署说明；当前作者未替收件人创建云端任务或验证其实际推送，须在收件人账号部署时验收。
