"""h3_minimax_official — official MiniMax China (api.minimax.cn) Ref2VA provider adapter.

This is a GENUINELY SEPARATE second provider channel: it never imports and never
modifies adapters/h3_autodl.py or the oldX runtime, and the AutoDL chain keeps its
current behavior. It exists because the AutoDL z0903 workflow declares no video
reference input (EK-18) while the user explicitly authorized the official China
direct API for full-reference (Ref2VA) generation.

Official V2 contract (independently re-verified 2026-10-11 against
platform.minimax.io/docs/api-reference/video-generation-v2-create, -query, -list
and the China guide platform.minimax.cn/docs/guides/video-generation):
  request  content block = {"type": "text", "text": ...} or
           {"type": "image_url"|"video_url"|"audio_url",
            "image_url"|"video_url"|"audio_url": {"url": ...},
            "role": "reference_image"|"reference_video"|"reference_audio"}
  create   POST 200 -> bare {"task_id": "..."} (NO base_resp wrapper)
  get task 200 -> {"task": {"id", "model", "status", "content": {"url"}, ...}}
  list     200 -> {"items": [...], "total": N}
  error    any -> {"type": "error", "error": {"type", "message", "http_code"},
                   "request_id": ...}
  status enum: queued | running | succeeded | failed | cancelled

Contract fact source: config/minimax_official.json (provider/model/limits + China
pricing snapshot 2026-10-11 with a binding policy hash). Credentials are env-only
(MINIMAX_API_KEY); MINIMAX_API_BASE_URL may only repeat the pinned China domain and
any other value is rejected BEFORE the key or the network is touched. The key is
never read outside a transport call, never logged, never echoed, never persisted;
header-safety is validated BEFORE any urllib header is constructed so an invalid
credential can never surface inside an exception message.

Scope this round: LOCAL-ONLY prepare (zero HTTP) with mixed multi-video/image/audio
references, offline doctor, guarded single submit (refuses before ANY POST without
a human authorization record bound to the exact request fingerprint — which itself
binds the exact outbound body digest — with a cross-process fingerprint lock and a
durable uncertainty marker written BEFORE transport) and a read-only single query
restricted to locally registered task ids. Paid budget this round: 0.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_UP
from pathlib import Path

from agents import product_storyboards as ps

LAB_ROOT = Path('F:/codex/laox_director_lab')
CONFIG_PATH = LAB_ROOT / 'config' / 'minimax_official.json'
EVIDENCE_DIR = LAB_ROOT / 'runtime' / 'evidence' / 'h3-minimax-official-v1'
STORE_DIR = LAB_ROOT / 'runtime' / 'h3-minimax-official-v1'
PREPARED_DIR = STORE_DIR / 'prepared'
AUTH_DIR = STORE_DIR / 'authorization'
LOCK_DIR = STORE_DIR / 'locks'
SUBMISSIONS_DIR = STORE_DIR / 'submissions'

CANONICAL_BASE = 'https://api.minimax.cn'
CREATE_PATH = '/v2/video_generation'
QUERY_PATH = '/v2/query/video_generation/{task_id}'
QUERY_LIST_PATH = '/v2/query/video_generation'
API_KEY_ENV = 'MINIMAX_API_KEY'
BASE_URL_ENV = 'MINIMAX_API_BASE_URL'

MODEL = 'MiniMax-H3'
RESOLUTIONS = ('768P', '2K')
RATIOS = ('adaptive', '21:9', '16:9', '4:3', '1:1', '3:4', '9:16')
DEFAULT_RATIO = '9:16'
DURATION_RANGE = (4, 15)
PROMPT_MAX_CHARS = 7000
REF_ROLES = ('reference_image', 'reference_video', 'reference_audio')
# Official content block: type carries the URL FIELD NAME (image_url/video_url/
# audio_url) whose value is a nested {"url": ...} object; the reference role rides
# in the separate "role" field. first_frame/last_frame (+ mixed, base_video) are
# deliberately NOT this adapter's contract.
CONTENT_TYPE_FOR_ROLE = {'reference_image': 'image_url',
                         'reference_video': 'video_url',
                         'reference_audio': 'audio_url'}
ROLE_FOR_CONTENT_TYPE = {v: k for k, v in CONTENT_TYPE_FOR_ROLE.items()}
CONTENT_TEXT_BLOCK_KEYS = ('type', 'text')
REJECTED_BLOCK_TYPES = ('first_frame', 'last_frame', 'mixed', 'base_video')
REJECTED_ROLES = ('first_frame', 'last_frame', 'mixed', 'base_video')
TOP_LEVEL_FIELDS = ('model', 'content', 'resolution', 'duration', 'ratio')
BODY_MAX_BYTES = 64 * 1024 * 1024
MIXED_MAX_COUNT = 12

TASK_STATUSES = ('queued', 'running', 'succeeded', 'failed', 'cancelled')

FFPROBE = Path('E:/software/mini/MiniMax Design/current/resources/ffmpeg/ffprobe.exe')
FFPROBE_TIMEOUT_S = 30.0
HTTP_TIMEOUT_S = 30.0

AUTH_SCHEMA = 'lab-h3-official-submit-authorization/v1'
MAX_BUDGET_CNY = 200.0
ESTIMATE_STATUS_CONFIRMED = 'human_confirmed_platform_quote'
ESTIMATE_UNIT = 'CNY_per_single_call'
ESTIMATE_FRESH_HOURS = 24.0
TARIFF_STALE_AFTER_DAYS = 30
RATE_PATTERN = re.compile(r'^\d+\.\d{2}$')

# Strict bounded identifier for anything that becomes a filename or is
# interpolated into a URL path (task ids, shot ids, intent ids). Unsafe ids are
# REJECTED, never sanitized into a different valid id.
SAFE_ID_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
# Credential header safety: printable ASCII token subset, no whitespace/control
# characters — validated BEFORE any urllib header construction so a malformed key
# can never reach an exception message (urllib echoes the offending header value).
BEARER_TOKEN_PATTERN = re.compile(r'^[A-Za-z0-9._~+/=-]{1,512}$')

MIME_BY_EXT = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
               'webp': 'image/webp', 'heic': 'image/heic', 'heif': 'image/heif',
               'mp4': 'video/mp4', 'mov': 'video/quicktime',
               'wav': 'audio/wav', 'mp3': 'audio/mpeg'}

PREPARED_SCHEMA = 'lab-h3-official-prepared/v2'

# The ONLY authorized read-only reference_video source outside the lab tree: the
# exact a1 clip bound in the dragonfruit case (user-authorized TEST material).
AUTHORIZED_VIDEO_SOURCE = {
    'path': 'F:/codex/laox_account/video_postprod/artifacts/imports/eb/'
            'ebba26902592edba75027de82e2d11d383e05511ddb24a046c5fd4da868c000e.mp4',
    'sha256': 'ebba26902592edba75027de82e2d11d383e05511ddb24a046c5fd4da868c000e',
}


class H3OfficialError(RuntimeError):
    """Fail-closed refusal; the message never contains credentials or media bytes."""


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def _canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha256_canonical(obj):
    return hashlib.sha256(_canonical(obj).encode('utf-8')).hexdigest()


def _refuse(code, detail=''):
    raise H3OfficialError(code + (': ' + detail if detail else ''))


def _validate_safe_id(value, field):
    # fullmatch: an anchored `$` would silently admit a terminal newline
    if not isinstance(value, str) or not SAFE_ID_PATTERN.fullmatch(value):
        _refuse('ID_INVALID', '%s must fully match %s (unsafe ids — including any '
                'trailing newline/control character — are rejected, never '
                'sanitized; got type %s)' % (field, SAFE_ID_PATTERN.pattern,
                                             type(value).__name__))
    return value


def _validate_bearer(key):
    """Header-safety gate BEFORE urllib sees the credential (R6): an invalid
    token refuses here with a message that cannot contain the value. fullmatch —
    an anchored `$` alone would admit a terminal newline, which urllib rejects
    with a ValueError whose message ECHOES the credential."""
    if not isinstance(key, str) or not key or not BEARER_TOKEN_PATTERN.fullmatch(key):
        _refuse('API_KEY_MALFORMED', 'credential failed the HTTP header safety '
                'pattern (value intentionally not echoed); refusing before any '
                'header construction or network action')
    return key


def _contained_file(path, root, code, detail=''):
    """Resolve + contain ONE file path inside an approved root BEFORE any read:
    symlinks/junction escapes and traversal refuse fail-closed."""
    p = Path(path)
    if p.is_symlink():
        _refuse(code, 'symlink refused: ' + detail)
    resolved = p.resolve()
    if resolved.is_symlink():
        _refuse(code, 'resolved symlink refused: ' + detail)
    root = Path(root).resolve()
    if not resolved.is_relative_to(root):
        _refuse(code, 'path escapes the approved root %s (%s)'
                % (root, detail or 'uncontained path'))
    return resolved


# ---------------------------------------------------------------- config / tariff

_CONFIG_CACHE = None


def load_config():
    """Strict load + shape validation of the public contract snapshot. Any drift
    (missing block, non-finite rate, unknown resolution set…) refuses fail-closed —
    a malformed contract must never reach request construction or quoting."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    if not CONFIG_PATH.is_file():
        _refuse('CONFIG_MISSING', str(CONFIG_PATH))
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        _refuse('CONFIG_INVALID', 'unparsable config: ' + type(exc).__name__)
    prov = cfg.get('provider') or {}
    if prov.get('base_url') != CANONICAL_BASE:
        _refuse('CONFIG_INVALID', 'provider.base_url must be the pinned China domain')
    model = cfg.get('model') or {}
    if model.get('name') != MODEL:
        _refuse('CONFIG_INVALID', 'model.name must be ' + MODEL)
    if list(model.get('resolutions') or []) != list(RESOLUTIONS):
        _refuse('CONFIG_INVALID', 'model.resolutions must be exactly %s' % (list(RESOLUTIONS),))
    if list(model.get('ratios') or []) != list(RATIOS):
        _refuse('CONFIG_INVALID', 'model.ratios must be exactly the %d official '
                'values %s' % (len(RATIOS), list(RATIOS)))
    if model.get('prompt_max_chars') != PROMPT_MAX_CHARS or \
            type(model.get('prompt_max_chars')) is not int:
        _refuse('CONFIG_INVALID', 'model.prompt_max_chars must be int %d' % PROMPT_MAX_CHARS)
    limits = cfg.get('media_limits') or {}
    for block in ('image', 'video', 'audio'):
        if not isinstance(limits.get(block), dict):
            _refuse('CONFIG_INVALID', 'media_limits.%s missing' % block)
    pricing = cfg.get('pricing') or {}
    tariff_policy_hash(pricing)          # validates every rate shape/value now
    _CONFIG_CACHE = cfg
    return cfg


def reset_config_cache():               # tests only
    global _CONFIG_CACHE
    _CONFIG_CACHE = None


def _rate(value, field):
    if not isinstance(value, str) or not RATE_PATTERN.fullmatch(value):
        _refuse('TARIFF_MALFORMED', '%s must be a decimal string like 0.50 (got %r)'
                % (field, value))
    try:
        dec = Decimal(value)
    except InvalidOperation:
        _refuse('TARIFF_MALFORMED', '%s is not a finite decimal' % field)
    if not dec.is_finite() or dec <= 0:
        _refuse('TARIFF_MALFORMED', '%s must be finite and > 0' % field)
    return dec


