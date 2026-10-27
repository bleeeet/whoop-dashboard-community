#!/usr/bin/env python3
"""Single-user private WHOOP site. Python standard library only.

python3 cloud_server.py   (all settings optional, see WORKBUDDY.md)
  PUBLIC_URL       fixed HTTPS origin; unset = taken from the request Host
  ADMIN_PASSWORD   12+ characters; unset = first visit sets the password in /setup
  SETUP_CODE       optional code required by /setup
  DATA_ROOT, PORT, CRON_SECRET, AUTOMATION_SECRET
  CLOUDBASE_ENV_ID + CLOUDBASE_APIKEY  persist state in CloudBase PostgreSQL (store.py)
Only main() restores state and starts the scheduler. Importing/App construction is inert.
"""
import base64
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import html
import http.cookies
import http.server
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import report
import store as statestore
import whoop

_WHOOP_MUTEX = threading.Lock()
TZ = timezone(timedelta(hours=8))
SLOTS = ((9, 0), (12, 0))  # Beijing time, daily
SESSION_SECONDS = 30 * 24 * 3600
# The first-run form is filled in slowly (WHOOP Client ID/Secret are copied from
# another site), so the stateless token below lasts longer than a session.
SETUP_TOKEN_SECONDS = 6 * 3600
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')
# Hosts that look public but are only reachable from inside the publish sandbox.
# Used to detect a bad origin before the user copies an unusable redirect URI.
INTERNAL_HOST_MARKERS = ('e2b.', '.e2b', 'sandbox.cloudstudio')


def escape(value):
    return html.escape(str(value), quote=True)


def page(title, body):
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + escape(title) + '</title><style>'
            'body{font:16px/1.6 system-ui;background:#101418;color:#e7edf2;max-width:760px;'
            'margin:24px auto;padding:0 18px;overflow-wrap:anywhere}a{color:#67d6b3}'
            'input,button,select{font:inherit;box-sizing:border-box;max-width:100%;padding:10px;'
            'border:1px solid #52616b;border-radius:6px;background:#19232b;color:inherit}'
            'input{display:block;width:100%;margin:5px 0 16px}button{cursor:pointer;margin:8px 8px 8px 0}'
            'input[type=checkbox]{display:inline-block;width:auto;margin:0 8px 0 0;vertical-align:middle}'
            'form{margin:20px 0}pre{white-space:pre-wrap}nav{display:flex;gap:16px;flex-wrap:wrap}'
            '.box{border:1px solid #2f6f5c;border-radius:10px;padding:14px 16px;margin:18px 0;background:#13201c}'
            '.box form{margin:10px 0 0}.primary{background:#1f7a5c;border-color:#2fa37b;color:#fff;font-weight:600}'
            '.warn{color:#ffb86b}code{background:#19232b;padding:2px 6px;border-radius:4px}ol li{margin:6px 0}'
            'label{display:block;margin:4px 0}'
            # Guided first-run layout: numbered steps, copy buttons, todo checklist.
            '.lead{color:#a9bccb;margin:0 0 18px}'
            '.step{border:1px solid #2b3a44;border-left:3px solid #2fa37b;border-radius:8px;'
            'padding:12px 14px;margin:12px 0;background:#141c22}'
            '.step h3{margin:0 0 6px;font-size:17px}'
            '.step .n{display:inline-block;min-width:22px;height:22px;line-height:22px;text-align:center;'
            'border-radius:50%;background:#2fa37b;color:#08110d;font-size:13px;font-weight:700;margin-right:8px}'
            '.step p{margin:6px 0}.step ol,.step ul{margin:6px 0;padding-left:22px}'
            '.val{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:8px 0}'
            '.val code{flex:1 1 260px;word-break:break-all;padding:8px 10px}'
            '.val button{margin:0;padding:8px 12px;white-space:nowrap}'
            '.chip{display:inline-block;background:#19232b;border:1px solid #33454f;border-radius:999px;'
            'padding:2px 10px;margin:2px 6px 2px 0;font-size:14px}'
            '.field{display:flex;gap:8px;align-items:center}.field input{margin:5px 0 16px}'
            '.field button{margin:0 0 11px;padding:10px 12px;white-space:nowrap}'
            '.todo{list-style:none;padding:0;margin:8px 0}'
            '.todo li{padding:8px 0;border-bottom:1px solid #24313a;display:flex;gap:10px;'
            'align-items:flex-start;flex-wrap:wrap}.todo li:last-child{border-bottom:0}'
            '.todo .state{min-width:1.4em}.todo .grow{flex:1 1 240px}'
            '.done{color:#67d6b3}.pending{color:#ffb86b}'
            '.muted{color:#8fa3b1;font-size:14px}'
            '.btn{display:inline-block;background:#1f7a5c;border:1px solid #2fa37b;border-radius:6px;'
            'padding:9px 14px;color:#fff;text-decoration:none;font-weight:600}'
            'details{margin:10px 0}summary{cursor:pointer;color:#67d6b3}'
            '</style><h1>' + escape(title) + '</h1>' + body
            + '<footer style="margin-top:40px;border-top:1px solid #52616b;padding-top:16px">'
            '<a href="mailto:bleetchenxuanling@gmail.com">开发联系：bleetchenxuanling@gmail.com</a></footer>'
            + COPY_SCRIPT + '</html>')


# Copy-to-clipboard and "eye" reveal are progressive enhancements: every value is also
# shown as selectable text, so the page still works with JavaScript disabled.
COPY_SCRIPT = '''<script>
(function () {
  function flash(button, text) {
    var original = button.getAttribute('data-label') || button.textContent;
    button.setAttribute('data-label', original);
    button.textContent = text;
    setTimeout(function () { button.textContent = original; }, 1600);
  }
  function copy(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var area = document.createElement('textarea');
      area.value = text;
      area.setAttribute('readonly', '');
      area.style.position = 'fixed';
      area.style.top = '-1000px';
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (error) { ok = false; }
      document.body.removeChild(area);
      ok ? resolve() : reject(new Error('copy failed'));
    });
  }
  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-copy]');
    if (button) {
      copy(button.getAttribute('data-copy')).then(function () {
        flash(button, '已复制');
      }, function () {
        flash(button, '请手动选中复制');
      });
      return;
    }
    var eye = event.target.closest('[data-reveal]');
    if (eye) { reveal(eye); }
  });
  function reveal(button) {
    var field = button.getAttribute('data-reveal');
    var form = button.form || document;
    var input = form.querySelector('[name="' + field + '"]');
    if (!input) { return; }
    if (button.getAttribute('data-shown') === '1') {
      input.value = '';
      input.type = 'password';
      button.setAttribute('data-shown', '0');
      button.textContent = '👁 查看';
      return;
    }
    var token = form.querySelector('[name="csrf"]');
    if (!token) { return; }
    button.disabled = true;
    fetch('/settings/reveal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'csrf=' + encodeURIComponent(token.value) + '&field=' + encodeURIComponent(field)
    }).then(function (response) {
      if (!response.ok) { throw new Error('reveal failed'); }
      return response.json();
    }).then(function (data) {
      input.value = data.value || '';
      input.type = 'text';
      button.setAttribute('data-shown', '1');
      button.textContent = '🙈 隐藏';
    }).catch(function () {
      button.textContent = '无法读取，请重试';
    }).then(function () {
      button.disabled = false;
    });
  }
})();
</script>'''


def csrf_field(token):
    return '<input type="hidden" name="csrf" value="' + escape(token) + '">'


PRIVACY_PAGE = page('隐私说明', '<p>这是一个个人私有的 WHOOP 健康网站，只供网站所有者本人登录使用。</p><ul>'
    '<li>经所有者授权后，网站通过 WHOOP 官方 API 读取其恢复、睡眠、运动、周期、个人资料和身体测量数据。</li>'
    '<li>数据只保存在所有者自己的云端环境中，不出售、不共享给任何第三方。</li>'
    '<li>仅当所有者自行填写 DeepSeek API Key 并开启 AI 分析时，会把汇总统计（不含姓名、邮箱、记录标识）发送给 DeepSeek 生成分析。</li>'
    '<li>所有者可随时在网站设置中断开授权，或在 WHOOP 账号中撤销本应用的访问权限。</li></ul>')


