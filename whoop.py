#!/usr/bin/env python3
"""WHOOP API 客户端：OAuth 授权、token 自动刷新、数据查询。"""

import http.server
import io
import json
import os
import secrets
import argparse
import socket
import tempfile
import time
import webbrowser
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE, ".env")
TOKEN_FILE = os.path.join(BASE, ".tokens.json")
DATA_DIR = os.path.join(BASE, "data")

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API = "https://api.prod.whoop.com/developer"
SCOPES = ("read:recovery read:cycles read:sleep read:workout "
          "read:profile read:body_measurement offline")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


class WhoopError(RuntimeError):
    """Safe public error: never contains response bodies or credentials."""


def load_env(path=None):
    env = {}
    try:
        with open(path or ENV_FILE, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line.startswith("export "):
                    line = line[7:].lstrip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip()
                    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                        v = v[1:-1]
                    else:
                        v = v.split(" #", 1)[0].rstrip()
                    env[k.strip()] = v
    except FileNotFoundError:
        pass
    env.update(os.environ)
    return env


ENV = load_env()
CLIENT_ID = ENV.get("WHOOP_CLIENT_ID", "")
CLIENT_SECRET = ENV.get("WHOOP_CLIENT_SECRET", "")
REDIRECT_URI = ENV.get("WHOOP_REDIRECT_URI", "http://localhost:8080/callback")


def validate_credentials():
    for name, value in (("WHOOP_CLIENT_ID", CLIENT_ID),
                        ("WHOOP_CLIENT_SECRET", CLIENT_SECRET)):
        if not isinstance(value, str) or not value.strip() or value.startswith("你的_"):
            raise WhoopError("请配置有效的 " + name)
    u = urllib.parse.urlsplit(REDIRECT_URI)
    if u.scheme not in ("http", "https") or not u.hostname or u.fragment or u.query or u.username:
        raise WhoopError("WHOOP_REDIRECT_URI 必须是无 query/fragment 的完整回调地址")
    try:
        u.port
    except ValueError:
        raise WhoopError("回调端口无效") from None


def atomic_write(path, content):
    """Create private temporary file on same filesystem, fsync, then replace."""
    path = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=parent)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            fd = None
            f.write(content.encode("utf-8") if isinstance(content, str) else content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if fd is not None:
            os.close(fd)
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path, value):
    atomic_write(path, json.dumps(value, indent=2, ensure_ascii=False))


