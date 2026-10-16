"""h3-minimax-official-v1 tests (UNIT ONLY — no real network POST/GET executes and
no paid path runs here; every transport is a clearly-labelled stub).

Official-contract fixtures below are INDEPENDENTLY SOURCED from the official docs
re-fetched 2026-10-11 (not mirrored from the adapter's own output):
  create  https://platform.minimax.io/docs/api-reference/video-generation-v2-create
  query   https://platform.minimax.io/docs/api-reference/video-generation-v2-query
  list    https://platform.minimax.io/docs/api-reference/video-generation-v2-list
  guide   https://platform.minimax.cn/docs/guides/video-generation

Covers: pinned China base + env-only credential handling, strict official V2
request contract (nested url objects + reference_* roles, model/resolution/
7-value ratios/duration int/prompt, AutoDL field leakage refusal), local media
fail-closed validation (schemes, symlinks, path authorization, dims/aspect/fps/
codecs/durations/counts, SILENT video allowed, images probed without duration),
real stdlib PNG/WAV probing, mixed declared references (multi-video/image/audio),
Base64 body ceiling with standard MIME types, fingerprint binding the outbound
body digest + tamper rejection (body/refs/quote/intent drift, legacy v1 records),
path/identifier containment (no unsafe-id sanitization), published-tariff quote
math with EXACT Decimal totals and safe upward-cent budget values (5.79166650 →
5.80; 9.26666640 → 9.27; 5.79 insufficient is rejected), credential header-safety
canary (malformed key never leaks into any message/stdout/stderr), offline doctor
honesty (no key ⇒ auth not_run), guarded submit chain (auth dir containment, full
binding set, exact numeric types, estimate freshness/sufficiency/mismatch/budget,
lock + uncertainty marker BEFORE transport, one attempt no retry incl. timeout,
429 and ambiguous/malformed task_id, task_id persistence, redaction) and the
read-only official query contract (registered ids only, task.id/model/status enum,
HTTPS result URL validation, signed-URL redaction).

All tests run against an ISOLATED TEMP runtime tree (paths monkeypatched to
tmp_path) — no UNIT authorization record is ever written into the production
runtime/h3-minimax-official-v1/authorization directory.
"""
import copy
import json
import os
import re
import struct
import subprocess
import sys
import wave
import zlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import h3_minimax_official as off  # noqa: E402
from agents import product_storyboards as ps  # noqa: E402

CASE_DIR = ROOT / 'cases' / 'koc-dragonfruit-dryrun'
A1 = off.AUTHORIZED_VIDEO_SOURCE
A2_PATH = ('F:/codex/laox_account/video_postprod/artifacts/imports/41/'
           '41063ea7800ee86a4ca2bee8194b64aa02487888d25f8228e37ace87cfe0382d.mp4')
A2_SHA = '41063ea7800ee86a4ca2bee8194b64aa02487888d25f8228e37ace87cfe0382d'
LEGACY_PREPARED_REL = ('runtime/h3-minimax-official-v1/prepared/'
                       '3a1845be8913276d379dbf929461e868ae8271da06664e22f858a638f34fe992.json')

# --- verbatim official examples (platform.minimax.io v2 docs, fetched 2026-10-11)
OFFICIAL_CREATE_OK = {'task_id': '424010985738629'}
OFFICIAL_ERROR_429 = {
    'type': 'error',
    'error': {'type': 'rate_limit_error',
              'message': 'rate limit, please retry later (1002)',
              'http_code': '429'},
    'request_id': '021785229015510a2c883cf675b9804d',
}
OFFICIAL_ERROR_401 = {
    'type': 'error',
    'error': {'type': 'authorized_error', 'message': '(1004)', 'http_code': '401'},
    'request_id': '021785229015510a2c883cf675b9804d',
}
OFFICIAL_LIST_OK = {'items': [], 'total': 0}
OFFICIAL_REF2VA_BLOCKS = [
    {'type': 'text', 'text': 'A boy playing basketball by the sea'},
    {'type': 'video_url', 'video_url': {'url': 'https://cdn.example/ref.mp4'},
     'role': 'reference_video'},
    {'type': 'image_url', 'image_url': {'url': 'https://cdn.example/ref.png'},
     'role': 'reference_image'},
    {'type': 'audio_url', 'audio_url': {'url': 'https://cdn.example/ref.mp3'},
     'role': 'reference_audio'},
]
OFFICIAL_TASK_SUCCEEDED = {
    'task': {'id': '424010985738629', 'model': 'MiniMax-H3',
             'status': 'succeeded',
             'content': {'url': 'https://video-product.cdn.minimax.io/out.mp4?sig=X'}},
}


def _refuse(fn, code):
    with pytest.raises(off.H3OfficialError, match=code):
        fn()


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    """Temp runtime tree: prepared/authorization/locks/submissions/evidence all
    under tmp so tests never touch production paths."""
    store = tmp_path / 'runtime' / 'h3-minimax-official-v1'
    for name, attr in (('prepared', 'PREPARED_DIR'), ('authorization', 'AUTH_DIR'),
                       ('locks', 'LOCK_DIR'), ('submissions', 'SUBMISSIONS_DIR')):
        monkeypatch.setattr(off, attr, store / name)
    monkeypatch.setattr(off, 'EVIDENCE_DIR', tmp_path / 'runtime' / 'evidence' / 'off')
    return store


# -------------------------------------------------- real stdlib media builders

def _write_png(path, width=256, height=256):
    """Minimal real grayscale PNG built with stdlib zlib/struct (no PIL)."""
    raw = b''.join(b'\x00' + b'\x80' * width for _ in range(height))

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data
                + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    payload = (b'\x89PNG\r\n\x1a\n'
               + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 0, 0, 0, 0))
               + chunk(b'IDAT', zlib.compress(raw, 9))
               + chunk(b'IEND', b''))
    path.write_bytes(payload)
    return path


def _write_wav(path, seconds=2.5, rate=8000):
    """Real silent WAV via the stdlib wave module."""
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(b'\x80' * int(rate * seconds))
    return path


def _build_case(tmp_path, monkeypatch, with_image=False, with_audio=False,
                extra_video=False):
    """Synthetic case inside a TEMP lab root (containment-honest): a copy of the
    real dragonfruit case whose shot-007 additionally declares optional
    image/audio/extra-video refs, with the review content hash recomputed so the
    deterministic reviewer stays consistent. Extra assets are created INSIDE the
    temp lab (a2 is copied there read-only from the case's declared import).
    Returns (case_dir, {'image':…, 'audio':…, 'extra_video':…})."""
    lab = tmp_path / 'lab'
    assets = lab / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    png = _write_png(assets / 'pack.png') if with_image else None
    wav = _write_wav(assets / 'room.wav', seconds=2.5) if with_audio else None
    a2_copy = None
    if extra_video:
        a2_copy = lab / 'imports' / 'a2.mp4'
        a2_copy.parent.mkdir(parents=True, exist_ok=True)
        a2_copy.write_bytes(Path(A2_PATH).read_bytes())
    case = lab / 'cases' / 'mixed-case'
    case.mkdir(parents=True, exist_ok=True)
    data = {name: json.loads((CASE_DIR / ('%s.json' % name)).read_text(encoding='utf-8'))
            for name in ('case', 'storyboards', 'review')}
    plan = data['storyboards']
    shot = next(s for s in plan['shots'] if s['shot_id'] == 'shot-007')
    if png is not None:
        plan['refs'].append({'ref_id': 'img1', 'path': str(png),
                             'sha256': off._sha256_file(png),
                             'role': 'packaging_image_reference', 'test_only': True})
        shot['refs'].append({'ref_id': 'img1', 'role': 'packaging_image_reference'})
    if wav is not None:
        plan['refs'].append({'ref_id': 'aud1', 'path': str(wav),
                             'sha256': off._sha256_file(wav),
                             'role': 'ambient_audio_reference', 'test_only': True})
        shot['refs'].append({'ref_id': 'aud1', 'role': 'ambient_audio_reference'})
    if a2_copy is not None:
        plan['refs'].append({'ref_id': 'a2', 'path': str(a2_copy),
                             'sha256': A2_SHA,
                             'role': 'demo_footage_reference', 'test_only': True})
        shot['refs'].append({'ref_id': 'a2', 'role': 'demo_footage_reference'})
    entry = next(e for e in data['review']['review']['shots']
                 if e['shot_id'] == 'shot-007')
    entry['content_sha256'] = ps.shot_content_hash(shot)
    for name, payload in data.items():
        (case / ('%s.json' % name)).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    monkeypatch.setattr(off, 'LAB_ROOT', lab)
    return case, {'image': png, 'audio': wav, 'extra_video': a2_copy}


# ------------------------------------------------------------------ config/base

def test_config_loads_with_pinned_china_base_and_policy_hash():
    cfg = off.load_config()
    assert cfg['provider']['base_url'] == off.CANONICAL_BASE == 'https://api.minimax.cn'
    assert cfg['model']['name'] == 'MiniMax-H3'
    assert len(cfg['model']['ratios']) == 7          # exactly seven official ratios
    assert off.current_policy_hash() == off.tariff_policy_hash(cfg['pricing'])
    assert len(off.current_policy_hash()) == 64


def test_base_url_override_only_accepts_pinned_domain(monkeypatch):
    assert off.resolve_base() == off.CANONICAL_BASE
    monkeypatch.setenv('MINIMAX_API_BASE_URL', off.CANONICAL_BASE)
    assert off.resolve_base() == off.CANONICAL_BASE
    for bad in ('https://api.minimaxi.com', 'http://api.minimax.cn',
                'https://evil.example.com', 'https://api.minimax.cn/'):
        monkeypatch.setenv('MINIMAX_API_BASE_URL', bad)
        _refuse(off.resolve_base, 'BASE_URL_NOT_VERIFIED')
    monkeypatch.delenv('MINIMAX_API_BASE_URL')
    assert off.resolve_base() == off.CANONICAL_BASE