def tariff_rates(pricing):
    rates = pricing.get('rates') or {}
    out = {}
    for key in ('output_per_sec', 'input_video_per_sec'):
        tier = rates.get(key)
        if not isinstance(tier, dict):
            _refuse('TARIFF_MALFORMED', 'rates.%s missing' % key)
        for res in RESOLUTIONS:
            out[(key, res)] = _rate(tier.get(res), 'rates.%s.%s' % (key, res))
    out[('input_image_extra_each',)] = _rate(rates.get('input_image_extra_each'),
                                             'rates.input_image_extra_each')
    free = rates.get('input_image_first_free')
    if type(free) is not int or isinstance(free, bool) or free < 0:
        _refuse('TARIFF_MALFORMED', 'rates.input_image_first_free must be a plain int >= 0')
    out[('input_image_first_free',)] = free
    if pricing.get('currency') != 'CNY':
        _refuse('TARIFF_MALFORMED', 'pricing.currency must be CNY')
    if not isinstance(pricing.get('snapshot_date'), str) or \
            not re.fullmatch(r'\d{4}-\d{2}-\d{2}', pricing['snapshot_date']):
        _refuse('TARIFF_MALFORMED', 'pricing.snapshot_date must be YYYY-MM-DD')
    return out


def tariff_policy_hash(pricing):
    """sha256 over the canonical pricing block — binds rates, snapshot date and
    sources; every paid gate compares against this exact hash."""
    tariff_rates(pricing)               # full validation before hashing
    return _sha256_canonical(pricing)


def current_policy_hash():
    return tariff_policy_hash(load_config()['pricing'])


def tariff_stale(pricing, now=None):
    """Stale = snapshot (or its explicit reconfirmation) older than the configured
    window. Stale tariffs are honestly reported in prepare quotes and REFUSED on
    the paid path unless explicitly reconfirmed."""
    now = now or datetime.now(timezone.utc)
    try:
        snap = datetime.strptime(pricing['snapshot_date'], '%Y-%m-%d')\
            .replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return True
    window = pricing.get('stale_after_days', TARIFF_STALE_AFTER_DAYS)
    if type(window) is not int or window <= 0:
        window = TARIFF_STALE_AFTER_DAYS
    ref = snap
    reconfirmed = pricing.get('reconfirmed_utc')
    if isinstance(reconfirmed, str):
        try:
            ref = max(ref, datetime.fromisoformat(reconfirmed.replace('Z', '+00:00')))
        except ValueError:
            pass
    return (now - ref).days > window


