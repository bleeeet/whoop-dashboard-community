import http.client
import importlib.util
import json
import io
import os
import shutil
import urllib.error
from pathlib import Path
import re
import tempfile
import threading
import types
import sys
import unittest
from datetime import datetime, timezone
from unittest import mock

SPEC = importlib.util.spec_from_file_location('cloud_server', Path(__file__).resolve().parents[1] / 'cloud_server.py')
cloud = importlib.util.module_from_spec(SPEC)
if SPEC.loader and SPEC.origin and Path(SPEC.origin).exists():
    SPEC.loader.exec_module(cloud)


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = cloud.App(self.tmp.name, 'http://localhost:8765', 'strong-password-123')
        self.server = cloud.make_server(self.app, '127.0.0.1', 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.cookie = ''

    def request(self, method, path, data=None, headers=None):
        from urllib.parse import urlencode
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        hdr = {'Cookie': self.cookie, 'Content-Type': 'application/x-www-form-urlencoded'}
        hdr.update(headers or {})
        conn.request(method, path, urlencode(data) if data is not None else None, hdr)
        r = conn.getresponse()
        body = r.read().decode()
        if r.getheader('Set-Cookie'):
            self.cookie = r.getheader('Set-Cookie').split(';')[0]
        result = (r.status, dict(r.getheaders()), body)
        conn.close()
        return result

    def request_raw(self, method, path, body, headers=None):
        conn = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        hdr = {'Cookie': self.cookie, 'Content-Type': 'application/json; charset=utf-8'}
        hdr.update(headers or {})
        conn.request(method, path, body.encode('utf-8'), hdr)
        r = conn.getresponse()
        body = r.read().decode()
        if r.getheader('Set-Cookie'):
            self.cookie = r.getheader('Set-Cookie').split(';')[0]
        result = (r.status, dict(r.getheaders()), body)
        conn.close()
        return result

    def login(self):
        _, _, body = self.request('GET', '/login')
        csrf = re.search('name="csrf" value="([^"]+)"', body)[1]
        status, _, _ = self.request('POST', '/login', {'csrf': csrf, 'password': 'strong-password-123'})
        self.assertEqual(status, 303)
        _, _, body = self.request('GET', '/settings')
        return re.search('name="csrf" value="([^"]+)"', body)[1]

    def test_config_rejects_unsafe_origin_and_password(self):
        for url in ('http://example.com', 'https://x/a', 'https://user@x', 'https://x?foo=bar'):
            with self.assertRaises(ValueError):
                cloud.App(self.tmp.name, url, 'strong-password-123')
        with self.assertRaises(ValueError):
            cloud.App(self.tmp.name, 'https://example.com', 'short')

    def test_auth_and_health(self):
        self.assertEqual(self.request('GET', '/health')[2], 'ok')
        for path in ('/', '/settings', '/dashboard', '/ai'):
            self.assertEqual(self.request('GET', path)[0], 303)
        self.assertEqual(self.request('POST', '/sync', {})[0], 403)

    def test_login_csrf_and_session_rotation(self):
        self.request('GET', '/login')
        old = self.cookie
        self.assertEqual(self.request('POST', '/login', {'password': 'strong-password-123'})[0], 403)
        csrf = self.login()
        self.assertNotEqual(old, self.cookie)
        self.assertEqual(self.request('POST', '/settings', {'csrf': 'bad'})[0], 403)
        self.assertEqual(self.request('POST', '/settings', {'csrf': csrf}, {'Origin': 'https://evil.invalid'})[0], 403)
        self.assertEqual(self.request('GET', '/logout')[0], 404)

    def test_secrets_never_echo_and_empty_preserves(self):
        csrf = self.login()
        values = {'csrf': csrf, 'client_id': 'id', 'client_secret': 'TOPSECRET', 'deepseek_key': 'APISECRET', 'schedule_time': '14:00', 'offset': '480'}
        self.assertEqual(self.request('POST', '/settings', values)[0], 303)
        body = self.request('GET', '/settings')[2]
        self.assertNotIn('TOPSECRET', body)
        self.assertNotIn('APISECRET', body)
        self.request('POST', '/settings', {'csrf': csrf, 'client_secret': '', 'deepseek_key': ''})
        self.assertEqual(self.app.settings()['client_secret'], 'TOPSECRET')
        self.request('POST', '/settings', {'csrf': csrf, 'remove_deepseek': '1'})
        self.assertFalse(self.app.settings().get('deepseek_key'))
        self.assertEqual(Path(self.tmp.name, 'settings.json').stat().st_mode & 0o777, 0o600)

    def test_oauth_state_one_use_bound_and_expiring(self):
        a = self.app.new_session(True)
        b = self.app.new_session(True)
        state = self.app.issue_state(a)
        self.assertFalse(self.app.consume_state(b, state))
        self.assertTrue(self.app.consume_state(a, state))
        self.assertFalse(self.app.consume_state(a, state))
        state = self.app.issue_state(a)
        with mock.patch.object(cloud.time, 'time', return_value=cloud.time.time()+901):
            self.assertFalse(self.app.consume_state(a, state))

    def test_oauth_redirect_ignores_host(self):
        csrf = self.login()
        self.app.update_settings({'client_id': 'id', 'client_secret': 'secret'})
        status, headers, _ = self.request('POST', '/oauth/start', {'csrf': csrf}, {'Host': 'evil.invalid'})
        self.assertEqual(status, 303)
        from urllib.parse import parse_qs, urlsplit
        q = parse_qs(urlsplit(headers['Location']).query)
        self.assertEqual(q['redirect_uri'], ['http://localhost:8765/oauth/callback'])
        self.assertEqual(self.request('GET', '/oauth/callback?code=x&state=bad')[0], 400)

    def test_schedule_utc_offset_and_catchup(self):
        # 09:00 and 12:00 Beijing time are 01:00 and 04:00 UTC.
        utc = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=timezone.utc)
        self.assertEqual(self.app.due_slot(utc(23, 0, 59)), '2026-10-22T12:00+08:00')
        self.assertEqual(self.app.due_slot(utc(23, 1)), '2026-10-23T09:00+08:00')
        self.assertEqual(self.app.due_slot(utc(23, 3, 59)), '2026-10-23T09:00+08:00')
        self.assertEqual(self.app.due_slot(utc(23, 4)), '2026-10-23T12:00+08:00')
        self.assertEqual(self.app.due_slot(utc(23, 15)), '2026-10-23T12:00+08:00')
        self.app.write_status({'schedule_slot': self.app.due_slot(utc(23, 1))})
        self.assertFalse(self.app.schedule_due(utc(23, 2)))
        self.assertTrue(self.app.schedule_due(utc(23, 4)))
        self.assertTrue(self.app.schedule_due(utc(24, 1)))

    def test_optional_ai_only_post_and_aggregate(self):
        csrf = self.login()
        with mock.patch.object(cloud.urllib.request, 'urlopen') as network:
            self.assertEqual(self.request('POST', '/ai', {'csrf': csrf})[0], 400)
            self.request('GET', '/ai')
            network.assert_not_called()
        data = Path(self.tmp.name, 'data')
        data.mkdir()
        (data/'recovery.json').write_text(json.dumps([{'user_id': 123, 'score': {'recovery_score': 70, 'hrv_rmssd_milli': 50}}]))
        aggregate = self.app.aggregate()
        self.assertNotIn('user_id', json.dumps(aggregate))
        self.assertEqual(aggregate['recovery_score']['mean'], 70)

    def test_cron_requires_nonempty_secret(self):
        self.assertEqual(self.request('POST', '/internal/sync', {})[0], 403)
        with mock.patch.dict(cloud.os.environ, {'CRON_SECRET': 'cronsecret'}):
            self.assertEqual(self.request('POST', '/internal/sync', {}, {'Authorization': 'Bearer wrong'})[0], 403)
            # Timer calls wait for the sync to finish and report its outcome.
            with mock.patch.object(self.app, '_sync_worker'):
                status, _, body = self.request('POST', '/internal/sync', {}, {'Authorization': 'Bearer cronsecret'})
            self.assertEqual(status, 200)
            self.assertIn('last_success', body)
            with mock.patch.object(self.app, '_sync_worker', side_effect=ValueError('SECRET')):
                status, _, body = self.request('POST', '/internal/sync', {}, {'Authorization': 'Bearer cronsecret'})
            self.assertEqual(status, 502)
            self.assertNotIn('SECRET', body)

    def test_cross_process_lock_and_failure_preserves_page(self):
        Path(self.tmp.name, 'dashboard.html').write_text('old page')
        with self.app.operation():
            import subprocess, sys
            code = 'import fcntl,sys; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
            p = subprocess.run([sys.executable, '-c', code, str(Path(self.tmp.name, 'operation.lock'))], capture_output=True)
            self.assertNotEqual(p.returncode, 0)
        with mock.patch.object(self.app, '_sync_worker', side_effect=RuntimeError('SECRET')):
            self.assertFalse(self.app.sync())
        self.assertEqual(Path(self.tmp.name, 'dashboard.html').read_text(), 'old page')
        self.assertNotIn('SECRET', json.dumps(self.app.status()))

    def test_csp_allows_only_exact_whoop_form_redirect_origin(self):
        _, headers, _ = self.request('GET', '/login')
        csp = headers['Content-Security-Policy']
        action = next(x.strip() for x in csp.split(';') if x.strip().startswith('form-action '))
        self.assertEqual(action, "form-action 'self' https://api.prod.whoop.com")
        self.assertIn("frame-ancestors 'none'", csp)

    def test_anonymous_capacity_never_evicts_authenticated_session_or_state(self):
        sid = self.app.new_session(True)
        state = self.app.issue_state(sid)
        for _ in range(1100):
            self.app.new_session(False)
        self.assertTrue((self.app.session(sid) or {}).get('auth'))
        self.assertTrue(self.app.consume_state(sid, state))
        self.assertLessEqual(sum(not v['auth'] for v in self.app._sessions.values()), 128)

    def test_login_page_rate_limits_before_allocating_anonymous_sessions(self):
        statuses = []
        for _ in range(21):
            self.cookie = ''
            statuses.append(self.request('GET', '/login')[0])
        self.assertEqual(statuses[:20], [200] * 20)
        self.assertEqual(statuses[20], 429)
        self.assertEqual(len(self.app._sessions), 20)

    def test_https_cookie_flags_and_session_expiry(self):
        self.app.secure = True
        _, headers, _ = self.request('GET', '/login')
        for flag in ('HttpOnly', 'SameSite=Lax', 'Secure', 'Path=/'):
            self.assertIn(flag, headers['Set-Cookie'])
        self.login()
        self.assertIn('Max-Age=' + str(cloud.SESSION_SECONDS), self.request('GET', '/settings')[1].get('Set-Cookie', 'Max-Age=' + str(cloud.SESSION_SECONDS)))
        with mock.patch.object(cloud.time, 'time', return_value=cloud.time.time() + cloud.SESSION_SECONDS + 61):
            self.assertEqual(self.request('GET', '/settings')[0], 303)

    def test_account_credentials_change_removes_all_previous_health_artifacts(self):
        self.app.update_settings({'client_id': 'old-id', 'client_secret': 'old-secret'})
        root = Path(self.tmp.name)
        (root/'data').mkdir()
        (root/'data'/'profile.json').write_text('{"email":"old@example.invalid"}')
        for name in ('dashboard.html', '.tokens.json', 'ai.json', 'status.json'):
            (root/name).write_text('{}')
        sid = self.app.new_session(True)
        state = self.app.issue_state(sid)
        self.app.update_settings({'client_id': 'new-id', 'client_secret': 'new-secret'})
        for name in ('data', 'dashboard.html', '.tokens.json', 'ai.json', 'status.json'):
            self.assertFalse((root/name).exists(), name)
        self.assertFalse(self.app.consume_state(sid, state))
        self.assertEqual(self.app.settings()['client_id'], 'new-id')

    def test_oauth_success_reauthorization_clears_same_client_history_and_state_replay(self):
        self.app.update_settings({'client_id': 'client', 'client_secret': 'secret'})
        csrf = self.login()
        status, headers, _ = self.request('POST', '/oauth/start', {'csrf': csrf})
        from urllib.parse import parse_qs, urlsplit
        state = parse_qs(urlsplit(headers['Location']).query)['state'][0]
        root = Path(self.tmp.name)
        (root/'data').mkdir()
        (root/'data'/'profile.json').write_text('{}')
        (root/'dashboard.html').write_text('old account')
        original = cloud.whoop.CLIENT_ID
        with mock.patch.object(cloud.whoop, 'post_form', return_value={'access_token': 'NEW_TOKEN', 'refresh_token': 'NEW_REFRESH'}), mock.patch.object(self.app, 'launch_sync', return_value=True):
            status, _, body = self.request('GET', '/oauth/callback?state=' + state + '&code=PRIVATE_CODE')
        self.assertEqual(status, 303)
        self.assertNotIn('NEW_TOKEN', body)
        self.assertFalse((root/'data').exists())
        self.assertFalse((root/'dashboard.html').exists())
        self.assertEqual(json.loads((root/'.tokens.json').read_text())['refresh_token'], 'NEW_REFRESH')
        self.assertEqual((root/'.tokens.json').stat().st_mode & 0o777, 0o600)
        self.assertTrue(cloud.whoop.CLIENT_ID == original, 'library global configuration was not restored')
        self.assertEqual(self.request('GET', '/oauth/callback?state=' + state + '&code=PRIVATE_CODE')[0], 400)

    def test_sync_success_full_incremental_and_render_failure_keep_page(self):
        self.app.update_settings({'client_id': 'id', 'client_secret': 'secret'})
        root = Path(self.tmp.name)
        shutil.copytree(Path(__file__).resolve().parents[1]/'fixtures'/'multi', root/'data')
        cloud.whoop.write_json(root/'.tokens.json', {'access_token': 'test'})
        with mock.patch.object(cloud.whoop, 'cmd_sync') as sync:
            self.assertTrue(self.app.sync())
            self.assertTrue(self.app.sync())
        self.assertEqual(sync.call_args_list, [mock.call(days=None), mock.call(days=7)])
        original = (root/'dashboard.html').read_bytes()
        self.assertEqual(self.app.status()['schedule_slot'], self.app.due_slot())
        with mock.patch.object(cloud.whoop, 'cmd_sync'), mock.patch.object(cloud.report, 'render', side_effect=ValueError('SECRET')):
            self.assertFalse(self.app.sync())
        self.assertEqual((root/'dashboard.html').read_bytes(), original)
        self.assertNotIn('SECRET', json.dumps(self.app.status()))
        restarted = cloud.App(self.tmp.name, 'http://localhost:8765', 'strong-password-123')
        self.assertTrue(restarted.status()['full_synced'])

    def test_ai_requires_explicit_post_consent_and_sends_only_aggregate(self):
        csrf = self.login()
        self.app.update_settings({'client_id': 'private-id', 'client_secret': 'CLIENTSECRET', 'deepseek_key': 'APISECRET'})
        data = Path(self.tmp.name)/'data'
        data.mkdir()
        (data/'recovery.json').write_text(json.dumps([{'user_id': 123, 'email': 'private@example.invalid', 'score': {'recovery_score': 70}}]))
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = json.dumps({'choices': [{'message': {'content': '<script>alert(1)</script> APISECRET CLIENTSECRET'}}]}).encode()
        with mock.patch.object(cloud.whoop, '_open_no_redirect', return_value=response) as network:
            self.request('GET', '/ai')
            self.assertEqual(self.request('POST', '/ai', {'csrf': csrf})[0], 400)
            network.assert_not_called()
            self.assertEqual(self.request('POST', '/ai', {'csrf': csrf, 'consent': '1'})[0], 303)
        request = network.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.deepseek.com/chat/completions')
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'deepseek-flash')
        for forbidden in ('user_id', 'private@example.invalid', '123', 'CLIENTSECRET', 'APISECRET'):
            self.assertNotIn(forbidden, json.dumps(payload))
        body = self.request('GET', '/ai')[2]
        self.assertNotIn('APISECRET', body)
        self.assertNotIn('CLIENTSECRET', body)
        self.assertNotIn('<script>alert(1)', body)
        self.assertIn('&lt;script&gt;', body)
    def test_ai_network_error_is_redacted_and_retains_previous_answer(self):
        csrf = self.login()
        self.app.update_settings({'deepseek_key': 'APISECRET'})
        data = Path(self.tmp.name)/'data'
        data.mkdir()
        (data/'recovery.json').write_text('[{"score":{"recovery_score":70}}]')
        cloud.whoop.write_json(Path(self.tmp.name)/'ai.json', {'answer': 'previous analysis'})
        error = urllib.error.HTTPError('https://api.deepseek.com/SECRET', 401, 'APISECRET', {}, io.BytesIO(b'PRIVATE_RESPONSE'))
        with mock.patch.object(cloud.whoop, '_open_no_redirect', side_effect=error):
            status, _, body = self.request('POST', '/ai', {'csrf': csrf, 'consent': '1'})
        self.assertEqual(status, 400)
        for secret in ('APISECRET', 'PRIVATE_RESPONSE', 'SECRET'):
            self.assertNotIn(secret, body)
        self.assertIn('previous analysis', self.request('GET', '/ai')[2])

    def test_private_files_and_state_changing_get_routes_are_never_served(self):
        self.login()
        for path in ('/.env', '/settings.json', '/.tokens.json', '/data/profile.json', '/status.json', '/operation.lock', '/%2e%2e/settings.json', '/sync', '/oauth/start', '/internal/sync'):
            self.assertEqual(self.request('GET', path)[0], 404, path)
        self.assertEqual(self.request('POST', '/logout', {'csrf': 'bad'})[0], 403)

    def test_scheduler_checks_missed_daily_slot_immediately_without_network(self):
        cloud.whoop.write_json(Path(self.tmp.name)/'.tokens.json', {'access_token': 'test'})
        def stop_wait(seconds):
            self.app._stop.set()
            return True
        with mock.patch.object(self.app, 'launch_sync', return_value=True) as launch, mock.patch.object(self.app._stop, 'wait', side_effect=stop_wait):
            self.app.start_scheduler()
            self.app._scheduler.join(2)
            self.assertFalse(self.app._scheduler.is_alive())
            launch.assert_called_once()
        self.app.close()

    def test_same_origin_referrer_policy_and_csrf_origin_contract(self):
        _, headers, _ = self.request('GET', '/login')
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        csrf = self.login()
        # Browsers retain the same-origin POST Origin under this policy. Opaque
        # origins remain rejected even when the CSRF token is otherwise valid.
        self.assertEqual(self.request('POST', '/settings', {'csrf': csrf}, {'Origin': 'null'})[0], 403)
        self.assertEqual(self.request('POST', '/settings', {'csrf': csrf}, {'Origin': self.app.public_url})[0], 303)
        self.assertEqual(self.request('POST', '/settings', {'csrf': 'invalid'}, {'Origin': self.app.public_url})[0], 403)

    def test_settings_input_types_contact_footer_and_dashboard_return_links(self):
        self.login()
        body = self.request('GET', '/settings')[2]
        for name in ('client_id', 'deepseek_model'):
            self.assertRegex(body, '<input type="text"[^>]*name="' + name + '"')
        for name in ('client_secret', 'deepseek_key'):
            self.assertRegex(body, '<input type="password"[^>]*name="' + name + '"')
        self.assertIn('input[type=checkbox]', body)
        self.assertIn('mailto:bleetchenxuanling@gmail.com', body)
        self.assertIn('开发联系：bleetchenxuanling@gmail.com', body)
        Path(self.tmp.name, 'dashboard.html').write_text('<html><body>synthetic dashboard</body></html>')
        body = self.request('GET', '/dashboard')[2]
        self.assertIn('href="/"', body)
        self.assertIn('href="/settings"', body)

    def test_weekly_login_view_escapes_markdown_and_links_navigation(self):
        self.assertEqual(self.request('GET', '/weekly')[0], 303)
        self.login()
        generator = mock.Mock(return_value='# 周报 <script>not executable</script>')
        with mock.patch.dict(sys.modules, {'weekly': types.SimpleNamespace(generate=generator)}):
            status, _, body = self.request('GET', '/weekly')
        self.assertEqual(status, 200)
        self.assertIn('&lt;script&gt;', body)
        self.assertNotIn('<script>not executable', body)
        # The home page keeps showing the setup checklist instead of redirecting, so a
        # half-configured site still explains what is missing where the user lands.
        self.assertEqual(self.request('GET', '/')[0], 200)
        self.assertIn('配置进度', self.request('GET', '/')[2])
        self.app.update_settings({'client_id': 'id', 'client_secret': 'secret'})
        self.assertIn('href="/weekly"', self.request('GET', '/')[2])
        generator.assert_called_once_with(str(self.app.root/'data'))

    def test_weekly_machine_token_is_independent_and_generation_holds_lock(self):
        self.assertEqual(self.request('POST', '/internal/weekly', {})[0], 403)
        with mock.patch.dict(cloud.os.environ, {'AUTOMATION_SECRET': '', 'CRON_SECRET': 'cronsecret'}):
            self.assertEqual(self.request('POST', '/internal/weekly', {}, {'Authorization': 'Bearer cronsecret'})[0], 403)
        def generate(data_dir):
            self.assertEqual(data_dir, str(self.app.root/'data'))
            with self.assertRaises(cloud.whoop.WhoopError):
                with cloud.whoop.InstanceLock(Path(self.tmp.name)/'operation.lock'):
                    pass
            return '# Synthetic weekly report'
        with mock.patch.dict(sys.modules, {'weekly': types.SimpleNamespace(generate=generate)}), mock.patch.dict(cloud.os.environ, {'AUTOMATION_SECRET': 'weeklysecret'}), mock.patch.object(cloud.whoop, 'cmd_sync') as sync:
            self.cookie = ''
            self.assertEqual(self.request('POST', '/internal/weekly', {}, {'Authorization': 'Bearer wrong'})[0], 403)
            status, headers, body = self.request('POST', '/internal/weekly', {}, {'Authorization': 'Bearer weeklysecret'})
            self.assertEqual(status, 200)
            self.assertTrue(headers['Content-Type'].startswith('text/markdown'))
            self.assertIn('# Synthetic weekly report', body)
            self.assertIn('尚未成功同步', body)
            sync.assert_not_called()

    def test_weekly_reports_include_persisted_last_success_or_explicit_missing_status(self):
        generator = types.SimpleNamespace(generate=lambda data_dir: '# Weekly snapshot')
        with mock.patch.dict(sys.modules, {'weekly': generator}):
            missing = self.app.weekly_report()
            self.assertIn('尚未成功同步', missing)
            self.app.write_status({'last_success': '2026-10-23T06:01:00+00:00'})
            saved = self.app.weekly_report()
            self.assertIn('最后成功同步', saved)
            self.assertIn('2026-10-23T06:01:00+00:00', saved)
            self.assertIn('快照', saved)
            self.assertNotIn('尚未成功同步', saved)


    def test_internal_export_requires_backup_secret(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.request('GET', '/internal/export')[0], 403)
        with mock.patch.dict(os.environ, {'BACKUP_SECRET': 's3cret-value'}):
            self.assertEqual(self.request('GET', '/internal/export')[0], 403)
            self.assertEqual(self.request(
                'GET', '/internal/export', headers={'Authorization': 'Bearer nope'})[0], 403)
            status, _, body = self.request(
                'GET', '/internal/export', headers={'Authorization': 'Bearer s3cret-value'})
            self.assertEqual(status, 200)
            payload = json.loads(body)
            self.assertEqual(payload['format'], 'whoop-state/1')
            self.assertIsInstance(payload['files'], dict)

    def test_state_backup_roundtrip(self):
        self.app.write_status({'last_success': '2026-10-23T06:01:00+00:00'})
        snapshot = self.app.export_state()
        self.assertIn('status.json', snapshot['files'])
        # wipe the tracked state, then rebuild it from the snapshot
        for name in cloud.statestore.NAMES:
            (self.app.root / name).unlink(missing_ok=True)
        self.assertIsNone(self.app.status().get('last_success'))
        self.assertGreaterEqual(self.app.restore_state(snapshot), 1)
        self.assertEqual(self.app.status().get('last_success'), '2026-10-23T06:01:00+00:00')
        self.assertEqual(self.app.export_state()['files'], snapshot['files'])

    def test_restore_rejects_bad_payloads(self):
        for bad in ({}, {'format': 'other', 'files': {}},
                    {'format': 'whoop-state/1', 'files': {}},
                    {'format': 'whoop-state/1', 'files': {'../evil.json': '{}'}},
                    {'format': 'whoop-state/1', 'files': {'settings.json': 5}}):
            with self.assertRaises(ValueError):
                self.app.restore_state(bad)


if __name__ == '__main__':
    unittest.main()
