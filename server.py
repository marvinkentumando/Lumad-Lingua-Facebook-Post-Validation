#!/usr/bin/env python3
"""Lumad Lingua: single-administrator sentiment validation server.

Python standard library only. Requires an admin password on non-loopback bindings.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
SEED = ROOT / 'data' / 'mansaka_candidates.csv'
STATE_DIR = Path(os.environ.get('LL_DATA_DIR', str(ROOT / '.data'))).resolve()
DB = STATE_DIR / 'annotations.sqlite3'
HOST = os.environ.get('LL_HOST', '127.0.0.1')
PORT = int(os.environ.get('PORT', os.environ.get('LL_PORT', '8000')))
PASSWORD = os.environ.get('LL_ADMIN_PASSWORD', '')
if not PASSWORD:
    if HOST not in ('127.0.0.1', 'localhost', '::1'):
        raise SystemExit('Refusing to listen publicly without LL_ADMIN_PASSWORD. Set it to a long unique passphrase.')
    PASSWORD = secrets.token_urlsafe(14)
    print('LOCAL DEMO PASSWORD (generated at startup):', PASSWORD, flush=True)
if len(PASSWORD) < 12:
    raise SystemExit('LL_ADMIN_PASSWORD must contain at least 12 characters.')
AUTH_SECRET = secrets.token_bytes(32)
LOGIN_FAILS: dict[str, list[float]] = {}
LOGIN_LOCK = threading.Lock()
ALLOWED_LANG = {'mansaka', 'mixed', 'not_mansaka', 'unsure'}
ALLOWED_SENT = {'Positive', 'Negative', 'Neutral'}
COOKIE_NAME = 'll_admin_session'
COOKIE_LIFE = 8 * 3600


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def connect():
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA busy_timeout=10000')
    return db


def init_db():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('''
          CREATE TABLE IF NOT EXISTS posts (
            post_id TEXT PRIMARY KEY,
            source_json TEXT NOT NULL,
            imported_at TEXT NOT NULL
          );
          CREATE TABLE IF NOT EXISTS validations (
            post_id TEXT PRIMARY KEY REFERENCES posts(post_id) ON DELETE CASCADE,
            human_language TEXT,
            sentiment TEXT,
            notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 1
          );
          CREATE TABLE IF NOT EXISTS validation_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            human_language TEXT,
            sentiment TEXT,
            notes TEXT NOT NULL,
            status TEXT NOT NULL,
            changed_at TEXT NOT NULL
          );
        ''')
        n = db.execute('SELECT COUNT(*) FROM posts').fetchone()[0]
        if n == 0 and SEED.is_file():
            with SEED.open('r', encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            saved = 0
            for r in rows:
                pid = (r.get('research_post_id') or '').strip()
                if not pid or not (r.get('anonymized_text') or r.get('cleaned_text')):
                    continue
                db.execute('INSERT OR IGNORE INTO posts VALUES (?, ?, ?)', (pid, json.dumps(r, ensure_ascii=False), now()))
                saved += 1
            print(f'Loaded {saved} candidate posts from CSV.', flush=True)


def token_for(ts):
    payload = str(ts)
    sig = hmac.new(AUTH_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f'{payload}.{sig}'


def authenticated(headers):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get('Cookie', ''))
        raw = cookie[COOKIE_NAME].value
        ts, sig = raw.rsplit('.', 1)
        if abs(time.time() - int(ts)) > COOKIE_LIFE:
            return False
        expected = hmac.new(AUTH_SECRET, ts.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except (KeyError, ValueError, TypeError):
        return False


def post_items():
    with connect() as db:
        rows = db.execute('''
          SELECT p.source_json, v.human_language, v.sentiment, v.notes, v.status,
                 v.updated_at, v.revision
          FROM posts p LEFT JOIN validations v ON p.post_id = v.post_id ORDER BY p.rowid
        ''').fetchall()
    out = []
    for r in rows:
        source = json.loads(r['source_json'])
        source.update({
            'human_language': r['human_language'],
            'human_sentiment': r['sentiment'],
            'human_notes': r['notes'] or '',
            'human_status': r['status'] or 'pending',
            'validated_at': r['updated_at'],
            'revision': r['revision'] or 0,
        })
        out.append(source)
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = 'LumadLinguaValidator/1.0'
    def log_message(self, format, *args):
        print('%s [%s] %s' % (self.client_address[0], self.log_date_time_string(), format % args), flush=True)

    def security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

    def respond(self, status, data, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(data, (dict, list)):
            raw = json.dumps(data, ensure_ascii=False).encode('utf-8')
        elif isinstance(data, str):
            raw = data.encode('utf-8')
        else:
            raw = data
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.security_headers()
        for name, value in (extra or []):
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(raw)

    def error(self, status, message):
        self.respond(status, {'error': message})

    def path_part(self):
        return urlparse(self.path).path

    def request_json(self, max_size=6_000_000):
        try:
            size = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= size <= max_size:
                return None
            data = self.rfile.read(size)
            return json.loads(data.decode('utf-8'))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            return None

    def require_auth(self):
        if not authenticated(self.headers):
            self.error(401, 'Admin access required. Enter your access password.')
            return False
        return True

    def require_csrf(self):
        if self.headers.get('X-Lumad-Request') != '1':
            self.error(403, 'Missing request protection header.')
            return False
        return True

    def do_GET(self):
        path = self.path_part()
        if path == '/api/session':
            return self.respond(200, {'authenticated': authenticated(self.headers), 'offline': False})
        if path.startswith('/api/'):
            if not self.require_auth():
                return
            if path == '/api/posts':
                return self.respond(200, {'posts': post_items()})
            if path == '/api/export':
                return self.export_csv()
            return self.error(404, 'Unknown endpoint')
        allowed = {'/': 'index.html', '/index.html': 'index.html', '/style.css': 'style.css', '/app.js': 'app.js', '/favicon.svg': 'favicon.svg'}
        filename = allowed.get(path)
        if filename is None:
            return self.error(404, 'Not found')
        target = STATIC / filename
        if not target.is_file():
            return self.error(404, 'Static file missing')
        ctype = {'html': 'text/html', 'css': 'text/css', 'js': 'text/javascript', 'svg': 'image/svg+xml'}.get(target.suffix[1:], 'application/octet-stream')
        return self.respond(200, target.read_bytes(), ctype+'; charset=utf-8')

    def do_POST(self):
        path = self.path_part()
        if not path.startswith('/api/'):
            return self.error(404, 'Not found')
        if not self.require_csrf():
            return
        if path == '/api/login':
            body = self.request_json(4_000)
            if not isinstance(body, dict):
                return self.error(400, 'Invalid login request.')
            ip = self.client_address[0]
            with LOGIN_LOCK:
                hits = [x for x in LOGIN_FAILS.get(ip, []) if time.time() - x < 900]
                LOGIN_FAILS[ip] = hits
                if len(hits) >= 10:
                    return self.error(429, 'Too many attempts. Try again later.')
            if not hmac.compare_digest(str(body.get('password', '')), PASSWORD):
                with LOGIN_LOCK:
                    LOGIN_FAILS[ip].append(time.time())
                return self.error(401, 'Incorrect access password.')
            with LOGIN_LOCK:
                LOGIN_FAILS.pop(ip, None)
            secure = (os.environ.get('LL_SECURE_COOKIES') == '1' or self.headers.get('X-Forwarded-Proto', '').split(',')[0].strip() == 'https')
            cookie = f'{COOKIE_NAME}={token_for(int(time.time()))}; HttpOnly; SameSite=Strict; Path=/; Max-Age={COOKIE_LIFE}' + ('; Secure' if secure else '')
            return self.respond(200, {'authenticated': True}, extra=[('Set-Cookie', cookie)])
        if not self.require_auth():
            return
        if path == '/api/logout':
            return self.respond(200, {'ok': True}, extra=[('Set-Cookie', f'{COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')])
        if path == '/api/validation':
            return self.save_validation()
        if path == '/api/import':
            return self.import_csv()
        return self.error(404, 'Unknown endpoint')

    def save_validation(self):
        body = self.request_json(100_000)
        if not isinstance(body, dict):
            return self.error(400, 'Expected JSON validation request.')
        pid = str(body.get('post_id', '')).strip()
        lang = body.get('language')
        sentiment = body.get('sentiment')
        notes = str(body.get('notes') or '')[:5000]
        skip = body.get('skip') is True
        if skip:
            status = 'skipped'
            lang = None
            sentiment = None
        elif lang not in ALLOWED_LANG:
            return self.error(400, 'Please select a language judgment.')
        elif lang in {'mansaka', 'mixed'}:
            if sentiment not in ALLOWED_SENT:
                return self.error(400, 'Please choose Positive, Negative, or Neutral.')
            status = 'validated'
        elif lang == 'not_mansaka':
            status = 'excluded'
            sentiment = None
        else:
            status = 'needs_review'
            sentiment = None
        timestamp = now()
        with connect() as db:
            if not db.execute('SELECT 1 FROM posts WHERE post_id=?', (pid,)).fetchone():
                return self.error(404, 'Post does not exist.')
            prev = db.execute('SELECT revision FROM validations WHERE post_id=?', (pid,)).fetchone()
            revision = int(prev['revision']) + 1 if prev else 1
            db.execute('''INSERT INTO validations (post_id, human_language, sentiment, notes, status, updated_at, revision)
                          VALUES (?, ?, ?, ?, ?, ?, ?)
                          ON CONFLICT(post_id) DO UPDATE SET
                            human_language=excluded.human_language, sentiment=excluded.sentiment,
                            notes=excluded.notes, status=excluded.status, updated_at=excluded.updated_at,
                            revision=excluded.revision''', (pid, lang, sentiment, notes, status, timestamp, revision))
            db.execute('INSERT INTO validation_history (post_id, revision, human_language, sentiment, notes, status, changed_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (pid, revision, lang, sentiment, notes, status, timestamp))
        self.respond(200, {'ok': True, 'post_id': pid, 'status': status, 'language': lang, 'sentiment': sentiment, 'notes': notes, 'validated_at': timestamp, 'revision': revision})

    def import_csv(self):
        body = self.request_json(6_000_000)
        if not isinstance(body, dict) or not isinstance(body.get('csv'), str):
            return self.error(400, 'Expected {csv: "..."}.')
        try:
            f = io.StringIO(body['csv'].lstrip('\ufeff'))
            reader = csv.DictReader(f)
            if not reader.fieldnames or not {'research_post_id', 'cleaned_text'}.issubset(set(reader.fieldnames)):
                return self.error(400, 'CSV needs research_post_id and cleaned_text columns.')
            new_rows = list(reader)
            if len(new_rows) > 15000:
                return self.error(400, 'Too many rows. Maximum 15,000 per import.')
            inserted = duplicates = invalid = 0
            with connect() as db:
                for r in new_rows:
                    pid = (r.get('research_post_id') or '').strip()
                    txt = (r.get('anonymized_text') or r.get('cleaned_text') or '').strip()
                    if not pid or not txt or len(pid) > 200 or len(txt) > 100_000:
                        invalid += 1
                        continue
                    before = db.total_changes
                    db.execute('INSERT OR IGNORE INTO posts VALUES (?, ?, ?)', (pid, json.dumps(r, ensure_ascii=False), now()))
                    if db.total_changes > before:
                        inserted += 1
                    else:
                        duplicates += 1
            return self.respond(200, {'ok': True, 'inserted': inserted, 'duplicates': duplicates, 'invalid': invalid})
        except (csv.Error, UnicodeError) as e:
            return self.error(400, 'Could not parse CSV: ' + str(e)[:180])

    def export_csv(self):
        mode = parse_qs(urlparse(self.path).query).get('mode', ['training'])[0]
        if mode not in {'training', 'all', 'history'}:
            return self.error(400, 'Unknown export mode.')
        out = io.StringIO(newline='')
        w = csv.writer(out)
        if mode == 'history':
            w.writerow(['research_post_id', 'revision', 'human_language_label', 'human_sentiment_label', 'validation_notes', 'human_review_status', 'changed_at'])
            with connect() as db:
                rows = db.execute('SELECT * FROM validation_history ORDER BY id').fetchall()
            for r in rows:
                w.writerow([r['post_id'], r['revision'], r['human_language'] or '', r['sentiment'] or '', r['notes'], r['status'], r['changed_at']])
        elif mode == 'training':
            w.writerow(['research_post_id', 'cleaned_text', 'anonymized_text', 'sentiment_label', 'language_verified', 'validated_at'])
            for r in post_items():
                if r['human_status'] == 'validated' and r['human_language'] in {'mansaka', 'mixed'} and r['human_sentiment'] in ALLOWED_SENT:
                    w.writerow([r.get('research_post_id', ''), r.get('cleaned_text', ''), r.get('anonymized_text', ''), r['human_sentiment'], r['human_language'], r['validated_at']])
        else:
            items = post_items()
            original_cols = list(dict.fromkeys(k for r in items for k in r if k not in {'human_language', 'human_sentiment', 'human_notes', 'human_status', 'validated_at', 'revision'})) if items else ['research_post_id']
            additions = ['human_language_label', 'human_sentiment_label', 'validation_notes', 'human_review_status', 'validated_at', 'review_revision']
            w.writerow(original_cols + additions)
            for r in items:
                w.writerow([r.get(k, '') for k in original_cols] + [r['human_language'] or '', r['human_sentiment'] or '', r['human_notes'], r['human_status'], r['validated_at'] or '', r['revision']])
        raw = ('\ufeff' + out.getvalue()).encode('utf-8')
        filename = 'lumad_lingua_' + {'training': 'validated_training', 'all': 'full_reviewed_dataset', 'history': 'validation_history'}[mode] + '.csv'
        return self.respond(200, raw, 'text/csv; charset=utf-8', extra=[('Content-Disposition', f'attachment; filename="{filename}"')])


def main():
    init_db()
    print(f'Lumad Lingua Validator available at http://{HOST}:{PORT}', flush=True)
    with ThreadingHTTPServer((HOST, PORT), Handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