def test_tariff_malformed_variants_refuse(tmp_path, monkeypatch):
    good = copy.deepcopy(off.load_config())
    bads = [
        {'rates': dict(good['pricing']['rates'],
                       output_per_sec={'768P': '-0.50', '2K': '0.80'})},   # negative
        {'rates': dict(good['pricing']['rates'],
                       output_per_sec={'768P': True, '2K': '0.80'})},      # bool
        {'rates': dict(good['pricing']['rates'],
                       input_image_extra_each='0.2')},                     # 1 decimal
        {'rates': dict(good['pricing']['rates'],
                       input_image_first_free=-1)},                        # negative int
        {'rates': dict(good['pricing']['rates'],
                       input_video_per_sec={'768P': None, '2K': '0.80'})},  # missing
        {'currency': 'USD'},
        {'snapshot_date': 'not-a-date'},
    ]
    for over in bads:
        broken = copy.deepcopy(good['pricing'])
        broken.update(over)
        _refuse(lambda b=broken: off.tariff_policy_hash(b), 'TARIFF_MALFORMED')


def test_tariff_staleness_rules():
    pricing = copy.deepcopy(off.load_config()['pricing'])
    assert off.tariff_stale(pricing) is False               # snapshot 2026-10-11 fresh
    old = dict(pricing, snapshot_date='2025-01-01')
    assert off.tariff_stale(old) is True
    reconfirmed = dict(old, reconfirmed_utc=datetime.now(timezone.utc).isoformat())
    assert off.tariff_stale(reconfirmed) is False           # explicit reconfirm wins


# ------------------------------------------------------------------- quote math

def test_quote_exact_decimal_and_safe_upward_cent_totals():
    q768 = off.quote(5, '768P', [6.583333])
    assert q768['total_cny_exact'] == '5.79166650'          # exact, never rounded down
    assert Decimal(str(q768['total_cny'])) == Decimal('5.80')   # safe upward-cent
    assert q768['total_cny_safe'] == '5.80'
    q2k = off.quote(5, '2K', [6.583333])
    assert q2k['total_cny_exact'] == '9.26666640'
    assert Decimal(str(q2k['total_cny'])) == Decimal('9.27')
    items = {line['item']: line for line in q768['line_items']}
    assert items['output_video']['amount'] == '2.50'
    assert items['input_video_seconds']['amount'] == '3.29166650'
    assert items['input_audio']['amount'] == '0.00'
    assert q768['policy_sha256'] == off.current_policy_hash()


def test_quote_counts_input_video_seconds_and_image_extras():
    assert off.quote(5, '768P', [5.0])['total_cny_safe'] == '5.00'
    assert off.quote(5, '2K', [5.0])['total_cny_safe'] == '8.00'
    # first 5 images free, extras 0.20 each (exact decimal preserved)
    assert off.quote(5, '768P', [5.0], image_count=5)['total_cny_safe'] == '5.00'
    assert off.quote(5, '768P', [5.0], image_count=6)['total_cny_safe'] == '5.20'
    assert off.quote(5, '768P', [5.0], image_count=9)['total_cny_safe'] == '5.80'
    # multiple input videos sum into the bill
    assert off.quote(6, '768P', [5.0, 4.0])['total_cny_safe'] == '7.50'


def test_quote_input_guards():
    for bad_dur in (5.0, True, '5', 3, 16, None):
        _refuse(lambda d=bad_dur: off.quote(d, '768P', [5.0]), 'DURATION')
    _refuse(lambda: off.quote(5, '1080P', [5.0]), 'RESOLUTION_INVALID')
    _refuse(lambda: off.quote(5, '768P', [None]), 'QUOTE_INPUT_INVALID')
    _refuse(lambda: off.quote(5, '768P', [float('nan')]), 'QUOTE_INPUT_INVALID')
    _refuse(lambda: off.quote(5, '768P', [-3.0]), 'QUOTE_INPUT_INVALID')
    _refuse(lambda: off.quote(5, '768P', [5.0], image_count=True), 'QUOTE_INPUT_INVALID')
    _refuse(lambda: off.quote(5, '768P', [5.0], image_count=-1), 'QUOTE_INPUT_INVALID')


# ------------------------------------------------------- official V2 contract

def test_request_field_validation_strict():
    assert off.validate_duration(5) == 5
    for bad in (5.0, True, '5', None, float('nan')):
        _refuse(lambda b=bad: off.validate_duration(b), 'DURATION_NOT_INTEGER')
    for bad in (3, 16, 0, -1):
        _refuse(lambda b=bad: off.validate_duration(b), 'DURATION_OUT_OF_RANGE')
    assert off.validate_resolution('768P') == '768P'
    assert off.validate_resolution('2K') == '2K'
    for bad in ('768p竖(768*1376)', '1080P', '768p', '', None):
        _refuse(lambda b=bad: off.validate_resolution(b), 'RESOLUTION_INVALID')  # AutoDL label
    assert off.validate_ratio('9:16') == '9:16'
    assert off.validate_ratio('adaptive') == 'adaptive'
    for bad in ('16:10', '9:16 ', 'adaptive ', 'vertical', None):
        _refuse(lambda b=bad: off.validate_ratio(b), 'RATIO_INVALID')
    assert off.validate_prompt('x' * 7000)
    _refuse(lambda: off.validate_prompt('x' * 7001), 'PROMPT_INVALID')
    _refuse(lambda: off.validate_prompt('   '), 'PROMPT_INVALID')
    _refuse(lambda: off.validate_prompt(None), 'PROMPT_INVALID')


def test_content_blocks_accept_official_example_reject_drift():
    # independently sourced official shape: nested url object + explicit role
    off.validate_content_blocks(json.loads(json.dumps(OFFICIAL_REF2VA_BLOCKS)))
    for role in ('first_frame', 'last_frame', 'mixed', 'base_video'):
        _refuse(lambda r=role: off.validate_content_blocks(
            [{'type': 'text', 'text': 'p'},
             {'type': 'image_url', 'image_url': {'url': 'https://x/i.png'}, 'role': r}]),
            'CONTENT_ROLE_REJECTED')
    # role must match the block type
    _refuse(lambda: off.validate_content_blocks(
        [{'type': 'text', 'text': 'p'},
         {'type': 'image_url', 'image_url': {'url': 'https://x/i.png'},
          'role': 'reference_video'}]),
        'CONTENT_ROLE_REJECTED')
    # old mirrored shape (type=reference_video, string url, no role) must refuse
    _refuse(lambda: off.validate_content_blocks(
        [{'type': 'text', 'text': 'p'},
         {'type': 'reference_video', 'video_url': 'data:video/mp4;base64,QQ=='}]),
        'CONTENT_ROLE_REJECTED')
    # url must be a nested object, not a string
    _refuse(lambda: off.validate_content_blocks(
        [{'type': 'text', 'text': 'p'},
         {'type': 'video_url', 'video_url': 'https://x/v.mp4',
          'role': 'reference_video'}]),
        'CONTENT_BLOCK_FIELDS_INVALID')
    for extra in ({'x': 1}, {'url': 'https://x/v.mp4', 'extra': 2}):
        _refuse(lambda e=extra: off.validate_content_blocks(
            [{'type': 'text', 'text': 'p'},
             {'type': 'video_url', 'video_url': e, 'role': 'reference_video'}]),
            'CONTENT_BLOCK_FIELDS_INVALID')
    _refuse(lambda: off.validate_content_blocks(
        [{'type': 'text', 'text': 'p'}]), 'CONTENT_BLOCKS_INVALID')  # no reference
    _refuse(lambda: off.validate_content_blocks(
        [{'type': 'video_url', 'video_url': {'url': 'https://x'},
          'role': 'reference_video'}]), 'CONTENT_BLOCKS_INVALID')   # text required
    _refuse(lambda: off.validate_content_blocks(
        [{'type': 'text', 'text': 'p'}, {'type': 'text', 'text': 'q'},
         {'type': 'video_url', 'video_url': {'url': 'https://x'},
          'role': 'reference_video'}]), 'CONTENT_BLOCKS_INVALID')   # exactly one text
    _refuse(lambda: off.validate_content_blocks(
        [{'type': 'text', 'text': 'p', 'extra': 1}]), 'CONTENT_BLOCK_FIELDS_INVALID')


def test_build_request_official_shape_and_autodl_leak_refusal(tmp_path, monkeypatch):
    media = tmp_path / 'ref.mp4'
    media.write_bytes(b'unit')                    # bytes never probed here (meta injected)
    meta = {'role': 'reference_video', 'path': str(media), 'bytes': 3,
            'duration_sec': 6.5, 'width': 768, 'height': 1376, 'fps': 24.0,
            'vcodec': 'h264', 'acodec': 'aac', 'sha256': 'a' * 64}
    monkeypatch.setattr(off, '_data_uri',
                        lambda m: ('data:video/mp4;base64,VU5JVAA=', 24))
    body = off.build_request('prompt text', '768P', 5, '9:16', [meta])
    assert set(body) == {'model', 'content', 'resolution', 'duration', 'ratio'}
    assert body['model'] == 'MiniMax-H3'
    assert body['duration'] == 5 and type(body['duration']) is int
    assert body['content'][0] == {'type': 'text', 'text': 'prompt text'}
    ref_block = body['content'][1]
    assert ref_block['type'] == 'video_url'
    assert ref_block['role'] == 'reference_video'
    assert isinstance(ref_block['video_url'], dict)
    assert set(ref_block['video_url']) == {'url'}
    assert ref_block['video_url']['url'].startswith('data:video/mp4;base64,')
    for autodl_field in ('workflow', 'input', 'ref_image_0', 'ref_audio_0', 'seed', 'uuid'):
        assert autodl_field not in body


