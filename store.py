"""Optional CloudBase PostgreSQL mirror for DATA_ROOT. Python standard library only.

Cloud containers lose their local disk on redeploy, restart or scale-to-zero.
When CLOUDBASE_ENV_ID and CLOUDBASE_APIKEY are set, the service restores the
small set of state files below at startup and uploads changed files after every
operation. Table (create once, see cloudbase/schema.sql):

    whoop_state(name text primary key, body text not null, updated_at timestamptz)

Without those variables the service keeps using the local DATA_ROOT only.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

# Only these names are ever read from or written to the remote table.
FILES = ('settings.json', 'status.json', '.tokens.json', 'ai.json')
DATASETS = ('cycles', 'recovery', 'sleep', 'workouts', 'profile', 'body')
NAMES = FILES + tuple('data/' + name + '.json' for name in DATASETS)
MAX_BODY = 32 * 1024 * 1024


class StoreError(RuntimeError):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    # Never forward the API key to another host.
    def redirect_request(self, *args, **kwargs):
        return None


class CloudBaseStore:
    """Key/value rows in a CloudBase PostgreSQL table via its REST gateway."""

    def __init__(self, env_id, api_key, table='whoop_state', base=None):
        if not re.fullmatch(r'[A-Za-z0-9-]{1,64}', env_id or ''):
            raise ValueError('Invalid CLOUDBASE_ENV_ID')
        if not api_key or any(c.isspace() for c in api_key):
            raise ValueError('Invalid CLOUDBASE_APIKEY')
        if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', table or ''):
            raise ValueError('Invalid table name')
        base = (base or 'https://' + env_id + '.api.tcloudbasegateway.com').rstrip('/')
        parsed = urllib.parse.urlsplit(base)
        local = parsed.hostname in ('localhost', '127.0.0.1')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and local):
            raise ValueError('CloudBase API base must use HTTPS')
        self.url = base + '/v1/rdb/rest/' + table
        self._key = api_key
        self._opener = urllib.request.build_opener(_RejectRedirects())

    def _call(self, method, query='', body=None, prefer=None):
        headers = {'Authorization': 'Bearer ' + self._key, 'Accept': 'application/json'}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if prefer:
            headers['Prefer'] = prefer
        request = urllib.request.Request(self.url + query, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=30) as response:
                return response.read(MAX_BODY + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            # Status code only; response bodies may echo request data.
            raise StoreError('CloudBase HTTP %d' % code) from None
        except (urllib.error.URLError, OSError):
            raise StoreError('CloudBase unreachable') from None

    def load(self):
        raw = self._call('GET', '?select=name,body')
        if len(raw) > MAX_BODY:
            raise StoreError('CloudBase response too large')
        rows = json.loads(raw or b'[]')
        if not isinstance(rows, list):
            raise StoreError('Invalid CloudBase response')
        return {r['name']: r['body'] for r in rows
                if isinstance(r, dict) and r.get('name') in NAMES and isinstance(r.get('body'), str)}

    def put(self, name, body):
        self._call('POST', '', [{'name': name, 'body': body}],
                   prefer='resolution=merge-duplicates,return=minimal')

    def delete(self, name):
        self._call('DELETE', '?name=eq.' + urllib.parse.quote(name, safe=''), prefer='return=minimal')


def from_env(environ=None):
    environ = os.environ if environ is None else environ
    env_id = environ.get('CLOUDBASE_ENV_ID', '').strip()
    api_key = environ.get('CLOUDBASE_APIKEY', '').strip()
    if not env_id and not api_key:
        return None
    return CloudBaseStore(env_id, api_key, environ.get('WHOOP_STATE_TABLE', 'whoop_state').strip(),
                          environ.get('CLOUDBASE_API_BASE', '').strip() or None)


def _read(root, name):
    try:
        return (Path(root) / name).read_bytes().decode('utf-8')
    except (OSError, UnicodeDecodeError):
        return None


def snapshot(root):
    result = {}
    for name in NAMES:
        text = _read(root, name)
        if text is not None:
            result[name] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return result


def restore(store, root, write):
    """Replace local tracked files with the remote copy. write(path, text) must be atomic."""
    rows = store.load()
    for name in NAMES:
        if name in rows:
            write(Path(root) / name, rows[name])
    return snapshot(root)


def push(store, root, before):
    """Upload files whose content changed since `before`; delete removed ones. Returns new snapshot."""
    after = snapshot(root)
    done = dict(before)
    try:
        for name, digest in after.items():
            if before.get(name) != digest:
                text = _read(root, name)
                if text is not None:
                    store.put(name, text)
                    done[name] = digest
        for name in set(before) - set(after):
            store.delete(name)
            done.pop(name, None)
    except StoreError:
        # Return what actually reached the store so the next push retries the rest.
        raise StoreError('CloudBase backup incomplete', done) from None
    return after