def local_time(value):
    try:
        return datetime.fromisoformat(value).astimezone(TZ).strftime('%Y-%m-%d %H:%M（北京时间）')
    except (TypeError, ValueError):
        return ''


WHOOP_SCOPES = ('read:recovery', 'read:cycles', 'read:sleep', 'read:workout',
                'read:profile', 'read:body_measurement')
DEV_DASHBOARD = 'https://developer-dashboard.whoop.com/'


def copy_row(value):
    """A value plus a copy button. The text stays selectable without JavaScript."""
    return ('<div class="val"><code>' + escape(value) + '</code>'
            '<button type="button" data-copy="' + escape(value) + '">复制</button></div>')


def guide_steps(origin):
    """The PDF tutorial, inline. Nobody should have to open a separate PDF to start."""
    callback = origin + '/oauth/callback'
    privacy = origin + '/privacy'
    scopes = ''.join('<span class="chip">' + escape(name) + '</span>' for name in WHOOP_SCOPES)
    steps = (
        ('复制这两个地址，后面两处要用',
         '<p>回调地址（要填进 <b>REDIRECT URLS</b>）：</p>' + copy_row(callback)
         + '<p>隐私政策地址（要填进 <b>PRIVACY POLICY</b>，网站自带这个页面）：</p>' + copy_row(privacy)),
        ('登录 WHOOP 开发者后台',
         '<p>用你的 WHOOP 账号（手环 App 那个）登录。第一次进入如果提示创建 Team，'
         '随便起个名字创建即可；已有 Team 会直接进入团队页。</p>'
         '<p><a class="btn" href="' + DEV_DASHBOARD + '" target="_blank" rel="noreferrer">'
         '打开 WHOOP 开发者后台 →</a></p>'),
        ('新建一个 App',
         '<p>点顶部 <b>APPS</b>，在「Your apps」页面右上角点 <b>CREATE NEW APP</b>。</p>'),
        ('填名字和联系邮箱',
         '<p><b>NAME</b>：随便填，例如「我的健康工作台」（授权时会显示这个名字）<br>'
         '<b>LOGO</b>：可以不传<br><b>CONTACTS</b>：填你自己的邮箱</p>'),
        ('填隐私政策和回调地址（最容易错的一步）',
         '<p><b>PRIVACY POLICY</b>（必填，不填无法创建）：粘贴第 1 步那个以 <code>/privacy</code> 结尾的地址<br>'
         '<b>REDIRECT URLS → Redirect #1</b>：粘贴第 1 步那个以 <code>/oauth/callback</code> 结尾的地址</p>'
         '<p class="muted">必须和这里显示的一字不差：以 https:// 开头、以 /oauth/callback 结尾、'
         '末尾没有多余的 / 、没有空格。填错会导致最后一步授权失败。</p>'),
        ('勾选 6 个权限，然后创建',
         '<p>在 <b>SCOPES</b> 里把 6 项全部勾上：</p><p>' + scopes + '</p>'
         '<p><b>WEBHOOKS</b> 不用填。最后点 <b>CREATE APP</b>。</p>'),
        ('复制 Client ID 和 Client Secret',
         '<p>创建后进入 App 详情页，<b>Credentials</b> 区域就是你要的两个值：'
         'Client ID 直接复制；Client Secret 默认显示为星号，点右边的眼睛图标显示后再复制。</p>'
         '<p class="muted">Client Secret 相当于密码：只填进你自己的网站，'
         '不要发到聊天、群里或发给别人。页面上的“SANDBOX · 10 MEMBERS”是正常状态，个人使用不用升级。</p>'),
    )
    return ''.join('<div class="step"><h3><span class="n">' + str(index) + '</span>' + title + '</h3>' + text + '</div>'
                   for index, (title, text) in enumerate(steps, 1))


def guide_faq(origin):
    return ('<details><summary>点「连接 WHOOP」后报错 / 授权失败？</summary>'
            '<p>99% 是回调地址不一致。复制这个地址：' + copy_row(origin + '/oauth/callback')
            + '到 WHOOP App 的编辑页逐字核对。</p></details>'
            '<details><summary>创建 App 时提示 Privacy Policy URL is required</summary>'
            '<p>第 5 步的 PRIVACY POLICY 没填，填 ' + '<code>' + escape(origin + '/privacy') + '</code>。</p></details>'
            '<details><summary>首页显示「同步失败」或数据没更新</summary>'
            '<p>先打开 WHOOP 手机 App 让手环同步，再在网站首页点「立即同步」。</p></details>'
            '<details><summary>以前建过 App（比如本地版用的 localhost 回调）</summary>'
            '<p>不用重建：在 App 详情页点右上角铅笔图标进入编辑，把 Redirect 改成这个网站的地址'
            '（或用 ADD 新增），Privacy Policy 也可改成网站的 /privacy，保存即可。Client ID / Secret 不变。</p></details>'
            '<details><summary>DeepSeek API Key 怎么获取（选填）</summary>'
            '<p>打开 <a href="https://platform.deepseek.com/" target="_blank" rel="noreferrer">platform.deepseek.com</a>，'
            '注册登录后充值少量余额（几元即可，每次分析约几分钱）→ 左侧菜单 <b>API keys</b> → 创建，'
            '复制以 <code>sk-</code> 开头的密钥（只显示一次）→ 粘贴到设置页。模型保持默认即可。</p>'
            '<p class="muted">AI 分析只发送最近 30 条记录的汇总统计（平均值、最大最小值），'
            '不发送姓名、邮箱或原始记录。</p></details>')


def callback_help(origin):
    return ('<div class="box"><b>WHOOP 回调地址</b>（要填进 WHOOP 后台 App 的 Redirect URL）：'
            + copy_row(origin + '/oauth/callback')
            + '<p class="muted">Privacy Policy 地址：' + copy_row(origin + '/privacy')
            + '完整建 App 的 7 步图文流程见 <a href="/guide">新手引导</a>。</p></div>')


def _read_text(path):
    try:
        return Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def origin_warning(origin):
    """Warn when the derived origin cannot be reached by the user's browser.

    If the gateway does not forward X-Wb-Base-Url we fall back to X-Forwarded-Host,
    which behind the WorkBuddy gateway is the sandbox-internal host. An OAuth redirect
    URI built from it can never complete, so say so instead of showing a plausible
    looking URL that silently fails later.
    """
    host = (urllib.parse.urlsplit(origin).hostname or '').lower()
    if not host or host in LOCAL_HOSTS:
        return ''
    if not any(marker in host for marker in INTERNAL_HOST_MARKERS):
        return ''
    return ('<p class="warn"><b>⚠️ 回调地址不可用：</b>当前推导出的地址是沙箱内网地址，'
            '用它配置 WHOOP 将无法完成授权。请为服务设置环境变量 <code>PUBLIC_URL</code>'
            '（值为用户实际访问的 https 域名，例如 '
            '<code>https://whoop-xxx.app.workbuddy.host</code>，不带末尾斜杠）后重新发布。</p>')


def progress_box(csrf, has_whoop, connected, has_ai):
    """Home-page checklist: the setup flow stays visible until it is finished.

    The PDF tutorial used to be the only place describing the steps, so anyone who
    opened the site first saw half-empty content and had to guess what was missing.
    """
    def item(done, text, action=''):
        mark = '<span class="state done">✅</span>' if done else '<span class="state pending">⬜</span>'
        return '<li>' + mark + '<span class="grow">' + text + '</span>' + action + '</li>'

    connect = ('<form method="post" action="/oauth/start" style="margin:0">' + csrf
               + '<button class="primary">连接 WHOOP →</button></form>') if has_whoop and not connected else ''
    items = [item(True, '设置登录密码')]
    items.append(item(has_whoop,
                      '填 WHOOP Client ID / Secret（<a href="/guide">怎么获取</a>）',
                      '' if has_whoop else '<a class="btn" href="/settings">去填写 →</a>'))
    items.append(item(connected, '连接 WHOOP 并同意授权（点一下就好）', connect))
    items.append(item(has_ai, '（选填）填 DeepSeek API Key，开启 AI 分析',
                      '' if has_ai else '<a class="btn" href="/settings">去填写 →</a>'))
    required_left = sum(1 for done in (has_whoop, connected) if not done)
    if required_left:
        head = '<b>配置进度：还差 ' + str(required_left) + ' 步</b>'
    elif has_ai:
        head = '<b>配置进度：全部完成 🎉</b>'
    else:
        head = '<b>配置进度：必需的都完成了 ✅</b> <span class="muted">DeepSeek 是选填的</span>'
    return ('<div class="box">' + head
            + '<p class="muted">从上往下做完即可，蓝色按钮是可以直接点的下一步。</p>'
            '<ul class="todo">' + ''.join(items) + '</ul></div>')