def test_build_request_standard_mime_types(tmp_path, monkeypatch):
    metas = []
    for role, ext, sha in (('reference_video', '.mov', 'b' * 64),
                           ('reference_image', '.jpg', 'c' * 64),
                           ('reference_audio', '.mp3', 'd' * 64)):
        p = tmp_path / ('m' + ext)
        p.write_bytes(b'unit')
        m = {'role': role, 'path': str(p), 'bytes': 4, 'sha256': sha}
        if role == 'reference_video':
            m.update({'duration_sec': 6.0, 'width': 768, 'height': 1376, 'fps': 24.0,
                      'vcodec': 'h264', 'acodec': None})
        metas.append(m)
    uris = {}
    monkeypatch.setattr(off, '_data_uri',
                        lambda m: (uris.setdefault(m['role'], 'data:x;base64,QQ=='), 8))
    body = off.build_request('p', '768P', 5, '9:16', metas)
    by_role = {b['role']: b for b in body['content'] if b['role']}
    assert by_role['reference_video']['type'] == 'video_url'
    assert by_role['reference_image']['type'] == 'image_url'
    assert by_role['reference_audio']['type'] == 'audio_url'
    exts = {'.mov': 'video/quicktime', '.jpg': 'image/jpeg', '.mp3': 'audio/mpeg'}
    for meta, (role, uri) in zip(metas, uris.items()):
        assert uri == 'data:%s;base64,QQ==' % exts[Path(meta['path']).suffix]


def test_build_request_body_ceiling_preflight(tmp_path, monkeypatch):
    meta = {'role': 'reference_video', 'path': 'x.mp4', 'bytes': 50 * 1024 * 1024,
            'duration_sec': 6.5, 'width': 768, 'height': 1376, 'fps': 24.0,
            'vcodec': 'h264', 'acodec': 'aac', 'sha256': 'a' * 64}
    monkeypatch.setattr(off, 'BODY_MAX_BYTES', 1024)
    _refuse(lambda: off.build_request('p', '768P', 5, '9:16', [meta]),
            'BODY_TOO_LARGE')                       # refused BEFORE any large read


# --------------------------------------------------- local media validation

def _video_limits():
    return off.load_config()['media_limits']


def _fake_probe(monkeypatch, video=None, image=None, audio=None):
    monkeypatch.setattr(off, 'probe_video', lambda p: dict(video))
    monkeypatch.setattr(off, 'probe_image', lambda p: dict(image or {}))
    monkeypatch.setattr(off, 'probe_audio', lambda p: dict(audio or {}))


def test_reference_schemes_and_paths_refused(tmp_path, monkeypatch):
    _refuse(lambda: off.validate_reference('reference_video', 'https://cdn/x.mp4'),
            'PUBLIC_URL_REFERENCE_NOT_IMPLEMENTED')
    _refuse(lambda: off.validate_reference('reference_video', 'data:video/mp4;base64,QQ=='),
            'PUBLIC_URL_REFERENCE_NOT_IMPLEMENTED')
    _refuse(lambda: off.validate_reference('reference_image', 'file:///F:/x/a.png'),
            'REFERENCE_FILE_SCHEME_REFUSED')
    for role in ('first_frame', 'last_frame', 'mixed', 'text', 'video', ''):
        _refuse(lambda r=role: off.validate_reference(r, 'F:/x/a.mp4'),
                'REFERENCE_ROLE_INVALID')
    outside = tmp_path / 'outside.mp4'
    outside.write_bytes(b'unit')
    _refuse(lambda: off.validate_reference('reference_video', outside),
            'LOCAL_REFERENCE_PATH_UNAUTHORIZED')     # not lab, not pinned a1
    try:
        link = tmp_path / 'link.mp4'
        link.symlink_to(outside)
    except OSError:
        pytest.skip('symlink creation not permitted on this host')
    _refuse(lambda: off.validate_reference('reference_video', link),
            'REFERENCE_SYMLINK_REFUSED')


def test_reference_wrong_pin_hash_refused(monkeypatch):
    # pinned a1 path exists but bytes differ — probe-free pre-check must fire first
    monkeypatch.setattr(off, '_sha256_file', lambda p: '0' * 64)
    _refuse(lambda: off.validate_reference('reference_video', A1['path']),
            'REFERENCE_HASH_MISMATCH')


def test_reference_metadata_bounds_refused(tmp_path, monkeypatch):
    base = {'duration_sec': 6.583333, 'width': 768, 'height': 1376, 'fps': 24.0,
            'vcodec': 'h264', 'acodec': 'aac'}
    inside = tmp_path / 'lab.mp4'                    # tmp not inside lab → patch root
    monkeypatch.setattr(off, 'LAB_ROOT', tmp_path)
    inside.write_bytes(b'unit')
    good = dict(base)
    monkeypatch.setattr(off, '_sha256_file', lambda p: 'f' * 64)
    _fake_probe(monkeypatch, video=good)
    meta = off.validate_reference('reference_video', inside)
    assert meta['sha256'] == 'f' * 64 and meta['acodec'] == 'aac'
    # SILENT video (no audio track) is allowed
    _fake_probe(monkeypatch, video=dict(base, acodec=None))
    silent = off.validate_reference('reference_video', inside)
    assert silent['acodec'] is None
    bads = [
        dict(base, vcodec='vp9'), dict(base, acodec='opus'),
        dict(base, fps=23.0), dict(base, fps=61.0), dict(base, fps=float('nan')),
        dict(base, width=200), dict(base, height=6000),
        dict(base, width=768, height=2600),           # aspect 0.295 < 0.4
        dict(base, duration_sec=1.5), dict(base, duration_sec=15.5),
        dict(base, duration_sec=float('inf')),
    ]
    for bad in bads:
        _fake_probe(monkeypatch, video=bad)
        _refuse(lambda b=bad: off.validate_reference('reference_video', inside),
                'MEDIA_(CODEC|FPS|DIMS|ASPECT|DURATION)_')
    monkeypatch.setattr(off, 'probe_image', lambda p: {'width': 3000, 'height': 7000})
    _refuse(lambda: off.validate_reference('reference_image',
                                           (lambda pp: (pp.write_bytes(b'x'), str(pp))[1])(
                                               tmp_path / 'i.png')),
            'MEDIA_DIMS_OUT_OF_RANGE')
    big = tmp_path / 'big.mp4'
    big.write_bytes(b'x' * 64)
    monkeypatch.setattr(off, 'probe_video', lambda p: dict(base))
    _refuse(lambda: off.validate_reference('reference_video', big,
                                           dict(_video_limits(), video=dict(
                                               _video_limits()['video'],
                                               max_bytes=10))),
            'MEDIA_SIZE_OUT_OF_RANGE')


def test_real_stdlib_png_probes_without_duration(tmp_path, monkeypatch):
    """R7: a REAL still image carries no format.duration — the image probe must
    read stream dims only (real ffprobe, stdlib-built PNG inside a temp lab)."""
    monkeypatch.setattr(off, 'LAB_ROOT', tmp_path)
    png = _write_png(tmp_path / 'real.png')
    meta = off.validate_reference('reference_image', png)
    assert meta['width'] == 256 and meta['height'] == 256
    assert 'duration_sec' not in meta                 # images are not duration-gated
    assert meta['sha256'] == off._sha256_file(png)


def test_real_stdlib_wav_audio_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(off, 'LAB_ROOT', tmp_path)
    wav = _write_wav(tmp_path / 'real.wav', seconds=2.5)
    meta = off.validate_reference('reference_audio', wav)
    assert 2.0 <= meta['duration_sec'] <= 3.0
    assert meta['acodec']                            # real codec from real ffprobe


def test_reference_counts_and_sums():
    def m(role, dur=6.0):
        return {'role': role, 'duration_sec': dur}
    assert off.validate_reference_counts([m('reference_video')])['videos'] == 1
    _refuse(lambda: off.validate_reference_counts([]), 'REF2VA_REFERENCE_REQUIRED')
    _refuse(lambda: off.validate_reference_counts(
        [m('reference_image')] * 10), 'MEDIA_COUNT_EXCEEDED')
    _refuse(lambda: off.validate_reference_counts(
        [m('reference_video')] * 4), 'MEDIA_COUNT_EXCEEDED')
    _refuse(lambda: off.validate_reference_counts(
        [m('reference_audio')] * 4), 'MEDIA_COUNT_EXCEEDED')
    _refuse(lambda: off.validate_reference_counts(
        [m('reference_image', 4.0)] * 7 + [m('reference_video', 4.0)] * 3
        + [m('reference_audio', 4.0)] * 3),
        'MEDIA_COUNT_EXCEEDED')                        # mixed 13 > 12 (sums stay legal)
    _refuse(lambda: off.validate_reference_counts(
        [m('reference_video', 10.0), m('reference_video', 10.0)]),
        'MEDIA_DURATION_OUT_OF_RANGE')                 # sum 20 > 15
    _refuse(lambda: off.validate_reference_counts(
        [m('reference_audio', 10.0), m('reference_audio', 10.0)]),
        'MEDIA_DURATION_OUT_OF_RANGE')


# ------------------------------------------------------- case binding + intent

def test_verify_pass_shot_real_case():
    _, shot = off.verify_pass_shot(CASE_DIR, 'shot-007')
    assert shot['duration_sec'] == 2.8 and shot['purpose'] == 'detail'
    assert shot['refs'][0]['ref_id'] == 'a1'
    _refuse(lambda: off.verify_pass_shot(CASE_DIR, 'shot-999'), 'SHOT_NOT_FOUND')
    _refuse(lambda: off.verify_pass_shot(CASE_DIR, '../review.json'), 'ID_INVALID')


def test_case_dir_containment(tmp_path, monkeypatch):
    monkeypatch.setattr(off, 'LAB_ROOT', tmp_path / 'lab')
    (tmp_path / 'elsewhere').mkdir(parents=True, exist_ok=True)
    _refuse(lambda: off.load_case(tmp_path / 'elsewhere'), 'CASE_DIR_UNAUTHORIZED')


