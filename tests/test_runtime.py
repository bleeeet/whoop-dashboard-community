"""Offline runtime regression tests; no WHOOP requests or persistent services."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import socket
import sys
import tempfile
import threading
import http.client
import email.message
import time
import urllib.response
import urllib.error
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class ImportTests(unittest.TestCase):
    def test_import_without_env(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('WHOOP_')}
        run = subprocess.run([sys.executable, '-B', '-c', 'import whoop'], cwd=ROOT,
                             env=env, capture_output=True)
        self.assertEqual(run.returncode, 0, 'import must not require credentials')


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.w = importlib.import_module('whoop')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token_patch = mock.patch.object(self.w, 'TOKEN_FILE', str(self.root / 'tokens.json'))
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)

    def test_env_priority(self):
        f = self.root / '.env'
        f.write_text('WHOOP_CLIENT_ID=file\nWHOOP_CLIENT_SECRET="quoted"\n')
        with mock.patch.dict(os.environ, {'WHOOP_CLIENT_ID': 'environment'}, clear=True):
            config = self.w.load_env(f)
        self.assertEqual(config['WHOOP_CLIENT_ID'], 'environment')
        self.assertEqual(config['WHOOP_CLIENT_SECRET'], 'quoted')

    def test_refresh_preserves_refresh_token(self):
        with mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(
                self.w, 'post_form', return_value={'access_token': 'new', 'expires_in': 100}):
            result = self.w.refresh({'refresh_token': 'keep'})
        self.assertEqual(result['refresh_token'], 'keep')
        self.assertEqual(json.loads(Path(self.w.TOKEN_FILE).read_text())['access_token'], 'new')
        if os.name != 'nt':
            self.assertEqual(Path(self.w.TOKEN_FILE).stat().st_mode & 0o777, 0o600)

    def test_pagination_does_not_return_partial_success(self):
        with mock.patch.object(self.w, 'get', return_value={'records': [{'id': 1}], 'next_token': 'more'}):
            with self.assertRaises(self.w.WhoopError):
                self.w.get_all('/v2/cycle', max_pages=1)

    def test_atomic_failure_preserves_old_file_and_cleans_temp(self):
        target = self.root / 'state.json'
        target.write_text('{"old": true}')
        with mock.patch.object(self.w.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.w.write_json(target, {'new': True})
        self.assertEqual(json.loads(target.read_text()), {'old': True})
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_token_not_mutated_and_private_before_replace(self):
        original = {'access_token': 'safe', 'expires_in': 600}
        replace = self.w.os.replace
        def check(src, dst):
            if os.name != 'nt':
                self.assertEqual(Path(src).stat().st_mode & 0o777, 0o600)
            replace(src, dst)
        with mock.patch.object(self.w.os, 'replace', side_effect=check):
            self.w.save_tokens(original)
        self.assertNotIn('expires_at', original)
        self.assertFalse(any(p.name.startswith('.tokens.json.') for p in self.root.iterdir()))

    def test_credentials_validated_before_network(self):
        with mock.patch.object(self.w, 'CLIENT_ID', ''), mock.patch.object(self.w, '_open_no_redirect') as net:
            for call in (lambda: self.w.get('/v2/cycle'),
                         lambda: self.w.post_form(self.w.TOKEN_URL, {}),
                         lambda: self.w.cmd_auth(True)):
                with self.assertRaises(self.w.WhoopError):
                    call()
            net.assert_not_called()

    def test_401_refreshes_only_once(self):
        def unauthorized():
            return urllib.error.HTTPError('https://example.invalid', 401, 'secret', {}, io.BytesIO(b'secret body'))
        with mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(self.w, 'access_token', return_value='old'), mock.patch.object(self.w, 'refresh', return_value={'access_token': 'new'}) as refresh, mock.patch.object(self.w, '_open_no_redirect', side_effect=[unauthorized(), unauthorized()]) as net:
            with self.assertRaises(self.w.WhoopError) as error:
                self.w.get('/v2/cycle')
            self.assertNotIn('secret', str(error.exception))
            self.assertEqual(net.call_count, 2)
            refresh.assert_called_once()

    def test_429_retries_are_bounded_and_redacted(self):
        def limited(*args, **kwargs):
            raise urllib.error.HTTPError('https://example.invalid', 429, 'private', {'Retry-After': '999999'}, io.BytesIO(b'private body'))
        with mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(self.w, '_open_no_redirect', side_effect=limited) as net, mock.patch.object(self.w.time, 'sleep') as sleep:
            with self.assertRaises(self.w.WhoopError) as error:
                self.w.post_form(self.w.TOKEN_URL, {})
            self.assertNotIn('private', str(error.exception))
            self.assertEqual(net.call_count, 4)
            self.assertEqual(sleep.call_count, 3)
            self.assertTrue(all(0 <= c.args[0] <= 30 for c in sleep.call_args_list))

    def test_token_and_api_reject_all_redirects_without_second_request(self):
        for kind in ('token', 'api'):
            for status in (301, 302, 303, 307, 308):
                with self.subTest(kind=kind, status=status):
                    requests = []
                    def transport(handler, request):
                        requests.append(request)
                        headers = email.message.Message()
                        if len(requests) == 1:
                            headers['Location'] = 'http://different.invalid/SECRET_LOCATION'
                            code, body = status, b'SECRET_BODY'
                        else:
                            code, body = 200, b'{}'
                        response = urllib.response.addinfourl(io.BytesIO(body), headers, request.full_url, code)
                        response.msg = 'synthetic response'
                        return response
                    with mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(self.w, 'access_token', return_value='SECRET_BEARER'), mock.patch.object(urllib.request.HTTPSHandler, 'https_open', transport), mock.patch.object(urllib.request.HTTPHandler, 'http_open', transport):
                        with self.assertRaises(self.w.WhoopError) as error:
                            if kind == 'token':
                                self.w.post_form(self.w.TOKEN_URL, {'client_secret': 'SECRET_FORM'})
                            else:
                                self.w.get('/v2/cycle')
                    self.assertEqual(len(requests), 1)
                    self.assertNotIn('SECRET', str(error.exception))

    def test_auth_hard_deadline_interrupts_dripping_request_headers(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        ready, stop = threading.Event(), threading.Event()
        errors = []
        real_monotonic = time.monotonic
        started = real_monotonic()
        # Compress the same 900-second production deadline into 0.6 seconds.
        def clock():
            return started + (real_monotonic() - started) * 1500
        def authorize():
            try:
                self.w.cmd_auth()
            except self.w.WhoopError as exc:
                errors.append(str(exc))
        connection = None
        writer = None
        with mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(self.w, 'REDIRECT_URI', f'http://127.0.0.1:{port}/callback'), mock.patch.object(self.w.webbrowser, 'open', side_effect=lambda url: ready.set()), mock.patch.object(self.w, 'post_form') as exchange, mock.patch.dict(os.environ, {'WHOOP_NO_OPEN': '0'}), mock.patch.object(self.w.time, 'monotonic', side_effect=clock), contextlib.redirect_stdout(io.StringIO()):
            thread = threading.Thread(target=authorize, daemon=True)
            thread.start()
            try:
                self.assertTrue(ready.wait(2))
                connection = socket.create_connection(('127.0.0.1', port), timeout=2)
                connection.sendall(b'GET /callback HTTP/1.1\r\nX-Drip: ')
                def drip():
                    while not stop.wait(0.02):
                        try:
                            connection.sendall(b'x')
                        except OSError:
                            return
                writer = threading.Thread(target=drip)
                writer.start()
                thread.join(1.5)
                self.assertFalse(thread.is_alive(), 'dripping headers bypassed the absolute OAuth deadline')
            finally:
                stop.set()
                if writer:
                    writer.join(2)
                if connection:
                    connection.close()
                thread.join(2)
            exchange.assert_not_called()
        self.assertEqual(errors, ['授权超时（900 秒）'])
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)

    def test_successful_pagination_and_repeated_cursor(self):
        with mock.patch.object(self.w, 'get', side_effect=[{'records': [1], 'next_token': 'a'}, {'records': [2]}]):
            self.assertEqual(self.w.get_all('/v2/cycle'), [1, 2])
        with mock.patch.object(self.w, 'get', return_value={'records': [], 'next_token': 'loop'}):
            with self.assertRaises(self.w.WhoopError):
                self.w.get_all('/v2/cycle')

    def test_callback_validation(self):
        uri = 'http://localhost:8080/callback'
        self.assertEqual(self.w.callback_code(uri + '?code=ok&state=valid', 'valid', uri), 'ok')
        for url in (uri + '?code=secret&state=wrong',
                    'http://localhost:8080/other?code=secret&state=valid',
                    'https://evil.invalid/callback?code=secret&state=valid',
                    uri + '?code=secret&state=valid&state=valid',
                    uri + '?error=secret&state=valid'):
            with self.assertRaises(self.w.WhoopError) as error:
                self.w.callback_code(url, 'valid', uri)
            self.assertNotIn('secret', str(error.exception))

    def test_manual_auth_no_listener(self):
        uri = 'https://cloud.example/callback'
        with mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(self.w, 'REDIRECT_URI', uri), mock.patch.object(self.w.secrets, 'token_urlsafe', return_value='state'), mock.patch('builtins.input', return_value=uri + '?state=state&code=code'), mock.patch.object(self.w.webbrowser, 'open'), mock.patch.object(self.w.http.server, 'HTTPServer') as listener, mock.patch.object(self.w, 'post_form', return_value={'access_token': 'token'}), contextlib.redirect_stdout(io.StringIO()):
            self.w.cmd_auth(manual=True)
            listener.assert_not_called()
        self.assertEqual(json.loads(Path(self.w.TOKEN_FILE).read_text())['access_token'], 'token')

    def test_lock_rejects_other_process_and_releases(self):
        path = str(self.root / '.lock')
        code = 'import whoop,sys; lock=whoop.InstanceLock(sys.argv[1]); lock.__enter__(); lock.__exit__()'
        with self.w.InstanceLock(path):
            child = subprocess.run([sys.executable, '-B', '-c', code, path], cwd=ROOT, capture_output=True)
            self.assertNotEqual(child.returncode, 0)
        child = subprocess.run([sys.executable, '-B', '-c', code, path], cwd=ROOT, capture_output=True)
        self.assertEqual(child.returncode, 0)

    def test_local_auth_ignores_bad_callbacks_and_closes_socket(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        uri = f'http://127.0.0.1:{port}/callback'
        ready = threading.Event()
        errors = []
        def authorize():
            try:
                self.w.cmd_auth()
            except BaseException as exc:
                errors.append(type(exc).__name__)
        with mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(self.w, 'REDIRECT_URI', uri), mock.patch.object(self.w.secrets, 'token_urlsafe', return_value='valid'), mock.patch.object(self.w.webbrowser, 'open', side_effect=lambda url: ready.set()), mock.patch.object(self.w, 'post_form', return_value={'access_token': 'token'}), mock.patch.dict(os.environ, {'WHOOP_NO_OPEN': '0'}), contextlib.redirect_stdout(io.StringIO()):
            thread = threading.Thread(target=authorize, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(3))
            for path, expected in (('/wrong?state=valid&code=secret', 400),
                                   ('/callback?state=wrong&code=secret', 400),
                                   ('/callback?state=valid&code=ok', 200)):
                conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                try:
                    conn.request('GET', path)
                    response = conn.getresponse()
                    self.assertEqual(response.status, expected)
                    self.assertNotIn(b'secret', response.read())
                finally:
                    conn.close()
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(Path(self.w.TOKEN_FILE).exists())
        with socket.socket() as probe:
            self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)

    def test_auth_timeout_closes_listener(self):
        with mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(self.w, 'REDIRECT_URI', 'http://localhost:8080/callback'), mock.patch.object(self.w.http.server.HTTPServer, '__init__', return_value=None), mock.patch.object(self.w.http.server.HTTPServer, 'server_close') as close, mock.patch.object(self.w.http.server.HTTPServer, 'handle_request') as handle, mock.patch.object(self.w.webbrowser, 'open'), mock.patch.object(self.w.time, 'monotonic', side_effect=[0, 901]), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(self.w.WhoopError, '900'):
                self.w.cmd_auth()
        close.assert_called_once()
        handle.assert_not_called()

    def test_sync_json_is_atomic_and_private(self):
        data = self.root / 'data'
        with mock.patch.object(self.w, 'DATA_DIR', str(data)), mock.patch.object(self.w, 'validate_credentials'), mock.patch.object(self.w, 'get_all', return_value=[{'id': 1, 'cycle_id': 1}]), mock.patch.object(self.w, 'get', return_value={}), contextlib.redirect_stdout(io.StringIO()):
            self.w.cmd_sync(days=7)
        self.assertEqual(len(list(data.glob('*.json'))), 6)
        for path in data.glob('*.json'):
            json.loads(path.read_text())
            if os.name != 'nt':
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.s = importlib.import_module('server')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.s.Runtime(self.root)
        self.runtime.data.mkdir()
        for name in self.s.NAMES:
            (self.runtime.data / (name + '.json')).write_text('{}' if name in ('body', 'profile') else '[]')

    def test_full_then_seven_days_and_restart(self):
        with mock.patch.object(self.s.whoop, 'cmd_sync') as sync:
            self.assertTrue(self.runtime.update())
            self.assertTrue(self.runtime.update())
            restarted = self.s.Runtime(self.root)
            self.assertTrue(restarted.update())
        self.assertEqual(sync.call_args_list, [mock.call(days=None), mock.call(days=7), mock.call(days=7)])
        self.assertIn(b'html', self.runtime.page.read_bytes())

    def test_failures_keep_last_page_and_retry(self):
        self.runtime.page.parent.mkdir()
        self.runtime.page.write_bytes(b'previous page')
        with mock.patch.object(self.s.whoop, 'cmd_sync', side_effect=RuntimeError('SECRET')) as sync, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertFalse(self.runtime.update())
            self.assertFalse(self.runtime.update())
        self.assertNotIn('SECRET', err.getvalue())
        self.assertEqual(sync.call_args_list, [mock.call(days=None), mock.call(days=None)])
        self.assertEqual(self.runtime.page.read_bytes(), b'previous page')
        self.assertEqual(self.runtime.status()['status'], 'error')
        with mock.patch.object(self.s.whoop, 'cmd_sync'):
            self.assertTrue(self.runtime.update())
        self.assertEqual(self.runtime.status()['status'], 'ready')

    def test_render_failure_keeps_page(self):
        self.runtime.page.parent.mkdir()
        self.runtime.page.write_bytes(b'previous page')
        with mock.patch.object(self.s.whoop, 'cmd_sync'), mock.patch.object(self.s.report, 'render', side_effect=ValueError('SECRET')), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.runtime.update())
        self.assertEqual(self.runtime.page.read_bytes(), b'previous page')
        self.assertFalse(self.runtime.full_synced)

    def test_demo_reads_only_multi(self):
        import shutil
        shutil.copytree(ROOT / 'fixtures' / 'multi', self.root / 'fixtures' / 'multi')
        runtime = self.s.Runtime(self.root, demo=True)
        with mock.patch.object(self.s.whoop, 'cmd_sync', side_effect=AssertionError('must stay offline')), mock.patch.object(self.s.whoop.urllib.request, 'urlopen', side_effect=AssertionError('network forbidden')):
            self.assertTrue(runtime.update())
        self.assertTrue(runtime.page.read_bytes().lower().startswith(b'<!doctype html>'))
        self.assertFalse(runtime.state_file.exists())

    def test_missing_demo_fixtures_fails_without_live_fallback(self):
        with mock.patch.object(self.s.whoop, 'cmd_sync') as sync, contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.s.Runtime(self.root, demo=True).update())
        sync.assert_not_called()

    def test_http_allowlist_and_redacted_status(self):
        self.runtime.page.parent.mkdir()
        self.runtime.page.write_bytes(b'<html>dashboard</html>')
        (self.root / '.env').write_text('SECRET')
        server = self.s.DashboardServer(('127.0.0.1', 0), self.s.make_handler(self.runtime))
        self.addCleanup(server.server_close)
        def request(path, method='GET'):
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                conn.request(method, path)
                response = conn.getresponse()
                result = response.status, response.read()
            finally:
                conn.close()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            return result
        self.assertEqual(request('/'), (200, b'<html>dashboard</html>'))
        for path in ('/.env', '/.tokens.json', '/data/profile.json', '/publish/index.html', '/../.env', '/%2eenv', '/%2e%2e/.env', '/status?token=SECRET', '//.env', '/fixtures/multi/profile.json'):
            code, body = request(path)
            self.assertEqual(code, 404, path)
            self.assertNotIn(b'SECRET', body)
        code, body = request('/status')
        self.assertEqual(code, 200)
        status = json.loads(body)
        self.assertEqual(set(status), {'mode', 'syncing', 'last_attempt', 'last_success', 'page_available', 'status'})
        self.assertNotIn(str(self.root).encode(), body)
        self.assertEqual(request('/', 'HEAD'), (200, b''))
        self.assertEqual(request('/status', 'POST')[0], 501)

    def test_cli_options(self):
        with mock.patch.dict(os.environ, {'PORT': '9000'}):
            args = self.s.parse_args(['--host', '0.0.0.0', '--demo', '--once'])
        self.assertEqual((args.host, args.port, args.interval, args.once, args.demo), ('0.0.0.0', 9000, 3600, True, True))
        for invalid in ('0', '-1', 'nan', 'inf'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.s.parse_args(['--interval', invalid])

    def test_once_never_starts_listener(self):
        with mock.patch.object(self.s, 'Runtime', return_value=self.runtime), mock.patch.object(self.s.whoop, 'InstanceLock', return_value=contextlib.nullcontext()), mock.patch.object(self.runtime, 'update', return_value=True), mock.patch.object(self.s, 'DashboardServer') as listener:
            self.assertEqual(self.s.main(['--demo', '--once']), 0)
            listener.assert_not_called()

    def test_scheduler_updates_immediately_then_at_interval(self):
        srv = mock.MagicMock()
        srv.handle_request.side_effect = [None, KeyboardInterrupt]
        with mock.patch.object(self.s, 'Runtime', return_value=self.runtime), mock.patch.object(self.s.whoop, 'InstanceLock', return_value=contextlib.nullcontext()), mock.patch.object(self.runtime, 'update', return_value=False) as update, mock.patch.object(self.s, 'DashboardServer', return_value=srv), mock.patch.object(self.s.time, 'monotonic', side_effect=[0, 3600, 3600]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.s.main([]), 0)
        self.assertEqual(update.call_count, 2)
        srv.server_close.assert_called_once()

    def test_failed_page_replace_keeps_previous_publication(self):
        self.runtime.page.parent.mkdir()
        self.runtime.page.write_bytes(b'old')
        with mock.patch.object(self.s.whoop, 'cmd_sync'), mock.patch.object(self.s.whoop.os, 'replace', side_effect=OSError('disk')), contextlib.redirect_stderr(io.StringIO()):
            self.assertFalse(self.runtime.update())
        self.assertEqual(self.runtime.page.read_bytes(), b'old')
        self.assertFalse(self.runtime.full_synced)


if __name__ == '__main__':
    unittest.main()