def setup_form(csrf, needs_code, values=None, title='第 8 步：填写并保存'):
    """The credential form. Shared by the first-run wizard and the empty-state page."""
    values = values or {}
    body = ('<h2 id="setup-form">' + escape(title) + '</h2>'
            '<form method="post" action="/setup">' + csrf_field(csrf))
    if needs_code:
        body += ('<label>初始化码（部署时 WorkBuddy 告诉你的）'
                 '<input type="password" name="setup_code" autocomplete="off" required></label>')
    body += ('<label>设置登录密码（至少 8 位，以后打开网站用它登录，自己记好）'
             '<input type="password" name="password" autocomplete="new-password" minlength="8" required></label>'
             '<label>再输一次登录密码'
             '<input type="password" name="password2" autocomplete="new-password" minlength="8" required></label>'
             '<label>WHOOP Client ID（必填，第 7 步复制的第一个值）'
             '<input type="text" name="client_id" autocomplete="off" value="' + escape(values.get('client_id', '')) + '" required></label>'
             '<label>WHOOP Client Secret（必填，第 7 步点眼睛后复制的第二个值）'
             '<input type="password" name="client_secret" autocomplete="new-password" required></label>'
             '<label>DeepSeek API Key（选填，留空即可；填了才有 AI 分析）'
             '<input type="password" name="deepseek_key" autocomplete="new-password" placeholder="可以现在就跳过，之后在设置里补"></label>'
             '<input type="hidden" name="auto_ai_present" value="1">'
             '<label><input type="checkbox" name="auto_ai" value="1" checked>'
             '填了 DeepSeek 时，每次同步后自动生成 AI 分析（会把汇总统计发给 DeepSeek 并按其价格计费，每次约几分钱）</label>'
             '<button class="primary">保存，下一步连接 WHOOP</button></form>')
    return body


def setup_page(csrf, origin, needs_code):
    body = ('<p class="lead">第一次使用，跟着下面 7 步走一遍就好，大约 5 分钟。'
            '你要拿到的只有 <b>Client ID</b> 和 <b>Client Secret</b> 两个值——WHOOP 没有“一个 API Key”，'
            '而是“两个值 + 一次登录授权”，授权一次后网站会自动续期，以后不用再填。</p>'
            '<p class="lead">DeepSeek 可以跳过：不填也能自动抓数据、看全部图表和周报，之后随时能在设置里补。</p>'
            + origin_warning(origin)
            + '<div class="box"><b>整体流程：</b><br>'
            '① 复制回调地址 → ② 在 WHOOP 开发者后台创建 App、填回调地址、勾 6 个权限 → '
            '③ 复制 Client ID / Secret 填回下面表单 → ④ 保存后点「连接 WHOOP」登录并同意授权 → 完成。</div>'
            + guide_steps(origin)
            + setup_form(csrf, needs_code)
            + '<h2>常见问题</h2>' + guide_faq(origin))
    return page('新手引导：约 5 分钟完成初始化', body)


def guide_page(origin):
    """The same steps as a standalone page, reachable later from the settings page."""
    return page('新手引导：创建 WHOOP 开发者 App',
                '<p class="lead">要在 WHOOP 开发者后台建一个 App，才能拿到 Client ID 和 Client Secret。'
                '下面 7 步就是完整流程（与压缩包里的《WHOOP API 获取教程.pdf》一致）。</p>'
                + origin_warning(origin) + guide_steps(origin) + '<h2>常见问题</h2>' + guide_faq(origin))