def test_test_intent_new_id_and_review_provenance():
    data = off.load_case(CASE_DIR)
    shot = next(s for s in data['storyboards']['shots'] if s['shot_id'] == 'shot-007')
    video_meta = {'sha256': A1['sha256'], 'duration_sec': 6.583333}
    intent = off.build_test_intent(data, shot, 5, video_meta)
    assert intent['intent_id'] == 'shot-007-h3off5s'
    assert intent['intent_id'] not in {s['shot_id'] for s in data['storyboards']['shots']}
    assert intent['storyboard_binding']['planned_duration_sec'] == 2.8   # original kept
    assert intent['generation_intent']['duration_sec'] == 5
    assert intent['generation_intent']['silent'] is True
    assert intent['review']['shots'][0]['verdict'] == 'PASS'
    assert 'NOT the native storyboardTeam LLM' in intent['review_provenance']
    assert intent['mini_plan']['planning']['h3_official_test_intent'][
        'reference_video_sha256'] == A1['sha256']
    _refuse(lambda: off.build_test_intent(data, shot, 5, video_meta,
                                          intent_id='shot-007'),
            'TEST_INTENT_ID_COLLISION')


def test_ref2va_prompt_silent_ratio_follows_config():
    data = off.load_case(CASE_DIR)
    shot = next(s for s in data['storyboards']['shots'] if s['shot_id'] == 'shot-007')
    video_meta = {'sha256': A1['sha256'], 'duration_sec': 6.583333}
    intent = off.build_test_intent(data, shot, 5, video_meta)
    prompt = off.render_ref2va_prompt(intent, data['storyboards']['product'], video_meta)
    for heading in ('subject_definitions:', 'summary:', 'retention_analysis:',
                    'detailed_description:', 'overall_soundscape:', 'non_diegetic_music:'):
        assert heading in prompt
    assert '<d>' not in prompt and '</d>' not in prompt
    assert '<Video 1>' in prompt and '<Subject 1>' in prompt
    assert '[reference generation]' in prompt
    assert 'no fixed corner overlays' in prompt
    assert len(prompt) <= 7000
    # the framing statement follows the CONFIGURED ratio, not a hardcoded one
    assert 'vertical 9:16 framing' in prompt
    wide = off.render_ref2va_prompt(intent, data['storyboards']['product'], video_meta,
                                    ratio='16:9')
    assert 'vertical 16:9 framing' in wide


# ------------------------------------------------------------- real prepare

def test_prepare_real_a1_local_only(isolated_runtime):
    """A2 positive path against the REAL pinned a1 bytes (read-only): full ffprobe
    truth, strict official body, exact quote with input seconds, atomic fingerprint-
    named record whose core binds the outbound body digest, zero HTTP/uploads/paid."""
    evidence = off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=5,
                           video_path=A1['path'])
    assert evidence['generation_intent']['reference_video_sha256'] == A1['sha256']
    probe = evidence['reference_media']['ffprobe']
    assert probe['duration_sec'] == pytest.approx(6.583333, abs=1e-3)
    assert probe['vcodec'] == 'h264' and probe['acodec'] == 'aac'
    assert probe['width'] == 768 and probe['height'] == 1376
    assert evidence['quote']['total_cny_exact'] == '5.79166650'      # exact decimal
    assert Decimal(str(evidence['quote']['total_cny'])) == Decimal('5.80')  # safe
    assert evidence['guards'] == {'paid_calls': 0, 'uploads': 0, 'http_calls': 0,
                                  'base64_in_evidence': False, 'secrets_in_evidence': False}
    serialized = json.dumps(evidence)
    assert 'base64,' not in serialized                # no media bytes in evidence
    assert 'MINIMAX_API_KEY' not in serialized
    prepared = json.loads(Path(evidence['prepared_path']).read_text(encoding='utf-8'))
    assert prepared['fingerprint'] == evidence['fingerprint']
    assert prepared['schema'] == off.PREPARED_SCHEMA == 'lab-h3-official-prepared/v2'
    assert prepared['binding']['planned_duration_sec'] == 2.8
    assert prepared['binding']['review_verdict'] == 'PASS'
    assert prepared['fixture_honesty']['auto_edit_eligible'] is False
    ref_block = prepared['request_body']['content'][1]
    assert ref_block['type'] == 'video_url' and ref_block['role'] == 'reference_video'
    assert ref_block['video_url']['url'].startswith('data:video/mp4;base64,')
    assert prepared['request_body_sha256'] == \
        prepared['fingerprint_core']['request_body_sha256']
    # immutable: same fingerprint + different core refuses; identical rerun idempotent
    again = off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=5,
                        video_path=A1['path'])
    assert again['fingerprint'] == evidence['fingerprint']
    tampered = copy.deepcopy(prepared)
    tampered['fingerprint_core']['prompt'] += ' tampered'
    tampered['fingerprint'] = off._sha256_canonical(tampered['fingerprint_core'])
    path = Path(evidence['prepared_path'])
    keep = path.read_text(encoding='utf-8')
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
    _refuse(lambda: off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=5,
                                video_path=A1['path']), 'PREPARED_IMMUTABLE_REFUSED')
    path.write_text(keep, encoding='utf-8')          # restore identical record
    # stored outbound body tampered while the core stays untouched → refused on reuse
    body_tampered = copy.deepcopy(prepared)
    body_tampered['request_body']['model'] = 'MiniMax-WRONG'
    path.write_text(json.dumps(body_tampered, ensure_ascii=False), encoding='utf-8')
    _refuse(lambda: off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=5,
                                video_path=A1['path']), 'PREPARED_BODY_TAMPERED')
    path.write_text(keep, encoding='utf-8')          # restore identical record


def test_prepare_rejects_non_a1_video_and_bad_duration(isolated_runtime, tmp_path):
    _refuse(lambda: off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=5,
                                video_path=tmp_path / 'nope.mp4'),
            'LOCAL_REFERENCE_PATH_UNAUTHORIZED')
    _refuse(lambda: off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=3,
                                video_path=A1['path']), 'DURATION_OUT_OF_RANGE')
    _refuse(lambda: off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=2.8,
                                video_path=A1['path']), 'DURATION_NOT_INTEGER')


def test_prepare_rejects_undeclared_reference(tmp_path, monkeypatch):
    """R5: a generation reference must be a DECLARED reviewed reference of the
    bound shot — an arbitrary lab file (even inside the lab root) refuses."""
    stray = tmp_path / 'stray.mp4'
    stray.write_bytes(b'not declared anywhere')
    case, _ = _build_case(tmp_path, monkeypatch)
    _refuse(lambda: off.prepare(case_dir=case, shot_id='shot-007', duration=5,
                                video_path=stray),
            'REFERENCE_NOT_DECLARED_FOR_SHOT')
    undeclared_png = _write_png(tmp_path / 'undeclared.png')
    _refuse(lambda: off.prepare(case_dir=case, shot_id='shot-007', duration=5,
                                video_path=A1['path'],
                                image_paths=[undeclared_png]),
            'REFERENCE_NOT_DECLARED_FOR_SHOT')


def test_prepare_mixed_references_official_shape_and_cost(tmp_path, monkeypatch,
                                                          isolated_runtime):
    """R7: mixed declared references (video + image + audio) build official
    content blocks with standard MIME types and honest per-type counts; the PNG
    and WAV are REAL stdlib-generated files probed by the REAL ffprobe."""
    case, assets = _build_case(tmp_path, monkeypatch, with_image=True, with_audio=True)
    evidence = off.prepare(case_dir=case, shot_id='shot-007', duration=5,
                           video_path=A1['path'],
                           image_paths=[assets['image']],
                           audio_paths=[assets['audio']])
    prepared = json.loads(Path(evidence['prepared_path']).read_text(encoding='utf-8'))
    assert evidence['reference_counts'] == {'images': 1, 'videos': 1, 'audios': 1,
                                            'video_seconds_sum':
                                                pytest.approx(6.583333, abs=1e-4),
                                            'audio_seconds_sum':
                                                pytest.approx(2.5, abs=0.2)}
    by_role = {b['role']: b for b in prepared['request_body']['content'] if b.get('role')}
    assert by_role['reference_video']['type'] == 'video_url'
    assert by_role['reference_video']['video_url']['url'].startswith(
        'data:video/mp4;base64,')
    assert by_role['reference_image']['type'] == 'image_url'
    assert by_role['reference_image']['image_url']['url'].startswith(
        'data:image/png;base64,')
    assert by_role['reference_audio']['type'] == 'audio_url'
    assert by_role['reference_audio']['audio_url']['url'].startswith(
        'data:audio/wav;base64,')
    # 1 image is inside the free first-5 and audio is free → same total as video-only
    assert evidence['quote']['total_cny_exact'] == '5.79166650'
    assert evidence['quote']['total_cny_safe'] == '5.80'
    decl = {d['ref_id'] for d in prepared['reference_declarations']}
    assert decl == {'a1', 'img1', 'aud1'}


def test_prepare_multiple_declared_videos(tmp_path, monkeypatch, isolated_runtime):
    """R7: multiple declared reference videos sum into counts and the bill."""
    case, assets = _build_case(tmp_path, monkeypatch, extra_video=True)
    evidence = off.prepare(case_dir=case, shot_id='shot-007', duration=5,
                           video_paths=[A1['path'], str(assets['extra_video'])])
    assert evidence['reference_counts']['videos'] == 2
    # 5s out + (6.583333 + 5.175)s in @ 0.50 → 2.50 + 5.87916650 = 8.37916650 → 8.38
    assert evidence['quote']['total_cny_exact'] == '8.37916650'
    assert evidence['quote']['total_cny_safe'] == '8.38'


# --------------------------------------------------------------- doctor honesty

