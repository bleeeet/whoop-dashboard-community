"""Cloud deployment behaviour: first-run setup, restart-safe sessions, derived origin,
CloudBase persistence (against a local fake of its PostgreSQL REST gateway)."""
import http.client
import http.server
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location('cloud_server', ROOT / 'cloud_server.py')
cloud = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cloud)
statestore = cloud.statestore


class FakeCloudBase:
    """Implements the subset of /v1/rdb/rest/:table that store.py uses."""

    def __init__(self):
        self.rows, self.requests, self.fail = {}, [], False
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self, code, body=b''):
                self.send_response(code)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def check(self):
                fake.requests.append((self.command, self.path, dict(self.headers)))
                if fake.fail:
                    self.answer(500, b'{"message":"boom"}')
                    return False
                if self.headers.get('Authorization') != 'Bearer test-key' or not self.path.startswith('/v1/rdb/rest/whoop_state'):
                    self.answer(401)
                    return False
                return True

            def do_GET(self):
                if self.check():
                    rows = [{'name': k, 'body': v} for k, v in fake.rows.items()]
                    self.answer(200, json.dumps(rows).encode())

            def do_POST(self):
                if self.check():
                    assert 'resolution=merge-duplicates' in self.headers.get('Prefer', '')
                    for row in json.loads(self.rfile.read(int(self.headers['Content-Length']))):
                        fake.rows[row['name']] = row['body']
                    self.answer(201)

            def do_DELETE(self):
                if self.check():
                    name = parse_qs(urlsplit(self.path).query)['name'][0]
                    fake.rows.pop(name.removeprefix('eq.'), None)
                    self.answer(204)

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:%d' % self.server.server_address[1]

    def store(self):
        return statestore.CloudBaseStore('env-test', 'test-key', base=self.base)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Site:
    def __init__(self, test, app):
        self.app, self.cookie = app, ''
        self.server = cloud.make_server(app, '127.0.0.1', 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        test.addCleanup(self.server.server_close)
        test.addCleanup(self.server.shutdown)

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=10)
        hdr = {'Cookie': self.cookie, 'Content-Type': 'application/x-www-form-urlencoded'}
        hdr.update(headers or {})
        conn.request(method, path, urlencode(data) if data is not None else None, hdr)
        r = conn.getresponse()
        body = r.read().decode()
        if r.getheader('Set-Cookie'):
            self.cookie = r.getheader('Set-Cookie').split(';')[0]
        conn.close()
        return r.status, dict(r.getheaders()), body

    def csrf(self, path, headers=None):
        return re.search('name="csrf" value="([^"]+)"', self.request('GET', path, headers=headers)[2])[1]


class SetupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def setup_site(self, **kwargs):
        site = Site(self, cloud.App(self.root, 'http://localhost:8765', '', **kwargs))
        return site, site.csrf('/setup')

    def test_first_visit_is_setup_and_whoop_required_deepseek_optional(self):
        site, csrf = self.setup_site()
        for path in ('/', '/login', '/settings', '/dashboard'):
            self.assertEqual(site.request('GET', path)[1].get('Location'), '/setup', path)
        body = site.request('GET', '/setup')[2]
        self.assertIn('http://localhost:8765/oauth/callback', body)
        self.assertRegex(body, 'name="client_id"[^>]*required')
        self.assertNotRegex(body, 'name="deepseek_key"[^>]*required')
        form = {'csrf': csrf, 'password': 'my-pass-01', 'password2': 'my-pass-01'}
        self.assertEqual(site.request('POST', '/setup', form)[0], 400)  # WHOOP missing
        self.assertEqual(site.request('POST', '/setup', dict(form, password2='other', client_id='i', client_secret='s'))[0], 400)
        status, headers, _ = site.request('POST', '/setup', dict(form, client_id='cid', client_secret='csecret'))
        self.assertEqual((status, headers['Location']), (303, '/settings'))
        settings = site.app.settings()
        self.assertEqual(settings['client_id'], 'cid')
        self.assertNotIn('deepseek_key', settings)
        self.assertNotIn('my-pass-01', json.dumps(settings))
        body = site.request('GET', '/settings')[2]
        self.assertIn('连接 WHOOP', body)
        self.assertNotIn('csecret', body)
        # Setup is single-use; afterwards the normal login applies.
        self.assertEqual(site.request('GET', '/setup')[1].get('Location'), '/')
        other = Site(self, site.app)
        self.assertEqual(other.request('GET', '/')[1].get('Location'), '/login')
        csrf = other.csrf('/login')
        self.assertEqual(other.request('POST', '/setup', {'csrf': csrf, 'password': 'evil-pass-1', 'password2': 'evil-pass-1', 'client_id': 'x', 'client_secret': 'y'})[0], 400)
        self.assertEqual(other.request('POST', '/login', {'csrf': csrf, 'password': 'wrong-pass'})[0], 403)
        self.assertEqual(other.request('POST', '/login', {'csrf': csrf, 'password': 'my-pass-01'})[0], 303)
        self.assertEqual(site.app.settings()['client_id'], 'cid')

    def test_setup_survives_a_lost_session(self):
        site, csrf = self.setup_site()
        # Container restart (or a cookie the browser never stored): the anonymous session
        # is gone. The signed token rendered into the page must still be accepted instead
        # of bouncing the user back to an empty form they already filled in.
        restarted = Site(self, site.app)
        restarted.cookie = ''
        form = {'csrf': csrf, 'password': 'my-pass-01', 'password2': 'my-pass-01',
                'client_id': 'cid', 'client_secret': 'csecret'}
        status, headers, _ = restarted.request('POST', '/setup', form)
        self.assertEqual((status, headers['Location']), (303, '/settings'))
        self.assertEqual(site.app.settings()['client_id'], 'cid')

    def test_setup_token_is_signed(self):
        site, _ = self.setup_site()
        self.assertTrue(site.app.check_setup_token(site.app.setup_token()))
        for bogus in ('', 's.9999999999.bogus', 's.1.bogus', 'x.9999999999.bogus'):
            self.assertFalse(site.app.check_setup_token(bogus), bogus)

    def test_factory_reset_needs_login_and_the_password(self):
        site, csrf = self.setup_site()
        site.request('POST', '/setup', {'csrf': csrf, 'password': 'my-pass-01', 'password2': 'my-pass-01',
                                        'client_id': 'cid', 'client_secret': 'csecret'})
        (self.root / 'data').mkdir(exist_ok=True)
        (self.root / 'data' / 'cycles.json').write_text('[]')
        (self.root / 'dashboard.html').write_text('<html></html>')
        self.assertTrue(site.app.settings().get('client_id'))
        self.assertIn('恢复出厂设置', site.request('GET', '/settings')[2])
        csrf = site.csrf('/settings')
        # Anonymous visitors cannot wipe the site.
        stranger = Site(self, site.app)
        stranger.cookie = ''
        self.assertEqual(stranger.request('POST', '/settings/reset', {'csrf': 'x', 'password': 'my-pass-01'})[0], 403)
        self.assertEqual(site.request('POST', '/settings/reset', {'csrf': csrf, 'password': 'wrong-pass'})[0], 403)
        self.assertEqual(site.app.settings().get('client_id'), 'cid')
        status, headers, _ = site.request('POST', '/settings/reset', {'csrf': csrf, 'password': 'my-pass-01'})
        self.assertEqual((status, headers['Location']), (303, '/setup'))
        self.assertTrue(site.app.needs_setup())
        self.assertFalse((self.root / 'dashboard.html').exists())
        self.assertFalse((self.root / 'data').exists())
        for path in ('/', '/settings'):
            self.assertEqual(site.request('GET', path)[1].get('Location'), '/setup', path)

    def test_wizard_walks_through_the_pdf_steps(self):
        site, _ = self.setup_site()
        body = site.request('GET', '/setup')[2]
        for needle in ('read:recovery', 'read:body_measurement', 'CREATE NEW APP', 'REDIRECT URLS',
                       'PRIVACY POLICY', 'data-copy', '第 8 步', 'DeepSeek'):
            self.assertIn(needle, body)
        self.assertIn('http://localhost:8765/oauth/callback', body)
        self.assertIn('http://localhost:8765/privacy', body)
        # The same guide is reachable on its own, before and after setup.
        self.assertEqual(site.request('GET', '/guide')[0], 200)
        site.request('POST', '/setup', {'csrf': site.csrf('/setup'), 'password': 'my-pass-01',
                                        'password2': 'my-pass-01', 'client_id': 'c', 'client_secret': 's'})
        self.assertEqual(site.request('GET', '/guide')[0], 200)

    def test_home_keeps_a_checklist_until_configured(self):
        site, csrf = self.setup_site()
        # Credentials filled, WHOOP not authorised yet: the home page must explain the
        # remaining step instead of redirecting somewhere else.
        site.request('POST', '/setup', {'csrf': csrf, 'password': 'my-pass-01', 'password2': 'my-pass-01',
                                        'client_id': 'cid', 'client_secret': 'csecret'})
        status, _, body = site.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('配置进度', body)
        self.assertIn('还差 1 步', body)
        self.assertIn('action="/oauth/start"', body)
        self.assertIn('（选填）', body)

    def test_settings_echo_ids_and_reveal_secrets_on_demand(self):
        site, csrf = self.setup_site()
        site.request('POST', '/setup', {'csrf': csrf, 'password': 'my-pass-01', 'password2': 'my-pass-01',
                                        'client_id': 'cid-123', 'client_secret': 'csecret-456',
                                        'deepseek_key': 'sk-test-789'})
        body = site.request('GET', '/settings')[2]
        # Non-secret identifiers are shown; secrets never reach the HTML.
        self.assertIn('value="cid-123"', body)
        self.assertNotIn('csecret-456', body)
        self.assertNotIn('sk-test-789', body)
        self.assertEqual(body.count('data-reveal="'), 2)
        token = site.csrf('/settings')
        status, _, raw = site.request('POST', '/settings/reveal', {'csrf': token, 'field': 'client_secret'})
        self.assertEqual((status, json.loads(raw)), (200, {'value': 'csecret-456'}))
        raw = site.request('POST', '/settings/reveal', {'csrf': token, 'field': 'deepseek_key'})[2]
        self.assertEqual(json.loads(raw), {'value': 'sk-test-789'})
        self.assertEqual(site.request('POST', '/settings/reveal', {'csrf': token, 'field': 'password'})[0], 400)
        anonymous = Site(self, site.app)
        anonymous.cookie = ''
        self.assertEqual(anonymous.request('POST', '/settings/reveal',
                                           {'csrf': token, 'field': 'client_secret'})[0], 403)

    def test_privacy_page_is_public_even_before_setup(self):
        site, _ = self.setup_site()
        status, _, body = site.request('GET', '/privacy')
        self.assertEqual(status, 200)
        self.assertIn('不出售', body)

    def test_setup_code_when_configured(self):
        site, csrf = self.setup_site(setup_code='code-123')
        form = {'csrf': csrf, 'password': 'my-pass-01', 'password2': 'my-pass-01', 'client_id': 'c', 'client_secret': 's'}
        self.assertEqual(site.request('POST', '/setup', dict(form, setup_code='nope'))[0], 400)
        self.assertEqual(site.request('POST', '/setup', dict(form, setup_code='code-123'))[0], 303)

    def test_login_survives_restart_and_logout_revokes_everywhere(self):
        site = Site(self, cloud.App(self.root, 'http://localhost:8765', 'strong-password-123'))
        csrf = site.csrf('/login')
        site.request('POST', '/login', {'csrf': csrf, 'password': 'strong-password-123'})
        cookie = site.cookie
        # A new process (cold start) with the same persisted settings accepts the cookie.
        restarted = Site(self, cloud.App(self.root, 'http://localhost:8765', 'strong-password-123'))
        restarted.cookie = cookie
        self.assertEqual(restarted.request('GET', '/settings')[0], 200)
        csrf = restarted.csrf('/settings')
        self.assertEqual(restarted.request('POST', '/logout', {'csrf': csrf})[0], 303)
        site.cookie = cookie
        self.assertEqual(site.request('GET', '/settings')[0], 303)
        # Forged or tampered tokens are rejected.
        site.cookie = 'session=' + cookie.split('=', 1)[1][:-2] + 'xx'
        self.assertEqual(site.request('GET', '/settings')[0], 303)

    def test_origin_from_host_when_public_url_unset(self):
        site = Site(self, cloud.App(self.root, '', 'strong-password-123'))
        host = {'Host': 'whoop-123.sh.run.tcloudbase.com'}
        _, headers, body = site.request('GET', '/login', headers=host)
        self.assertIn('Secure', headers['Set-Cookie'])
        csrf = re.search('name="csrf" value="([^"]+)"', body)[1]
        site.request('POST', '/login', {'csrf': csrf, 'password': 'strong-password-123'}, dict(host, Origin='https://whoop-123.sh.run.tcloudbase.com'))
        body = site.request('GET', '/settings', headers=host)[2]
        self.assertIn('https://whoop-123.sh.run.tcloudbase.com/oauth/callback', body)
        csrf = re.search('name="csrf" value="([^"]+)"', body)[1]
        self.assertEqual(site.request('POST', '/settings', {'csrf': csrf}, dict(host, Origin='https://evil.invalid'))[0], 403)
        site.app.update_settings({'client_id': 'id', 'client_secret': 'secret'})
        status, headers, _ = site.request('POST', '/oauth/start', {'csrf': csrf}, dict(host, Origin='https://whoop-123.sh.run.tcloudbase.com'))
        self.assertEqual(status, 303)
        self.assertEqual(parse_qs(urlsplit(headers['Location']).query)['redirect_uri'], ['https://whoop-123.sh.run.tcloudbase.com/oauth/callback'])
        self.assertEqual(site.request('GET', '/settings', headers={'Host': 'bad host'})[0], 500)


class FeatureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.app = cloud.App(self.root, 'http://localhost:8765', 'strong-password-123')
        self.app.update_settings({'client_id': 'id', 'client_secret': 'secret'})
        shutil.copytree(ROOT / 'fixtures' / 'multi', self.root / 'data')
        cloud.whoop.write_json(self.root / '.tokens.json', {'access_token': 'test'})

    def test_auto_ai_after_sync_only_with_key_and_opt_in(self):
        with mock.patch.object(cloud.whoop, 'cmd_sync'), mock.patch.object(self.app, 'analyze') as analyze:
            self.assertTrue(self.app.sync())
            analyze.assert_not_called()
            self.app.update_settings({'deepseek_key': 'k'})
            self.assertTrue(self.app.sync())
            analyze.assert_called_once()
            self.app.update_settings({'auto_ai_present': '1'})  # checkbox cleared
            self.assertTrue(self.app.sync())
            analyze.assert_called_once()
        with mock.patch.object(cloud.whoop, 'cmd_sync'), mock.patch.object(self.app, 'analyze', side_effect=ValueError('AI down')):
            self.app.update_settings({'auto_ai_present': '1', 'auto_ai': '1'})
            self.assertTrue(self.app.sync())

    def test_home_summary_shows_this_morning_and_yesterday(self):
        days = cloud.report.build_days(cloud.report.load(str(self.root / 'data')))
        last = datetime.strptime(days[-1]['date'], '%Y-%m-%d').replace(hour=10, tzinfo=cloud.TZ)
        rows = dict(self.app.summary(last))
        self.assertEqual(len(rows), 2)
        morning = next(v for k, v in rows.items() if k.startswith('今天早上'))
        self.assertIn('恢复', morning)
        self.assertIn('昨晚睡眠', morning)
        self.assertIn('日负荷', next(v for k, v in rows.items() if k.startswith('昨天全天')))
        self.assertEqual(self.app.summary(datetime(2000, 1, 1, tzinfo=timezone.utc)), [])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCloudBase()
        self.addCleanup(self.fake.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def app(self, root=None):
        app = cloud.App(root or self.root, 'http://localhost:8765', 'strong-password-123', store=self.fake.store())
        app.restore()
        return app

    def test_from_env_and_validation(self):
        self.assertIsNone(statestore.from_env({}))
        store = statestore.from_env({'CLOUDBASE_ENV_ID': 'my-env-1a2b', 'CLOUDBASE_APIKEY': 'k'})
        self.assertEqual(store.url, 'https://my-env-1a2b.api.tcloudbasegateway.com/v1/rdb/rest/whoop_state')
        for env in ({'CLOUDBASE_ENV_ID': 'x'}, {'CLOUDBASE_APIKEY': 'k'}, {'CLOUDBASE_ENV_ID': 'a/b', 'CLOUDBASE_APIKEY': 'k'},
                    {'CLOUDBASE_ENV_ID': 'e', 'CLOUDBASE_APIKEY': 'k', 'CLOUDBASE_API_BASE': 'http://evil.example'}):
            with self.assertRaises(ValueError):
                statestore.from_env(env)

    def test_state_survives_container_replacement(self):
        app = self.app()
        app.update_settings({'client_id': 'cid', 'client_secret': 'sec', 'deepseek_key': 'dk'})
        shutil.copytree(ROOT / 'fixtures' / 'multi', self.root / 'data')
        cloud.whoop.write_json(self.root / '.tokens.json', {'access_token': 'a', 'refresh_token': 'r1'})
        with mock.patch.object(cloud.whoop, 'cmd_sync'), mock.patch.object(app, 'analyze'):
            self.assertTrue(app.sync())
        self.assertIn('data/cycles.json', self.fake.rows)
        self.assertEqual(json.loads(self.fake.rows['.tokens.json'])['refresh_token'], 'r1')
        self.assertTrue(all(name in statestore.NAMES for name in self.fake.rows))
        # Fresh empty disk, as after scale-to-zero or redeploy.
        fresh = tempfile.TemporaryDirectory()
        self.addCleanup(fresh.cleanup)
        restored = self.app(Path(fresh.name))
        restored.ensure_ready()
        self.assertEqual(restored.settings()['client_id'], 'cid')
        self.assertTrue(restored.status()['full_synced'])
        self.assertTrue((Path(fresh.name) / 'dashboard.html').is_file())
        self.assertEqual(json.loads((Path(fresh.name) / '.tokens.json').read_text())['refresh_token'], 'r1')
        # Only changed rows are uploaded on the next operation.
        before = len(self.fake.requests)
        restored.update_settings({})
        self.assertEqual(len(self.fake.requests), before)

    def test_refreshed_token_is_backed_up_even_when_sync_fails(self):
        app = self.app()
        app.update_settings({'client_id': 'cid', 'client_secret': 'sec'})
        cloud.whoop.write_json(self.root / '.tokens.json', {'access_token': 'a', 'refresh_token': 'old'})
        def rotate_then_fail(days=None):
            cloud.whoop.write_json(self.root / '.tokens.json', {'access_token': 'b', 'refresh_token': 'rotated'})
            raise cloud.whoop.WhoopError('network')
        with mock.patch.object(cloud.whoop, 'cmd_sync', side_effect=rotate_then_fail):
            self.assertFalse(app.sync())
        self.assertEqual(json.loads(self.fake.rows['.tokens.json'])['refresh_token'], 'rotated')

    def test_account_change_deletes_remote_health_rows(self):
        app = self.app()
        app.update_settings({'client_id': 'old', 'client_secret': 'sec'})
        shutil.copytree(ROOT / 'fixtures' / 'multi', self.root / 'data')
        cloud.whoop.write_json(self.root / '.tokens.json', {'access_token': 'a'})
        app.update_settings({})
        self.assertIn('data/sleep.json', self.fake.rows)
        app.update_settings({'client_id': 'new'})
        self.assertFalse([n for n in self.fake.rows if n.startswith('data/') or n == '.tokens.json'])

    def test_store_outage_flags_and_retries_without_losing_changes(self):
        app = self.app()
        self.fake.fail = True
        app.update_settings({'client_id': 'cid', 'client_secret': 'sec'})
        self.assertTrue(app.backup_failed)
        self.fake.fail = False
        app.update_settings({})
        self.assertFalse(app.backup_failed)
        self.assertEqual(json.loads(self.fake.rows['settings.json'])['client_id'], 'cid')

    def test_startup_refuses_to_run_when_store_unreachable(self):
        self.fake.fail = True
        with self.assertRaises(statestore.StoreError):
            self.app()

    def test_api_key_only_in_header_and_errors_hide_bodies(self):
        app = self.app()
        app.update_settings({'client_id': 'cid', 'client_secret': 'sec'})
        for method, path, headers in self.fake.requests:
            self.assertNotIn('test-key', path)
            self.assertEqual(headers['Authorization'], 'Bearer test-key')
        self.fake.fail = True
        with self.assertRaises(statestore.StoreError) as caught:
            self.fake.store().load()
        self.assertNotIn('boom', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