class App:
    def __init__(self, data_root, public_url='', admin_password='', store=None, setup_code=''):
        public_url = public_url or ''
        if public_url:
            u = urllib.parse.urlsplit(public_url)
            try:
                u.port
            except ValueError:
                raise ValueError('Invalid PUBLIC_URL') from None
            local = u.hostname in LOCAL_HOSTS
            if (not u.hostname or u.username or u.password or u.path or u.query or u.fragment
                    or u.scheme not in ('http', 'https') or (u.scheme == 'http' and not local)
                    or '\\' in public_url or any(c.isspace() for c in public_url)):
                raise ValueError('PUBLIC_URL must be an HTTPS origin (HTTP loopback allowed for tests)')
        if admin_password and (not isinstance(admin_password, str) or len(admin_password) < 12):
            raise ValueError('ADMIN_PASSWORD must have at least 12 characters')
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.public_url = public_url
        self.redirect_uri = public_url + '/oauth/callback' if public_url else ''
        # None = derive per request from the forwarded origin.
        self.secure = urllib.parse.urlsplit(public_url).scheme == 'https' if public_url else None
        self._password = hashlib.sha256(admin_password.encode()).digest() if admin_password else None
        self.setup_code = setup_code or ''
        self.store = store
        self._remote = None
        self.backup_failed = False
        self._guard = threading.RLock()
        self._op = threading.Lock()
        self._sessions = {}
        self._rates = {}
        self._launch_lock = threading.Lock()
        self._worker = None
        self._stop = threading.Event()
        self._scheduler = None
        self._retry_after = 0

    def settings(self):
        with self._guard:
            value = whoop.read_json(self.root / 'settings.json', {})
            return value if isinstance(value, dict) else {}

    def status(self):
        with self._guard:
            value = whoop.read_json(self.root / 'status.json', {})
            value = value if isinstance(value, dict) else {}
            # Explicit allowlist; never expose a raw error/config object.
            return {k: value[k] for k in ('last_success', 'last_attempt', 'schedule_slot', 'error', 'full_synced') if k in value}

    def write_status(self, value):
        allowed = {'last_success', 'last_attempt', 'schedule_slot', 'error', 'full_synced'}
        with self._guard:
            whoop.write_json(self.root / 'status.json', {k: v for k, v in value.items() if k in allowed})

    @contextlib.contextmanager
    def operation(self):
        if not self._op.acquire(blocking=False):
            raise whoop.WhoopError('Operation busy')
        try:
            with whoop.InstanceLock(self.root / 'operation.lock'):
                with _WHOOP_MUTEX:
                    names = ('ENV_FILE', 'TOKEN_FILE', 'DATA_DIR', 'ENV', 'CLIENT_ID', 'CLIENT_SECRET', 'REDIRECT_URI')
                    previous = {k: getattr(whoop, k) for k in names}
                    config = self.settings()
                    whoop.ENV_FILE = str(self.root / '.env')
                    whoop.TOKEN_FILE = str(self.root / '.tokens.json')
                    whoop.DATA_DIR = str(self.root / 'data')
                    whoop.ENV = {}
                    whoop.CLIENT_ID = config.get('client_id', '')
                    whoop.CLIENT_SECRET = config.get('client_secret', '')
                    # Token refresh does not use it; validate_credentials needs a well-formed URL.
                    whoop.REDIRECT_URI = (self.public_url or config.get('site_origin') or 'https://localhost') + '/oauth/callback'
                    try:
                        yield
                    finally:
                        for k, v in previous.items():
                            setattr(whoop, k, v)
                        # Also after failures: a refreshed WHOOP token must never be lost.
                        self._backup()
        finally:
            self._op.release()

    def _backup(self):
        if not self.store:
            return
        try:
            self._remote = statestore.push(self.store, self.root, self._remote or {})
            self.backup_failed = False
        except statestore.StoreError as error:
            if len(error.args) > 1:
                self._remote = error.args[1]
            self.backup_failed = True
            print('云端备份失败，下次操作时重试', file=sys.stderr, flush=True)

    def restore(self):
        """Startup only: load persisted state before serving. Raises if the store is unreachable,
        because starting empty would later overwrite the remote copy."""
        if self.store:
            with self._guard:
                self._remote = statestore.restore(self.store, self.root, whoop.atomic_write)

    def ensure_ready(self):
        with self.operation():
            self._session_key()
            dashboard = self.root / 'dashboard.html'
            if not dashboard.is_file() and (self.root / 'data' / 'cycles.json').is_file():
                raw = report.load(str(self.root / 'data'))
                whoop.atomic_write(dashboard, report.render(report.build_days(raw), raw))

    # --- backup / restore ---

    def export_state(self):
        """Snapshot every tracked state file, for disaster recovery.

        Mirrors exactly what store.py would sync to the cloud, so a snapshot can
        rebuild a wiped sandbox. It contains secrets (password hash, WHOOP client
        secret, refresh token), which is why /internal/export is secret-gated and
        disabled unless BACKUP_SECRET is set.
        """
        files = {}
        for name in statestore.NAMES:
            text = _read_text(self.root / name)
            if text is not None:
                files[name] = text
        return {'format': 'whoop-state/1', 'created_at': _utc_now(), 'files': files}

    def restore_state(self, payload):
        """Replace tracked state files from an export snapshot. Returns the file count."""
        if not isinstance(payload, dict) or payload.get('format') != 'whoop-state/1':
            raise ValueError('备份文件格式不正确')
        files = payload.get('files')
        if not isinstance(files, dict) or not files:
            raise ValueError('备份内容为空')
        for name, text in files.items():
            if name not in statestore.NAMES:
                raise ValueError('备份包含未知文件：%s' % name)
            if not isinstance(text, str):
                raise ValueError('备份内容格式不正确：%s' % name)
        with self.operation():
            for name, text in files.items():
                whoop.atomic_write(self.root / name, text)
        return len(files)

    # --- password and signed sessions ---

    def needs_setup(self):
        return self._password is None and not isinstance(self.settings().get('password'), dict)

    def check_password(self, password):
        password = password.encode()
        if self._password is not None:
            return hmac.compare_digest(hashlib.sha256(password).digest(), self._password)
        stored = self.settings().get('password')
        if not isinstance(stored, dict):
            return False
        try:
            digest = hashlib.pbkdf2_hmac('sha256', password, bytes.fromhex(stored['salt']), int(stored['rounds']))
            return hmac.compare_digest(digest.hex(), stored['hash'])
        except (KeyError, TypeError, ValueError):
            return False

    def _session_key(self):
        with self._guard:
            config = self.settings()
            key = config.get('session_key')
            if not isinstance(key, str) or len(key) < 32:
                key = secrets.token_urlsafe(32)
                whoop.write_json(self.root / 'settings.json', dict(config, session_key=key))
            return key.encode()

    def _sign(self, text):
        epoch = str(self.settings().get('session_epoch', 0))
        mac = hmac.new(self._session_key(), (epoch + '|' + text).encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(mac).decode().rstrip('=')

    def _signed_session(self, sid):
        # Authenticated sessions survive restarts and scale-to-zero: 'a.<expires>.<nonce>.<mac>'.
        parts = sid.split('.') if isinstance(sid, str) and len(sid) < 200 else []
        if len(parts) != 4 or parts[0] != 'a' or not parts[1].isdigit():
            return None
        if not hmac.compare_digest(parts[3], self._sign('sid:' + parts[1] + '.' + parts[2])):
            return None
        expires = int(parts[1])
        if not time.time() < expires <= time.time() + SESSION_SECONDS + 60:
            return None
        return {'auth': True, 'csrf': self._sign('csrf:' + sid), 'expires': expires, 'states': {}}

    def setup(self, values):
        """First visit without ADMIN_PASSWORD: set the login password and WHOOP credentials."""
        password = values.get('password', '')
        if self.setup_code and not hmac.compare_digest(values.get('setup_code', '').strip().encode(), self.setup_code.encode()):
            raise ValueError('初始化码不正确')
        if len(password) < 8 or len(password) > 256:
            raise ValueError('登录密码至少 8 位')
        if password != values.get('password2', ''):
            raise ValueError('两次输入的密码不一致')
        if not values.get('client_id', '').strip() or not values.get('client_secret', '').strip():
            raise ValueError('请填写 WHOOP Client ID 和 Client Secret')
        with self.operation():
            if not self.needs_setup():
                raise ValueError('网站已经初始化，请直接登录')
            self._apply_settings(values)
            salt = secrets.token_bytes(16)
            rounds = 200_000
            digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, rounds)
            config = self.settings()
            config['password'] = {'salt': salt.hex(), 'rounds': rounds, 'hash': digest.hex()}
            whoop.write_json(self.root / 'settings.json', config)

    def setup_token(self):
        """Stateless CSRF token for the first-run form.

        Anonymous sessions live only in memory, so a container restart - or a cookie the
        browser never stored - would reject a form the user just spent several minutes
        filling in, and /setup would ask them to fill it out again forever. The token is
        signed, so it still blocks cross-site forgery without depending on session state.
        """
        expires = str(int(time.time() + SETUP_TOKEN_SECONDS))
        return 's.' + expires + '.' + self._sign('setup:' + expires)

    def check_setup_token(self, value):
        parts = value.split('.') if isinstance(value, str) and len(value) < 200 else []
        if len(parts) != 3 or parts[0] != 's' or not parts[1].isdigit():
            return False
        if int(parts[1]) <= int(time.time()):
            return False
        return hmac.compare_digest(parts[2], self._sign('setup:' + parts[1]))

    def logout_everywhere(self):
        with self.operation():
            config = self.settings()
            config['session_epoch'] = int(config.get('session_epoch', 0)) + 1
            whoop.write_json(self.root / 'settings.json', config)
        with self._guard:
            self._sessions = {k: v for k, v in self._sessions.items() if not v['auth']}

    def reveal_field(self, name):
        """Return one stored credential for the owner's own settings page.

        Client ID and the model name are rendered into the HTML directly. Secrets are
        fetched only when the owner clicks the eye button, so they never sit in the
        page source, the browser cache, or a screenshot of the settings page.
        """
        if name not in ('client_id', 'client_secret', 'deepseek_key', 'deepseek_model'):
            raise ValueError('unknown field')
        return str(self.settings().get(name) or '')

    def factory_reset(self):
        """Wipe stored state and go back to the first-run wizard.

        A mistyped Client Secret or a forgotten password otherwise locks the owner
        out permanently: without this, the only way back is deleting files inside
        the container, which nobody receiving this project can do. Runs inside
        operation(), so a CloudBase mirror loses the same rows.
        """
        with self.operation():
            data = self.root / 'data'
            if data.exists():
                shutil.rmtree(data)
            for name in set(statestore.NAMES) | {'dashboard.html'}:
                (self.root / name).unlink(missing_ok=True)
        with self._guard:
            self._sessions.clear()

    def remember_origin(self, origin):
        if not self.public_url and self.settings().get('site_origin') != origin:
            with self._guard:
                whoop.write_json(self.root / 'settings.json', dict(self.settings(), site_origin=origin))

    def _clear_account(self):
        # Call only while holding operation(). Never mix accounts, even when the
        # same developer App is used to authorize a different WHOOP user.
        with self._guard:
            for name in ('dashboard.html', 'ai.json', '.tokens.json', 'status.json'):
                (self.root / name).unlink(missing_ok=True)
            data = self.root / 'data'
            if data.exists():
                shutil.rmtree(data)
            for session in self._sessions.values():
                session['states'].clear()

    def update_settings(self, values):
        with self.operation():
            self._apply_settings(values)

    def _apply_settings(self, values):
        # Call only while holding operation().
        current = self.settings()
        updated = dict(current)
        for name in ('client_id', 'client_secret', 'deepseek_key', 'deepseek_model'):
            if values.get(name, '').strip():
                value = values[name].strip()
                if len(value) > 4096 or any(ord(c) < 32 for c in value):
                    raise ValueError('Invalid configuration')
                updated[name] = value
        updated.setdefault('deepseek_model', 'deepseek-flash')
        if values.get('remove_deepseek') == '1':
            updated.pop('deepseek_key', None)
        if values.get('auto_ai_present') == '1':
            updated['auto_ai'] = values.get('auto_ai') == '1'
        updated.setdefault('auto_ai', True)
        # Schedule is intentionally fixed, matching the deployment contract.
        updated['schedule_time'], updated['offset'] = '09:00,12:00', 480
        changed = any(updated.get(k) != current.get(k) for k in ('client_id', 'client_secret'))
        if changed:
            self._clear_account()
        whoop.write_json(self.root / 'settings.json', updated)

    def new_session(self, authenticated=False):
        with self._guard:
            now = time.time()
            self._sessions = {k: v for k, v in self._sessions.items() if v['expires'] > now}
            # Anonymous churn must never evict a logged-in session or its
            # pending OAuth state. Each population has its own bounded budget.
            peers = {k: v for k, v in self._sessions.items() if v['auth'] == bool(authenticated)}
            if authenticated and len(peers) >= 1024:
                raise ValueError('Authenticated session capacity reached')
            if not authenticated and len(peers) >= 128:
                oldest = min(peers, key=lambda k: peers[k]['expires'])
                del self._sessions[oldest]
            if authenticated:
                payload = str(int(now + SESSION_SECONDS)) + '.' + secrets.token_urlsafe(12)
                sid = 'a.' + payload + '.' + self._sign('sid:' + payload)
                self._sessions[sid] = self._signed_session(sid)
                return sid
            sid = secrets.token_urlsafe(32)
            self._sessions[sid] = {'auth': False, 'csrf': secrets.token_urlsafe(32),
                                   'expires': now + 12 * 3600, 'states': {}}
            return sid

    def session(self, sid):
        with self._guard:
            value = self._sessions.get(sid)
            if value and value['expires'] > time.time():
                # Re-check the signature so logout_everywhere() revokes cached sessions too.
                if not value['auth'] or self._signed_session(sid):
                    return dict(value)
            self._sessions.pop(sid, None)
            restored = self._signed_session(sid)
            if restored:
                self._sessions[sid] = restored
                return dict(restored)
            return None

    def issue_state(self, sid):
        with self._guard:
            if not (self.session(sid) or {}).get('auth'):
                raise ValueError('Login required')
            state = secrets.token_urlsafe(32)
            self._sessions[sid]['states'] = {state: time.time() + 900}
            return state

    def consume_state(self, sid, state):
        with self._guard:
            session = self._sessions.get(sid)
            if not session or not self.session(sid) or not session['auth']:
                return False
            expires = session['states'].pop(state, 0)
            return expires > time.time()

    def limited(self, key, maximum, seconds):
        with self._guard:
            now = time.time()
            self._rates = {k: v for k, v in self._rates.items() if v[1] > now}
            count, until = self._rates.get(key, (0, now + seconds))
            if len(self._rates) >= 4096 and key not in self._rates:
                return True
            self._rates[key] = (count + 1, until)
            return count >= maximum

    def due_slot(self, when=None):
        """Latest scheduled slot (09:00 or 12:00 Beijing time) at or before `when`."""
        local = (when or datetime.now(timezone.utc)).astimezone(TZ)
        for days in (0, 1):
            for hour, minute in reversed(SLOTS):
                slot = (local - timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
                if slot <= local:
                    return slot.isoformat(timespec='minutes')

    def schedule_due(self, when=None):
        return self.status().get('schedule_slot') != self.due_slot(when)

    def _sync_worker(self):
        config = self.settings()
        if not config.get('client_id') or not config.get('client_secret') or not (self.root / '.tokens.json').is_file():
            raise ValueError('WHOOP not connected')
        old = self.status()
        whoop.cmd_sync(days=7 if old.get('full_synced') else None)
        raw = report.load(str(self.root / 'data'))
        generated = report.render(report.build_days(raw), raw)
        whoop.atomic_write(self.root / 'dashboard.html', generated)

    def sync(self):
        try:
            with self.operation():
                previous = self.status()
                attempted = datetime.now(timezone.utc).isoformat()
                slot = self.due_slot()
                try:
                    self._sync_worker()
                except (Exception, SystemExit):
                    self.write_status(dict(previous, last_attempt=attempted, error='同步失败，请检查配置、授权和网络后重试'))
                    self._retry_after = time.time() + 300
                    return False
                self.write_status(dict(previous, last_attempt=attempted,
                                       last_success=datetime.now(timezone.utc).isoformat(),
                                       schedule_slot=slot, full_synced=True, error=''))
        except (Exception, SystemExit):
            self._retry_after = time.time() + 300
            return False
        config = self.settings()
        if config.get('deepseek_key') and config.get('auto_ai', True):
            # The user opted in when saving the key; failure keeps the previous answer.
            with contextlib.suppress(Exception):
                self.analyze()
        return True

    def sync_and_wait(self, timeout=840):
        """For the cloud timer: run (or join) one sync and wait, so scale-to-zero cannot cut it off."""
        with self._launch_lock:
            if not (self._worker and self._worker.is_alive()):
                self._worker = threading.Thread(target=self.sync, name='whoop-sync', daemon=True)
                self._worker.start()
            worker = self._worker
        worker.join(timeout)
        return not worker.is_alive()

    def launch_sync(self):
        with self._launch_lock:
            if self._worker and self._worker.is_alive():
                return False
            self._worker = threading.Thread(target=self.sync, name='whoop-sync', daemon=True)
            self._worker.start()
            return True

    def start_scheduler(self):
        def run():
            while not self._stop.is_set():
                if (self.root / '.tokens.json').exists() and self.schedule_due() and time.time() >= self._retry_after:
                    self.launch_sync()
                self._stop.wait(30)
        self._scheduler = threading.Thread(target=run, name='whoop-daily', daemon=True)
        self._scheduler.start()

    def close(self):
        self._stop.set()
        if self._scheduler:
            self._scheduler.join(2)

    def summary(self, when=None):
        """Plain numbers for the home page: this morning (last night) and yesterday (full day)."""
        days = {d['date']: d for d in report.build_days(report.load(str(self.root / 'data')))}
        local = (when or datetime.now(timezone.utc)).astimezone(TZ)
        today, yesterday = days.get(local.strftime('%Y-%m-%d')), days.get((local - timedelta(days=1)).strftime('%Y-%m-%d'))
        rows = []
        def number(value, suffix='', digits=0):
            return '—' if not isinstance(value, (int, float)) else (('%.' + str(digits) + 'f') % value) + suffix
        if today:
            rec = report.d_score(today, 'recovery') or {}
            sleep = report.d_score(today, 'sleep') or {}
            total = report.d_sleep_total(today)
            rows.append(('今天早上（' + today['date'][5:] + '）',
                         '恢复 ' + number(rec.get('recovery_score'), '%') + ' · 昨晚睡眠 '
                         + (report.fmt_dur(total) if total else '—') + ' · 睡眠表现 '
                         + number(sleep.get('sleep_performance_percentage'), '%') + ' · HRV '
                         + number(rec.get('hrv_rmssd_milli'), ' ms') + ' · 静息心率 '
                         + number(rec.get('resting_heart_rate'), ' bpm')))
        if yesterday:
            rows.append(('昨天全天（' + yesterday['date'][5:] + '）',
                         '日负荷 ' + number(report.d_strain(yesterday), '', 1) + ' · 运动 '
                         + str(len(yesterday['workouts'])) + ' 次 · 恢复 '
                         + number(report.d_rec(yesterday), '%')))
        return rows

    def aggregate(self):
        # A fixed numeric allowlist; no names, identifiers, timestamps or raw records.
        fields = {'recovery': ('recovery_score', 'hrv_rmssd_milli', 'resting_heart_rate'),
                  'cycles': ('strain', 'average_heart_rate'),
                  'sleep': ('sleep_performance_percentage', 'sleep_efficiency_percentage')}
        result = {}
        for dataset, names in fields.items():
            rows = whoop.read_json(self.root / 'data' / (dataset + '.json'), [])
            if not isinstance(rows, list):
                continue
            for name in names:
                values = []
                for row in rows[-30:]:
                    score = row.get('score') if isinstance(row, dict) else None
                    value = score.get(name) if isinstance(score, dict) else None
                    if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value):
                        values.append(value)
                if values:
                    result[name] = {'count': len(values), 'mean': round(sum(values) / len(values), 2),
                                    'min': min(values), 'max': max(values)}
        return result

    def weekly_report(self):
        with self.operation():
            # Optional at import time while the standalone weekly module is
            # packaged separately; generation itself remains under the lock.
            import weekly
            markdown = weekly.generate(str(self.root / 'data'))
            last_success = self.status().get('last_success')
            if not isinstance(last_success, str) or not last_success.strip():
                last_success = '尚未成功同步'
            return (markdown.rstrip() + '\n\n---\n\n'
                    + '数据快照说明：最后成功同步：' + last_success
                    + '。本周报基于已保存的数据快照；生成周报不会触发 WHOOP 同步。\n')

    def analyze(self):
        with self.operation():
            config = self.settings()
            if not config.get('deepseek_key'):
                raise ValueError('DeepSeek not configured')
            aggregate = self.aggregate()
            if not aggregate:
                raise ValueError('No aggregate data')
            payload = {'model': config.get('deepseek_model', 'deepseek-flash'),
                       'messages': [{'role': 'system', 'content': '用中文简要分析这些健康统计，仅解释趋势，不作医疗诊断。'},
                                    {'role': 'user', 'content': json.dumps(aggregate, ensure_ascii=False)}],
                       'max_tokens': 1200, 'stream': False}
            request = urllib.request.Request('https://api.deepseek.com/chat/completions',
                         data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + config['deepseek_key'],
                         'Content-Type': 'application/json'}, method='POST')
            # Reuse the private non-redirecting transport; never forward API keys.
            try:
                with whoop._open_no_redirect(request, timeout=60) as response:
                    answer = json.loads(response.read(1024 * 1024))['choices'][0]['message']['content']
            except urllib.error.HTTPError as error:
                # Close explicitly: Python's resource warning can include the
                # remote HTTP reason, which must never reach logs.
                error.close()
                raise ValueError('AI request failed') from None
            if not isinstance(answer, str):
                raise ValueError('Invalid AI response')
            # Defense in depth: even a malicious response cannot echo known secrets.
            for value in (config.get('client_id'), config.get('client_secret'), config.get('deepseek_key')):
                if value:
                    answer = answer.replace(value, '[已隐藏]')
            whoop.write_json(self.root / 'ai.json', {'answer': answer[:24000]})
            return answer[:24000]