def test_doctor_offline_no_key_reports_not_run(monkeypatch):
    monkeypatch.delenv('MINIMAX_API_KEY', raising=False)
    report = off.doctor(live_auth=False)
    assert report['auth_verification'] == 'not_run'
    assert report['credential_configured'] is False
    assert report['paid_calls'] == 0 and report['uploads'] == 0
    assert report['config']['model'] == 'MiniMax-H3'
    assert report['config']['pricing_policy_sha256'] == off.current_policy_hash()
    assert 'value never read' in report['credential_note']


def test_doctor_live_auth_without_key_stays_not_run_no_network(monkeypatch):
    monkeypatch.delenv('MINIMAX_API_KEY', raising=False)

    class Boom:
        def get_list(self, *a, **k):
            raise AssertionError('no network call may happen without a key')

    off.set_transport(Boom())
    try:
        report = off.doctor(live_auth=True)
    finally:
        off.set_transport(off.UrllibTransport())
    assert report['auth_verification'] == 'not_run'
    assert report['http_calls'] == 0


def test_doctor_live_auth_one_get_redacted(monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-synthetic-key')

    class OneGet:
        calls = 0

        def get_list(self, base, key, page_num=1, page_size=1, timeout=30.0):
            OneGet.calls += 1
            assert key == 'unit-synthetic-key'
            return {'http_status': 200, 'json': dict(OFFICIAL_LIST_OK,
                                                     items=[dict(OFFICIAL_TASK_SUCCEEDED
                                                                 ['task'])])}

    off.set_transport(OneGet())
    try:
        report = off.doctor(live_auth=True)
    finally:
        off.set_transport(off.UrllibTransport())
        monkeypatch.delenv('MINIMAX_API_KEY')
    assert OneGet.calls == 1
    assert report['auth_verification'] == 'ok'
    assert report['live_get']['http_status'] == 200
    serialized = json.dumps(report)
    assert 'unit-synthetic-key' not in serialized
    # official list payload (items/task history) never copied into the report
    assert 'items' not in serialized and 'task_id' not in serialized \
        and 'video-product.cdn' not in serialized


def test_doctor_live_auth_typed_error_map(monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-synthetic-key')

    class Err:
        def get_list(self, base, key, page_num=1, page_size=1, timeout=30.0):
            return {'http_status': 401, 'json': dict(OFFICIAL_ERROR_401)}

    off.set_transport(Err())
    try:
        report = off.doctor(live_auth=True)
    finally:
        off.set_transport(off.UrllibTransport())
        monkeypatch.delenv('MINIMAX_API_KEY')
    assert report['auth_verification'] == 'unauthorized'
    assert report['live_get']['error_type'] == 'authorized_error'
    assert 'authorized_error' not in json.dumps(report['live_get']['request_id'])


# ----------------------------------------------- credential header canary (R6)

def test_malformed_key_refuses_sanitized_before_urllib(monkeypatch):
    canary = 'unit-canary-Zq9' + 'Ab3xY' * 4 + 'END'
    bad = canary + '\r\nX-Evil: 1'
    monkeypatch.setenv('MINIMAX_API_KEY', bad)
    with pytest.raises(off.H3OfficialError, match='API_KEY_MALFORMED') as excinfo:
        off.doctor(live_auth=True)
    assert canary not in str(excinfo.value) and '\r\n' not in str(excinfo.value)
    transport = off.UrllibTransport()
    for call in (lambda: transport.get_list(off.CANONICAL_BASE, bad),
                 lambda: transport.post_create(off.CANONICAL_BASE, bad, {}),
                 lambda: transport.get_task(off.CANONICAL_BASE, bad, 't1')):
        with pytest.raises(off.H3OfficialError, match='API_KEY_MALFORMED') as ei:
            call()
        assert canary not in str(ei.value)
    # space-only and pure terminal-newline keys are equally malformed
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit key with spaces')
    with pytest.raises(off.H3OfficialError, match='API_KEY_MALFORMED'):
        off.doctor(live_auth=True)
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-terminal-newline-key\n')
    with pytest.raises(off.H3OfficialError, match='API_KEY_MALFORMED'):
        off.doctor(live_auth=True)


def test_cli_never_emits_secret_traceback_or_cause(monkeypatch, tmp_path):
    """R6 end-to-end canary: the CLI process with a newline-bearing fake key must
    exit non-zero with sanitized output — no key material, no traceback, no
    exception cause on stdout/stderr. Covers both an embedded newline and the
    pure terminal-newline form that an anchored `$` regex would miss."""
    canary = 'unit-canary-CLIq7' + 'Zz9Qw' * 4 + 'TAIL'
    for bad in (canary + '\nX-Evil: 1', 'unit-terminal-newline-KEY99\n'):
        env = dict(os.environ)
        env['MINIMAX_API_KEY'] = bad
        env.pop('PYTHONPATH', None)
        proc = subprocess.run(
            [sys.executable, '-X', 'utf8',
             str(ROOT / 'scripts' / 'h3-official-materials.py'),
             'doctor', '--live-auth'],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
        assert proc.returncode == 1
        combined = proc.stdout + proc.stderr
        assert canary not in combined and 'X-Evil' not in combined
        assert 'unit-terminal-newline-KEY99' not in combined
        assert 'Traceback' not in combined
        assert 'API_KEY_MALFORMED' in combined


# ------------------------------------------------------- guarded submit (stub)

class StubTransport:
    """UNIT ONLY transport stub speaking the OFFICIAL V2 shapes: bare task_id on
    create, task.id envelopes on query, typed error envelopes on failure. It
    records the single POST and asserts the uncertainty marker already exists
    when the POST starts. Never touches the network."""

    def __init__(self, behavior='ok'):
        self.behavior = behavior
        self.posts = []
        self.gets = []

    def post_create(self, base, key, body, timeout=30.0):
        self.posts.append({'base': base, 'body': body})
        lock = off.LOCK_DIR
        markers = sorted(lock.glob('*.lock'), key=lambda m: m.stat().st_mtime) \
            if lock.is_dir() else []
        assert markers, 'uncertainty marker must exist BEFORE transport'
        newest = json.loads(markers[-1].read_text(encoding='utf-8'))
        assert newest['state'] == 'uncertain', \
            'the just-acquired lock must still be the uncertainty marker: %r' % newest
        if self.behavior == 'timeout':
            return {'transport_error': 'TimeoutError'}
        if self.behavior == '429':
            return {'http_status': 429, 'json': dict(OFFICIAL_ERROR_429)}
        if self.behavior == '401':
            return {'http_status': 401, 'json': dict(OFFICIAL_ERROR_401)}
        if self.behavior == 'ambiguous':
            return {'http_status': 200, 'json': {}}
        if self.behavior == 'envelope_reject':
            return {'http_status': 200, 'json': dict(OFFICIAL_ERROR_429)}
        if self.behavior == 'malformed_id':
            return {'http_status': 200, 'json': {'task_id': '../../etc/passwd'}}
        if self.behavior == 'newline_id':
            return {'http_status': 200, 'json': {'task_id': 'unit-task-001\n'}}
        return {'http_status': 200, 'json': dict(OFFICIAL_CREATE_OK,
                                                 task_id='unit-task-001')}

    def get_task(self, base, key, task_id, timeout=30.0):
        self.gets.append(task_id)
        if self.behavior == 'timeout':
            return {'transport_error': 'TimeoutError'}
        if self.behavior == 'mismatch':
            return {'http_status': 200,
                    'json': {'task': {'id': 'other-task', 'status': 'running',
                                      'model': 'MiniMax-H3'}}}
        if self.behavior == 'bad_model':
            return {'http_status': 200,
                    'json': {'task': {'id': task_id, 'status': 'running',
                                      'model': 'Other-Model'}}}
        if self.behavior == 'bad_status':
            return {'http_status': 200,
                    'json': {'task': {'id': task_id, 'status': 'SUCCESS',
                                      'model': 'MiniMax-H3'}}}
        if self.behavior == 'non_https':
            return {'http_status': 200,
                    'json': {'task': {'id': task_id, 'status': 'succeeded',
                                      'model': 'MiniMax-H3',
                                      'content': {'url': 'http://plain.example/u.mp4'}}}}
        if self.behavior == 'queued':
            return {'http_status': 200,
                    'json': {'task': {'id': task_id, 'status': 'queued',
                                      'model': 'MiniMax-H3'}}}
        if self.behavior == 'http_error':
            return {'http_status': 500,
                    'json': {'type': 'error',
                             'error': {'type': 'server_error', 'message': 'x',
                                       'http_code': '500'},
                             'request_id': 'req-500'}}
        return {'http_status': 200,
                'json': {'task': {'id': task_id, 'status': 'succeeded',
                                  'model': 'MiniMax-H3',
                                  'content': {'url':
                                      'https://signed.example/unit.mp4?sig=SECRETSIG'}}}}


def _real_prepared(isolated_runtime):
    """Real local prepare against the pinned a1 (isolated temp prepared dir)."""
    evidence = off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=5,
                           video_path=A1['path'])
    return evidence['prepared_path'], json.loads(
        Path(evidence['prepared_path']).read_text(encoding='utf-8'))


def _auth_payload(record, pricing, over=None):
    now = datetime.now(timezone.utc)
    payload = {
        'schema': off.AUTH_SCHEMA,
        'human_authorized': True,
        'authorized_by': 'user (UNIT SYNTHETIC record)',
        'consent_statement': 'UNIT TEST synthetic consent — not a real human consent',
        'recorded_utc': now.isoformat(),
        'provider': 'minimax_official_china', 'model': 'MiniMax-H3',
        'base_url': off.CANONICAL_BASE,
        'request_fingerprint': record['fingerprint'],
        'reference_sha256': [r['sha256'] for r in record['references']],
        'pricing_policy_sha256': off.tariff_policy_hash(pricing),
        'max_calls': 1, 'calls_used': 0,
        'budget': {'currency': 'CNY', 'max_total': 200},
        'cost_estimate': {
            'status': off.ESTIMATE_STATUS_CONFIRMED, 'currency': 'CNY',
            'unit': off.ESTIMATE_UNIT,
            'estimate_cny': record['quote']['total_cny'],
            'request_fingerprint': record['fingerprint'],
            'pricing_policy_sha256': off.tariff_policy_hash(pricing),
            'quote_utc': now.isoformat(),
            'provenance': {'recorded_by': 'trusted-coordinator (UNIT SYNTHETIC)',
                           'source': 'UNIT SYNTHETIC human-confirmed quotation — not '
                                     'real pricing evidence',
                           'recorded_utc': now.isoformat()},
        },
    }
    if over:
        payload.update(over)
    return payload


def _auth_file(isolated_runtime, payload):
    off.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    path = off.AUTH_DIR / ('unit-auth-%s.json' % abs(hash(json.dumps(
        payload, sort_keys=True, default=str))))
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def test_submit_refuses_before_any_post_without_valid_authorization(
        isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    stub = StubTransport()
    # missing record
    _refuse(lambda: off.guarded_submit(prepared_path, off.AUTH_DIR / 'missing.json',
                                       transport=stub), 'AUTH_RECORD_MISSING')
    # outside the authorization dir (valid content, wrong location)
    outside = isolated_runtime.parent / 'outside.json'
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text(json.dumps(_auth_payload(record, off.load_config()['pricing'])),
                       encoding='utf-8')
    _refuse(lambda: off.guarded_submit(prepared_path, outside, transport=stub),
            'AUTH_RECORD_PATH_UNAUTHORIZED')
    # prepared record outside the prepared store refuses on containment first
    _refuse(lambda: off.guarded_submit(outside, off.AUTH_DIR / 'missing.json',
                                       transport=stub), 'PREPARED_PATH_UNAUTHORIZED')
    assert stub.posts == []                       # refused BEFORE any POST
    assert not (isolated_runtime / 'locks').exists() or \
        list((isolated_runtime / 'locks').glob('*')) == []   # no state mutation


def test_submit_refuses_malformed_or_misbound_authorization(
        isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    stub = StubTransport()
    pricing = off.load_config()['pricing']
    bads = [
        ('AUTH_RECORD_INVALID', {'human_authorized': False}),
        ('AUTH_RECORD_INVALID', {'consent_statement': 'short'}),
        ('AUTH_RECORD_MISBOUND', {'provider': 'autodl'}),
        ('AUTH_RECORD_MISBOUND', {'model': 'MiniMax-Max'}),
        ('AUTH_RECORD_MISBOUND', {'base_url': 'https://api.minimaxi.com'}),
        ('AUTH_FINGERPRINT_MISMATCH', {'request_fingerprint': 'a' * 64}),
        ('AUTH_RECORD_MISBOUND', {'reference_sha256': ['b' * 64]}),
        ('AUTH_TARIFF_MISBOUND', {'pricing_policy_sha256': 'c' * 64}),
        ('AUTH_RECORD_INVALID', {'max_calls': 1.0}),
        ('AUTH_RECORD_INVALID', {'max_calls': True}),
        ('AUTH_RECORD_INVALID', {'max_calls': 2}),
        ('AUTH_CALLS_EXHAUSTED', {'calls_used': 1}),
        ('AUTH_RECORD_INVALID', {'calls_used': 0.0}),
        ('AUTH_RECORD_INVALID', {'calls_used': True}),
        ('AUTH_BUDGET_INVALID', {'budget': {'currency': 'CNY', 'max_total': 0}}),
        ('AUTH_BUDGET_INVALID', {'budget': {'currency': 'CNY', 'max_total': True}}),
        ('AUTH_BUDGET_INVALID', {'budget': {'currency': 'CNY', 'max_total': 201}}),
        ('AUTH_ESTIMATE_INVALID', {'cost_estimate': None}),
        ('AUTH_ESTIMATE_INVALID', {'cost_estimate': '5 CNY'}),
        ('AUTH_ESTIMATE_INVALID', {'cost_estimate': dict(
            _auth_payload(record, pricing)['cost_estimate'], status='assumed')}),
        ('AUTH_ESTIMATE_INVALID', {'cost_estimate': dict(
            _auth_payload(record, pricing)['cost_estimate'], estimate_cny=None)}),
        ('AUTH_ESTIMATE_INVALID', {'cost_estimate': dict(
            _auth_payload(record, pricing)['cost_estimate'], estimate_cny=True)}),
        ('AUTH_ESTIMATE_INVALID', {'cost_estimate': dict(
            _auth_payload(record, pricing)['cost_estimate'], estimate_cny=-1)}),
        ('AUTH_ESTIMATE_MISBOUND', {'cost_estimate': dict(
            _auth_payload(record, pricing)['cost_estimate'],
            request_fingerprint='a' * 64)}),
        ('AUTH_ESTIMATE_MISBOUND', {'cost_estimate': dict(
            _auth_payload(record, pricing)['cost_estimate'],
            pricing_policy_sha256='c' * 64)}),
    ]
    for code, over in bads:
        auth = _auth_file(isolated_runtime, _auth_payload(record, pricing, over))
        with pytest.raises(off.H3OfficialError, match=code):
            off.guarded_submit(prepared_path, auth, transport=stub)
        assert stub.posts == []
    assert list((isolated_runtime / 'locks').glob('*')) == []


def test_submit_refuses_stale_estimate_overbudget_mismatch_and_insufficient(
        isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    pricing = off.load_config()['pricing']
    stub = StubTransport()
    stale = copy.deepcopy(_auth_payload(record, pricing))
    stale['cost_estimate']['quote_utc'] = (
        datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    _refuse(lambda: off.guarded_submit(prepared_path,
                                       _auth_file(isolated_runtime, stale),
                                       transport=stub), 'AUTH_ESTIMATE_STALE')
    future = copy.deepcopy(_auth_payload(record, pricing))
    future['cost_estimate']['quote_utc'] = (
        datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    _refuse(lambda: off.guarded_submit(prepared_path,
                                       _auth_file(isolated_runtime, future),
                                       transport=stub), 'AUTH_ESTIMATE_STALE')
    over = copy.deepcopy(_auth_payload(record, pricing))
    over['cost_estimate']['estimate_cny'] = 0.01            # far below true quote
    _refuse(lambda: off.guarded_submit(prepared_path,
                                       _auth_file(isolated_runtime, over),
                                       transport=stub), 'AUTH_ESTIMATE_MISMATCH')
    # R3: 5.79 is BELOW the exact 5.79166650 — insufficient, no 0.005 tolerance
    rounded_down = copy.deepcopy(_auth_payload(record, pricing))
    rounded_down['cost_estimate']['estimate_cny'] = 5.79
    _refuse(lambda: off.guarded_submit(prepared_path,
                                       _auth_file(isolated_runtime, rounded_down),
                                       transport=stub), 'AUTH_ESTIMATE_MISMATCH')
    # the safe upward-cent value 5.80 is the minimum accepted estimate
    safe = copy.deepcopy(_auth_payload(record, pricing))
    safe['cost_estimate']['estimate_cny'] = 5.80
    auth = _auth_file(isolated_runtime, safe)
    result = off.guarded_submit(prepared_path, auth, transport=stub)
    assert result['state'] == 'SUBMITTED'
    overbudget = copy.deepcopy(_auth_payload(record, pricing))
    overbudget['budget'] = {'currency': 'CNY', 'max_total': 1.0}   # < estimate
    overbudget['cost_estimate']['estimate_cny'] = record['quote']['total_cny']
    _refuse(lambda: off.guarded_submit(prepared_path,
                                       _auth_file(isolated_runtime, overbudget),
                                       transport=stub), 'AUTH_BUDGET_INSUFFICIENT')


def test_submit_refuses_missing_key_and_stale_tariff(isolated_runtime, monkeypatch):
    prepared_path, record = _real_prepared(isolated_runtime)
    auth = _auth_file(isolated_runtime, _auth_payload(record,
                                                      off.load_config()['pricing']))
    monkeypatch.delenv('MINIMAX_API_KEY', raising=False)
    _refuse(lambda: off.guarded_submit(prepared_path, auth, transport=StubTransport()),
            'API_KEY_MISSING')
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    stale_pricing = dict(off.load_config()['pricing'], snapshot_date='2025-01-01')
    monkeypatch.setattr(off, '_CONFIG_CACHE', dict(off.load_config(),
                                                   pricing=stale_pricing))
    try:
        _refuse(lambda: off.guarded_submit(prepared_path, auth, transport=StubTransport()),
                'AUTH_TARIFF_STALE')
    finally:
        off.reset_config_cache()
    assert list((isolated_runtime / 'locks').glob('*')) == []


def test_submit_revalidation_refuses_drift(isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    auth = _auth_file(isolated_runtime, _auth_payload(record,
                                                      off.load_config()['pricing']))
    tampered = copy.deepcopy(record)
    tampered['references'][0]['sha256'] = 'd' * 64          # media "changed"
    tampered['fingerprint_core']['references'][0]['sha256'] = 'd' * 64
    tampered['fingerprint'] = off._sha256_canonical(tampered['fingerprint_core'])
    _atomic = prepared_path
    Path(_atomic).write_text(json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
    _refuse(lambda: off.guarded_submit(_atomic, auth, transport=StubTransport()),
            'REF_SOURCE_STALE')
    # restore + break the fingerprint binding itself
    Path(_atomic).write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    fp_drift = copy.deepcopy(record)
    fp_drift['fingerprint'] = 'e' * 64
    Path(_atomic).write_text(json.dumps(fp_drift, ensure_ascii=False), encoding='utf-8')
    _refuse(lambda: off.guarded_submit(_atomic, auth, transport=StubTransport()),
            'PREPARED_FINGERPRINT_STALE')
    Path(_atomic).write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    # metadata drift (duration) without hash change
    meta_drift = copy.deepcopy(record)
    meta_drift['references'][0]['duration_sec'] = 6.0
    Path(_atomic).write_text(json.dumps(meta_drift, ensure_ascii=False), encoding='utf-8')
    _refuse(lambda: off.guarded_submit(_atomic, auth, transport=StubTransport()),
            'REF_SOURCE_STALE')
    Path(_atomic).write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    # stored quote drift
    quote_drift = copy.deepcopy(record)
    quote_drift['quote']['total_cny_exact'] = '1.00'
    Path(_atomic).write_text(json.dumps(quote_drift, ensure_ascii=False), encoding='utf-8')
    _refuse(lambda: off.guarded_submit(_atomic, auth, transport=StubTransport()),
            'QUOTE_DRIFT')
    Path(_atomic).write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')


def test_submit_rejects_tampered_outbound_body_r4(isolated_runtime, monkeypatch):
    """R4 exact coordinator probe: replace the outbound body (model=WRONG,
    duration=15, content=[]) while leaving the fingerprint core untouched — the
    digest binding must refuse BEFORE any lock/state/network."""
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    auth = _auth_file(isolated_runtime, _auth_payload(record,
                                                      off.load_config()['pricing']))
    stub = StubTransport()
    tampered = copy.deepcopy(record)
    tampered['request_body']['model'] = 'MiniMax-WRONG'
    tampered['request_body']['duration'] = 15
    tampered['request_body']['content'] = []
    Path(prepared_path).write_text(json.dumps(tampered, ensure_ascii=False),
                                   encoding='utf-8')
    _refuse(lambda: off.guarded_submit(prepared_path, auth, transport=stub),
            'PREPARED_BODY_TAMPERED')
    assert stub.posts == []
    assert list((isolated_runtime / 'locks').glob('*')) == []
    # attacker re-signs core AND fingerprint to match the tampered body: the
    # authorization stays bound to the ORIGINAL fingerprint → misbound refusal
    resigned = copy.deepcopy(tampered)
    resigned['fingerprint_core']['request_body_sha256'] = \
        off._sha256_canonical(tampered['request_body'])
    resigned['fingerprint'] = off._sha256_canonical(resigned['fingerprint_core'])
    Path(prepared_path).write_text(json.dumps(resigned, ensure_ascii=False),
                                   encoding='utf-8')
    _refuse(lambda: off.guarded_submit(prepared_path, auth, transport=stub),
            'AUTH_FINGERPRINT_MISMATCH')
    assert stub.posts == []


def test_submit_legacy_v1_record_rejected_and_preserved(isolated_runtime):
    """R4: the historical initial prepared record (fingerprint 3a1845be…, old
    invalid body contract) is rejected on submit and never overwritten."""
    legacy_src = ROOT / LEGACY_PREPARED_REL
    if not legacy_src.is_file():
        pytest.skip('historical initial prepared record not present on this host')
    raw = legacy_src.read_bytes()
    legacy = json.loads(raw.decode('utf-8'))
    assert legacy['schema'] == 'lab-h3-official-prepared/v1'   # the old schema
    dest = isolated_runtime / 'prepared' / ('%s.json' % legacy['fingerprint'])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    _refuse(lambda: off.guarded_submit(dest, isolated_runtime / 'authorization' /
                                       'none.json', transport=StubTransport()),
            'PREPARED_RECORD_INVALID')
    assert dest.read_bytes() == raw                        # preserved byte-exact


def test_submit_success_path_single_attempt_marker_and_registration(
        isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    auth = _auth_file(isolated_runtime, _auth_payload(record,
                                                      off.load_config()['pricing']))
    stub = StubTransport()
    result = off.guarded_submit(prepared_path, auth, transport=stub)
    assert result['state'] == 'SUBMITTED' and result['task_id'] == 'unit-task-001'
    assert len(stub.posts) == 1                              # exactly ONE POST
    body = stub.posts[0]['body']
    assert stub.posts[0]['base'] == off.CANONICAL_BASE
    assert set(body) == {'model', 'content', 'resolution', 'duration', 'ratio'}
    assert body['content'][1]['type'] == 'video_url'         # official shape sent
    lock = json.loads((isolated_runtime / 'locks' /
                       ('%s.lock' % record['fingerprint'])).read_text(encoding='utf-8'))
    assert lock['state'] == 'submitted' and lock['task_id'] == 'unit-task-001'
    reg = json.loads((isolated_runtime / 'submissions' / 'unit-task-001.json')
                     .read_text(encoding='utf-8'))
    assert reg['fingerprint'] == record['fingerprint']
    assert reg['provider'] == 'minimax_official_china' \
        and reg['model'] == 'MiniMax-H3' and reg['base_url'] == off.CANONICAL_BASE
    spent = json.loads(auth.read_text(encoding='utf-8'))
    assert spent['calls_used'] == 1 and spent['task_id'] == 'unit-task-001'
    # resubmission is refused fail-closed: the single authorized call is exhausted
    # (the fingerprint lock also still stands — one attempt only, never retried)
    _refuse(lambda: off.guarded_submit(prepared_path, auth, transport=stub),
            'AUTH_CALLS_EXHAUSTED')
    assert len(stub.posts) == 1
    assert json.loads((isolated_runtime / 'locks' /
                       ('%s.lock' % record['fingerprint'])).read_text(
                           encoding='utf-8'))['state'] == 'submitted'


def test_submit_timeout_and_errors_keep_uncertainty_no_retry(
        isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    pricing = off.load_config()['pricing']
    cases = (('timeout', 'SUBMIT_UNCERTAIN_KEPT_LOCKED', '9:16'),
             ('ambiguous', 'SUBMIT_UNCERTAIN_KEPT_LOCKED', '16:9'),
             ('malformed_id', 'SUBMIT_UNCERTAIN_KEPT_LOCKED', '1:1'),
             ('newline_id', 'SUBMIT_UNCERTAIN_KEPT_LOCKED', '21:9'),
             ('429', 'HTTP_429', '4:3'),
             ('401', 'HTTP_401', '3:4'),
             ('envelope_reject', 'SUBMIT_REJECTED', 'adaptive'))
    for behavior, code, ratio in cases:   # distinct ratios ⇒ distinct fingerprints
        evidence = off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=5,
                               ratio=ratio, video_path=A1['path'])
        prepared_path = evidence['prepared_path']
        record = json.loads(Path(prepared_path).read_text(encoding='utf-8'))
        auth = _auth_file(isolated_runtime, _auth_payload(record, pricing))
        stub = StubTransport(behavior=behavior)
        with pytest.raises(off.H3OfficialError, match=code):
            off.guarded_submit(prepared_path, auth, transport=stub)
        assert len(stub.posts) == 1                          # one attempt, no retry
        lock = json.loads((isolated_runtime / 'locks' /
                           ('%s.lock' % record['fingerprint'])).read_text(encoding='utf-8'))
        assert lock['state'] in ('uncertain', 'failed')      # durable marker kept
        # a resubmit attempt under the SAME fingerprint is refused by the kept lock
        _refuse(lambda: off.guarded_submit(prepared_path, auth, transport=stub),
                'SUBMIT_LOCK_EXISTS')


def test_submit_error_messages_are_sanitized(isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-synthetic-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    auth = _auth_file(isolated_runtime, _auth_payload(record,
                                                      off.load_config()['pricing']))
    stub = StubTransport(behavior='429')
    try:
        off.guarded_submit(prepared_path, auth, transport=stub)
    except off.H3OfficialError as exc:
        message = str(exc)
        assert 'unit-synthetic-key' not in message
        assert 'base64' not in message.lower()
        assert 'rate limit, please retry later' not in message  # no platform echo
        assert 'request_id' in message or 'type=' in message
    else:
        raise AssertionError('expected refusal')


# ------------------------------------------------------------------- query

def test_query_only_registered_ids_and_shape(isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    _refuse(lambda: off.query('unknown-task'), 'TASK_NOT_REGISTERED')
    # unsafe ids are REJECTED (never sanitized into a valid lookup)
    for bad in ('../etc/passwd', 'a/b', 'unit task', '.hidden', 'x' * 65):
        _refuse(lambda b=bad: off.query(b), 'ID_INVALID')
    prepared_path, record = _real_prepared(isolated_runtime)
    auth = _auth_file(isolated_runtime, _auth_payload(record,
                                                      off.load_config()['pricing']))
    off.guarded_submit(prepared_path, auth, transport=StubTransport())
    happy = StubTransport()
    off.set_transport(happy)                    # UNIT ONLY: no real network in tests
    try:
        summary = off.query('unit-task-001', write_evidence=False)
    finally:
        off.set_transport(off.UrllibTransport())
    assert summary['status'] == 'succeeded'                  # official enum spelling
    assert summary['model'] == 'MiniMax-H3'
    assert summary['attempts'] == 1 and summary['polling'] is False
    assert summary['result_url'].startswith('present_but_redacted')
    serialized = json.dumps(summary)
    assert 'SECRETSIG' not in serialized and 'signed.example' not in serialized
    for behavior, code in (('timeout', 'QUERY_TRANSPORT_ERROR'),
                           ('http_error', 'HTTP_500'),
                           ('mismatch', 'QUERY_TASK_MISMATCH'),
                           ('bad_model', 'QUERY_MODEL_MISMATCH'),
                           ('bad_status', 'QUERY_RESPONSE_MALFORMED'),
                           ('non_https', 'QUERY_RESULT_URL_INVALID')):
        stub = StubTransport(behavior=behavior)
        off.set_transport(stub)
        try:
            _refuse(lambda: off.query('unit-task-001'), code)
        finally:
            off.set_transport(off.UrllibTransport())
        assert stub.gets == ['unit-task-001']                # single bounded GET


def test_query_queued_status_and_registration_binding(isolated_runtime, monkeypatch):
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    auth = _auth_file(isolated_runtime, _auth_payload(record,
                                                      off.load_config()['pricing']))
    off.guarded_submit(prepared_path, auth, transport=StubTransport())
    reg_path = isolated_runtime / 'submissions' / 'unit-task-001.json'
    reg = json.loads(reg_path.read_text(encoding='utf-8'))
    reg['model'] = 'Other-Model'                     # misbound local registration
    reg_path.write_text(json.dumps(reg, ensure_ascii=False), encoding='utf-8')
    stub = StubTransport(behavior='queued')
    off.set_transport(stub)
    try:
        _refuse(lambda: off.query('unit-task-001'), 'REGISTRATION_INVALID')
        assert stub.gets == []                       # refused BEFORE any network
        reg['model'] = 'MiniMax-H3'
        reg_path.write_text(json.dumps(reg, ensure_ascii=False), encoding='utf-8')
        summary = off.query('unit-task-001', write_evidence=False)
    finally:
        off.set_transport(off.UrllibTransport())
    assert summary['status'] == 'queued' and summary['result_url'] is None


def test_safe_ids_and_bearers_reject_terminal_newline_and_controls():
    """R5/R6 exact probe regression: an anchored `$` in re.match silently admits
    a trailing newline — every identifier/credential gate must fullmatch."""
    for bad_id in ('unit-id\n', 'unit-id\r\n', 'unit-id\t', 'unit-id\x00',
                   '../etc/passwd\n'):
        with pytest.raises(off.H3OfficialError, match='ID_INVALID'):
            off._validate_safe_id(bad_id, 'task_id')
    for bad_key in ('UNIT_ONLY_TOKEN_123\n', 'UNIT_ONLY_TOKEN_123\r\n',
                    'UNIT_ONLY_TOKEN_123\t', 'key with spaces',
                    'key\nX-Evil: 1'):
        with pytest.raises(off.H3OfficialError, match='API_KEY_MALFORMED'):
            off._validate_bearer(bad_key)


def test_tariff_rate_and_date_reject_terminal_newline():
    good = copy.deepcopy(off.load_config())
    for bad_rate in ('0.50\n', '0.5\n'):
        broken = copy.deepcopy(good['pricing'])
        broken['rates']['output_per_sec']['768P'] = bad_rate
        _refuse(lambda b=broken: off.tariff_policy_hash(b), 'TARIFF_MALFORMED')
    broken = copy.deepcopy(good['pricing'])
    broken['snapshot_date'] = '2026-10-11\n'
    _refuse(lambda: off.tariff_policy_hash(broken), 'TARIFF_MALFORMED')


def test_doctor_live_auth_requires_official_list_shape(monkeypatch):
    """R2: a bare HTTP 200 dict (empty object, base_resp wrapper) is NOT proof of
    an authenticated official channel — only the official list shape (items array
    + integer total, no base_resp) yields auth ok, without printing task history."""
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-synthetic-key')

    class Shape:
        def __init__(self, body):
            self.body = body
            self.calls = 0

        def get_list(self, base, key, page_num=1, page_size=1, timeout=30.0):
            self.calls += 1
            return {'http_status': 200, 'json': self.body}

    try:
        for malformed in ({}, {'tasks': []}, {'total': 'many'},
                          {'base_resp': {'status_code': 0}},
                          {'items': {}, 'total': 3},
                          {'items': [], 'total': True}):
            stub = Shape(malformed)
            off.set_transport(stub)
            report = off.doctor(live_auth=True)
            assert report['auth_verification'] == 'indeterminate', malformed
            assert report['live_get']['official_list_shape'] is False
            assert stub.calls == 1
            assert 'base_resp' not in json.dumps(report)
        stub = Shape(dict(OFFICIAL_LIST_OK, items=[dict(OFFICIAL_TASK_SUCCEEDED['task'])]))
        off.set_transport(stub)
        report = off.doctor(live_auth=True)
        assert report['auth_verification'] == 'ok'
        assert report['live_get']['official_list_shape'] is True
        assert 'video-product.cdn' not in json.dumps(report)   # no task history
    finally:
        off.set_transport(off.UrllibTransport())
        monkeypatch.delenv('MINIMAX_API_KEY')


def test_submit_rejects_outer_view_tamper_full_consistency(isolated_runtime,
                                                            monkeypatch):
    """Follow-up R4 gate: outer record views the old revalidator never compared —
    test_intent (incl. sound policy), binding, FULL quote (upward-safe total,
    currency, policy hash — exact-only comparison would pass total_cny=0.01),
    request view, provider view, and REMOVED metadata fields — each refuses
    before any lock/state/network."""
    monkeypatch.setenv('MINIMAX_API_KEY', 'unit-key')
    prepared_path, record = _real_prepared(isolated_runtime)
    auth = _auth_file(isolated_runtime, _auth_payload(record,
                                                      off.load_config()['pricing']))
    stub = StubTransport()
    keep = json.dumps(record, ensure_ascii=False)

    def tamper(mutate):
        broken = copy.deepcopy(record)
        mutate(broken)
        Path(prepared_path).write_text(json.dumps(broken, ensure_ascii=False),
                                       encoding='utf-8')

    tampers = [
        ('TEST_INTENT_DRIFT', lambda r: r['test_intent'].__setitem__(
            'audio_policy', 'voiceover allowed')),
        ('TEST_INTENT_DRIFT', lambda r: r['test_intent'].__setitem__(
            'spoken_locked', False)),
        ('TEST_INTENT_DRIFT', lambda r: r.pop('test_intent')),
        ('PREPARED_BINDING_TAMPERED', lambda r: r['binding'].__setitem__(
            'version', 99)),
        ('PREPARED_BINDING_TAMPERED', lambda r: r['binding'].__setitem__(
            'review_verdict', 'RETURN')),
        ('PREPARED_BINDING_TAMPERED', lambda r: r.pop('binding')),
        ('QUOTE_DRIFT', lambda r: r['quote'].__setitem__('total_cny', 0.01)),
        ('QUOTE_DRIFT', lambda r: r['quote'].__setitem__('total_cny_safe', '0.01')),
        ('QUOTE_DRIFT', lambda r: r['quote'].__setitem__('currency', 'USD')),
        ('QUOTE_DRIFT', lambda r: r['quote'].__setitem__('policy_sha256', 'f' * 64)),
        ('QUOTE_DRIFT', lambda r: r['quote'].__setitem__(
            'line_items', r['quote']['line_items'][:-1])),
        ('QUOTE_DRIFT', lambda r: r.pop('quote')),
        ('PREPARED_REQUEST_TAMPERED', lambda r: r['request'].__setitem__(
            'resolution', '2K')),
        ('PREPARED_PROVIDER_TAMPERED', lambda r: r['provider'].__setitem__(
            'base_url', 'https://api.minimaxi.com')),
        ('PREPARED_RECORD_INVALID', lambda r: r.pop('reference_declarations')),
        ('PREPARED_RECORD_INVALID', lambda r: r['reference_declarations'][0]
            .__setitem__('ref_id', 'bogus')),
        ('PREPARED_RECORD_INVALID', lambda r: r['reference_counts'].__setitem__(
            'images', 5)),
    ]
    for code, mutate in tampers:
        tamper(mutate)
        with pytest.raises(off.H3OfficialError, match=code):
            off.guarded_submit(prepared_path, auth, transport=stub)
        assert stub.posts == []                       # no network on rejection
        assert list((isolated_runtime / 'locks').glob('*')) == []   # no state
        Path(prepared_path).write_text(keep, encoding='utf-8')      # restore
    # restored record submits normally afterwards
    result = off.guarded_submit(prepared_path, auth, transport=stub)
    assert result['state'] == 'SUBMITTED' and len(stub.posts) == 1


def test_prepare_reuse_rejects_outer_view_tamper(isolated_runtime):
    """Same full-view consistency on immutable prepare reuse: a stored record
    whose outer views were edited under an unchanged fingerprint refuses
    instead of being silently reused as evidence."""
    evidence = off.prepare(case_dir=CASE_DIR, shot_id='shot-007', duration=5,
                           video_path=A1['path'])
    path = Path(evidence['prepared_path'])
    record = json.loads(path.read_text(encoding='utf-8'))
    keep = json.dumps(record, ensure_ascii=False)

    def rerun(expect):
        _refuse(lambda: off.prepare(case_dir=CASE_DIR, shot_id='shot-007',
                                    duration=5, video_path=A1['path']), expect)
        path.write_text(keep, encoding='utf-8')          # restore

    broken = copy.deepcopy(record)
    broken['quote']['total_cny'] = 0.01                  # safe-total tamper only
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
    rerun('QUOTE_DRIFT')
    broken = copy.deepcopy(record)
    broken['test_intent']['silent'] = False
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
    rerun('TEST_INTENT_DRIFT')
    broken = copy.deepcopy(record)
    broken['binding'].pop('review_entry_sha256')
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
    rerun('PREPARED_BINDING_TAMPERED')
    broken = copy.deepcopy(record)
    broken.pop('reference_counts')
    path.write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
    rerun('PREPARED_RECORD_INVALID')


# ------------------------------------------------------ AutoDL chain separation

def test_autodl_adapter_source_unchanged():
    """The official channel must not modify the AutoDL chain: the official adapter
    source never imports it, and the AutoDL file still matches the coordinator
    baseline hash."""
    official_source = Path(off.__file__).read_text(encoding='utf-8')
    assert not re.search(r'^\s*(from|import)\s+.*h3_autodl', official_source, re.M), \
        'the official adapter must not import the AutoDL chain'
    autodl = Path('F:/codex/laox_director_lab/adapters/h3_autodl.py')
    assert off._sha256_file(autodl) == \
        'cc392ee2b07b2a8f46d1f19b2c7d267edd2fe5e1705ae3bf7c36140d70687c44'