class InstanceLock:
    """Advisory cross-platform process lock, shared by server and CLI.

    Keep the lock file: unlinking a locked inode can allow a second owner.
    Library callers (including cloud_server) must hold this across sync/report
    and token mutations. Nested locks are deliberately not supported.
    """
    def __init__(self, path=None):
        self.path = os.fspath(path or os.path.join(BASE, ".whoop.lock"))
        self.file = None

    def __enter__(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.file = open(self.path, "a+b")
        try:
            os.chmod(self.path, 0o600)
            if os.name == "nt":
                import msvcrt
                self.file.seek(0, 2)
                if not self.file.tell():
                    self.file.write(b"0")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise WhoopError("已有 WHOOP 实例正在运行") from None
        return self

    def __exit__(self, *exc):
        if self.file is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None


# --- token 存取 ---

def save_tokens(tok):
    if not isinstance(tok, dict) or not isinstance(tok.get("access_token"), str) or not tok["access_token"]:
        raise WhoopError("token 响应缺少 access_token")
    result = dict(tok)
    try:
        result["expires_at"] = time.time() + float(tok.get("expires_in", 3600)) - 60
    except (TypeError, ValueError):
        raise WhoopError("token 有效期无效") from None
    write_json(TOKEN_FILE, result)
    return result


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward credentials or tokens, even to another WHOOP URL."""
    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        error = WhoopError("请求失败（已拒绝 HTTP 重定向）")
        error.status = code
        raise error from None

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def _open_no_redirect(req, timeout=30):
    # A private opener avoids changing urllib's process-global behavior used by
    # cloud adapters or other integrations in the same interpreter.
    return urllib.request.build_opener(_RejectRedirects()).open(req, timeout=timeout)


class _DeadlineReader(io.RawIOBase):
    """Socket reader with an absolute deadline, including drip-fed headers.

    socket timeout alone resets on every successful recv, allowing an endless
    slow request. Check the total deadline before AND after each socket read.
    """
    def __init__(self, connection, deadline):
        super().__init__()
        self.connection = connection
        self.deadline = deadline

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("OAuth callback deadline reached")
        self.connection.settimeout(min(1.0, remaining))
        count = self.connection.recv_into(buffer)
        if time.monotonic() >= self.deadline:
            raise TimeoutError("OAuth callback deadline reached")
        return count


def _request_json(req, label, max_retries=3):
    for attempt in range(max_retries + 1):
        try:
            with _open_no_redirect(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            code = e.code
            retry_after = e.headers.get("Retry-After", "") if e.headers else ""
            e.close()
            if code == 429 and attempt < max_retries:
                try:
                    delay = float(retry_after)
                except (ValueError, TypeError):
                    delay = 2 ** attempt
                # Never sleep indefinitely on an untrusted Retry-After header.
                time.sleep(max(0, min(30, delay)))
                continue
            err = WhoopError(f"{label}失败（HTTP {code}）")
            err.status = code
            raise err from None
        except (OSError, ValueError):
            raise WhoopError(label + "失败（网络或 JSON 响应异常）") from None


def post_form(url, data):
    validate_credentials()
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA})
    return _request_json(req, "token 请求")


def refresh(tok):
    validate_credentials()
    if not tok.get("refresh_token"):
        raise WhoopError("缺少 refresh_token，请重新授权")
    new = post_form(TOKEN_URL, {
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "scope": "offline",
    })
    if not isinstance(new, dict):
        raise WhoopError("token 响应格式无效")
    if not new.get("refresh_token"):
        new = dict(new, refresh_token=tok["refresh_token"])
    return save_tokens(new)


def access_token():
    validate_credentials()
    tok = read_json(TOKEN_FILE, {})
    if not isinstance(tok, dict) or not tok.get("access_token"):
        raise WhoopError("还没授权。先运行：python3 whoop.py auth")
    if time.time() >= tok.get("expires_at", 0):
        tok = refresh(tok)
    return tok["access_token"]


# --- OAuth 授权 ---

def callback_code(url, state, redirect_uri=None):
    """Validate full redirect URL; no callback values appear in error messages."""
    expected = urllib.parse.urlsplit(redirect_uri or REDIRECT_URI)
    actual = urllib.parse.urlsplit(url)
    def origin(u):
        return u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80)
    try:
        valid = origin(actual) == origin(expected)
    except ValueError:
        valid = False
    if not valid or actual.path != expected.path or actual.fragment or actual.username:
        raise WhoopError("回调地址不匹配")
    q = urllib.parse.parse_qs(actual.query, keep_blank_values=True)
    states = q.get("state", [])
    if len(states) != 1 or not secrets.compare_digest(states[0].encode("utf-8"), state.encode("utf-8")):
        raise WhoopError("回调 state 不匹配")
    if "error" in q:
        raise WhoopError("WHOOP 授权被拒绝，请重新授权")
    codes = q.get("code", [])
    if len(codes) != 1 or not codes[0]:
        raise WhoopError("回调缺少唯一授权码")
    return codes[0]


def cmd_auth(manual=False):
    validate_credentials()
    if manual not in (False, True, "--manual"):
        raise WhoopError("auth 仅支持 --manual")
    state = secrets.token_urlsafe(32)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
        "response_type": "code", "scope": SCOPES, "state": state,
    })
    def open_browser():
        print("请在浏览器中授权：\n" + url)
        if os.environ.get("WHOOP_NO_OPEN") != "1":
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass
    if manual:
        open_browser()
        try:
            pasted = input("请粘贴完整回调 URL：").strip()
        except (EOFError, KeyboardInterrupt):
            raise WhoopError("手动授权已取消") from None
        code = callback_code(pasted, state)
    else:
        target = urllib.parse.urlsplit(REDIRECT_URI)
        if target.scheme != "http" or target.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise WhoopError("本地授权需要 HTTP loopback 回调；云端请用 auth --manual")
        result = {}
        class Handler(http.server.BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.rfile.close()
                self.rfile = io.BufferedReader(_DeadlineReader(self.connection, deadline))

            def do_GET(self):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self.connection.settimeout(min(1.0, remaining))
                try:
                    # Host is intentionally not trusted; origin comes from configured redirect.
                    if urllib.parse.urlsplit(self.path).scheme or not self.path.startswith("/"):
                        raise WhoopError("回调路径无效")
                    parsed = urllib.parse.urlsplit(self.path)
                    if parsed.path != target.path:
                        raise WhoopError("回调路径不匹配")
                    code = callback_code(REDIRECT_URI + ("?" + parsed.query if parsed.query else ""), state)
                except WhoopError:
                    self.send_response(400)
                    body = b"Invalid OAuth callback. Please retry."
                else:
                    result["code"] = code
                    self.send_response(200)
                    body = b"Authorization received. You may close this window."
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *args):
                pass
        class CallbackServer(http.server.HTTPServer):
            def get_request(self):
                connection, addr = super().get_request()
                connection.settimeout(1)
                return connection, addr
            def handle_error(self, *args):
                pass
        if target.hostname == "::1":
            class IPv6CallbackServer(CallbackServer):
                address_family = socket.AF_INET6
            server_type = IPv6CallbackServer
        else:
            server_type = CallbackServer
        srv = server_type(("::1" if target.hostname == "::1" else "127.0.0.1", target.port or 80), Handler)
        try:
            srv.timeout = 1
            deadline = time.monotonic() + 900
            open_browser()
            while not result and time.monotonic() < deadline:
                srv.timeout = min(1.0, max(0, deadline - time.monotonic()))
                srv.handle_request()
            if not result:
                raise WhoopError("授权超时（900 秒）")
            code = result["code"]
        finally:
            srv.server_close()
    tok = post_form(TOKEN_URL, {
        "grant_type": "authorization_code", "code": code,
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "redirect_uri": REDIRECT_URI,
    })
    save_tokens(tok)
    print("授权完成，token 已安全保存")


# --- API 调用 ---

def get(path, **params):
    validate_credentials()
    if not path.startswith("/v2/") or "?" in path or "#" in path:
        raise WhoopError("API 路径无效")
    q = {k: v for k, v in params.items() if v is not None}
    url = API + path + ("?" + urllib.parse.urlencode(q) if q else "")
    token = access_token()
    for attempt in range(2):
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "User-Agent": UA})
        try:
            return _request_json(req, "API 请求")
        except WhoopError as e:
            if getattr(e, "status", None) != 401 or attempt:
                raise
            token = refresh(read_json(TOKEN_FILE, {}))["access_token"]


def get_all(path, start=None, end=None, limit=25, max_pages=200):
    """All records or a clear error; never silently return truncated history."""
    if max_pages < 1:
        raise WhoopError("分页上限必须大于零")
    out, token, seen = [], None, set()
    for _ in range(max_pages):
        d = get(path, start=start, end=end, limit=limit, nextToken=token)
        if not isinstance(d, dict) or not isinstance(d.get("records"), list):
            raise WhoopError("分页响应格式无效")
        out.extend(d["records"])
        token = d.get("next_token")
        if not token:
            return out
        if not isinstance(token, str) or token in seen:
            raise WhoopError("分页游标重复或无效")
        seen.add(token)
    raise WhoopError("已达分页上限，数据尚未完整；请缩小时间范围或提高 max_pages")


# --- 展示 ---

def fmt_dur(ms):
    m = round(ms / 60000)
    return f"{m // 60}h{m % 60:02d}m"


def cmd_today():
    cycles = get("/v2/cycle", limit=1).get("records", [])
    if not cycles:
        sys.exit("没有 cycle 数据")
    c = cycles[0]
    cid = c["id"]
    started = datetime.fromisoformat(c["start"].replace("Z", "+00:00")).astimezone()

    print(f"== WHOOP {started:%Y-%m-%d} ==")
    s = c.get("score") or {}
    if s:
        print(f"Strain      {s.get('strain', 0):.1f}   卡路里 {round(s.get('kilojoule', 0) / 4.184)} kcal")
        print(f"平均心率     {s.get('average_heart_rate', '-')} bpm   最高 {s.get('max_heart_rate', '-')} bpm")
    else:
        print(f"Strain      计算中（{c.get('score_state')}）")

    try:
        r = get(f"/v2/cycle/{cid}/recovery")
        rs = r.get("score") or {}
        if rs:
            print(f"恢复分       {rs.get('recovery_score', '-')}%   HRV {rs.get('hrv_rmssd_milli', 0):.0f}ms   "
                  f"静息心率 {rs.get('resting_heart_rate', '-')} bpm")
            if rs.get("spo2_percentage"):
                print(f"血氧         {rs['spo2_percentage']:.1f}%   体温 {rs.get('skin_temp_celsius', 0):.1f}°C")
    except WhoopError:
        print("恢复分       暂无")

    try:
        sl = get(f"/v2/cycle/{cid}/sleep")
        ss = sl.get("score") or {}
        st = ss.get("stage_summary") or {}
        if st:
            total = (st.get("total_light_sleep_time_milli", 0) + st.get("total_slow_wave_sleep_time_milli", 0)
                     + st.get("total_rem_sleep_time_milli", 0))
            print(f"睡眠         {fmt_dur(total)}   效率 {ss.get('sleep_efficiency_percentage', 0):.0f}%   "
                  f"表现 {ss.get('sleep_performance_percentage', '-')}%")
            print(f"  深睡 {fmt_dur(st.get('total_slow_wave_sleep_time_milli', 0))}  "
                  f"REM {fmt_dur(st.get('total_rem_sleep_time_milli', 0))}  "
                  f"浅睡 {fmt_dur(st.get('total_light_sleep_time_milli', 0))}  "
                  f"醒 {fmt_dur(st.get('total_awake_time_milli', 0))}")
    except WhoopError:
        print("睡眠         暂无")


def merge_by_id(old, new, key="id"):
    """新记录覆盖同键旧记录，按时间排序。历史只增不减。"""
    idx = {r[key]: r for r in old if key in r}
    idx.update({r[key]: r for r in new if key in r})
    return sorted(idx.values(), key=lambda r: r.get("start") or r.get("created_at") or "")


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def cmd_sync(days=None):
    """抓取数据并入历史库。默认全量，可传天数只拉最近 N 天。"""
    validate_credentials()
    start = None
    if days:
        start = (datetime.now(timezone.utc) - timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    os.makedirs(DATA_DIR, exist_ok=True)
    # recovery 没有 id 字段，主键是 cycle_id
    sets = (
        ("cycles", "/v2/cycle", "id"),
        ("recovery", "/v2/recovery", "cycle_id"),
        ("sleep", "/v2/activity/sleep", "id"),
        ("workouts", "/v2/activity/workout", "id"),
    )
    for name, path, key in sets:
        f_path = os.path.join(DATA_DIR, name + ".json")
        old = read_json(f_path, [])
        rec = merge_by_id(old, get_all(path, start=start), key)
        write_json(f_path, rec)
        print(f"{name:10s} {len(rec)} 条（新增 {len(rec) - len(old)}）")

    for name, path in (("profile", "/v2/user/profile/basic"), ("body", "/v2/user/measurement/body")):
        write_json(os.path.join(DATA_DIR, name + ".json"), get(path))
    print("已存到 " + DATA_DIR)


def cmd_raw(path, *args):
    params = dict(a.split("=", 1) for a in args if "=" in a)
    print(json.dumps(get(path, **params), indent=2, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("auth")
    auth.add_argument("--manual", action="store_true")
    sub.add_parser("today")
    sync = sub.add_parser("sync")
    sync.add_argument("days", nargs="?", type=int)
    raw = sub.add_parser("raw")
    raw.add_argument("path")
    raw.add_argument("params", nargs="*")
    args = parser.parse_args(argv)
    try:
        with InstanceLock():
            if args.command == "auth":
                cmd_auth(args.manual)
            elif args.command == "sync":
                if args.days is not None and args.days < 1:
                    raise WhoopError("同步天数必须大于零")
                cmd_sync(args.days)
            elif args.command == "today":
                cmd_today()
            else:
                cmd_raw(args.path, *args.params)
    except (WhoopError, OSError, ValueError):
        # Exceptions from third-party/network objects can contain secrets.
        print("WHOOP 操作失败，请检查配置、授权和网络后重试", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