def make_server(app, host='0.0.0.0', port=8080):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # URLs can contain authorization codes; never log requests.

        def reply(self, code, body='', location=None, cookie=None, kind='text/html; charset=utf-8'):
            encoded = body.encode('utf-8') if isinstance(body, str) else body
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(encoded)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'same-origin')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self' https://api.prod.whoop.com; frame-ancestors 'none'; base-uri 'none'")
            if location:
                self.send_header('Location', location)
            if cookie:
                age = SESSION_SECONDS if cookie.startswith('a.') else 43200
                self.send_header('Set-Cookie', 'session=' + cookie + '; Path=/; HttpOnly; SameSite=Lax; Max-Age=' + str(age) + ('; Secure' if self.secure() else ''))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            self.wfile.write(encoded)

        def origin(self):
            """The site origin users see.

            Order: an explicit PUBLIC_URL env override, then the publish gateway's
            X-Wb-Base-Url, then the forwarded Host. X-Forwarded-Host is deliberately
            last: behind the WorkBuddy gateway it carries the sandbox-internal host
            (e.g. 8080-<id>.e2b.gz5.sandbox.cloudstudio.club), which is not
            reachable by the user's browser and would produce an OAuth redirect URI
            that can never complete.
            """
            if app.public_url:
                return app.public_url
            raw = (self.headers.get('X-Wb-Base-Url') or '').split(',')[0].strip()
            if raw:
                # The gateway hands over the full share URL; keep only scheme://host[:port].
                parts = urllib.parse.urlsplit(raw)
                if parts.scheme in ('http', 'https') and parts.hostname:
                    host = parts.hostname
                    if parts.port:
                        host += ':' + str(parts.port)
                    return parts.scheme + '://' + host
            host = (self.headers.get('X-Forwarded-Host') or self.headers.get('Host') or '').split(',')[0].strip()
            if not re.fullmatch(r'[A-Za-z0-9.-]{1,253}(:[0-9]{1,5})?', host):
                raise ValueError('Invalid Host')
            name = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
            # TLS ends at the cloud gateway, so every public host is HTTPS.
            return ('http://' if name in LOCAL_HOSTS else 'https://') + host

        def secure(self):
            if app.secure is not None:
                return app.secure
            try:
                return self.origin().startswith('https://')
            except ValueError:
                return True

        def identity(self):
            try:
                cookie = http.cookies.SimpleCookie(self.headers.get('Cookie', ''))
                sid = cookie['session'].value if 'session' in cookie else ''
            except http.cookies.CookieError:
                sid = ''
            return sid, app.session(sid)

        def fields(self):
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 16384 or self.headers.get('Transfer-Encoding'):
                raise ValueError('Invalid body size')
            if self.headers.get_content_type() != 'application/x-www-form-urlencoded':
                raise ValueError('Invalid content type')
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError('Incomplete body')
            values = urllib.parse.parse_qs(data.decode('utf-8'), keep_blank_values=True, max_num_fields=30)
            if any(len(v) != 1 for v in values.values()):
                raise ValueError('Duplicate fields')
            return {k: v[0] for k, v in values.items()}

        def raw_body(self, limit=8 * 1024 * 1024):
            """Read an unformatted request body (used by /internal/restore)."""
            length = int(self.headers.get('Content-Length', '0') or '0')
            if length < 0 or length > limit or self.headers.get('Transfer-Encoding'):
                raise ValueError('内容过大')
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError('内容不完整')
            return data

        def valid_csrf(self, session, fields):
            origin = self.headers.get('Origin')
            token = fields.get('csrf', '')
            return bool(session and (origin is None or origin == self.origin())
                        and hmac.compare_digest(token.encode(), session['csrf'].encode()))

        def do_GET(self):
            try:
                self.get()
            except (Exception, SystemExit):
                self.reply(500, '请求失败，请稍后重试')

        def get(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if self.path == '/health':
                return self.reply(200, 'ok', kind='text/plain; charset=utf-8')
            if path == '/internal/export':
                secret = os.environ.get('BACKUP_SECRET', '')
                authorization = self.headers.get('Authorization', '')
                if not secret or not hmac.compare_digest(authorization.encode(), ('Bearer ' + secret).encode()):
                    return self.reply(403, 'Forbidden')
                if app.limited('export', 10, 60):
                    return self.reply(429, '请求过于频繁，请稍后重试')
                return self.reply(200, json.dumps(app.export_state(), ensure_ascii=False),
                                  kind='application/json; charset=utf-8')
            if self.path == '/privacy':
                # Public: WHOOP requires a privacy policy URL for every developer App.
                return self.reply(200, PRIVACY_PAGE)
            allowed = ('/', '/login', '/setup', '/guide', '/settings', '/dashboard', '/status', '/ai', '/weekly', '/oauth/callback')
            if path not in allowed or (parsed.query and path != '/oauth/callback'):
                return self.reply(404, 'Not found')
            sid, session = self.identity()
            if app.needs_setup():
                if path == '/guide':
                    return self.reply(200, guide_page(self.origin()))
                if path != '/setup':
                    return self.reply(303, location='/setup')
                if not session:
                    if app.limited(('login-page', self.client_address[0]), 20, 300):
                        return self.reply(429, '请求过于频繁，请稍后重试')
                    sid = app.new_session()
                    session = app.session(sid)
                return self.reply(200, setup_page(app.setup_token(), self.origin(), bool(app.setup_code)), cookie=sid)
            if path == '/setup':
                return self.reply(303, location='/')
            if path == '/login':
                if session and session['auth']:
                    return self.reply(303, location='/')
                if not session:
                    if app.limited(('login-page', self.client_address[0]), 20, 300):
                        return self.reply(429, '登录请求过于频繁，请稍后重试')
                    sid = app.new_session()
                    session = app.session(sid)
                return self.reply(200, page('登录 WHOOP 私人工作台', '<form method="post" action="/login">' + csrf_field(session['csrf']) + '<label>登录密码<input type="password" name="password" autocomplete="current-password" required></label><button>登录</button></form>'), cookie=sid)
            if not session or not session['auth']:
                return self.reply(303, location='/login')
            if path == '/guide':
                return self.reply(200, guide_page(self.origin()))
            if path == '/oauth/callback':
                q = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                if any(len(v) != 1 for v in q.values()) or set(q) - {'code', 'state', 'error', 'error_description', 'scope'}:
                    return self.reply(400, '授权回调无效，请重新连接')
                with app.operation():
                    if not app.consume_state(sid, q.get('state', [''])[0]) or not q.get('code', [''])[0] or 'error' in q:
                        return self.reply(400, '授权回调无效或已过期，请重新连接')
                    tok = whoop.post_form(whoop.TOKEN_URL, {'grant_type': 'authorization_code',
                        'code': q['code'][0], 'client_id': whoop.CLIENT_ID,
                        'client_secret': whoop.CLIENT_SECRET, 'redirect_uri': self.origin() + '/oauth/callback'})
                    if not isinstance(tok, dict) or not isinstance(tok.get('access_token'), str) or not tok['access_token']:
                        raise ValueError('Invalid token response')
                    app._clear_account()
                    whoop.save_tokens(tok)
                app.launch_sync()
                return self.reply(303, location='/')
            if path == '/dashboard':
                with app._guard:
                    file = app.root / 'dashboard.html'
                    body = file.read_bytes() if file.is_file() else None
                if body is not None:
                    links = '<div style="max-width:780px;margin:16px auto;padding:0 20px"><a href="/">返回首页</a> · <a href="/settings">设置</a></div>'
                    body = body.replace(b'<body>', ('<body>' + links).encode(), 1)
                    return self.reply(200, body)
                return self.reply(503, '尚无工作台，请先连接 WHOOP 并同步')
            if path == '/status':
                return self.reply(200, json.dumps(app.status(), ensure_ascii=False), kind='application/json; charset=utf-8')
            nav = '<nav><a href="/">首页</a><a href="/dashboard">健康工作台</a><a href="/settings">设置</a><a href="/ai">AI 分析</a><a href="/weekly">每周周报</a></nav>'
            csrf = csrf_field(session['csrf'])
            config = app.settings()
            connected = (app.root / '.tokens.json').is_file()
            if path == '/weekly':
                return self.reply(200, page('每周周报', nav + '<pre>' + escape(app.weekly_report()) + '</pre>'))
            if path == '/settings':
                body = nav
                if config.get('client_id') and config.get('client_secret'):
                    state = '已连接，后台每天自动更新' if connected else '还差最后一步：点下面按钮登录 WHOOP 并同意授权'
                    body += ('<div class="box"><b>WHOOP 授权：</b>' + state + '<form method="post" action="/oauth/start">' + csrf
                             + '<button class="primary">' + ('重新连接 WHOOP' if connected else '连接 WHOOP') + '</button></form></div>')
                body += callback_help(self.origin())
                body += ('<p class="muted">Client ID 和模型名会直接显示；'
                         'Client Secret 与 DeepSeek Key 不回显在网页源码里，'
                         '点「显示已保存的值」按需读取，改完保存即可。留空 = 保持原值。</p>'
                         '<form method="post" action="/settings">' + csrf)
                for name, title, placeholder, secret in (
                        ('client_id', 'WHOOP Client ID（必填）', '粘贴 WHOOP 后台 App 的 Client ID', False),
                        ('client_secret', 'WHOOP Client Secret（必填）', '粘贴 WHOOP 后台 App 的 Client Secret', True),
                        ('deepseek_key', 'DeepSeek API Key（选填，不填只抓数据不做 AI 分析）', 'sk-...', True),
                        ('deepseek_model', 'DeepSeek 模型', 'deepseek-flash', False)):
                    saved = str(config.get(name) or '')
                    if secret:
                        hint = '已保存，点右侧按钮查看' if saved else placeholder
                        body += ('<label>' + title + ('　<span class="done">✅ 已设置</span>' if saved else '')
                                 + '<span class="field"><input type="password" autocomplete="new-password" name="' + name
                                 + '" placeholder="' + escape(hint) + '">'
                                 + ('<button type="button" data-reveal="' + name + '" data-shown="0" title="查看已保存的值">👁 查看</button>' if saved else '')
                                 + '</span></label>')
                    else:
                        body += ('<label>' + title + '　<span class="done">✅ 已设置</span>'
                                 '<input type="text" autocomplete="off" name="' + name + '" value="' + escape(saved)
                                 + '" placeholder="' + escape(placeholder) + '"></label>')
                body += ('<input type="hidden" name="auto_ai_present" value="1"><label><input type="checkbox" name="auto_ai" value="1"'
                         + (' checked' if config.get('auto_ai', True) else '') + '>填了 DeepSeek 时，每次同步后自动生成 AI 分析（会把汇总统计发给 DeepSeek，按其价格计费，每次约几分钱）</label>'
                         '<label><input type="checkbox" name="remove_deepseek" value="1">删除已保存的 DeepSeek key</label>'
                         '<p>每天北京时间 09:00 和 12:00 自动更新；错过时下次打开网站后台补跑。</p><button class="primary">保存设置</button></form>')
                body += ('<div class="box"><b>恢复出厂设置</b>'
                         '<p>清除登录密码、WHOOP 凭据、已同步的数据和 AI 分析，回到第一次打开时的初始化界面。'
                         '凭据填错了、或想换一个 WHOOP 开发者应用时用它重来一次。</p>'
                         '<form method="post" action="/settings/reset">' + csrf
                         + '<label>输入当前登录密码以确认<input type="password" name="password" autocomplete="current-password" required></label>'
                         '<button>清除全部配置并重新开始</button></form></div>')
                return self.reply(200, page('设置', body))
            if path == '/ai':
                saved = whoop.read_json(app.root / 'ai.json', {})
                answer = saved.get('answer', '') if isinstance(saved, dict) else ''
                body = nav + '<p>仅在主动提交后，将最近最多 30 条记录的聚合统计发给 DeepSeek 官方 API，并按其规则收费。不会发送姓名、邮箱、记录标识或原始记录。分析不替代医疗诊断。</p><form method="post" action="/ai">' + csrf + '<label><input type="checkbox" name="consent" value="1" required>同意发送汇总健康数据并承担 API 费用</label><button>生成 AI 分析</button></form><pre>' + escape(answer) + '</pre>'
                return self.reply(200, page('AI 分析（可选）', body))
            status = app.status()
            has_whoop = bool(config.get('client_id') and config.get('client_secret'))
            has_ai = bool(config.get('deepseek_key'))
            body = nav + progress_box(csrf, has_whoop, connected, has_ai)
            rows = app.summary() if connected else []
            if rows:
                body += '<div class="box">' + ''.join('<p><b>' + escape(k) + '</b><br>' + escape(v) + '</p>' for k, v in rows) + '<a href="/dashboard">查看完整健康工作台 →</a></div>'
            elif not has_whoop:
                body += ('<p class="muted">还没填 WHOOP 凭据，所以这里暂时没有数据。'
                         '按 <a href="/guide">新手引导</a> 在 WHOOP 开发者后台建一个 App，'
                         '拿到 Client ID 和 Client Secret 后填进 <a href="/settings">设置</a>，'
                         '再点上面的「连接 WHOOP」授权，数据就会自动开始同步。</p>')
            body += '<p>最后成功同步：' + escape(local_time(status.get('last_success')) or '尚未同步') + '</p>'
            if status.get('error'):
                body += '<p class="warn">' + escape(status['error']) + '</p>'
            if app.backup_failed:
                body += '<p class="warn">云端数据库备份失败，系统会在下次操作时重试；若持续出现请让 WorkBuddy 检查 CloudBase 配置。</p>'
            body += '<p>每天北京时间 09:00 和 12:00 自动更新。</p><form method="post" action="/sync">' + csrf + '<button>立即同步</button></form><form method="post" action="/logout">' + csrf + '<button>退出登录（所有设备）</button></form>'
            return self.reply(200, page('WHOOP 私人健康网站', body))

        def do_POST(self):
            try:
                self.post()
            except (Exception, SystemExit):
                self.reply(400, '操作失败，请检查设置、授权及网络后重试')

        def post_setup(self):
            """First-run form.

            Uses a stateless signed token, so submitting still works when the anonymous
            session is gone (container restart) or the cookie was never stored - both
            otherwise send the user back to an empty form after they already typed in
            their WHOOP credentials.
            """
            if app.limited(('login', self.client_address[0]), 10, 300):
                return self.reply(429, '尝试过多，请稍后重试')
            values = self.fields()
            sid, session = self.identity()
            if not ((session and self.valid_csrf(session, values))
                    or app.check_setup_token(values.get('csrf', ''))):
                return self.reply(403, page('无法提交',
                    '<p>这个表单已失效：页面停留太久，或服务重启过。</p>'
                    '<p>请<a href="/setup">返回重新填写一次</a>即可，已填的内容需要再粘一次。</p>'))
            try:
                app.setup(values)
            except ValueError as error:
                return self.reply(400, page('初始化失败',
                    '<p>' + escape(error) + '</p>'
                    '<p><a href="/setup">返回重填</a>　<a href="/login">去登录</a></p>'))
            with app._guard:
                app._sessions.pop(sid, None)
            return self.reply(303, location='/settings', cookie=app.new_session(True))

        def post(self):
            path = self.path
            if path == '/setup':
                return self.post_setup()
            if path == '/internal/weekly':
                secret = os.environ.get('AUTOMATION_SECRET', '')
                authorization = self.headers.get('Authorization', '')
                if not secret or not hmac.compare_digest(authorization.encode(), ('Bearer ' + secret).encode()):
                    return self.reply(403, 'Forbidden')
                if app.limited('weekly', 10, 60):
                    return self.reply(429, '请稍后重试')
                return self.reply(200, app.weekly_report(), kind='text/markdown; charset=utf-8')
            if path == '/internal/sync':
                secret = os.environ.get('CRON_SECRET', '')
                authorization = self.headers.get('Authorization', '')
                if not secret or not hmac.compare_digest(authorization.encode(), ('Bearer ' + secret).encode()):
                    return self.reply(403, 'Forbidden')
                if app.limited('cron', 10, 60):
                    return self.reply(429, '请稍后重试')
                if not app.sync_and_wait():
                    return self.reply(202, '同步仍在进行', kind='text/plain; charset=utf-8')
                status = app.status()
                ok = not status.get('error') and bool(status.get('last_success'))
                return self.reply(200 if ok else 502, json.dumps(status, ensure_ascii=False), kind='application/json; charset=utf-8')
            if path == '/internal/restore':
                secret = os.environ.get('BACKUP_SECRET', '')
                authorization = self.headers.get('Authorization', '')
                if not secret or not hmac.compare_digest(authorization.encode(), ('Bearer ' + secret).encode()):
                    return self.reply(403, 'Forbidden')
                if app.limited('restore', 10, 60):
                    return self.reply(429, '请求过于频繁，请稍后重试')
                try:
                    count = app.restore_state(json.loads(self.raw_body().decode('utf-8')))
                except (ValueError, UnicodeDecodeError):
                    return self.reply(400, '恢复失败：备份文件格式不正确')
                return self.reply(200, '已恢复 %d 个状态文件' % count, kind='text/plain; charset=utf-8')
            if path not in ('/login', '/setup', '/settings', '/settings/reset', '/settings/reveal', '/oauth/start', '/sync', '/ai', '/logout'):
                return self.reply(404, 'Not found')
            sid, session = self.identity()
            if not session or (path not in ('/login', '/setup') and not session['auth']):
                return self.reply(403, '请先登录')
            values = self.fields()
            if not self.valid_csrf(session, values):
                return self.reply(403, '请求验证失败，请刷新页面')
            if path == '/setup':
                if app.limited(('login', self.client_address[0]), 10, 300):
                    return self.reply(429, '尝试过多，请稍后重试')
                try:
                    app.setup(values)
                except ValueError as error:
                    return self.reply(400, page('初始化失败', '<p>' + escape(error) + '</p><a href="/setup">返回重填</a>'))
                with app._guard:
                    app._sessions.pop(sid, None)
                return self.reply(303, location='/settings', cookie=app.new_session(True))
            if path == '/login':
                if app.limited(('login', self.client_address[0]), 10, 300):
                    return self.reply(429, '登录尝试过多，请稍后重试')
                if not app.check_password(values.get('password', '')):
                    return self.reply(403, page('登录失败', '<p>密码不正确。</p><a href="/login">重新登录</a>'))
                with app._guard:
                    app._sessions.pop(sid, None)
                return self.reply(303, location='/', cookie=app.new_session(True))
            if path == '/logout':
                app.logout_everywhere()
                return self.reply(303, location='/login', cookie='deleted')
            if app.limited(('action', sid), 30, 60):
                return self.reply(429, '操作过于频繁，请稍后重试')
            if path == '/settings/reveal':
                if app.limited(('reveal', sid), 30, 60):
                    return self.reply(429, '请稍后重试')
                try:
                    value = app.reveal_field(values.get('field', ''))
                except ValueError:
                    return self.reply(400, '不支持的字段')
                return self.reply(200, json.dumps({'value': value}, ensure_ascii=False),
                                  kind='application/json; charset=utf-8')
            if path == '/settings/reset':
                if app.limited(('reset', sid), 5, 3600):
                    return self.reply(429, '操作过于频繁，请稍后重试')
                if not app.check_password(values.get('password', '')):
                    return self.reply(403, page('无法重置',
                        '<p>登录密码不正确，未做任何改动。</p><a href="/settings">返回设置</a>'))
                app.factory_reset()
                return self.reply(303, location='/setup', cookie='deleted')
            if path == '/settings':
                app.update_settings(values)
                return self.reply(303, location='/settings')
            if path == '/oauth/start':
                with app.operation():
                    whoop.validate_credentials()
                    app.remember_origin(self.origin())
                    state = app.issue_state(sid)
                    url = whoop.AUTH_URL + '?' + urllib.parse.urlencode({'client_id': whoop.CLIENT_ID,
                        'redirect_uri': self.origin() + '/oauth/callback', 'response_type': 'code', 'scope': whoop.SCOPES, 'state': state})
                return self.reply(303, location=url)
            if path == '/sync':
                launched = app.launch_sync()
                return self.reply(202 if launched else 409, page('同步', '<p>同步已提交或正在进行，请稍后回首页查看状态。</p><a href="/">返回首页</a>'))
            if path == '/ai':
                if not app.settings().get('deepseek_key') or values.get('consent') != '1':
                    return self.reply(400, '请配置 DeepSeek 并明确同意发送汇总数据及 API 费用')
                if app.limited(('ai', sid), 3, 3600):
                    return self.reply(429, 'AI 调用过于频繁，请稍后重试')
                app.analyze()
                return self.reply(303, location='/ai')

    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        def get_request(self):
            conn, address = super().get_request()
            conn.settimeout(10)
            return conn, address
        def handle_error(self, *args):
            pass
    if ':' in host:
        Server.address_family = socket.AF_INET6
    return Server((host, port), Handler)


def default_data_root():
    """Where state lives when DATA_ROOT is not set.

    Must be outside the source tree: a publish/redeploy replaces the whole source
    directory, so anything written next to cloud_server.py disappears on the next
    deploy. The publish sandbox reuses one container, so a /tmp path survives
    redeploys. Deciding this in code (not only in start.sh) means a bare
    `python3 cloud_server.py`, or any auto-detected entry point, is already safe.
    """
    for candidate in ('/tmp/whoop-app-data',
                      os.path.join(os.path.expanduser('~'), '.whoop-app-data')):
        try:
            Path(candidate).mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.access(candidate, os.W_OK):
                return candidate
        except OSError:
            continue
    # Last resort: stay next to the code and say so, rather than failing to start.
    fallback = Path(__file__).resolve().parent / 'runtime'
    fallback.mkdir(parents=True, exist_ok=True, mode=0o700)
    return str(fallback)


def main():
    app = None
    server = None
    try:
        app = App(os.environ.get('DATA_ROOT') or default_data_root(),
                  os.environ.get('PUBLIC_URL', '').strip().rstrip('/'), os.environ.get('ADMIN_PASSWORD', ''),
                  store=statestore.from_env(), setup_code=os.environ.get('SETUP_CODE', '').strip())
        port = int(os.environ.get('PORT', '8080'))
        if not 1 <= port <= 65535:
            raise ValueError('Invalid port')
        # Service lock prevents duplicate scheduler instances on the same volume.
        with whoop.InstanceLock(app.root / 'server.lock'):
            app.restore()
            app.ensure_ready()
            server = make_server(app, os.environ.get('HOST', '0.0.0.0'), port)
            app.start_scheduler()
            print('WHOOP private service ready' + ('（CloudBase 持久化已启用）' if app.store else '（仅本地 DATA_ROOT）'), flush=True)
            server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 0
    except (Exception, SystemExit):
        print('WHOOP 服务启动失败，请检查 PUBLIC_URL、ADMIN_PASSWORD、端口、CloudBase 数据库配置（CLOUDBASE_ENV_ID/CLOUDBASE_APIKEY/whoop_state 表）或已有实例', file=sys.stderr)
        return 1
    finally:
        if app:
            app.close()
        if server:
            server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
