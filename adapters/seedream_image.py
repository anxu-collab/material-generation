"""Volcengine Ark Seedream image channel (seedream-image-v1).

Purpose: produce still reference images (product / sensation / "effect" visuals) that feed the
H3 video channels (AutoDL z0902 ref_image_0..5, official MiniMax reference_image). Independent of
the H3 adapters: imports none of them.

Hard guards
- Default is a plan (dry-run): zero network, zero key access.
- A paid call needs a human batch authorization record (total image cap + expiry + consent).
  Every attempt is written to an append-only ledger BEFORE the HTTP call; timeouts and unknown
  outcomes count as spent. The cap is enforced across runs per authorization_id.
- The key (ARK_API_KEY) comes from the environment, else from the project-local git-ignored
  config/seedream.local.env (installed there at the user's request 2026-10-11). It is never
  printed, written to a ledger/result/evidence file, or put in an exception message.
- The model id is pinned. No fallback to any other model. No automatic retry (a retry may bill twice).
- Efficacy-claim markers from config/koc_material_policy.json TAG a prompt (claim_risk) and never
  block it; the downstream editor decides use (user decision 2026-10-11, "all_tagged").
- Returned image URLs are short-lived vendor URLs: the lifetime and AutoDL reachability are
  UNVERIFIED. The image is also downloaded and hashed so the lab owns the bytes.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

LAB_ROOT = Path('F:/codex/laox_director_lab')
STORE_DIR = LAB_ROOT / 'runtime' / 'seedream-image-v1'
OUTPUT_DIR = LAB_ROOT / 'output'          # the one place to look: output/<产品名>/
PRODUCT_FOLDER = {'laox-cable': '磁吸理线器', 'laox-stand': '笔记本支架',
                  'laox-vacuum': '桌面迷你吸尘器', 'tianyiqing': '天益清'}
POLICY_PATH = LAB_ROOT / 'config' / 'koc_material_policy.json'

ENDPOINT = 'https://ark.cn-beijing.volces.com/api/v3/images/generations'
ENDPOINT_HOST = 'ark.cn-beijing.volces.com'
MODEL = 'doubao-seedream-5-0-flash-260915'
API_KEY_ENV = 'ARK_API_KEY'
KEY_ENV_FILE = LAB_ROOT / 'config' / 'seedream.local.env'   # project-local, git-ignored
DEFAULT_SIZE = '1088x1920'                       # 1080p portrait matched to z0902 1088p竖 (user 2026-10-11)
VERIFIED_SIZES = ('1080x1920', '1088x1920')      # real calls 2026-10-11 returned exactly these sizes
SIZE_PATTERN = re.compile(r'^\d{3,4}x\d{3,4}$')
PROMPT_MAX_CHARS = 4000                          # local cap; vendor limit UNVERIFIED
MAX_ITEMS_PER_RUN = 50
MAX_IMAGES_PER_AUTHORIZATION = 200
HTTP_TIMEOUT_S = 120.0
IMAGE_MAX_BYTES = 30 * 1024 * 1024
IMAGE_MAGICS = {b'\xff\xd8\xff': 'jpg', b'\x89PNG': 'png', b'RIFF': 'webp'}
AUTH_SCHEMA = 'lab-seedream-batch-authorization/v1'
SAFE_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
BEARER = re.compile(r'^[A-Za-z0-9._~+/=-]{8,512}$')
PASSTHROUGH_FIELDS = ('product_id', 'purpose', 'category', 'shot_id', 'visual_mode')
REF_ROOT = LAB_ROOT / 'cases'
MAX_REF_IMAGES = 4
REF_IMAGE_MAX_BYTES = 8 * 1024 * 1024


class SeedreamError(RuntimeError):
    pass


def _refuse(code, detail=''):
    raise SeedreamError('%s%s' % (code, (': ' + detail) if detail else ''))


def _utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode('utf-8')).hexdigest()


def _store(store):
    return Path(store) if store else STORE_DIR


# ----------------------------------------------------------------- key
def parse_env_key(path):
    """Read only the ARK_API_KEY assignment; every other line is ignored unread."""
    value = None
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            match = re.match(r'\s*%s\s*=\s*(.*)$' % API_KEY_ENV, line)
            if match:
                value = match.group(1).strip().strip('"').strip("'")
    return value


def resolve_key(environ=None, env_file=None):
    """Environment first, then the project-local git-ignored key file."""
    env = os.environ if environ is None else environ
    key = (env.get(API_KEY_ENV) or '').strip()
    path = Path(env_file or KEY_ENV_FILE)
    if not key and path.exists():
        key = parse_env_key(path) or ''
    if not key:
        _refuse('KEY_MISSING', '%s is not set in the environment or in %s'
                % (API_KEY_ENV, path.name))
    if not BEARER.match(key):
        _refuse('KEY_MALFORMED', 'credential has an unexpected shape (value not shown)')
    return key


# ----------------------------------------------------------------- spec
def claim_markers(policy_path=None):
    try:
        with open(policy_path or POLICY_PATH, 'r', encoding='utf-8') as handle:
            markers = json.load(handle).get('unverified_claim_markers') or []
    except (OSError, ValueError):
        return []
    return [m for m in markers if isinstance(m, str) and m]


def validate_item(item, markers=()):
    if not isinstance(item, dict):
        _refuse('ITEM_INVALID', 'item must be an object')
    item_id = item.get('item_id')
    if not isinstance(item_id, str) or not SAFE_ID.match(item_id):
        _refuse('ITEM_ID_INVALID', 'item_id must match %s' % SAFE_ID.pattern)
    prompt = item.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > PROMPT_MAX_CHARS:
        _refuse('PROMPT_INVALID', '%s: prompt must be 1..%d chars' % (item_id, PROMPT_MAX_CHARS))
    size = item.get('size', DEFAULT_SIZE)
    if not isinstance(size, str) or not (size in VERIFIED_SIZES or size == '2K'
                                         or SIZE_PATTERN.match(size)):
        _refuse('SIZE_INVALID', '%s: size must be 2K or WxH' % item_id)
    refs = item.get('ref_images') or []
    if not isinstance(refs, list) or len(refs) > MAX_REF_IMAGES:
        _refuse('REF_IMAGES_INVALID', '%s: at most %d reference images' % (item_id, MAX_REF_IMAGES))
    ref_data, ref_shas = [], []
    for ref in refs:
        data, mime = _read_ref_image(ref, item_id)
        ref_shas.append(_sha(data))
        ref_data.append('data:%s;base64,%s' % (mime, base64.b64encode(data).decode('ascii')))
    body = {'model': MODEL, 'prompt': prompt, 'size': size,
            'response_format': 'url', 'watermark': False}
    identity = dict(body, ref_image_sha256=ref_shas)
    if ref_data:
        body['image'] = ref_data if len(ref_data) > 1 else ref_data[0]
    extra = {k: item[k] for k in PASSTHROUGH_FIELDS
             if isinstance(item.get(k), str) and item[k]}
    return {'item_id': item_id, 'body': body, 'fingerprint': _sha(_canonical(identity)),
            'size_verified': size in VERIFIED_SIZES, 'ref_image_sha256': ref_shas,
            'ref_images': [str(r) for r in refs],
            'claim_risk_markers': [m for m in markers if m in prompt], 'meta': extra}


def _read_ref_image(ref, item_id):
    if not isinstance(ref, str):
        _refuse('REF_IMAGE_INVALID', item_id)
    path = Path(ref) if Path(ref).is_absolute() else LAB_ROOT / ref
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        _refuse('REF_IMAGE_MISSING', '%s: %s' % (item_id, ref))
    if not resolved.is_relative_to(REF_ROOT.resolve()):
        _refuse('REF_IMAGE_OUTSIDE_CASES', '%s: references must live under cases/' % item_id)
    data = resolved.read_bytes()
    if len(data) > REF_IMAGE_MAX_BYTES:
        _refuse('REF_IMAGE_TOO_LARGE', item_id)
    mime = 'image/jpeg' if data.startswith(b'\xff\xd8\xff') else \
        'image/png' if data.startswith(b'\x89PNG') else None
    if not mime:
        _refuse('REF_IMAGE_FORMAT', '%s: jpg/png only' % item_id)
    return data, mime


def load_jobs(path, markers=None):
    with open(path, 'r', encoding='utf-8') as handle:
        spec = json.load(handle)
    batch_id = spec.get('batch_id')
    if not isinstance(batch_id, str) or not SAFE_ID.match(batch_id):
        _refuse('BATCH_ID_INVALID')
    items = spec.get('items')
    if not isinstance(items, list) or not items or len(items) > MAX_ITEMS_PER_RUN:
        _refuse('ITEMS_INVALID', '1..%d items required' % MAX_ITEMS_PER_RUN)
    marks = claim_markers() if markers is None else markers
    planned = [validate_item(i, marks) for i in items]
    ids = [p['item_id'] for p in planned]
    if len(set(ids)) != len(ids):
        _refuse('ITEM_ID_DUPLICATE')
    return batch_id, planned


def plan(jobs_path, markers=None):
    """Dry-run. No network, no key."""
    batch_id, planned = load_jobs(jobs_path, markers)
    return {'mode': 'dry_run', 'batch_id': batch_id, 'model': MODEL, 'endpoint': ENDPOINT,
            'network_calls': 0, 'paid_calls': 0,
            'items': [{'item_id': p['item_id'], 'fingerprint': p['fingerprint'],
                       'size': p['body']['size'], 'size_verified': p['size_verified'],
                       'claim_risk_markers': p['claim_risk_markers']} for p in planned]}


# ----------------------------------------------------------------- authorization + ledger
def load_authorization(path, now=None):
    with open(path, 'r', encoding='utf-8') as handle:
        rec = json.load(handle)
    if rec.get('schema') != AUTH_SCHEMA:
        _refuse('AUTH_SCHEMA', 'expected %s' % AUTH_SCHEMA)
    if rec.get('human_authorized') is not True:
        _refuse('AUTH_NOT_HUMAN', 'human_authorized must be true')
    if not isinstance(rec.get('authorization_id'), str) or not SAFE_ID.match(rec['authorization_id']):
        _refuse('AUTH_ID_INVALID')
    if rec.get('model') != MODEL:
        _refuse('AUTH_MODEL', 'authorization must name %s' % MODEL)
    cap = rec.get('max_images')
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= MAX_IMAGES_PER_AUTHORIZATION:
        _refuse('AUTH_CAP', 'max_images must be an int in 1..%d' % MAX_IMAGES_PER_AUTHORIZATION)
    consent = rec.get('consent')
    if not isinstance(consent, str) or len(consent.strip()) < 8:
        _refuse('AUTH_CONSENT', 'consent provenance text required')
    try:
        expires = datetime.strptime(rec.get('expires_utc', ''), '%Y-%m-%dT%H:%M:%SZ') \
            .replace(tzinfo=timezone.utc)
    except ValueError:
        _refuse('AUTH_EXPIRY', 'expires_utc must be YYYY-MM-DDTHH:MM:SSZ')
    if expires <= (now or datetime.now(timezone.utc)):
        _refuse('AUTH_EXPIRED')
    return rec


def _ledger_path(store):
    return _store(store) / 'ledger.jsonl'


def _ledger_read(store):
    path = _ledger_path(store)
    if not path.exists():
        return []
    with open(path, 'r', encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _ledger_append(store, entry):
    path = _ledger_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write(_canonical(entry) + '\n')
        handle.flush()
        os.fsync(handle.fileno())


def attempts_used(authorization_id, store=None):
    return sum(1 for e in _ledger_read(store)
               if e.get('event') == 'attempt' and e.get('authorization_id') == authorization_id)


def _existing_fingerprints(store):
    return {(e['item_id'], e['fingerprint']) for e in _ledger_read(store)
            if e.get('event') == 'success'}


def _next_version(item_id, store):
    return 1 + sum(1 for e in _ledger_read(store)
                   if e.get('event') == 'attempt' and e.get('item_id') == item_id)


# ----------------------------------------------------------------- transport
def default_transport(url, headers, data, timeout):
    request = urllib.request.Request(url, data=data, headers=headers, method='POST') \
        if data is not None else urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(IMAGE_MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        return error.code, error.read(1 << 20)


def _check_https(url, hosts_suffix):
    match = re.match(r'^https://([A-Za-z0-9.-]+)(/|$)', url or '')
    if not match or not match.group(1).endswith(hosts_suffix):
        _refuse('URL_REJECTED', 'host must be https and end with %s' % hosts_suffix)
    return match.group(1)


def _generate_one(planned, key, transport):
    status, raw = transport(ENDPOINT, {'Content-Type': 'application/json',
                                       'Authorization': 'Bearer ' + key},
                            json.dumps(planned['body']).encode('utf-8'), HTTP_TIMEOUT_S)
    try:
        resp = json.loads(raw or b'{}')
    except ValueError:
        resp = {}
    if status != 200:
        err = resp.get('error') or {}
        _refuse('API_ERROR', 'http=%s code=%s message=%s'
                % (status, err.get('code'), str(err.get('message'))[:300].replace(key, '***')))
    data = (resp.get('data') or [{}])[0]
    url = data.get('url')
    host = _check_https(url, '.volces.com')
    dl_status, image = transport(url, {}, None, HTTP_TIMEOUT_S)
    if dl_status != 200 or not image or len(image) > IMAGE_MAX_BYTES:
        _refuse('DOWNLOAD_FAILED', 'http=%s bytes=%s' % (dl_status, len(image or b'')))
    ext = next((e for m, e in IMAGE_MAGICS.items() if image.startswith(m)), None)
    if not ext:
        _refuse('IMAGE_MAGIC', 'downloaded bytes are not jpg/png/webp')
    return {'usage': resp.get('usage'), 'model_returned': resp.get('model'),
            'size_field': data.get('size'), 'url_host': host, 'remote_url_ephemeral': url,
            'image': image, 'ext': ext}


# ----------------------------------------------------------------- run
def run(jobs_path, authorization_path, transport=None, store=None,
        environ=None, markers=None, env_file=None, now=None):
    batch_id, planned = load_jobs(jobs_path, markers)
    auth = load_authorization(authorization_path, now)
    store_dir = _store(store)
    done = _existing_fingerprints(store)
    todo = [p for p in planned if (p['item_id'], p['fingerprint']) not in done]
    remaining = auth['max_images'] - attempts_used(auth['authorization_id'], store)
    if len(todo) > remaining:
        _refuse('AUTH_CAP_EXCEEDED', 'need %d image(s), authorization has %d left; nothing was '
                'sent' % (len(todo), remaining))
    key = resolve_key(environ, env_file) if todo else None
    send = transport or default_transport
    images_dir = store_dir / 'images'
    results_dir = store_dir / 'results'
    images_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    report = {'batch_id': batch_id, 'model': MODEL, 'skipped_existing': len(planned) - len(todo),
              'results': [], 'failures': []}
    for item in todo:
        version = _next_version(item['item_id'], store)
        _ledger_append(store, {'event': 'attempt', 'authorization_id': auth['authorization_id'],
                               'batch_id': batch_id, 'item_id': item['item_id'],
                               'version': version, 'fingerprint': item['fingerprint'],
                               'utc': _utc()})
        try:
            out = _generate_one(item, key, send)
        except SeedreamError as error:
            _ledger_append(store, {'event': 'failure', 'authorization_id': auth['authorization_id'],
                                   'item_id': item['item_id'], 'version': version,
                                   'detail': str(error)[:400], 'utc': _utc()})
            report['failures'].append({'item_id': item['item_id'], 'detail': str(error)[:400]})
            continue                       # no retry: a retry may bill twice
        except Exception as error:         # transport/timeouts: outcome unknown, stays counted
            detail = ('OUTCOME_UNKNOWN: %s' % type(error).__name__)
            _ledger_append(store, {'event': 'failure', 'authorization_id': auth['authorization_id'],
                                   'item_id': item['item_id'], 'version': version,
                                   'detail': detail, 'utc': _utc()})
            report['failures'].append({'item_id': item['item_id'], 'detail': detail})
            continue
        stem = '%s.v%d' % (item['item_id'], version)
        product_id = item['meta'].get('product_id')
        if store_dir.resolve() == STORE_DIR.resolve():
            folder = PRODUCT_FOLDER.get(product_id, product_id) if product_id and SAFE_ID.match(product_id) else '_未分产品'
            product_dir = OUTPUT_DIR / folder
        else:
            product_dir = images_dir / product_id if product_id and SAFE_ID.match(product_id) else images_dir
        product_dir.mkdir(parents=True, exist_ok=True)
        image_path = product_dir / ('%s.%s' % (stem, out['ext']))
        image_path.write_bytes(out['image'])
        record = {'schema': 'lab-seedream-result/v1', 'batch_id': batch_id,
                  'item_id': item['item_id'], 'version': version, 'model': MODEL,
                  'model_returned': out['model_returned'], 'fingerprint': item['fingerprint'],
                  'authorization_id': auth['authorization_id'], 'prompt': item['body']['prompt'],
                  'requested_size': item['body']['size'], 'size_field': out['size_field'],
                  'usage': out['usage'], 'claim_risk_markers': item['claim_risk_markers'],
                  'meta': item['meta'], 'created_utc': _utc(),
                  'ref_images': item['ref_images'], 'ref_image_sha256': item['ref_image_sha256'],
                  'image_path': str(image_path).replace('\\', '/'),
                  'image_sha256': _sha(out['image']), 'image_bytes': len(out['image']),
                  'url_host': out['url_host'],
                  'remote_url_ephemeral': out['remote_url_ephemeral'],
                  'remote_url_note': 'short-lived vendor URL; lifetime and AutoDL reachability '
                                     'UNVERIFIED'}
        (results_dir / (stem + '.json')).write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        _ledger_append(store, {'event': 'success', 'authorization_id': auth['authorization_id'],
                               'item_id': item['item_id'], 'version': version,
                               'fingerprint': item['fingerprint'],
                               'image_sha256': record['image_sha256'], 'utc': _utc()})
        report['results'].append({'item_id': item['item_id'], 'version': version,
                                  'image_path': record['image_path'],
                                  'image_sha256': record['image_sha256']})
    report['attempts_used'] = attempts_used(auth['authorization_id'], store)
    report['attempts_cap'] = auth['max_images']
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Seedream image channel (plan/run)')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_plan = sub.add_parser('plan')
    p_plan.add_argument('--jobs', required=True)
    p_run = sub.add_parser('run')
    p_run.add_argument('--jobs', required=True)
    p_run.add_argument('--authorization', required=True)
    args = parser.parse_args(argv)
    try:
        out = plan(args.jobs) if args.cmd == 'plan' else run(args.jobs, args.authorization)
    except (SeedreamError, OSError, ValueError) as error:
        detail = str(error) if isinstance(error, SeedreamError) \
            else 'INPUT_UNREADABLE: %s' % type(error).__name__
        print(json.dumps({'status': 'refused', 'detail': detail}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not out.get('failures') else 1


if __name__ == '__main__':
    sys.exit(main())
