// CloudBase 定时云函数：每天北京时间 09:00、12:00 调用网站同步接口，并等待同步完成。
// 环境变量：WHOOP_SITE_URL（网站 HTTPS 地址）、CRON_SECRET（与网站服务的 CRON_SECRET 相同）。
exports.main = async () => {
  const site = (process.env.WHOOP_SITE_URL || '').replace(/\/+$/, '');
  const secret = process.env.CRON_SECRET || '';
  if (!site.startsWith('https://') || !secret) {
    throw new Error('请配置 WHOOP_SITE_URL（https://...）和 CRON_SECRET');
  }
  const response = await fetch(site + '/internal/sync', {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + secret },
    redirect: 'manual',
  });
  const text = (await response.text()).slice(0, 500);
  if (response.status !== 200) {
    // 202 = 仍在同步（首次全量较慢），502 = 同步失败；不输出密钥。
    throw new Error('同步未成功：HTTP ' + response.status + ' ' + text);
  }
  return { ok: true, status: JSON.parse(text) };
};
