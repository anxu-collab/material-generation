"""Persistent, single-analysis queue and recoverable library projections. No network."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid


STAGES = ('inspect_media', 'analyze_video', 'validate_catalog', 'await_catalog_review', 'publish_catalog')
_LOCK = threading.RLock()
_LOCAL = threading.local()


def utc():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone required')
    return result.astimezone(timezone.utc)


def safe_path(root, relative):
    root = Path(root).resolve()
    part = Path(relative)
    if part.is_absolute() or part.drive or '..' in part.parts:
        raise ValueError('relative project path required')
    result = (root / part).resolve()
    if not result.is_relative_to(root):
        raise ValueError('path escapes project')
    return result


@contextmanager
def _db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS calls (authorization_id TEXT, task_id TEXT, body TEXT)')
        con.execute('BEGIN IMMEDIATE')
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _rows(con):
    return [json.loads(r['body']) for r in con.execute('SELECT body FROM tasks ORDER BY rowid')]


def _save(con, row):
    con.execute('INSERT OR REPLACE INTO tasks VALUES (?,?)', (row['task_id'], canonical(row)))


def _get(con, task_id):
    row = con.execute('SELECT body FROM tasks WHERE task_id=?', (task_id,)).fetchone()
    if row is None:
        raise ValueError('unknown task')
    return json.loads(row['body'])


def _owned(con, task_id, token, now):
    row = _get(con, task_id)
    if row['state'] != 'running' or row.get('claim_token') != token or instant(row['lease_until']) <= instant(now):
        raise ValueError('lost or expired task lease')
    return row


def enqueue(db, binding, stages):
    if not stages or any(s not in STAGES for s in stages) or len(set(stages)) != len(stages):
        raise ValueError('invalid stages')
    if [STAGES.index(s) for s in stages] != sorted(STAGES.index(s) for s in stages):
        raise ValueError('stages out of order')
    task_id = digest({'binding': binding, 'stages': stages})
    with _db(db) as con:
        if con.execute('SELECT 1 FROM tasks WHERE task_id=?', (task_id,)).fetchone():
            return task_id
        _save(con, {'task_id': task_id, 'binding': binding, 'stages': stages, 'stage_index': 0,
                    'stage': stages[0], 'state': 'queued', 'results': {}, 'attempts': 0,
                    'in_flight': False, 'events': [], 'created_utc': utc(), 'reason': ''})
    return task_id


def list_tasks(db):
    if not Path(db).exists():
        return []
    with _db(db) as con:
        return _rows(con)


def get_task(db, task_id):
    with _db(db) as con:
        return _get(con, task_id)


def claim_next(db, owner_id, now_utc):
    now = instant(now_utc)
    with _db(db) as con:
        rows = _rows(con)
        # One active analysis task across workers, not merely per process.
        if any((r['state'] == 'running' and instant(r['lease_until']) > now)
               or (r['state'] == 'uncertain' and r['in_flight']) for r in rows):
            return None
        for row in rows:
            if row['state'] not in ('queued', 'retry_wait'):
                continue
            if row.get('retry_at') and instant(row['retry_at']) > now:
                continue
            row.update(state='running', owner_id=owner_id, claim_token=uuid.uuid4().hex,
                       lease_until=(now + timedelta(seconds=120)).isoformat(), attempts=row['attempts'] + 1)
            _save(con, row)
            return row
    return None


def heartbeat(db, task_id, token):
    now = utc()
    with _db(db) as con:
        row = _owned(con, task_id, token, now)
        row['lease_until'] = (instant(now) + timedelta(seconds=120)).isoformat()
        _save(con, row)


def mark_inflight(db, task_id, token, now_utc):
    with _db(db) as con:
        row = _owned(con, task_id, token, now_utc)
        if row.get('cancel_requested'):
            raise ValueError('cancellation requested')
        row['in_flight'] = True
        _save(con, row)


def reserve_call(db, task_id, token, authorization, profile, now_utc):
    """Durably reserve an explicit bounded authorization before any paid request."""
    with _db(db) as con:
        row = _owned(con, task_id, token, now_utc)
        if row.get('cancel_requested'):
            raise ValueError('cancellation requested')
        a = authorization
        if (not a.get('approved_by') or not a.get('consent') or not a.get('authorization_id')
                or instant(a['expires_utc']) <= instant(now_utc)
                or a.get('provider') != profile.get('provider') or a.get('model') != profile.get('model')
                or row['binding']['product_id'] != a.get('product_id')
                or row['binding']['clip_id'] not in a.get('clip_ids', [])
                or type(a.get('max_calls')) is not int or not 1 <= a['max_calls'] <= 3
                or not isinstance(a.get('budget_cny'), (int, float)) or a['budget_cny'] <= 0):
            raise ValueError('invalid or out-of-scope authorization')
        if row['stage'] != 'analyze_video' or row['in_flight']:
            raise ValueError('call already in flight or wrong stage')
        prior = con.execute('SELECT body FROM calls WHERE authorization_id=?', (a['authorization_id'],)).fetchall()
        if len(prior) >= a['max_calls'] or any(json.loads(r['body'])['authorization_sha256'] != digest(a) for r in prior):
            raise ValueError('authorization exhausted or changed')
        # Do not infer a provider price from the caller's budget.
        if a.get('price_known') is not True or not isinstance(a.get('max_cost_per_call_cny'), (int, float)):
            raise ValueError('bounded per-call price evidence required')
        cost = a['max_cost_per_call_cny']
        if cost <= 0 or (len(prior) + 1) * cost > a['budget_cny']:
            raise ValueError('budget exceeded')
        record = {'authorization_sha256': digest(a), 'at_utc': now_utc,
                  'reserved_cny': cost, 'request_key': task_id, 'state': 'in_flight'}
        con.execute('INSERT INTO calls VALUES (?,?,?)', (a['authorization_id'], task_id, canonical(record)))
        row.update(in_flight=True, authorization_id=a['authorization_id'])
        _save(con, row)
    return record


def advance(db, task_id, claim_token, event, now_utc):
    with _db(db) as con:
        row = _owned(con, task_id, claim_token, now_utc)
        kind = event.get('type')
        states = {'wait_authorization': 'waiting_authorization', 'wait_review': 'waiting_review',
                  'uncertain': 'uncertain', 'fatal_error': 'failed', 'cancel': 'cancelled', 'stale': 'stale'}
        if row.get('cancel_requested'):
            if kind == 'stage_done':
                row['results'][row['stage']] = event['result']
                row['in_flight'] = False
            row['state'] = 'uncertain' if row['in_flight'] else 'cancelled'
            row['reason'] = 'cancelled_after_handler_exit'
            row['events'].append({'at_utc': now_utc, 'type': 'cancel', 'state': row['state']})
            row.pop('claim_token', None)
            _save(con, row)
            return row
        if kind == 'stage_done':
            if row['stage'] == 'await_catalog_review':
                raise ValueError('review requires explicit review decision')
            row['results'][row['stage']] = event['result']
            row['stage_index'] += 1
            row.update(in_flight=False, attempts=0, retry_at=None)
            if row['stage_index'] == len(row['stages']):
                row['state'] = 'completed'
            else:
                row.update(stage=row['stages'][row['stage_index']], state='queued')
        elif kind == 'retryable_error':
            if row['in_flight']:
                row['state'] = 'uncertain'
            elif row['attempts'] >= 3:
                row['state'] = 'failed'
            else:
                row['state'] = 'retry_wait'
                row['retry_at'] = (instant(now_utc) + timedelta(seconds=(5, 20)[row['attempts'] - 1])).isoformat()
        elif kind in states:
            row['state'] = 'uncertain' if row['in_flight'] and kind in ('fatal_error', 'cancel') else states[kind]
        else:
            raise ValueError('invalid task transition')
        row['reason'] = event.get('reason', '')
        row['events'].append({'at_utc': now_utc, 'type': kind, 'state': row['state']})
        row.pop('claim_token', None)
        _save(con, row)
        return row


def recover(db, now_utc):
    recovered = []
    with _db(db) as con:
        for row in _rows(con):
            if row['state'] == 'running' and instant(row['lease_until']) <= instant(now_utc):
                state = ('uncertain' if row['in_flight'] or row.get('publication_started') else
                         'cancelled' if row.get('cancel_requested') else 'queued')
                row.update(state=state, reason='expired_lease')
                row.pop('claim_token', None)
                _save(con, row)
                recovered.append(row['task_id'])
    return {'recovered': recovered}


def cancel(db, task_id):
    with _db(db) as con:
        row = _get(con, task_id)
        if row['state'] == 'completed':
            raise ValueError('completed task cannot be cancelled')
        if row['state'] == 'running':
            # A synchronous handler still owns execution until it exits.
            row.update(cancel_requested=True, reason='cancellation_requested')
        else:
            row.update(state='uncertain' if row['in_flight'] or row.get('publication_started') else 'cancelled',
                       reason='user_cancelled')
            row.pop('claim_token', None)
        _save(con, row)


def resume_authorized(db):
    with _db(db) as con:
        for row in _rows(con):
            if row['state'] == 'waiting_authorization':
                row.update(state='queued', attempts=0)
                _save(con, row)


def record_review(db, task_id, catalog, accepted):
    with _db(db) as con:
        row = _get(con, task_id)
        if row['state'] != 'waiting_review' or row['stage'] != 'await_catalog_review':
            raise ValueError('task not waiting for review')
        if accepted:
            if catalog.get('state') != 'reviewed' or catalog.get('binding') != row['binding']:
                raise ValueError('review binding mismatch')
            row['results']['await_catalog_review'] = catalog
            row['stage_index'] += 1
            row.update(stage=row['stages'][row['stage_index']], state='queued', attempts=0)
        else:
            row.update(state='failed', reason='catalog_rejected')
        _save(con, row)


def finish_publication(db, claimed, root, effect):
    """Serialize cancellation and publication; the terminal queue update commits with the effect guard."""
    with library_write_lock(root):
        with _db(db) as con:
            row = _owned(con, claimed['task_id'], claimed['claim_token'], utc())
            if row['stage'] != 'publish_catalog':
                raise ValueError('publication stage required')
            if not row.get('cancel_requested'):
                # Durable marker before a process could die during file replacements.
                row['publication_started'] = True
                _save(con, row)
        with _db(db) as con:
            row = _owned(con, claimed['task_id'], claimed['claim_token'], utc())
            if row.get('cancel_requested'):
                row.update(state='cancelled', reason='cancelled_before_publication', publication_started=False)
            else:
                try:
                    result = effect()
                except (ValueError, OSError, RuntimeError) as exc:
                    # The redo journal may exist: do not report a successful cancellation.
                    row.update(state='uncertain', reason='publication_' + type(exc).__name__)
                else:
                    row['results']['publish_catalog'] = result
                    row.update(state='completed', stage_index=len(row['stages']),
                               publication_started=False, reason='')
            row.pop('claim_token', None)
            row['events'].append({'at_utc': utc(), 'type': 'publication_finished', 'state': row['state']})
            _save(con, row)
    return {'type': 'committed', 'task_id': row['task_id']}


def run_once(db, handlers, owner_id):
    recover(db, utc())
    row = claim_next(db, owner_id, utc())
    if row is None:
        return {'state': 'idle'}
    stop = threading.Event()
    def keep_alive():
        while not stop.wait(20):
            try:
                heartbeat(db, row['task_id'], row['claim_token'])
            except ValueError:
                return
    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()
    try:
        if row['stage'] == 'await_catalog_review':
            event = {'type': 'wait_review'}
        elif row['stage'] not in handlers:
            event = {'type': 'wait_authorization', 'reason': 'capability_unverified'}
        else:
            event = handlers[row['stage']](row)
    except FileNotFoundError as exc:
        event = {'type': 'fatal_error', 'reason': type(exc).__name__}
    except OSError as exc:
        event = {'type': 'retryable_error', 'reason': type(exc).__name__}
    except (ValueError, RuntimeError) as exc:
        # Raw supplier errors may contain credentials; store only the exception class.
        event = {'type': 'fatal_error', 'reason': type(exc).__name__}
    finally:
        stop.set()
        thread.join(timeout=1)
    if event.get('type') == 'committed' and event.get('task_id') == row['task_id'] and row['stage'] == 'publish_catalog':
        return get_task(db, row['task_id'])
    return advance(db, row['task_id'], row['claim_token'], event, utc())


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.tmp-' + uuid.uuid4().hex)
    with temp.open('wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _journal_path(root):
    return safe_path(root, 'runtime/material-analysis/library-transaction.json')


def _recover_library(root):
    journal = _journal_path(root)
    if not journal.exists():
        return
    doc = json.loads(journal.read_text(encoding='utf-8'))
    if doc['state'] == 'committed':
        return
    for item in doc['writes']:
        path = safe_path(root, item['path'])
        _atomic(path, base64.b64decode(item['data'], validate=True))
    doc['state'] = 'committed'
    _atomic(journal, canonical(doc).encode())


@contextmanager
def library_write_lock(root):
    root = Path(root).resolve()
    with _LOCK:
        depths = getattr(_LOCAL, 'depths', {})
        _LOCAL.depths = depths
        key = str(root)
        if depths.get(key):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        lock = safe_path(root, 'runtime/material-analysis/library.lock')
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open('a+b') as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
            depths[key] = 1
            try:
                _recover_library(root)
                yield
            finally:
                depths.pop(key, None)
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)


def project_documents(root, documents):
    """Caller holds library_write_lock; redo journal survives partial replacements."""
    if not getattr(_LOCAL, 'depths', {}).get(str(Path(root).resolve())):
        raise ValueError('library lock required')
    writes = []
    backup = safe_path(root, 'runtime/material-analysis/backups/' + uuid.uuid4().hex)
    for relative, obj in documents.items():
        path = safe_path(root, relative)
        if path.exists():
            old = path.read_bytes()
            target = backup / relative
            _atomic(target, old)
        writes.append({'path': relative, 'data': base64.b64encode((canonical(obj) + '\n').encode()).decode()})
    journal = {'state': 'prepared', 'writes': writes, 'at_utc': utc()}
    _atomic(_journal_path(root), canonical(journal).encode())
    _recover_library(root)