def _dec_seconds(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _refuse('QUOTE_INPUT_INVALID', '%s must be a plain finite number (got %r)'
                % (field, value))
    if not math.isfinite(value) or value <= 0:
        _refuse('QUOTE_INPUT_INVALID', '%s must be finite and > 0' % field)
    return Decimal(str(round(float(value), 6)))


def quote(duration, resolution, video_durations, image_count=0, audio_count=0,
          pricing=None):
    """Published-tariff estimate with explicit line items, exact Decimal math and
    a SAFE upward-cent total for budget gates (R3). Input-video seconds are ALWAYS
    billed at the OUTPUT resolution tier and never omitted; the first 5 input
    images are free, extras cost 0.20 CNY each; audio is free. This is a
    published-tariff calculation, not an exact bill guarantee."""
    validate_duration(duration)
    validate_resolution(resolution)
    rates = tariff_rates(pricing or load_config()['pricing'])
    out_rate = rates[('output_per_sec', resolution)]
    in_rate = rates[('input_video_per_sec', resolution)]
    free_imgs = rates[('input_image_first_free',)]
    extra_rate = rates[('input_image_extra_each',)]
    if type(image_count) is not int or image_count < 0 or type(audio_count) is not int \
            or audio_count < 0:
        _refuse('QUOTE_INPUT_INVALID', 'image/audio counts must be plain ints >= 0')
    secs = [_dec_seconds(d, 'video_duration') for d in (video_durations or [])]
    lines = [
        {'item': 'output_video', 'seconds': float(duration), 'rate': str(out_rate),
         'amount': str(Decimal(duration) * out_rate)},
        {'item': 'input_video_seconds', 'seconds': float(sum(secs)) if secs else 0.0,
         'rate': str(in_rate),
         'amount': str(sum(secs, Decimal('0')) * in_rate)},
        {'item': 'input_image_extra', 'count': max(image_count - free_imgs, 0),
         'rate': str(extra_rate),
         'amount': str(Decimal(max(image_count - free_imgs, 0)) * extra_rate)},
        {'item': 'input_audio', 'count': audio_count, 'rate': '0',
         'amount': '0.00'},
    ]
    total = sum((Decimal(line['amount']) for line in lines), Decimal('0'))
    if not total.is_finite() or total <= 0:
        _refuse('QUOTE_INPUT_INVALID', 'computed total must be finite and > 0')
    safe_total = total.quantize(Decimal('0.01'), rounding=ROUND_UP)
    return {
        'schema': 'lab-h3-official-quote/v2',
        'currency': 'CNY',
        'policy_sha256': tariff_policy_hash(pricing or load_config()['pricing']),
        'tariff_snapshot_date': (pricing or load_config()['pricing'])['snapshot_date'],
        'tariff_stale': tariff_stale(pricing or load_config()['pricing']),
        'disclaimer': 'published-tariff calculation, not an exact bill guarantee',
        'rounding_policy': 'exact Decimal retained in line items and total_cny_exact; '
                           'total_cny is the upward-cent ceiling used by budget gates',
        'line_items': lines,
        'total_cny': float(safe_total),
        'total_cny_exact': str(total),
        'total_cny_safe': str(safe_total),
    }


# ------------------------------------------------------------ request validation

def validate_duration(duration):
    if type(duration) is not int:        # rejects bool, float, str, None
        _refuse('DURATION_NOT_INTEGER', '%r — provider requires a plain int %d..%d'
                % (duration, *DURATION_RANGE))
    low, high = DURATION_RANGE
    if not low <= duration <= high:
        _refuse('DURATION_OUT_OF_RANGE', '%d outside %d..%d' % (duration, low, high))
    return duration


def validate_resolution(resolution):
    if resolution not in RESOLUTIONS:
        _refuse('RESOLUTION_INVALID', '%r must be one of %s (AutoDL workflow labels '
                'are a different provider contract)' % (resolution, list(RESOLUTIONS)))
    return resolution


def validate_ratio(ratio):
    if ratio not in RATIOS:
        _refuse('RATIO_INVALID', '%r must be one of the %d official values %s'
                % (ratio, len(RATIOS), list(RATIOS)))
    return ratio


def validate_prompt(prompt):
    if not isinstance(prompt, str) or not prompt.strip():
        _refuse('PROMPT_INVALID', 'prompt must be a non-empty string')
    if len(prompt) > PROMPT_MAX_CHARS:
        _refuse('PROMPT_INVALID', 'prompt length %d exceeds %d chars'
                % (len(prompt), PROMPT_MAX_CHARS))
    return prompt


def validate_content_blocks(blocks):
    """Official V2 content contract: exactly ONE text block plus >=1 reference
    block. Reference blocks carry the URL FIELD NAME as type (image_url/
    video_url/audio_url), a nested {"url": ...} object, and an explicit
    reference_* role matching that type. first_frame/last_frame/mixed/base_video
    and any extra field refuse."""
    if not isinstance(blocks, list) or not blocks:
        _refuse('CONTENT_BLOCKS_INVALID', 'content must be a non-empty list')
    saw_text = 0
    saw_reference = 0
    for block in blocks:
        if not isinstance(block, dict):
            _refuse('CONTENT_BLOCKS_INVALID', 'content block must be an object')
        kind = block.get('type')
        if kind == 'text':
            if set(block) != set(CONTENT_TEXT_BLOCK_KEYS):
                _refuse('CONTENT_BLOCK_FIELDS_INVALID', 'text block keys must be %s'
                        % (list(CONTENT_TEXT_BLOCK_KEYS),))
            if not isinstance(block.get('text'), str) or not block['text'].strip():
                _refuse('CONTENT_BLOCKS_INVALID', 'text block must carry non-empty text')
            saw_text += 1
        elif kind in ROLE_FOR_CONTENT_TYPE:
            if set(block) != {'type', kind, 'role'}:
                _refuse('CONTENT_BLOCK_FIELDS_INVALID', '%s block keys must be '
                        '[type, %s, role]' % (kind, kind))
            role = block.get('role')
            if role != ROLE_FOR_CONTENT_TYPE[kind]:
                if role in REJECTED_ROLES:
                    _refuse('CONTENT_ROLE_REJECTED', 'role %r is not the explicit '
                            'reference contract this adapter implements' % role)
                _refuse('CONTENT_ROLE_REJECTED', 'role %r does not match type %r '
                        '(expected %r)' % (role, kind, ROLE_FOR_CONTENT_TYPE[kind]))
            url_obj = block.get(kind)
            if not isinstance(url_obj, dict) or set(url_obj) != {'url'} \
                    or not isinstance(url_obj.get('url'), str) or not url_obj['url']:
                _refuse('CONTENT_BLOCK_FIELDS_INVALID', '%s must be a nested object '
                        'exactly {"url": <non-empty string>}' % kind)
            saw_reference += 1
        elif kind in REJECTED_BLOCK_TYPES:
            _refuse('CONTENT_ROLE_REJECTED', '%s mode is not the explicit reference '
                    'contract this adapter implements' % kind)
        else:
            _refuse('CONTENT_ROLE_REJECTED', 'unknown content block type %r' % (kind,))
    if saw_text != 1:
        _refuse('CONTENT_BLOCKS_INVALID', 'exactly one non-empty text block is required')
    if saw_reference < 1:
        _refuse('CONTENT_BLOCKS_INVALID', 'Ref2VA requires at least one actual '
                'reference block (image_url/video_url/audio_url with a reference_* role)')
    return blocks


def _finite(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        _refuse('MEDIA_METADATA_INVALID', '%s must be a finite number (got %r)'
                % (field, value))
    return value


def _ffprobe_json(path):
    if not FFPROBE.is_file():
        _refuse('FFPROBE_MISSING', str(FFPROBE))
    cmd = [str(FFPROBE), '-v', 'error', '-show_streams', '-show_format',
           '-of', 'json', str(path)]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=FFPROBE_TIMEOUT_S, creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        _refuse('FFPROBE_TIMEOUT', 'probe exceeded %.0fs' % FFPROBE_TIMEOUT_S)
    if proc.returncode != 0:
        _refuse('FFPROBE_FAILED', 'exit=%d (bounded %.0fs, argument array)'
                % (proc.returncode, FFPROBE_TIMEOUT_S))
    try:
        return json.loads(proc.stdout.decode('utf-8', 'replace'))
    except ValueError:
        _refuse('FFPROBE_OUTPUT_INVALID', 'probe output is not JSON')


def _probe_streams(path):
    data = _ffprobe_json(path)
    streams = data.get('streams') or []
    fmt = data.get('format') or {}
    raw = fmt.get('duration')
    if isinstance(raw, str):                     # ffprobe emits duration as a string
        try:
            raw = float(raw.strip())
        except ValueError:
            raw = raw                            # falls through to _finite refusal
    dur = _finite(raw, 'format.duration')
    return streams, float(dur)


def probe_video(path):
    streams, duration = _probe_streams(path)
    vstream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    astream = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    if vstream is None:
        _refuse('MEDIA_METADATA_INVALID', 'no video stream')
    num, _, den = str(vstream.get('r_frame_rate', '0/1')).partition('/')
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = float('nan')
    return {
        'duration_sec': duration,
        'width': int(_finite(vstream.get('width'), 'width')),
        'height': int(_finite(vstream.get('height'), 'height')),
        'fps': _finite(fps, 'fps'),
        'vcodec': str(vstream.get('codec_name') or ''),
        # None = silent video (no audio track) — allowed; codec validated only
        # when a track actually exists.
        'acodec': (str(astream.get('codec_name') or '') or None) if astream else None,
    }


def probe_image(path):
    """Image probe reads ONLY stream dimensions — real still images (PNG/JPG/…)
    carry no format.duration, so requiring one would reject every real image."""
    data = _ffprobe_json(path)
    streams = data.get('streams') or []
    stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if stream is None:
        _refuse('MEDIA_METADATA_INVALID', 'no image stream')
    return {'width': int(_finite(stream.get('width'), 'width')),
            'height': int(_finite(stream.get('height'), 'height'))}


def probe_audio(path):
    streams, duration = _probe_streams(path)
    stream = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    if stream is None:
        _refuse('MEDIA_METADATA_INVALID', 'no audio stream')
    return {'duration_sec': duration, 'acodec': str(stream.get('codec_name') or '')}


def _check_dims_aspect(width, height, limits, role):
    lo, hi = limits['min_dim'], limits['max_dim']
    if not lo <= width <= hi or not lo <= height <= hi:
        _refuse('MEDIA_DIMS_OUT_OF_RANGE', '%s %dx%d outside %d..%d'
                % (role, width, height, lo, hi))
    ratio = width / height
    if not limits['aspect_min'] <= ratio <= limits['aspect_max']:
        _refuse('MEDIA_ASPECT_OUT_OF_RANGE', '%s aspect %.3f outside %.2f..%.2f'
                % (role, ratio, limits['aspect_min'], limits['aspect_max']))


def validate_reference(role, path, limits=None):
    """Full fail-closed validation of ONE actual local reference file BEFORE any
    body construction or expensive byte read: scheme/role/path authorization
    (lab-local or the exact pinned a1 clip, symlinks refused), extension, size
    bound, then bounded ffprobe truth (codecs, fps, dims, aspect, durations).
    Silent video (no audio track) is allowed; when an audio track exists its
    codec must be in the allowed set."""
    limits = limits or load_config()['media_limits']
    if role not in REF_ROLES:
        _refuse('REFERENCE_ROLE_INVALID', '%r must be one of %s (first_frame/'
                'last_frame/mixed modes are not this contract)' % (role, list(REF_ROLES)))
    name = str(path)
    lowered = name.lower()
    if lowered.startswith('file:'):
        _refuse('REFERENCE_FILE_SCHEME_REFUSED', 'file:// URLs are not sent as URLs')
    if lowered.startswith(('http://', 'https://', 'data:')):
        _refuse('PUBLIC_URL_REFERENCE_NOT_IMPLEMENTED', 'remote references need trusted '
                'verified metadata + a byte-identical local copy; arbitrary remote fetch '
                'and external upload are out of scope in prepare')
    if not name:
        _refuse('REFERENCE_PATH_INVALID', 'empty reference path')
    p = Path(name)
    if p.is_symlink():
        _refuse('REFERENCE_SYMLINK_REFUSED', str(p))
    resolved = p.resolve()
    if resolved.is_symlink():
        _refuse('REFERENCE_SYMLINK_REFUSED', str(resolved))
    authorized_a1 = Path(AUTHORIZED_VIDEO_SOURCE['path']).resolve()
    if not (resolved.is_relative_to(LAB_ROOT.resolve()) or resolved == authorized_a1):
        _refuse('LOCAL_REFERENCE_PATH_UNAUTHORIZED', 'local references must resolve '
                'inside the lab or be the exact pinned read-only a1 clip')
    if not resolved.is_file():
        _refuse('REFERENCE_FILE_MISSING', str(resolved))
    if role == 'reference_video' and resolved == authorized_a1:
        if _sha256_file(resolved) != AUTHORIZED_VIDEO_SOURCE['sha256']:
            _refuse('REFERENCE_HASH_MISMATCH', 'pinned a1 bytes changed')
    block = limits['image' if role == 'reference_image'
                   else 'video' if role == 'reference_video' else 'audio']
    ext = resolved.suffix.lower()
    if ext not in block['exts']:
        _refuse('MEDIA_EXT_INVALID', '%s requires %s (got %s)' % (role, block['exts'], ext))
    size = resolved.stat().st_size
    if size <= 0 or size > block['max_bytes']:
        _refuse('MEDIA_SIZE_OUT_OF_RANGE', '%s bytes=%d limit=%d'
                % (role, size, block['max_bytes']))
    meta = {'role': role, 'path': str(resolved), 'bytes': size}
    if role == 'reference_image':
        probed = probe_image(resolved)          # dims only — no duration for images
        _check_dims_aspect(probed['width'], probed['height'], block, role)
        meta.update(probed)
    elif role == 'reference_video':
        probed = probe_video(resolved)
        if probed['vcodec'] not in block['video_codecs']:
            _refuse('MEDIA_CODEC_INVALID', 'video codec %r not in %s'
                    % (probed['vcodec'], block['video_codecs']))
        if probed['acodec'] is not None and probed['acodec'] not in block['audio_codecs']:
            _refuse('MEDIA_CODEC_INVALID', 'embedded audio codec %r not in %s '
                    '(silent video without an audio track is allowed)'
                    % (probed['acodec'], block['audio_codecs']))
        if not block['fps_min'] <= probed['fps'] <= block['fps_max']:
            _refuse('MEDIA_FPS_OUT_OF_RANGE', 'fps %.3f outside %.3f..%.3f'
                    % (probed['fps'], block['fps_min'], block['fps_max']))
        _check_dims_aspect(probed['width'], probed['height'], block, role)
        if not block['each_duration_min'] <= probed['duration_sec'] \
                <= block['each_duration_max']:
            _refuse('MEDIA_DURATION_OUT_OF_RANGE', 'video %.6fs outside %.1f..%.1f'
                    % (probed['duration_sec'], block['each_duration_min'],
                       block['each_duration_max']))
        meta.update(probed)
    else:
        probed = probe_audio(resolved)
        if not probed['acodec']:
            _refuse('MEDIA_CODEC_INVALID', 'audio codec missing')
        if not block['each_duration_min'] <= probed['duration_sec'] \
                <= block['each_duration_max']:
            _refuse('MEDIA_DURATION_OUT_OF_RANGE', 'audio %.6fs outside %.1f..%.1f'
                    % (probed['duration_sec'], block['each_duration_min'],
                       block['each_duration_max']))
        meta.update(probed)
    meta['sha256'] = _sha256_file(resolved)
    return meta


def validate_reference_counts(refs, limits=None):
    """Count/sum ceilings across the whole reference set (fail-closed)."""
    limits = limits or load_config()['media_limits']
    roles = [r['role'] for r in refs]
    if len(refs) < 1:
        _refuse('REF2VA_REFERENCE_REQUIRED', 'Ref2VA needs at least one actual reference')
    images = roles.count('reference_image')
    videos = roles.count('reference_video')
    audios = roles.count('reference_audio')
    if images > limits['image']['max_count']:
        _refuse('MEDIA_COUNT_EXCEEDED', 'images %d > %d' % (images, limits['image']['max_count']))
    if videos > limits['video']['max_count']:
        _refuse('MEDIA_COUNT_EXCEEDED', 'videos %d > %d' % (videos, limits['video']['max_count']))
    if audios > limits['audio']['max_count']:
        _refuse('MEDIA_COUNT_EXCEEDED', 'audios %d > %d' % (audios, limits['audio']['max_count']))
    if len(refs) > MIXED_MAX_COUNT:
        _refuse('MEDIA_COUNT_EXCEEDED', 'mixed references %d > %d' % (len(refs), MIXED_MAX_COUNT))
    vsum = math.fsum(r['duration_sec'] for r in refs if r['role'] == 'reference_video')
    if vsum > limits['video']['sum_duration_max']:
        _refuse('MEDIA_DURATION_OUT_OF_RANGE', 'video sum %.6fs > %.1f'
                % (vsum, limits['video']['sum_duration_max']))
    asum = math.fsum(r['duration_sec'] for r in refs if r['role'] == 'reference_audio')
    if asum > limits['audio']['sum_duration_max']:
        _refuse('MEDIA_DURATION_OUT_OF_RANGE', 'audio sum %.6fs > %.1f'
                % (asum, limits['audio']['sum_duration_max']))
    return {'images': images, 'videos': videos, 'audios': audios,
            'video_seconds_sum': round(vsum, 6), 'audio_seconds_sum': round(asum, 6)}


def _data_uri(meta):
    ext = Path(meta['path']).suffix.lstrip('.').lower()
    mime = MIME_BY_EXT.get(ext)
    if mime is None:
        _refuse('MEDIA_EXT_INVALID', 'no standard MIME type for extension %r' % ext)
    with open(meta['path'], 'rb') as f:
        payload = base64.b64encode(f.read()).decode('ascii')
    return 'data:%s;base64,%s' % (mime, payload), len(payload)


def _projected_body_bytes(refs):
    """Base64 size projection BEFORE any large read (ceil(4/3) per media byte plus
    a bounded allowance for the prompt and JSON envelope)."""
    media = sum(int(math.ceil(r['bytes'] * 4 / 3)) for r in refs)
    return media + len(json.dumps({'model': MODEL, 'content': [], 'resolution': '',
                                   'duration': 0, 'ratio': ''})) + PROMPT_MAX_CHARS + 1024


def build_request(prompt, resolution, duration, ratio, refs):
    """Strict official V2 body: ONLY model/content/resolution/duration/ratio at the
    top level; content = one text block + official reference blocks (type carries
    the URL field name, nested {"url": DataURI}, explicit reference_* role). Local
    media is inlined as Base64 Data URIs (official 64MB body ceiling, pre-flighted)."""
    validate_prompt(prompt)
    validate_resolution(resolution)
    validate_duration(duration)
    validate_ratio(ratio)
    validate_reference_counts(refs)
    if _projected_body_bytes(refs) > BODY_MAX_BYTES:
        _refuse('BODY_TOO_LARGE', 'projected body exceeds %d bytes before encoding'
                % BODY_MAX_BYTES)
    blocks = [{'type': 'text', 'text': prompt}]
    for meta in refs:
        uri, uri_len = _data_uri(meta)
        field = CONTENT_TYPE_FOR_ROLE[meta['role']]
        blocks.append({'type': field, field: {'url': uri},
                       'role': meta['role']})
        meta['data_uri_chars'] = uri_len
    validate_content_blocks(blocks)
    body = {'model': MODEL, 'content': blocks, 'resolution': resolution,
            'duration': duration, 'ratio': ratio}
    encoded = json.dumps(body, ensure_ascii=False).encode('utf-8')
    if len(encoded) > BODY_MAX_BYTES:
        _refuse('BODY_TOO_LARGE', 'encoded body %d > %d' % (len(encoded), BODY_MAX_BYTES))
    if set(body) != set(TOP_LEVEL_FIELDS):
        _refuse('REQUEST_FIELDS_INVALID', 'top-level fields drifted: %s' % sorted(body))
    for forbidden in ('workflow', 'input', 'ref_image_0', 'ref_audio_0', 'seed', 'uuid'):
        if forbidden in body:
            _refuse('REQUEST_FIELDS_INVALID', 'AutoDL field %r leaked into official body'
                    % forbidden)
    return body


def compute_fingerprint(request, refs, binding, test_intent, body):
    """The fingerprint core binds the EXACT outbound body digest (canonical JSON of
    the full request body incl. Base64 media), the decoded media bytes (per-ref
    sha256/bytes), roles, settings, prompt, the PASS-shot binding and the complete
    TEST intent — so request/body/ref/cost/intent tampering is detectable before
    any lock, state mutation or network action (R4)."""
    core = {
        'schema': 'lab-h3-official-fingerprint/v2',
        'model': request['model'], 'resolution': request['resolution'],
        'duration': request['duration'], 'ratio': request['ratio'],
        'prompt': request['prompt'],
        'request_body_sha256': _sha256_canonical(body),
        'references': [{'role': r['role'], 'sha256': r['sha256'], 'bytes': r['bytes']}
                       for r in refs],
        'binding': binding,
        'test_intent': test_intent,
    }
    return _sha256_canonical(core), core


# ------------------------------------------------------------- case binding

def load_case(case_dir):
    """Load a case ONLY from inside the lab's approved cases root; each case file
    is resolved and contained before reading (traversal/symlink escape refuse)."""
    cases_root = (LAB_ROOT / 'cases').resolve()
    case_dir = _contained_dir(case_dir, cases_root, 'CASE_DIR_UNAUTHORIZED')
    payloads = {}
    for name in ('case.json', 'storyboards.json', 'review.json'):
        path = case_dir / name
        if path.is_symlink():
            _refuse('CASE_FILE_UNAUTHORIZED', 'case file symlink refused: ' + name)
        resolved = path.resolve()
        if not resolved.is_relative_to(case_dir):
            _refuse('CASE_FILE_UNAUTHORIZED', 'case file escapes its case dir: ' + name)
        if not resolved.is_file():
            _refuse('CASE_FILE_MISSING', str(resolved))
        try:
            payloads[name[:-5]] = json.loads(resolved.read_text(encoding='utf-8'))
        except ValueError:
            _refuse('CASE_FILE_INVALID', 'unparsable JSON: ' + name)
    return payloads


def _contained_dir(path, root, code):
    p = Path(path)
    if p.is_symlink():
        _refuse(code, 'symlinked directory refused')
    resolved = p.resolve()
    if resolved.is_symlink():
        _refuse(code, 'resolved symlink refused')
    root = Path(root).resolve()
    if not resolved.is_relative_to(root):
        _refuse(code, 'directory escapes the approved root %s' % root)
    return resolved


def verify_pass_shot(case_dir, shot_id):
    """Only a PASS shot whose content hash AND version still match the review record
    is preparable. RETURN / stale hash / unknown duration / 3D action / missing
    declared ref media (now with lab-root-or-pinned-a1 containment) all fail closed."""
    _validate_safe_id(shot_id, 'shot_id')
    data = load_case(case_dir)
    plan, review = data['storyboards'], data['review']
    shot = next((s for s in plan['shots'] if s['shot_id'] == shot_id), None)
    if shot is None:
        _refuse('SHOT_NOT_FOUND', shot_id)
    entry = next((s for s in review['review']['shots'] if s['shot_id'] == shot_id), None)
    if entry is None:
        _refuse('SHOT_REVIEW_MISSING', shot_id)
    if entry['verdict'] != 'PASS' or shot_id not in review['final']['pass']:
        _refuse('SHOT_NOT_PASS', '%s verdict=%s' % (shot_id, entry['verdict']))
    if entry['version'] != shot['version']:
        _refuse('SHOT_REVIEW_STALE', 'version drift (shot=%s review=%s)'
                % (shot['version'], entry['version']))
    if ps.shot_content_hash(shot) != entry['content_sha256']:
        _refuse('SHOT_REVIEW_STALE', 'content hash mismatch for ' + shot_id)
    if shot.get('duration_sec') is None:
        _refuse('SHOT_DURATION_UNKNOWN', shot_id)
    if shot.get('needs_3d'):
        _refuse('SHOT_REQUIRES_3D', '3D-dependent unreviewed action')
    authorized_a1 = Path(AUTHORIZED_VIDEO_SOURCE['path']).resolve()
    for ref in shot['refs']:
        declared = next((r for r in plan['refs'] if r['ref_id'] == ref['ref_id']), None)
        if declared is None:
            _refuse('MISSING_REF_MEDIA', 'ref %s not declared' % ref['ref_id'])
        ref_path = Path(declared['path'])
        if ref_path.is_symlink() or ref_path.resolve().is_symlink():
            _refuse('MISSING_REF_MEDIA', 'declared ref %s is a symlink' % ref['ref_id'])
        resolved_ref = ref_path.resolve()
        if not (resolved_ref.is_relative_to(LAB_ROOT.resolve())
                or resolved_ref == authorized_a1):
            _refuse('REF_MEDIA_PATH_UNAUTHORIZED', 'declared ref %s resolves outside '
                    'the lab and is not the pinned a1 clip' % ref['ref_id'])
        if not resolved_ref.is_file():
            _refuse('MISSING_REF_MEDIA', declared['path'])
        if _sha256_file(resolved_ref) != declared['sha256']:
            _refuse('REF_MEDIA_HASH_MISMATCH', ref['ref_id'])
    return data, shot


def _declared_binding_for(data, shot, path):
    """A generation reference must be a DECLARED, REVIEWED reference of the bound
    shot (resolved path AND byte hash match the plan declaration) — never an
    arbitrary lab file (R5)."""
    resolved = Path(path).resolve()
    for ref in shot['refs']:
        declared = next((r for r in data['storyboards']['refs']
                         if r['ref_id'] == ref['ref_id']), None)
        if declared is None:
            _refuse('MISSING_REF_MEDIA', 'ref %s not declared' % ref['ref_id'])
        if Path(declared['path']).resolve() == resolved:
            return declared
    _refuse('REFERENCE_NOT_DECLARED_FOR_SHOT', 'generation references must be declared '
            'reviewed references of the bound shot (path+hash match), not arbitrary '
            'lab files')


# --------------------------------------------------- independent TEST intent

def build_test_intent(data, shot, duration, video_meta, intent_id=None):
    """Independent 5s silent-B-roll TEST generation intent bound to the FULL a1
    reference video (no frame extraction, no replacement). NEW shot id, NEW local
    deterministic review, NEW hash; the original PASS storyboard (2.8s) is never
    rewritten. Honest provenance: silent, no spoken content, generated audio is
    discarded downstream — clearly NOT a real storyboardTeam LLM review."""
    duration = validate_duration(duration)
    case, plan = data['case'], data['storyboards']
    product = plan['product']
    new_shot_id = intent_id or (shot['shot_id'] + '-h3off%ds' % duration)
    _validate_safe_id(new_shot_id, 'intent_id')
    if any(existing['shot_id'] == new_shot_id for existing in plan['shots']):
        _refuse('TEST_INTENT_ID_COLLISION', new_shot_id)
    ref = shot['refs'][0]
    declared = next(r for r in plan['refs'] if r['ref_id'] == ref['ref_id'])
    intent_shot = {
        'shot_id': new_shot_id, 'product_id': shot['product_id'], 'version': 1,
        'purpose': shot['purpose'], 'narration': shot['narration'],
        'refs': [{'ref_id': ref['ref_id'], 'role': ref['role']}],
        'action': {k: (list(v) if isinstance(v, list) else v)
                   for k, v in shot['action'].items()},
        'action_source': 'h3_official_test_intent(authored from PASS shot %s v%s)'
                         % (shot['shot_id'], shot['version']),
        'ref_fit': 'fitting', 'framing': shot['framing'], 'camera': shot['camera'],
        'movement': shot['movement'],
        'duration_sec': float(duration),
        'model_capability': dict(shot['model_capability']),
        'must': list(shot['must']) + ['无口播静默片段：不得出现台词、字幕、固定角标或水印'],
        'must_not': list(shot['must_not']),
        'risk_flags': [], 'needs_3d': False, 'needs_3d_reason': None,
        'complex_grip': False, 'overlay_request': None, 'reuse': 'one_off_test_intent',
        'audio': {'has_original': False, 'must_preserve': False, 'policy': 'silent_placeholder'},
        'generated': {'status': 'to_be_generated', 'verified_real_product_test': False,
                      'provider': 'h3_minimax_official_test'},
        'counterexample_of': None,
    }
    mini_plan = {
        'schema_version': 'product-storyboards/v1',
        'case_id': case['case_id'] + '-h3off-test-intent',
        'created_utc': _utc(), 'policy_version': plan['policy_version'],
        'planning': {
            'engine': 'deterministic-local-v1', 'llm_invoked': False,
            'distinct_use_purposes': [shot['purpose']],
            'action_provenance': 'independent 5s TEST generation intent reusing the case '
                                 'action/refs/provenance; original PASS storyboard untouched',
            'native_storyboard_team_context': plan['planning'].get(
                'native_storyboard_team_context'),
            'h3_official_test_intent': {
                'generation_duration_sec': duration,
                'storyboard_shot_id': shot['shot_id'],
                'storyboard_version': shot['version'],
                'storyboard_content_sha256': ps.shot_content_hash(shot),
                'storyboard_planned_duration_sec': shot['duration_sec'],
                'reference_mode': 'reference_video_full_clip',
                'reference_video_sha256': video_meta['sha256'],
                'reference_video_duration_sec': video_meta['duration_sec'],
                'silent': True, 'spoken': False, 'rerender_of_accepted_ref': False,
                'generated_audio_discarded_downstream': True,
            },
        },
        'product': json.loads(json.dumps(product, ensure_ascii=False)),
        'narration_segments': [s for s in plan['narration_segments']
                               if s['segment_id'] == shot['shot_id'].replace('shot', 'seg')],
        'refs': json.loads(json.dumps(plan['refs'], ensure_ascii=False)),
        'shots': [intent_shot],
        'source_bindings': [],
    }
    if not mini_plan['narration_segments']:
        _refuse('TEST_INTENT_SEGMENT_MISSING', shot['shot_id'])
    review = ps.review_plan(mini_plan)
    verdict = review['shots'][0]
    if verdict['verdict'] != 'PASS':
        _refuse('TEST_INTENT_NOT_PASS', 'local reviewer refused: '
                + json.dumps(verdict['problems'], ensure_ascii=False))
    return {
        'schema': 'lab-h3-official-test-intent/v1',
        'intent_id': new_shot_id, 'created_utc': _utc(),
        'case_id': case['case_id'],
        'storyboard_binding': {'shot_id': shot['shot_id'], 'version': shot['version'],
                               'content_sha256': ps.shot_content_hash(shot),
                               'planned_duration_sec': shot['duration_sec']},
        'generation_intent': {'duration_sec': duration, 'silent': True, 'spoken': False,
                              'not_a_rerender_of': declared['ref_id'],
                              'reference_video_sha256': video_meta['sha256']},
        'mini_plan': mini_plan,
        'review': review,
        'review_content_sha256': verdict['content_sha256'],
        'review_provenance': 'deterministic-rules-v1 local re-review; NOT the native '
                             'storyboardTeam LLM (not_run)',
    }


# ------------------------------------------------------------- Ref2VA prompt

def _en(v):
    return str(v or '').strip()


def render_ref2va_prompt(intent, product, video_meta, ratio=DEFAULT_RATIO):
    """Six-section Ref2VA rewrite (skill h3-prompt-writing, full-reference mode):
    English body, consistent <Subject 1>/<Video 1> labels, silent B-roll — no <d>
    dialogue tags, no corner overlays/watermarks, spoken_locked preserved. The
    framing statement follows the CONFIGURED ratio, not a hardcoded one."""
    shot = intent['mini_plan']['shots'][0]
    dur = intent['generation_intent']['duration_sec']
    sha16 = video_meta['sha256'][:16]
    ref_dur = video_meta['duration_sec']
    action = shot['action']
    process = ', '.join(_en(s) for s in action['process'])
    product_name = product['name']
    subject_term = product.get('subject_term') or product_name
    lines = [
        'subject_definitions:',
        '<Subject 1> is the product demonstrated in the case (%s, %s). The product '
        'identity is unverified: the source material is an existing generated demo '
        'clip, not a real product test recording, and the output must not be treated '
        'as a product test or identity proof.'
        % (product_name, subject_term),
        '<Video 1> is the reference video (existing demo clip, source sha256 %s…, '
        '%.3f s): it provides the product appearance, tabletop scene, vertical '
        'framing, lighting and hand-motion reference for this TEST generation. It is '
        'read-only source material; no frame is extracted and the clip itself is '
        'never re-rendered.' % (sha16, ref_dur),
        'summary:',
        '[reference generation] The target video is a %d-second silent vertical B-roll '
        'TEST clip of <Subject 1>: %s. <Video 1> serves as the appearance/scene/motion '
        'reference; the output is a new TEST generation, not a rerender, edit or '
        'continuation of <Video 1>, and contains no speech, subtitles, captions, '
        'logos, watermarks or fixed corner overlays.' % (dur, _en(shot['narration'])),
        'retention_analysis:',
        '<Subject 1> (appears in [Shot 1]): partially_preserved - the product category '
        'appearance and color relation are retained; the reference is an unverified '
        'generated demo, so generated details may differ from the reference.',
        '<Video 1> (appearance, scene and motion reference): weak_reference - only the '
        'tabletop product demonstration framing, lighting direction and slow hand '
        'motion are broadly retained; no timeline, cut or pixel-level fidelity is '
        'required and the source is never copied or continued.',
        'detailed_description:',
        'The target video is a natural realistic tabletop product B-roll with even '
        'lighting in vertical %s framing; no captions, no subtitles, no logos, no '
        'watermarks, no readable text and no fixed corner overlays at any time; no '
        'face is visible (only hands may enter the frame when the action needs them).'
        % ratio,
        '[Shot 1] %s; %s; %s. Framing: %s; camera: %s; movement: %s. The action starts '
        'immediately, develops in one single continuous take with no cuts, and the '
        'final state stays visible until the last frame.'
        % (_en(action['start']), process, _en(action['end']),
           _en(shot['framing']), _en(shot['camera']), _en(shot['movement'])),
        'overall_soundscape:',
        'No voiceover and no spoken content of any kind. Only a quiet indoor room tone '
        'at low level; any audio generated by the model is discarded downstream and is '
        'not part of the deliverable.',
        'non_diegetic_music:',
        'N/A (silent B-roll TEST; no audience-only score).',
    ]
    prompt = '\n'.join(lines)
    validate_prompt(prompt)
    if '<d>' in prompt or '</d>' in prompt:
        _refuse('SILENT_INTENT_CONTAINS_DIALOGUE_TAG', 'silent TEST prompt must not '
                'carry dialogue tags')
    for banned in ('watermark on', 'corner overlay on', 'fixed corner label:'):
        if banned in prompt.lower():
            _refuse('PROMPT_OVERLAY_CONFLICT', 'prompt must not request overlays')
    return prompt


# ------------------------------------------------------------- prepare (local)

def _provider_view():
    return {'id': 'minimax_official_china', 'base_url': CANONICAL_BASE,
            'create_endpoint': CREATE_PATH}


def _request_view(resolution, duration, ratio, prompt):
    return {'model': MODEL, 'resolution': resolution, 'duration': duration,
            'ratio': ratio, 'prompt': prompt}


def _expected_binding(data, shot, case_dir):
    return {
        'case_dir': str(Path(case_dir).resolve()),
        'shot_id': shot['shot_id'], 'version': shot['version'],
        'content_sha256': ps.shot_content_hash(shot),
        'review_verdict': 'PASS',
        'review_entry_sha256': next(e['content_sha256'] for e in
                                    data['review']['review']['shots']
                                    if e['shot_id'] == shot['shot_id']),
        'planned_duration_sec': shot['duration_sec'],
    }


def _test_intent_core(intent, duration):
    """The COMPLETE deterministic TEST-intent view, including the sound policy
    (silent/spoken/spoken_locked/audio_policy) — every field is compared on both
    submit revalidation and immutable prepare reuse."""
    return {
        'intent_id': intent['intent_id'],
        'duration_sec': duration, 'silent': True, 'spoken': False,
        'spoken_locked': True,
        'audio_policy': 'generated audio discarded downstream; no voiceover',
        'review_engine': intent['review']['engine'],
        'review_verdict': 'PASS',
        'review_content_sha256': intent['review_content_sha256'],
        'review_provenance': intent['review_provenance'],
    }


def _compare_view(stored, expected, code, label):
    """Dict-equality gate: any tampered value AND any removed metadata field
    refuse with the same code (fail-closed, before lock/state/network)."""
    if stored != expected:
        _refuse(code, '%s does not match the freshly rederived value from the '
                'current trusted files (tampered or removed fields refused)' % label)


def _scrub_body(body):
    safe = []
    for block in body['content']:
        clone = dict(block)
        for field in ('image_url', 'video_url', 'audio_url'):
            value = clone.get(field)
            if isinstance(value, dict) and isinstance(value.get('url'), str) \
                    and value['url'].startswith('data:'):
                clone[field] = {'url': '[local media encoded as Base64 data URI; '
                                       '%d chars omitted]' % len(value['url'])}
        safe.append(clone)
    scrubbed = dict(body)
    scrubbed['content'] = safe
    return scrubbed


def _atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp-%s' % os.getpid())
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def prepare(case_dir, shot_id, duration=5, video_path=None, video_paths=None,
            image_paths=None, audio_paths=None, resolution='768P',
            ratio=DEFAULT_RATIO, write_evidence=True):
    """End-to-end LOCAL preparation of ONE official Ref2VA TEST request with MIXED
    references (multiple declared videos + optional declared images/audio): PASS-shot
    binding → declared-reference binding + full per-type validation (ffprobe truth;
    silent video allowed; images need no duration) → independent TEST intent +
    deterministic PASS re-review → six-section prompt (ratio-following) → strict
    official body build (nested url objects + roles, Base64 data URIs, 64MB ceiling)
    → published-tariff quote (exact Decimal + safe upward-cent total) → atomic
    immutable prepared record named by a fingerprint that binds the outbound body
    digest. Zero HTTP, zero uploads, zero paid calls."""
    duration = validate_duration(duration)
    resolution = validate_resolution(resolution)
    ratio = validate_ratio(ratio)
    data, shot = verify_pass_shot(case_dir, shot_id)
    videos = list(video_paths) if video_paths else (
        [video_path] if video_path is not None else None)
    if videos is None:
        ref = shot['refs'][0]
        videos = [next(r for r in data['storyboards']['refs']
                       if r['ref_id'] == ref['ref_id'])['path']]
    refs = []
    declared_bindings = []
    for path in videos:
        declared = _declared_binding_for(data, shot, path)
        meta = validate_reference('reference_video', path)
        if meta['sha256'] != declared['sha256']:
            _refuse('REF_MEDIA_HASH_MISMATCH', 'reference bytes do not match the '
                    'declared reviewed sha256 for ' + declared['ref_id'])
        refs.append(meta)
        declared_bindings.append(declared)
    for path in (image_paths or []):
        declared = _declared_binding_for(data, shot, path)
        meta = validate_reference('reference_image', path)
        if meta['sha256'] != declared['sha256']:
            _refuse('REF_MEDIA_HASH_MISMATCH', 'reference bytes do not match the '
                    'declared reviewed sha256 for ' + declared['ref_id'])
        refs.append(meta)
        declared_bindings.append(declared)
    for path in (audio_paths or []):
        declared = _declared_binding_for(data, shot, path)
        meta = validate_reference('reference_audio', path)
        if meta['sha256'] != declared['sha256']:
            _refuse('REF_MEDIA_HASH_MISMATCH', 'reference bytes do not match the '
                    'declared reviewed sha256 for ' + declared['ref_id'])
        refs.append(meta)
        declared_bindings.append(declared)
    video_meta = refs[0]                     # primary reference_video drives the intent
    intent = build_test_intent(data, shot, duration, video_meta)
    prompt = render_ref2va_prompt(intent, data['storyboards']['product'], video_meta,
                                  ratio=ratio)
    counts = validate_reference_counts(refs)
    body = build_request(prompt, resolution, duration, ratio, refs)
    binding = _expected_binding(data, shot, case_dir)
    test_intent_core = _test_intent_core(intent, duration)
    fingerprint, core = compute_fingerprint(
        {'model': MODEL, 'resolution': resolution, 'duration': duration,
         'ratio': ratio, 'prompt': prompt}, refs, binding, test_intent_core, body)
    pricing = load_config()['pricing']
    cost_quote = quote(duration, resolution,
                       [r['duration_sec'] for r in refs if r['role'] == 'reference_video'],
                       counts['images'], counts['audios'], pricing)
    declarations_view = [{'ref_id': d['ref_id'], 'role': d['role']}
                         for d in declared_bindings]
    refs_view = [{k: v for k, v in r.items() if k != 'data_uri_chars'} for r in refs]
    record = {
        'schema': PREPARED_SCHEMA,
        'created_utc': _utc(), 'fingerprint': fingerprint,
        'provider': _provider_view(),
        'request': _request_view(resolution, duration, ratio, prompt),
        'references': refs_view,
        'reference_declarations': declarations_view,
        'reference_counts': counts,
        'request_body': body,
        'request_body_sha256': core['request_body_sha256'],
        'binding': binding,
        'test_intent': test_intent_core,
        'fixture_honesty': {
            'generated_demo_identity_unverified': True,
            'auto_edit_eligible': False,
            'source_test_only': True,
            'not_a_rerender_of': intent['generation_intent']['not_a_rerender_of'],
            'reference_source_read_only': True,
        },
        'quote': cost_quote,
        'guards': {'paid_calls': 0, 'uploads': 0, 'http_calls': 0},
        'fingerprint_core': core,
    }
    PREPARED_DIR.mkdir(parents=True, exist_ok=True)
    out = PREPARED_DIR / ('%s.json' % fingerprint)
    if out.is_file():
        existing = json.loads(out.read_text(encoding='utf-8'))
        if existing.get('schema') != PREPARED_SCHEMA:
            _refuse('PREPARED_IMMUTABLE_REFUSED', 'a legacy/unknown-schema record '
                    'already exists under this fingerprint name; it is preserved and '
                    'never overwritten')
        if _sha256_canonical(existing.get('fingerprint_core') or {}) \
                != existing.get('fingerprint'):
            _refuse('PREPARED_FINGERPRINT_STALE', 'stored fingerprint no longer '
                    'matches its core material')
        if existing.get('request_body') != body or \
                existing.get('request_body_sha256') != core['request_body_sha256']:
            _refuse('PREPARED_BODY_TAMPERED', 'stored outbound body does not match '
                    'the freshly rederived body for this fingerprint')
        # FULL required-field consistency on immutable reuse (R3/R4): request/
        # provider/binding/complete TEST intent incl. sound policy/references/
        # declarations/counts and the FULL quote (exact AND upward-safe totals,
        # line items, currency, policy hash) — recomputed from the current trusted
        # files; removed fields refuse exactly like tampered ones.
        _compare_view(existing.get('fingerprint_core'), core,
                      'PREPARED_IMMUTABLE_REFUSED', 'fingerprint_core')
        _compare_view(existing.get('request'),
                      _request_view(resolution, duration, ratio, prompt),
                      'PREPARED_REQUEST_TAMPERED', 'request')
        _compare_view(existing.get('provider'), _provider_view(),
                      'PREPARED_PROVIDER_TAMPERED', 'provider')
        _compare_view(existing.get('binding'), binding,
                      'PREPARED_BINDING_TAMPERED', 'binding')
        _compare_view(existing.get('test_intent'), test_intent_core,
                      'TEST_INTENT_DRIFT', 'test_intent')
        _compare_view(existing.get('references'), refs_view,
                      'PREPARED_RECORD_INVALID', 'references')
        _compare_view(existing.get('reference_declarations'), declarations_view,
                      'PREPARED_RECORD_INVALID', 'reference_declarations')
        _compare_view(existing.get('reference_counts'), counts,
                      'PREPARED_RECORD_INVALID', 'reference_counts')
        _compare_view(existing.get('quote'), cost_quote, 'QUOTE_DRIFT', 'quote')
        record = existing                  # idempotent: same bytes, same fingerprint
    else:
        _atomic_write(out, record)
    evidence = {
        'schema': 'lab-h3-official-prepare-evidence/v2', 'utc': _utc(),
        'case_dir': str(Path(case_dir)), 'shot_id': shot_id,
        'duration_sec': duration, 'resolution': resolution, 'ratio': ratio,
        'fingerprint': fingerprint, 'prepared_path': str(out),
        'storyboard_binding': {k: binding[k] for k in
                               ('shot_id', 'version', 'content_sha256',
                                'planned_duration_sec')},
        'generation_intent': {'duration_sec': duration, 'silent': True, 'spoken': False,
                              'reference_video_sha256': video_meta['sha256'],
                              'reference_video_duration_sec': video_meta['duration_sec'],
                              'not_a_rerender_of':
                                  intent['generation_intent']['not_a_rerender_of']},
        'reference_media': {'path': video_meta['path'], 'sha256': video_meta['sha256'],
                            'bytes': video_meta['bytes'],
                            'ffprobe': {k: video_meta[k] for k in
                                        ('duration_sec', 'width', 'height', 'fps',
                                         'vcodec', 'acodec')},
                            'source_read_only': True},
        'reference_set': [{'role': r['role'], 'sha256': r['sha256'],
                           'bytes': r['bytes'],
                           'duration_sec': r.get('duration_sec')}
                          for r in refs],
        'reference_counts': counts,
        'request_preview_scrubbed': _scrub_body(body),
        'quote': cost_quote,
        'intent_review': {'verdict': 'PASS',
                          'content_sha256': intent['review_content_sha256'],
                          'provenance': intent['review_provenance']},
        'guards': {'paid_calls': 0, 'uploads': 0, 'http_calls': 0,
                   'base64_in_evidence': False, 'secrets_in_evidence': False},
    }
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _utc().replace(':', '').replace('+', '_')
        ev = EVIDENCE_DIR / ('prepare-%s-%s.json' % (shot_id, stamp))
        ev.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                      encoding='utf-8')
        intent_out = EVIDENCE_DIR / ('test-intent-%s.json' % intent['intent_id'])
        intent_out.write_text(json.dumps(intent, ensure_ascii=False, indent=2),
                              encoding='utf-8')
        evidence['evidence_files'] = [str(ev), str(intent_out)]
    return evidence


# ------------------------------------------------------------------- transport

class UrllibTransport:
    """The ONLY network edge of this adapter: bounded single-shot requests, no
    redirects (a redirect handler would forward the bearer header), no debug
    headers, response body read is capped, and no credential ever leaves the
    Authorization header. The bearer token is header-safety-validated BEFORE any
    urllib object is constructed, and every unexpected transport/JSON/header
    exception is caught and sanitized (type name only) so a malformed credential
    can never surface inside an exception message."""

    def _request(self, base, path, method, key, payload=None, timeout=HTTP_TIMEOUT_S):
        _validate_bearer(key)               # refuses malformed tokens pre-urllib
        url = base.rstrip('/') + path
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8') \
            if payload is not None else None
        try:
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header('Authorization', 'Bearer ' + key)
            req.add_header('Content-Type', 'application/json')

            class _NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None                 # never forward the bearer on redirects

            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read(1 << 20)
                parsed = None
                if raw:
                    try:
                        parsed = json.loads(raw.decode('utf-8', 'replace'))
                    except ValueError:
                        parsed = None
                return {'http_status': resp.status, 'json': parsed}
        except urllib.error.HTTPError as exc:
            raw = exc.read(1 << 20) if hasattr(exc, 'read') else b''
            try:
                parsed = json.loads(raw.decode('utf-8', 'replace')) if raw else None
            except ValueError:
                parsed = None
            return {'http_status': exc.code, 'json': parsed}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {'transport_error': type(exc).__name__}   # sanitized, no url/key
        except ValueError:
            # e.g. invalid header value escaping the pre-validation net: the raw
            # message would contain the credential — return a tag, never the value
            return {'transport_error': 'header_invalid'}

    def post_create(self, base, key, body, timeout=HTTP_TIMEOUT_S):
        return self._request(base, CREATE_PATH, 'POST', key, payload=body,
                             timeout=timeout)

    def get_task(self, base, key, task_id, timeout=HTTP_TIMEOUT_S):
        return self._request(base, QUERY_PATH.format(task_id=task_id), 'GET', key,
                             timeout=timeout)

    def get_list(self, base, key, page_num=1, page_size=1, timeout=HTTP_TIMEOUT_S):
        return self._request(base, '%s?page_num=%d&page_size=%d'
                             % (QUERY_LIST_PATH, page_num, page_size), 'GET', key,
                             timeout=timeout)


_TRANSPORT = UrllibTransport()


def set_transport(transport):           # tests only
    global _TRANSPORT
    _TRANSPORT = transport


def resolve_base():
    """Pinned China domain: MINIMAX_API_BASE_URL may only repeat it verbatim; any
    other value is rejected BEFORE the key or the network is touched."""
    override = os.environ.get(BASE_URL_ENV)
    if override is not None and override != CANONICAL_BASE:
        _refuse('BASE_URL_NOT_VERIFIED', '%s must be exactly %s (China guide canonical);'
                ' refusing unverified base before key/network' % (BASE_URL_ENV, CANONICAL_BASE))
    return CANONICAL_BASE


def _api_key(required=True):
    key = os.environ.get(API_KEY_ENV)
    if not key:
        if required:
            _refuse('API_KEY_MISSING', '%s is not configured in the environment; '
                    'authenticated paths stay not_run' % API_KEY_ENV)
        return None
    return key


_HTTP_MEANINGS = {401: 'unauthorized', 402: 'payment required', 404: 'not found',
                  422: 'unprocessable entity', 429: 'rate limited',
                  500: 'server error'}


def _typed_error_info(payload):
    """Official V2 typed error envelope: top-level {"type": "error", "error":
    {"type", "message", "http_code"}, "request_id"}. Only the error TYPE and
    request id are surfaced — the platform message is never echoed (it may
    contain request inputs)."""
    if not isinstance(payload, dict) or payload.get('type') != 'error':
        return None
    err = payload.get('error') if isinstance(payload.get('error'), dict) else {}
    err_type = err.get('type') if isinstance(err.get('type'), str) else None
    request_id = payload.get('request_id') if isinstance(payload.get('request_id'), str) \
        else None
    return {'error_type': err_type, 'request_id': request_id}


def _official_list_shape_ok(payload):
    """The official list response is {"items": [...], "total": <int>} with NO
    base_resp. A 200 whose body is anything else (empty dict, base_resp wrapper,
    tasks-less blob) is NOT proof of a working authenticated channel."""
    return (isinstance(payload, dict)
            and isinstance(payload.get('items'), list)
            and type(payload.get('total')) is int
            and not isinstance(payload.get('total'), bool)
            and 'base_resp' not in payload)


def _typed_http_error(result):
    status = result.get('http_status')
    info = _typed_error_info(result.get('json')) or {}
    meaning = _HTTP_MEANINGS.get(status, 'http error')
    _refuse('HTTP_%s' % status, '%s (official typed error type=%r request_id=%s; '
            'response sanitized, no request inputs or platform message echoed)'
            % (meaning, info.get('error_type'), info.get('request_id')))


# --------------------------------------------------------------------- doctor

def doctor(live_auth=False):
    """Offline presence/schema report by default: credential BOOLEAN only (the key
    is never read into the report), pinned base, config/tariff summary. --live-auth
    is one safe read-only GET (list, page 1 size 1, 30s, no retry, no POST); with
    no key it reports not_run instead of pretending success. The official list
    response body (items/total) is NEVER copied into the report."""
    base = resolve_base()
    cfg = load_config()
    report = {
        'utc': _utc(), 'provider': 'minimax_official_china', 'base': base,
        'credential_configured': bool(os.environ.get(API_KEY_ENV)),
        'credential_note': 'presence boolean only; value never read into reports',
        'auth_verification': 'not_run',
        'config': {'model': cfg['model']['name'],
                   'resolutions': cfg['model']['resolutions'],
                   'ratios': cfg['model']['ratios'],
                   'pricing_snapshot_date': cfg['pricing']['snapshot_date'],
                   'pricing_policy_sha256': current_policy_hash(),
                   'pricing_stale': tariff_stale(cfg['pricing'])},
        'automatic_retry': False, 'redirect_followed': False,
        'paid_calls': 0, 'uploads': 0, 'http_calls': 0,
    }
    if live_auth:
        key = _api_key(required=False)
        if not key:
            report['auth_verification'] = 'not_run'
            report['auth_note'] = '%s not configured; live GET skipped' % API_KEY_ENV
        else:
            _validate_bearer(key)             # malformed key refuses, value not echoed
            report['http_calls'] = 1
            result = _TRANSPORT.get_list(base, key)
            if result.get('transport_error'):
                report['auth_verification'] = 'not_run'
                report['auth_error'] = result['transport_error']
            else:
                status = result.get('http_status')
                payload = result.get('json') if isinstance(result.get('json'), dict) else {}
                info = _typed_error_info(payload) or {}
                report['auth_verification'] = (
                    'ok' if status == 200 and info == {}
                    and _official_list_shape_ok(payload) else
                    'unauthorized' if status == 401 else 'indeterminate')
                report['live_get'] = {'endpoint': 'query_list page 1 size 1',
                                      'http_status': status,
                                      'official_list_shape':
                                          _official_list_shape_ok(payload),
                                      'error_type': info.get('error_type'),
                                      'request_id': info.get('request_id')}
    return report


# ------------------------------------------------------------ guarded submit

def _load_authorization(path, fingerprint, refs_hashes, policy_hash):
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(record, dict) or record.get('schema') != AUTH_SCHEMA:
        _refuse('AUTH_RECORD_INVALID', 'schema must be ' + AUTH_SCHEMA)
    if record.get('human_authorized') is not True:
        _refuse('AUTH_RECORD_INVALID', 'human_authorized must be true (CLI flags and '
                'key presence are never consent)')
    for field in ('authorized_by', 'consent_statement', 'recorded_utc'):
        value = record.get(field)
        if not isinstance(value, str) or isinstance(value, bool) or not value.strip():
            _refuse('AUTH_RECORD_INVALID', '%s must carry the human consent provenance' % field)
        if field == 'consent_statement' and len(value.strip()) < 10:
            _refuse('AUTH_RECORD_INVALID', 'consent_statement must carry the human '
                    'consent text')
    if record.get('provider') != 'minimax_official_china':
        _refuse('AUTH_RECORD_MISBOUND', 'provider must be minimax_official_china')
    if record.get('model') != MODEL:
        _refuse('AUTH_RECORD_MISBOUND', 'model must be ' + MODEL)
    if record.get('base_url') != CANONICAL_BASE:
        _refuse('AUTH_RECORD_MISBOUND', 'base_url must be ' + CANONICAL_BASE)
    if record.get('request_fingerprint') != fingerprint:
        _refuse('AUTH_FINGERPRINT_MISMATCH', 'authorization is bound to a different request')
    declared_refs = record.get('reference_sha256')
    if not isinstance(declared_refs, list) or not all(
            isinstance(h, str) and len(h) == 64 for h in declared_refs):
        _refuse('AUTH_RECORD_MISBOUND', 'reference_sha256 must be a list of sha256 '
                'hex strings')
    if sorted(declared_refs) != sorted(refs_hashes):
        _refuse('AUTH_RECORD_MISBOUND', 'reference hash set does not match this request')
    if record.get('pricing_policy_sha256') != policy_hash:
        _refuse('AUTH_TARIFF_MISBOUND', 'authorization is bound to a different pricing '
                'policy hash')
    if type(record.get('max_calls')) is not int or record['max_calls'] != 1:
        _refuse('AUTH_RECORD_INVALID', 'max_calls must be exactly integer 1')
    calls_used = record.get('calls_used')
    if type(calls_used) is not int or not 0 <= calls_used < 1:
        _refuse('AUTH_CALLS_EXHAUSTED' if type(calls_used) is int and calls_used == 1
                else 'AUTH_RECORD_INVALID',
                'calls_used must be integer 0 for a fresh single-call authorization')
    budget = record.get('budget')
    if not isinstance(budget, dict) or budget.get('currency') != 'CNY':
        _refuse('AUTH_BUDGET_INVALID', 'budget.currency must be CNY')
    amount = budget.get('max_total')
    if type(amount) not in (int, float) or isinstance(amount, bool) \
            or not math.isfinite(amount) or not 0 < amount <= MAX_BUDGET_CNY:
        _refuse('AUTH_BUDGET_INVALID', 'budget.max_total must be a plain finite number '
                '0 < x <= %.0f CNY' % MAX_BUDGET_CNY)
    est = record.get('cost_estimate')
    if not isinstance(est, dict):
        _refuse('AUTH_ESTIMATE_INVALID', 'a structured cost_estimate is required')
    if est.get('status') != ESTIMATE_STATUS_CONFIRMED:
        _refuse('AUTH_ESTIMATE_INVALID', 'status must be %s (human-confirmed platform '
                'quotation recorded by the trusted coordinator)' % ESTIMATE_STATUS_CONFIRMED)
    if est.get('currency') != 'CNY' or est.get('unit') != ESTIMATE_UNIT:
        _refuse('AUTH_ESTIMATE_INVALID', 'currency must be CNY and unit must be '
                + ESTIMATE_UNIT)
    value = est.get('estimate_cny')
    if type(value) not in (int, float) or isinstance(value, bool) \
            or not math.isfinite(value) or value <= 0:
        _refuse('AUTH_ESTIMATE_INVALID', 'estimate_cny must be a plain finite number > 0')
    if est.get('request_fingerprint') != fingerprint:
        _refuse('AUTH_ESTIMATE_MISBOUND', 'estimate is not bound to this request')
    if est.get('pricing_policy_sha256') != policy_hash:
        _refuse('AUTH_ESTIMATE_MISBOUND', 'estimate is bound to a different pricing policy')
    prov = est.get('provenance')
    if not isinstance(prov, dict) \
            or not isinstance(prov.get('recorded_by'), str) or not prov['recorded_by'].strip() \
            or not isinstance(prov.get('source'), str) or not prov['source'].strip() \
            or not isinstance(prov.get('recorded_utc'), str) or not prov['recorded_utc'].strip():
        _refuse('AUTH_ESTIMATE_INVALID', 'provenance must state recorded_by/source/'
                'recorded_utc')
    quoted = est.get('quote_utc')
    try:
        quoted_at = datetime.fromisoformat(str(quoted).replace('Z', '+00:00'))
        age_h = (datetime.now(timezone.utc) - quoted_at).total_seconds() / 3600.0
    except (ValueError, TypeError):
        _refuse('AUTH_ESTIMATE_STALE', 'quote_utc must be an ISO timestamp')
    if age_h < -1.0 or age_h > ESTIMATE_FRESH_HOURS:
        _refuse('AUTH_ESTIMATE_STALE', 'estimate must be fresh (<= %.0fh)' % ESTIMATE_FRESH_HOURS)
    if value > amount:
        _refuse('AUTH_BUDGET_INSUFFICIENT', 'estimate %.2f CNY exceeds the authorized '
                'budget %.2f CNY — refused before any network or state mutation'
                % (value, amount))
    return record


def _revalidate_prepared(record):
    """Full rederivation gate before submit (R3/R4): schema currency, fingerprint/
    core self-consistency, stored-body digest vs core, FRESH per-reference
    validation of the CURRENT media bytes, current PASS-shot binding, and FULL
    required-field consistency — request view incl. prompt, provider view,
    complete binding (version + current review entry hash), complete TEST intent
    incl. the sound policy (silent/spoken/spoken_locked/audio_policy), references
    metadata, declared bindings, counts, and the FULL quote (exact AND upward-
    safe totals, line items, currency, policy hash) — recomputed from trusted
    current files. Any drift or REMOVED metadata field refuses BEFORE the lock;
    dict equality means tampered and removed are indistinguishable and both fail."""
    if not isinstance(record, dict) or record.get('schema') != PREPARED_SCHEMA:
        _refuse('PREPARED_RECORD_INVALID', 'unknown or legacy prepared record schema '
                '(historical v1 records are preserved on disk and rejected, never '
                'submitted or overwritten)')
    core = record.get('fingerprint_core')
    if not isinstance(core, dict):
        _refuse('PREPARED_RECORD_INVALID', 'fingerprint_core missing')
    if _sha256_canonical(core) != record.get('fingerprint'):
        _refuse('PREPARED_FINGERPRINT_STALE', 'stored fingerprint no longer matches its '
                'core material')
    stored_body = record.get('request_body')
    if not isinstance(stored_body, dict) \
            or core.get('request_body_sha256') != _sha256_canonical(stored_body):
        _refuse('PREPARED_BODY_TAMPERED', 'stored outbound body does not match the '
                'digest bound into the fingerprint core')
    _compare_view(record.get('request_body_sha256'), core.get('request_body_sha256'),
                  'PREPARED_BODY_TAMPERED', 'request_body_sha256')
    refs_meta = record.get('references')
    if not isinstance(refs_meta, list) or not refs_meta:
        _refuse('PREPARED_RECORD_INVALID', 'references missing')
    fresh_refs = []
    for meta in refs_meta:
        fresh = validate_reference(meta.get('role'), meta.get('path'))
        if fresh['sha256'] != meta.get('sha256'):
            _refuse('REF_SOURCE_STALE', 'referenced media bytes changed: '
                    + str(meta.get('path')))
        fresh_refs.append(fresh)
    fresh_view = [{k: v for k, v in r.items() if k != 'data_uri_chars'}
                  for r in fresh_refs]
    _compare_view(refs_meta, fresh_view, 'REF_SOURCE_STALE',
                  'reference metadata (drifted from current probe truth)')
    _compare_view(core.get('references'),
                  [{'role': r['role'], 'sha256': r['sha256'], 'bytes': r['bytes']}
                   for r in fresh_refs],
                  'PREPARED_RECORD_INVALID', 'fingerprint_core.references')
    binding = record.get('binding') or {}
    data, shot = verify_pass_shot(binding.get('case_dir'), binding.get('shot_id'))
    entry = next(e for e in data['review']['review']['shots']
                 if e['shot_id'] == binding.get('shot_id'))
    if entry['verdict'] != 'PASS':
        _refuse('SHOT_NOT_PASS', 'shot verdict changed since prepare')
    if ps.shot_content_hash(shot) != binding.get('content_sha256'):
        _refuse('SHOT_REVIEW_STALE', 'shot content hash changed since prepare')
    expected_binding = _expected_binding(data, shot, binding.get('case_dir'))
    _compare_view(record.get('binding'), expected_binding,
                  'PREPARED_BINDING_TAMPERED', 'binding')
    _compare_view(core.get('binding'), expected_binding,
                  'PREPARED_BINDING_TAMPERED', 'fingerprint_core.binding')
    primary = next(r for r in fresh_refs if r['role'] == 'reference_video')
    request = record.get('request') or {}
    # authoritative settings come from the DIGEST-VERIFIED outbound body — the
    # outer request view is itself subject to this comparison
    body_res = stored_body.get('resolution')
    body_dur = stored_body.get('duration')
    body_ratio = stored_body.get('ratio')
    duration = body_dur
    intent = build_test_intent(data, shot, duration, primary)
    intent_core = _test_intent_core(intent, duration)
    _compare_view(core.get('test_intent'), intent_core, 'TEST_INTENT_DRIFT',
                  'fingerprint_core.test_intent')
    _compare_view(record.get('test_intent'), intent_core, 'TEST_INTENT_DRIFT',
                  'test_intent (incl. silent/spoken/spoken_locked/audio policy)')
    rebuilt_prompt = render_ref2va_prompt(intent, data['storyboards']['product'],
                                          primary, ratio=body_ratio)
    _compare_view(core.get('prompt'), rebuilt_prompt, 'PREPARED_RECORD_INVALID',
                  'fingerprint_core.prompt')
    _compare_view(request, _request_view(body_res, body_dur, body_ratio,
                                         rebuilt_prompt),
                  'PREPARED_REQUEST_TAMPERED', 'request')
    _compare_view(record.get('provider'), _provider_view(),
                  'PREPARED_PROVIDER_TAMPERED', 'provider')
    rebuilt_body = build_request(rebuilt_prompt, body_res, body_dur, body_ratio,
                                 fresh_refs)
    _compare_view(stored_body, rebuilt_body, 'PREPARED_BODY_TAMPERED',
                  'outbound body (does not reproduce from current validated '
                  'media/settings/prompt)')
    counts = validate_reference_counts(fresh_refs)
    _compare_view(record.get('reference_counts'), counts, 'PREPARED_RECORD_INVALID',
                  'reference_counts')
    declarations_view = [{'ref_id': d['ref_id'], 'role': d['role']}
                         for d in (_declared_binding_for(data, shot, r['path'])
                                   for r in fresh_refs)]
    _compare_view(record.get('reference_declarations'), declarations_view,
                  'PREPARED_RECORD_INVALID', 'reference_declarations')
    pricing = load_config()['pricing']
    fresh_quote = quote(body_dur, body_res,
                        [r['duration_sec'] for r in fresh_refs
                         if r['role'] == 'reference_video'],
                        counts['images'], counts['audios'], pricing)
    _compare_view(record.get('quote'), fresh_quote, 'QUOTE_DRIFT',
                  'quote (line items, currency, policy hash, exact and upward-safe '
                  'totals)')
    return record


def _acquire_lock(fingerprint):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCK_DIR / ('%s.lock' % fingerprint)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        existing = {}
        try:
            existing = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            existing = {'state': 'unreadable'}
        _refuse('SUBMIT_LOCK_EXISTS', 'fingerprint %s… already has lock state=%r — one '
                'attempt only, never retried, never evaded by re-fingerprinting'
                % (fingerprint[:12], existing.get('state')))
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump({'state': 'uncertain', 'fingerprint': fingerprint, 'created_utc': _utc(),
                   'note': 'marker written BEFORE transport; survives timeout/network '
                           'error/ambiguous response to block resubmission'}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    return path


def _update_lock(path, **fields):
    payload = json.loads(path.read_text(encoding='utf-8'))
    payload.update(fields)
    payload['updated_utc'] = _utc()
    tmp = path.with_suffix('.lock.tmp-%s' % os.getpid())
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def guarded_submit(prepared_path, authorization_path, transport=None):
    """Future single paid submission to the OFFICIAL China endpoint. Refuses before
    ANY POST or state mutation unless: the prepared record revalidates (schema,
    fingerprint/core/body digest, fresh media hashes + full body/intent/quote
    rederivation + PASS shot binding), the prepared record lives inside PREPARED_DIR,
    a human authorization record INSIDE AUTH_DIR binds provider+model+base+exact
    fingerprint+reference hashes+pricing policy hash with max_calls=1/calls_used=0,
    a fresh valid numeric estimate that is at least the safe upward-cent total and
    <= a bounded CNY budget, and the tariff is not stale. Then: exclusive
    cross-process fingerprint lock, durable uncertainty marker BEFORE transport,
    ONE attempt, no retry (incl. 429), task_id persisted on success (official V2
    create returns a BARE task_id — no base_resp)."""
    transport = transport or _TRANSPORT
    prepared_path = _contained_file(prepared_path, PREPARED_DIR,
                                    'PREPARED_PATH_UNAUTHORIZED',
                                    'prepared records must live inside the prepared '
                                    'store')
    record = json.loads(prepared_path.read_text(encoding='utf-8'))
    record = _revalidate_prepared(record)
    fingerprint = record['fingerprint']
    auth_path = Path(authorization_path)
    if auth_path.is_symlink() or auth_path.resolve().is_symlink():
        _refuse('AUTH_RECORD_PATH_UNAUTHORIZED', 'authorization record symlink refused')
    auth_path = auth_path.resolve()
    if not auth_path.is_relative_to(AUTH_DIR.resolve()):
        _refuse('AUTH_RECORD_PATH_UNAUTHORIZED', 'authorization records must live inside '
                + str(AUTH_DIR) + '; outside path refused before any read/write/call')
    pricing = load_config()['pricing']
    if tariff_stale(pricing):
        _refuse('AUTH_TARIFF_STALE', 'pricing snapshot %s is stale (> %d days) and not '
                'explicitly reconfirmed — paid paths refuse'
                % (pricing.get('snapshot_date'), TARIFF_STALE_AFTER_DAYS))
    policy_hash = tariff_policy_hash(pricing)
    if not auth_path.is_file():
        _refuse('AUTH_RECORD_MISSING', str(auth_path)
                + ' — paid submission requires an explicit human authorization record')
    refs_hashes = [r['sha256'] for r in record['references']]
    auth = _load_authorization(auth_path, fingerprint, refs_hashes, policy_hash)
    local_quote = quote(record['request']['duration'], record['request']['resolution'],
                        [r['duration_sec'] for r in record['references']
                         if r['role'] == 'reference_video'],
                        record['reference_counts']['images'],
                        record['reference_counts']['audios'], pricing)
    est_value = Decimal(str(auth['cost_estimate']['estimate_cny']))
    safe_total = Decimal(local_quote['total_cny_safe'])
    if est_value < safe_total:
        _refuse('AUTH_ESTIMATE_MISMATCH', 'authorized estimate %s CNY is below the '
                'published-tariff recomputation for this exact request (exact %s, safe '
                'upward-cent %s) — insufficient; no tolerance rounding'
                % (est_value, local_quote['total_cny_exact'], local_quote['total_cny_safe']))
    base = resolve_base()
    key = _api_key(required=True)          # presence-checked before the lock
    _validate_bearer(key)
    lock_path = _acquire_lock(fingerprint)  # exclusive BEFORE any POST
    result = transport.post_create(base, key, record['request_body'])
    if result.get('transport_error'):
        _update_lock(lock_path, state='uncertain', reason='transport_timeout_or_network',
                     transport_error=result['transport_error'])
        _refuse('SUBMIT_UNCERTAIN_KEPT_LOCKED', 'no definitive response; uncertainty '
                'marker kept, automatic retry is forbidden')
    status = result.get('http_status')
    payload = result.get('json') if isinstance(result.get('json'), dict) else {}
    if status != 200 or _typed_error_info(payload) is not None:
        info = _typed_error_info(payload) or {}
        _update_lock(lock_path, state='failed', http_status=status,
                     error_type=info.get('error_type'))
        if status in _HTTP_MEANINGS:
            _typed_http_error(result)
        _refuse('SUBMIT_REJECTED', 'http_status=%r error_type=%r (sanitized)'
                % (status, info.get('error_type')))
    task_id = payload.get('task_id')      # official V2: bare task_id, no wrapper
    if not isinstance(task_id, str) or not SAFE_ID_PATTERN.fullmatch(task_id):
        _update_lock(lock_path, state='uncertain', reason='ambiguous_or_malformed_task_id')
        _refuse('SUBMIT_UNCERTAIN_KEPT_LOCKED', '200 response without a well-formed '
                'task_id is ambiguous; marker kept, no resubmit')
    _update_lock(lock_path, state='submitted', task_id=task_id)
    SUBMISSIONS_DIR.mkdir(parents=True, exist_ok=True)
    registration = {
        'schema': 'lab-h3-official-submission/v1', 'task_id': task_id,
        'fingerprint': fingerprint, 'submitted_utc': _utc(),
        'provider': 'minimax_official_china', 'model': MODEL, 'base_url': base,
        'prepared_path': str(prepared_path),
        'authorization_path': str(auth_path),
        'status': 'registered', 'download': 'out_of_scope_this_round',
    }
    _atomic_write(SUBMISSIONS_DIR / ('%s.json' % task_id), registration)
    auth['calls_used'] = 1
    auth['last_call_utc'] = _utc()
    auth['task_id'] = task_id
    auth_path.write_text(json.dumps(auth, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'state': 'SUBMITTED', 'task_id': task_id, 'fingerprint': fingerprint,
            'paid_calls': 1, 'note': 'one attempt, no retry; query via scripts '
            'h3-official-materials.py query --task'}


# ----------------------------------------------------------------- read-only query

def _registered_task(task_id):
    _validate_safe_id(task_id, 'task_id')   # strict BEFORE any filename/URL use
    path = SUBMISSIONS_DIR / ('%s.json' % task_id)
    if not path.is_file():
        _refuse('TASK_NOT_REGISTERED', 'query is restricted to task ids registered by '
                'this lab (%s)' % SUBMISSIONS_DIR)
    registration = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(registration, dict) \
            or registration.get('task_id') != task_id \
            or registration.get('provider') != 'minimax_official_china' \
            or registration.get('model') != MODEL \
            or registration.get('base_url') != CANONICAL_BASE \
            or not isinstance(registration.get('fingerprint'), str) \
            or len(registration.get('fingerprint') or '') != 64:
        _refuse('REGISTRATION_INVALID', 'local registration is malformed or misbound '
                '(task/provider/model/base/fingerprint)')
    return registration


def query(task_id, write_evidence=True):
    """Single read-only GET for a LOCALLY REGISTERED task id (30s bound, no retry,
    no polling loop, no auto-cancel). Official V2 shape: {"task": {"id", "model",
    "status", "content": {"url"}}}; status ∈ queued/running/succeeded/failed/
    cancelled; a succeeded task's URL must be valid HTTPS. Signed URLs are never
    printed or stored."""
    registration = _registered_task(task_id)
    base = resolve_base()
    key = _api_key(required=True)
    result = _TRANSPORT.get_task(base, key, task_id)
    if result.get('transport_error'):
        _refuse('QUERY_TRANSPORT_ERROR', 'single bounded GET failed (%s); no retry'
                % result['transport_error'])
    status = result.get('http_status')
    payload = result.get('json') if isinstance(result.get('json'), dict) else {}
    if status != 200:
        if status in _HTTP_MEANINGS:
            _typed_http_error(result)
        _refuse('QUERY_HTTP_ERROR', 'http_status=%r (sanitized)' % (status,))
    if _typed_error_info(payload) is not None:
        info = _typed_error_info(payload)
        _refuse('QUERY_TYPED_ERROR', 'official typed error type=%r request_id=%s '
                '(sanitized)' % (info.get('error_type'), info.get('request_id')))
    task = payload.get('task') if isinstance(payload.get('task'), dict) else None
    if not isinstance(task, dict):
        _refuse('QUERY_RESPONSE_MALFORMED', 'task object missing')
    returned_id = task.get('id')            # official V2 field is `id`, not task_id
    if returned_id != task_id:
        _refuse('QUERY_TASK_MISMATCH', 'returned task id %r != requested' % (returned_id,))
    model = task.get('model')
    if model is not None and model != MODEL:
        _refuse('QUERY_MODEL_MISMATCH', 'returned model %r != %s' % (model, MODEL))
    task_status = task.get('status')
    if task_status not in TASK_STATUSES:
        _refuse('QUERY_RESPONSE_MALFORMED', 'task.status %r not in official enum %s'
                % (task_status, list(TASK_STATUSES)))
    content = task.get('content') if isinstance(task.get('content'), dict) else {}
    url = content.get('url') if isinstance(content.get('url'), str) else None
    if task_status == 'succeeded':
        if not url:
            _refuse('QUERY_RESPONSE_MALFORMED', 'succeeded task without content.url')
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or not parsed.netloc:
            _refuse('QUERY_RESULT_URL_INVALID', 'succeeded task content.url is not a '
                    'valid HTTPS URL (scheme/host checked; URL never echoed)')
        url = url                              # valid; only its digest is reported
    summary = {
        'schema': 'lab-h3-official-query/v1', 'utc': _utc(), 'task_id': task_id,
        'fingerprint': registration['fingerprint'],
        'status': task_status, 'model': model,
        'result_url': 'present_but_redacted (signed URL never printed/stored)'
        if url else None,
        'result_url_sha256_16': hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
        if url else None,
        'attempts': 1, 'polling': False,
        'note': 'download/render out of scope this round; report stays pending until '
                'a real generated task exists',
    }
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = _utc().replace(':', '').replace('+', '_')
        (EVIDENCE_DIR / ('query-%s-%s.json' % (task_id, stamp))).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary
