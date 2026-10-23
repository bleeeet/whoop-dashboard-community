#!/usr/bin/env python3
"""Private local WHOOP dashboard runtime (standard library only).

--interval is in seconds (default 3600). --once performs sync/report then exits.
The cloud adapter can reuse whoop.InstanceLock, cmd_sync and report functions;
it must serialize ALL token writes and sync/report with the same lock file.
"""
import argparse
import http.server
import json
import math
import os
from pathlib import Path
import socket
import sys
import threading
import time
from datetime import datetime, timezone

import report
import whoop

BASE = Path(__file__).resolve().parent
NAMES = ('cycles', 'recovery', 'sleep', 'workouts', 'profile', 'body')


def now():
    return datetime.now(timezone.utc).isoformat()


class Runtime:
    def __init__(self, base=BASE, demo=False):
        self.base = Path(base)
        self.demo = demo
        self.data = self.base / 'data'
        self.page = self.base / 'publish' / 'index.html'
        self.state_file = self.data / 'runtime.json'
        stored = whoop.read_json(self.state_file, {}) if not demo else {}
        if not isinstance(stored, dict):
            stored = {}
        self.full_synced = stored.get('full_synced') is True
        self.last_success = stored.get('last_success') if isinstance(stored.get('last_success'), str) else None
        self.last_attempt = None
        self.failed = False
        self.busy = False
        self._mutex = threading.Lock()
        self._state_mutex = threading.Lock()

    def status(self):
        # An explicit allowlist: never return paths, credentials, raw exceptions,
        # token details, names, health records or configuration values.
        with self._state_mutex:
            return {'mode': 'demo' if self.demo else 'live',
                    'syncing': self.busy, 'last_attempt': self.last_attempt,
                    'last_success': self.last_success,
                    'page_available': self.page.is_file(),
                    'status': 'error' if self.failed else ('ready' if self.last_success else 'pending')}

    def update(self):
        """Caller holds InstanceLock for entire runtime lifetime.

        Failures never replace the published page. Full sync is marked only
        after sync AND rendering succeed, so an interrupted first run retries
        the full history. Disk state supports the same behavior after restart.
        """
        with self._mutex:
            with self._state_mutex:
                self.busy = True
                self.last_attempt = now()
            try:
                if self.demo:
                    source = self.base / 'fixtures' / 'multi'
                else:
                    source = self.data
                    # cmd_sync uses the established public module configuration.
                    whoop.cmd_sync(days=7 if self.full_synced else None)
                # report.load is forgiving; runtime must reject incomplete input.
                for name in NAMES:
                    path = source / (name + '.json')
                    with path.open(encoding='utf-8') as f:
                        value = json.load(f)
                    if not isinstance(value, dict if name in ('profile', 'body') else list):
                        raise ValueError('Invalid dataset')
                raw = report.load(str(source))
                page = report.render(report.build_days(raw), raw)
                # Generate everything before touching the existing publication.
                whoop.atomic_write(self.page, page)
                succeeded = now()
                if not self.demo:
                    try:
                        whoop.write_json(self.state_file, {'full_synced': True, 'last_success': succeeded})
                    except OSError:
                        # Publication already succeeded. Missing checkpoint merely
                        # causes a safe full resync at the next process start.
                        print('WHOOP 页面已更新，但同步检查点未保存', file=sys.stderr)
                with self._state_mutex:
                    self.full_synced = True
                    self.last_success = succeeded
                    self.failed = False
                return True
            except (Exception, SystemExit):
                # Never log the exception: urllib / adapters may include secrets.
                with self._state_mutex:
                    self.failed = True
                print('WHOOP 更新失败；保留上次页面，下一周期重试', file=sys.stderr)
                return False
            finally:
                with self._state_mutex:
                    self.busy = False


def make_handler(runtime):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self._serve(False)

        def do_HEAD(self):
            self._serve(True)

        def _serve(self, head):
            # Exact request-target allowlist: no decode, filesystem joining,
            # redirects, directory listing, API mutation or static asset routes.
            if self.path == '/status':
                body = json.dumps(runtime.status(), ensure_ascii=False).encode('utf-8')
                code, kind = 200, 'application/json; charset=utf-8'
            elif self.path == '/':
                try:
                    body = runtime.page.read_bytes()
                    code, kind = 200, 'text/html; charset=utf-8'
                except OSError:
                    body = '仪表盘尚未生成，请稍后重试。'.encode('utf-8')
                    code, kind = 503, 'text/plain; charset=utf-8'
            else:
                body, code, kind = b'Not found', 404, 'text/plain; charset=utf-8'
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


class DashboardServer(http.server.HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(2)
        return connection, address

    def handle_error(self, *args):
        pass


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=os.environ.get('PORT', '8788'))
    p.add_argument('--interval', type=float, default=3600, help='同步间隔秒数（默认 3600）')
    p.add_argument('--once', action='store_true')
    p.add_argument('--demo', action='store_true', help='仅读 fixtures/multi，不调用 WHOOP')
    args = p.parse_args(argv)
    if not 0 <= args.port <= 65535:
        p.error('port 必须在 0 到 65535 之间')
    if not math.isfinite(args.interval) or args.interval <= 0:
        p.error('interval 必须是大于零的有限秒数')
    return args


def main(argv=None):
    args = parse_args(argv)
    runtime = Runtime(demo=args.demo)
    srv = None
    try:
        with whoop.InstanceLock():
            if args.once:
                return 0 if runtime.update() else 1
            cls = DashboardServer
            if ':' in args.host:
                class IPv6Server(DashboardServer):
                    address_family = socket.AF_INET6
                cls = IPv6Server
            srv = cls((args.host, args.port), make_handler(runtime))
            srv.timeout = min(args.interval, 1)
            runtime.update()  # Start immediately, including on an empty installation.
            due = time.monotonic() + args.interval
            print(f'WHOOP 仪表盘监听 {args.host}:{srv.server_port}', flush=True)
            while True:
                srv.handle_request()
                if time.monotonic() >= due:
                    runtime.update()  # serial with report; errors retry next interval
                    due = time.monotonic() + args.interval
    except KeyboardInterrupt:
        return 0
    except (whoop.WhoopError, OSError, ValueError):
        print('WHOOP 服务启动失败，请检查配置或是否已有实例', file=sys.stderr)
        return 1
    finally:
        if srv is not None:
            srv.server_close()


if __name__ == '__main__':
    sys.exit(main())
