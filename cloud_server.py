#!/usr/bin/env python3
"""Single-user private WHOOP site. Python standard library only.

PUBLIC_URL=https://your.domain ADMIN_PASSWORD=<12+ characters> DATA_ROOT=/data
PORT=8080 python3 cloud_server.py
Only main() starts the daily scheduler. Importing/App construction is inert.
"""
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
import whoop

_WHOOP_MUTEX = threading.Lock()
TZ = timezone(timedelta(hours=8))


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
            '</style><h1>' + escape(title) + '</h1>' + body
            + '<footer style="margin-top:40px;border-top:1px solid #52616b;padding-top:16px">'
            '<a href="mailto:bleetchenxuanling@gmail.com">开发联系：bleetchenxuanling@gmail.com</a></footer></html>')


def csrf_field(token):
    return '<input type="hidden" name="csrf" value="' + escape(token) + '">'


class App:
    def __init__(self, data_root, public_url, admin_password):
        u = urllib.parse.urlsplit(public_url)
        try:
            port = u.port
        except ValueError:
            raise ValueError('Invalid PUBLIC_URL') from None
        local = u.hostname in ('localhost', '127.0.0.1', '::1')
        if (not u.hostname or u.username or u.password or u.path or u.query or u.fragment
                or u.scheme not in ('http', 'https') or (u.scheme == 'http' and not local)
                or '\\' in public_url or any(c.isspace() for c in public_url)):
            raise ValueError('PUBLIC_URL must be an HTTPS origin (HTTP loopback allowed for tests)')
        if not isinstance(admin_password, str) or len(admin_password) < 12:
            raise ValueError('ADMIN_PASSWORD must have at least 12 characters')
        self.root = Path(data_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.public_url = public_url
        self.redirect_uri = public_url + '/oauth/callback'
        self.secure = u.scheme == 'https'
        self._password = hashlib.sha256(admin_password.encode()).digest()
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
                    whoop.REDIRECT_URI = self.redirect_uri
                    try:
                        yield
                    finally:
                        for k, v in previous.items():
                            setattr(whoop, k, v)
        finally:
            self._op.release()

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
            # Schedule is intentionally fixed, matching the deployment contract.
            updated['schedule_time'], updated['offset'] = '14:00', 480
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
            sid = secrets.token_urlsafe(32)
            self._sessions[sid] = {'auth': bool(authenticated), 'csrf': secrets.token_urlsafe(32),
                                   'expires': now + 12 * 3600, 'states': {}}
            return sid

    def session(self, sid):
        with self._guard:
            value = self._sessions.get(sid)
            if value and value['expires'] > time.time():
                return dict(value)
            self._sessions.pop(sid, None)
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
        local = (when or datetime.now(timezone.utc)).astimezone(TZ)
        slot = local.replace(hour=14, minute=0, second=0, microsecond=0)
        if local < slot:
            slot -= timedelta(days=1)
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
                return True
        except (Exception, SystemExit):
            self._retry_after = time.time() + 300
            return False

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
                self.send_header('Set-Cookie', 'session=' + cookie + '; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200' + ('; Secure' if app.secure else ''))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            self.wfile.write(encoded)

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

        def valid_csrf(self, session, fields):
            origin = self.headers.get('Origin')
            token = fields.get('csrf', '')
            return bool(session and (origin is None or origin == app.public_url)
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
            allowed = ('/', '/login', '/settings', '/dashboard', '/status', '/ai', '/weekly', '/oauth/callback')
            if path not in allowed or (parsed.query and path != '/oauth/callback'):
                return self.reply(404, 'Not found')
            sid, session = self.identity()
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
            if path == '/oauth/callback':
                q = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
                if any(len(v) != 1 for v in q.values()) or set(q) - {'code', 'state', 'error', 'error_description', 'scope'}:
                    return self.reply(400, '授权回调无效，请重新连接')
                with app.operation():
                    if not app.consume_state(sid, q.get('state', [''])[0]) or not q.get('code', [''])[0] or 'error' in q:
                        return self.reply(400, '授权回调无效或已过期，请重新连接')
                    tok = whoop.post_form(whoop.TOKEN_URL, {'grant_type': 'authorization_code',
                        'code': q['code'][0], 'client_id': whoop.CLIENT_ID,
                        'client_secret': whoop.CLIENT_SECRET, 'redirect_uri': app.redirect_uri})
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
            if path == '/weekly':
                return self.reply(200, page('每周周报', nav + '<pre>' + escape(app.weekly_report()) + '</pre>'))
            if path == '/settings':
                config = app.settings()
                body = nav + '<p>WHOOP 回调地址：<code>' + escape(app.redirect_uri) + '</code></p><p><a href="https://developer-dashboard.whoop.com/" rel="noreferrer">WHOOP 开发者后台</a></p>'
                body += '<p>凭据不回显；留空保留原值。修改 WHOOP 凭据或重新授权将清除旧账号数据。</p><form method="post" action="/settings">' + csrf
                for name, title, placeholder in (('client_id', 'WHOOP Client ID', ''), ('client_secret', 'WHOOP Client Secret', ''), ('deepseek_key', 'DeepSeek API Key（可选）', ''), ('deepseek_model', 'DeepSeek 模型', 'deepseek-flash')):
                    input_type = 'text' if name in ('client_id', 'deepseek_model') else 'password'
                    autocomplete = 'off' if input_type == 'text' else 'new-password'
                    body += '<label>' + title + ('（已设置）' if config.get(name) else '') + '<input type="' + input_type + '" autocomplete="' + autocomplete + '" name="' + name + '" placeholder="' + placeholder + '"></label>'
                body += '<label><input type="checkbox" name="remove_deepseek" value="1">删除已保存的 DeepSeek key</label><p>每天北京时间 14:00 更新，离线错过后后台补跑。</p><button>保存设置</button></form>'
                body += '<form method="post" action="/oauth/start">' + csrf + '<button>连接 WHOOP</button></form>'
                return self.reply(200, page('设置', body))
            if path == '/ai':
                saved = whoop.read_json(app.root / 'ai.json', {})
                answer = saved.get('answer', '') if isinstance(saved, dict) else ''
                body = nav + '<p>仅在主动提交后，将最近最多 30 条记录的聚合统计发给 DeepSeek 官方 API，并按其规则收费。不会发送姓名、邮箱、记录标识或原始记录。分析不替代医疗诊断。</p><form method="post" action="/ai">' + csrf + '<label><input type="checkbox" name="consent" value="1" required>同意发送汇总健康数据并承担 API 费用</label><button>生成 AI 分析</button></form><pre>' + escape(answer) + '</pre>'
                return self.reply(200, page('AI 分析（可选）', body))
            status = app.status()
            body = nav + '<p>最后成功同步：' + escape(status.get('last_success', '尚未同步')) + '</p><p>' + escape(status.get('error', '')) + '</p><p>每天北京时间 14:00 自动更新，后台恢复后补跑。</p><form method="post" action="/sync">' + csrf + '<button>立即同步</button></form><form method="post" action="/logout">' + csrf + '<button>退出登录</button></form>'
            return self.reply(200, page('WHOOP 私人健康网站', body))

        def do_POST(self):
            try:
                self.post()
            except (Exception, SystemExit):
                self.reply(400, '操作失败，请检查设置、授权及网络后重试')

        def post(self):
            path = self.path
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
                launched = app.launch_sync()
                return self.reply(202 if launched else 409, '已提交' if launched else '同步进行中')
            if path not in ('/login', '/settings', '/oauth/start', '/sync', '/ai', '/logout'):
                return self.reply(404, 'Not found')
            sid, session = self.identity()
            if not session or (path != '/login' and not session['auth']):
                return self.reply(403, '请先登录')
            values = self.fields()
            if not self.valid_csrf(session, values):
                return self.reply(403, '请求验证失败，请刷新页面')
            if path == '/login':
                if app.limited(('login', self.client_address[0]), 10, 300):
                    return self.reply(429, '登录尝试过多，请稍后重试')
                if not hmac.compare_digest(hashlib.sha256(values.get('password', '').encode()).digest(), app._password):
                    return self.reply(403, '登录失败')
                with app._guard:
                    app._sessions.pop(sid, None)
                return self.reply(303, location='/', cookie=app.new_session(True))
            if path == '/logout':
                with app._guard:
                    app._sessions.pop(sid, None)
                return self.reply(303, location='/login', cookie='deleted')
            if app.limited(('action', sid), 30, 60):
                return self.reply(429, '操作过于频繁，请稍后重试')
            if path == '/settings':
                app.update_settings(values)
                return self.reply(303, location='/settings')
            if path == '/oauth/start':
                with app.operation():
                    whoop.validate_credentials()
                    state = app.issue_state(sid)
                    url = whoop.AUTH_URL + '?' + urllib.parse.urlencode({'client_id': whoop.CLIENT_ID,
                        'redirect_uri': app.redirect_uri, 'response_type': 'code', 'scope': whoop.SCOPES, 'state': state})
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


def main():
    app = None
    server = None
    try:
        app = App(os.environ.get('DATA_ROOT', str(Path(__file__).resolve().parent / 'runtime')),
                  os.environ.get('PUBLIC_URL', ''), os.environ.get('ADMIN_PASSWORD', ''))
        port = int(os.environ.get('PORT', '8080'))
        if not 1 <= port <= 65535:
            raise ValueError('Invalid port')
        # Service lock prevents duplicate scheduler instances on the same volume.
        with whoop.InstanceLock(app.root / 'server.lock'):
            server = make_server(app, os.environ.get('HOST', '0.0.0.0'), port)
            app.start_scheduler()
            print('WHOOP private service ready', flush=True)
            server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 0
    except (Exception, SystemExit):
        print('WHOOP 服务启动失败，请检查 PUBLIC_URL、密码、端口或已有实例', file=sys.stderr)
        return 1
    finally:
        if app:
            app.close()
        if server:
            server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
