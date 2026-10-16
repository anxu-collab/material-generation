"""h3_autodl — orchestration/mapping/authorization layer for real AutoDL H3 calls.

ACTIVE ROUTE (h3-autodl-z0902-integration, 2026-10-11, Orca task_8c5482b0a195):
workflow minimax_h3_z0902, image-only request contract (prompt + ref_image_0..5,
optional seed/duration/resolution), project default resolution 1088p竖(1088*1920).
The contract is USER_SUPPLIED_NOT_SERVER_VERIFIED (single source of truth:
config/autodl_h3_z0902.json) and its runtime acceptance is
NOT_RUN_USER_PROHIBITED — the user explicitly ordered 你只是对接，不允许测试, so this
round delivered STATIC code only: zero tests, zero imports of this module, zero
network, zero API calls. Historical live GET evidence for z0903 proves nothing
about z0902.

Transport (static design for the future guarded submission): reuse ONLY the
http_json low-level method of the pinned oldX H3 runtime
(F:/codex/laox_account/app/runtime/h3/laox_h3.py, SHA256 pinned below) through
the private lab-rooted importlib instance. The runtime's submit/settings/
prepare_bundle/fetch_schema/validate_schema stay untouched and UNUSED on this
route — they are z0903/audio/768p-locked (see EK-23: settings() line 56 rejects
any other workflow, RESOLUTIONS line 9 holds only 768p竖, references() line 302
requires 1..3 audio assets). AutoDL configuration resolves from
AUTODL_ART_BASE_URL/AUTODL_ART_TOKEN ENVIRONMENT VARIABLES ONLY (official
https://autodl.art domains); oldX app/api/autodl.env is never read on this
route. Guards default to network_enabled=false, generation_enabled=false,
dry_run=true: guarded submit refuses BEFORE any state or network mutation, and
a configured token is never consent.

HISTORICAL ROUTE (h3-autodl-v1, 2026-10-11): the z0903 silent-TEST B-roll chain
below is preserved verbatim under the LEGACY banner with
LEGACY_Z0903_ENABLED=False — every public legacy entry refuses first. Its
evidence/receipts remain under runtime/evidence/h3-autodl-v1/ and are not
affected. z0902 must never be routed through the audio-locked legacy
serializer.
"""
from __future__ import annotations

import hashlib
import importlib.abc
import importlib.util
import json
import math
import os
import re
import subprocess
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from agents import product_storyboards as ps

LAB_ROOT = Path('F:/codex/laox_director_lab')
RUNTIME_SOURCE = Path('F:/codex/laox_account/app/runtime/h3/laox_h3.py')
RUNTIME_SHA256 = '8b173b0e4b02e03fb8c1289083c701b6583b01ef1fb8e9310e9699433e0f1851'
# virtual module path whose parents[3] is the lab runtime root (mirrors the original
# depth app/runtime/h3/laox_h3.py); the loader still reads the real oldX source —
# spec.origin only re-roots the runtime's ROOT computation
EXEC_ROOT = LAB_ROOT / 'runtime'
VIRTUAL_FILE = EXEC_ROOT / 'private/app/runtime/laox_h3.py'
MAX_BUDGET_CNY = 200.0
# shared reference-pattern guards (used by BOTH routes; extension match only —
# signed/no-extension URLs never match and are never fetched to "check")
VIDEO_REF_PATTERN = re.compile(r'\.(mp4|mov|webm|mkv|avi|m4v)([?#]|$)', re.IGNORECASE)
AUDIO_REF_PATTERN = re.compile(r'\.(wav|mp3|flac|aac|m4a|ogg|opus)([?#]|$)', re.IGNORECASE)


class H3AdapterError(RuntimeError):
    """Fail-closed refusal; the message never contains credentials."""


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _refuse(code, detail=''):
    raise H3AdapterError(code + (': ' + detail if detail else ''))


def _atomic_write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


class _NoBytecodeSourceLoader(importlib.abc.Loader):
    """Private loader that reads ONLY the SHA-locked source file and compiles it
    in memory against the virtual filename. It never consults or writes any
    __pycache__ (a stock SourceFileLoader would both read and create .pyc files
    inside oldX, violating the read-only-exact-source boundary), never touches
    sys.dont_write_bytecode, and the loaded module is never registered in
    sys.modules — nothing else can import or monkeypatch it by name."""

    def __init__(self, source_path, virtual_file):
        self._source_path = str(source_path)
        self._virtual_file = str(virtual_file)

    def create_module(self, spec):
        return None                       # default module object creation

    def exec_module(self, module):
        with open(self._source_path, 'rb') as f:
            source = f.read()
        module.__file__ = self._virtual_file
        exec(compile(source, self._virtual_file, 'exec'), module.__dict__)


def _load_runtime(name, virtual_file=None, expected_root=None):
    """Private instance of the original runtime via _NoBytecodeSourceLoader: the
    oldX source is read byte-for-byte read-only (SHA pinned below), compiled to
    the virtual filename, and the ROOT binding is asserted after load."""
    if _sha256_file(RUNTIME_SOURCE) != RUNTIME_SHA256:
        raise H3AdapterError(
            'H3ADAPTER_RUNTIME_DRIFT: oldX runtime source no longer matches the pinned '
            'SHA256 ' + RUNTIME_SHA256[:16] + '…; refuse to load an unverified client.')
    virtual = str(virtual_file) if virtual_file is not None else str(RUNTIME_SOURCE)
    spec = importlib.util.spec_from_loader(name, _NoBytecodeSourceLoader(RUNTIME_SOURCE, virtual),
                                           origin=virtual)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if expected_root is not None and module.ROOT.resolve() != Path(expected_root).resolve():
        raise H3AdapterError('H3ADAPTER_ROOT_BINDING_FAILED: runtime instance ROOT='
                             + str(module.ROOT) + ' expected=' + str(expected_root))
    return module


_RESOLVER = None
_EXEC = None


def runtime_resolver():
    """Original-ROOT instance (legacy z0903 route): settings/tool resolution and
    read-only live GET. NOT used by the active z0902 route."""
    global _RESOLVER
    if _RESOLVER is None:
        _RESOLVER = _load_runtime('lab_h3_resolver_private_1')
    return _RESOLVER


def runtime_exec():
    """Lab-ROOT instance: the single private runtime instance whose http_json low-
    level method the future guarded z0902 submission reuses (nothing else on the
    z0902 route touches this instance).

    Explicit dependency injection (TaskSpec-sanctioned, legacy z0903 wiring) into
    THIS PRIVATE instance only: settings/tool are delegated to the original-ROOT
    resolver instance, so the lab-rooted runtime keeps the real credential
    resolution (oldX autodl.env with the original env-override precedence) and
    the oldX runtime.local.json tool paths for the LEGACY route. The z0902 route
    ignores both attributes and reads only AUTODL_ART_* environment variables.
    Neither instance is registered in sys.modules; no shared module is modified,
    no credential is copied or re-persisted."""
    global _EXEC
    if _EXEC is None:
        _EXEC = _load_runtime('lab_h3_exec_private_1', VIRTUAL_FILE, EXEC_ROOT)
        resolver = runtime_resolver()
        _EXEC.settings = resolver.settings
        _EXEC.tool = resolver.tool
    return _EXEC


def _reset_instances():  # tests only — frozen this round (user prohibited tests)
    global _RESOLVER, _EXEC
    _RESOLVER = None
    _EXEC = None


# =====================================================================
# ACTIVE ROUTE — AutoDL minimax_h3_z0902, image-only (2026-10-11)
# Contract source: USER_SUPPLIED_NOT_SERVER_VERIFIED (TaskSpec user_contract
# revision 2). Runtime verification: NOT_RUN_USER_PROHIBITED.
# =====================================================================

Z0902_WORKFLOW = 'minimax_h3_z0902'
Z0902_CONFIG_PATH = LAB_ROOT / 'config' / 'autodl_h3_z0902.json'
Z0902_DEFAULT_RESOLUTION = '1088p竖(1088*1920)'      # project default, explicitly
                                                    # serialized (provider declared
                                                    # default 768p竖 is NOT used)
Z0902_RESOLUTION_OPTIONS = (
    '480p竖(480*864)',
    '480p横(864*480)',
    '768p竖(768*1376)',
    '768p横(1376*768)',
    '1088p竖(1088*1920)',
    '1088p横(1920*1088)',
    '1440p竖(1440*2560)',
    '1440p横(2560*1440)',
)
Z0902_DURATION_RANGE = (1, 15)                       # user contract; default 5
Z0902_DURATION_DEFAULT = 5
Z0902_PROMPT_RANGE = (1, 20000)                      # user contract character range
Z0902_IMAGE_SLOTS = tuple('ref_image_%d' % i for i in range(6))
Z0902_REQUIRED_IMAGE_SLOT = 'ref_image_0'
Z0902_BODY_BASE_KEYS = frozenset({'prompt', 'duration', 'resolution', 'seed'})
Z0902_ALLOWED_BODY_KEYS = frozenset(Z0902_BODY_BASE_KEYS | set(Z0902_IMAGE_SLOTS))
Z0902_OFFICIAL_BASES = frozenset({'https://autodl.art', 'https://www.autodl.art'})
Z0902_REQUEST_SCHEMA = 'lab-h3-z0902-request/v1'
Z0902_AUTH_SCHEMA = 'lab-h3-submit-authorization/v2-z0902'
Z0902_EVIDENCE_DIR = EXEC_ROOT / 'evidence' / 'h3-autodl-z0902-integration'
Z0902_PREPARED_DIR = EXEC_ROOT / 'h3-autodl-z0902-integration' / 'prepared'
Z0902_AUTH_DIR = EXEC_ROOT / 'h3-autodl-z0902-integration' / 'authorization'
Z0902_RUNS_DIR = EXEC_ROOT / 'h3-autodl-z0902-integration' / 'runs'
Z0902_LOCKS_DIR = EXEC_ROOT / 'h3-autodl-z0902-integration' / 'locks'
# repair-1 structured-quote standard, re-bound to the z0902 workflow
ESTIMATE_STATUS_CONFIRMED = 'human_confirmed_platform_quote'
ESTIMATE_UNIT = 'CNY_per_single_call'

_Z0902_CONFIG = None


def load_z0902_config():
    """Load and cross-check the user-contract config (local file read only; no
    network, no runtime instance). Any workflow/resolution inconsistency with
    the adapter constants fails closed — there is no fallback. The global cache
    is written ONLY after every check has passed on the local value, so a
    refused load can never leave a half-checked config cached for a later
    call."""
    global _Z0902_CONFIG
    if _Z0902_CONFIG is None:
        try:
            config = json.loads(Z0902_CONFIG_PATH.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise H3AdapterError('Z0902_CONFIG_UNREADABLE: ' + str(Z0902_CONFIG_PATH)
                                 + ' (' + type(exc).__name__ + ') — the user contract '
                                 'file is the single source of truth; refuse without it')
        if not isinstance(config, dict) or config.get('workflow') != Z0902_WORKFLOW:
            _refuse('Z0902_WORKFLOW_MISMATCH', 'config workflow must be exactly '
                    + Z0902_WORKFLOW + ' — fail closed, no fallback to z0903 or any '
                    'other workflow')
        contract = config.get('request_contract')
        resolution = (contract or {}).get('resolution') or {}
        if resolution.get('project_default') != Z0902_DEFAULT_RESOLUTION \
                or list(resolution.get('options') or []) != list(Z0902_RESOLUTION_OPTIONS):
            _refuse('Z0902_CONFIG_RESOLUTION_DRIFT', 'config resolution contract no '
                    'longer matches the adapter constants — reconcile explicitly, '
                    'never proceed on drift')
        _Z0902_CONFIG = config
    return _Z0902_CONFIG


def _z0902_guards(config):
    guards = config.get('guards')
    if not isinstance(guards, dict):
        _refuse('Z0902_CONFIG_INVALID', 'guards block missing from '
                + str(Z0902_CONFIG_PATH))
    return guards


def _assert_z0902_execution_allowed(config):
    """FIRST gate of the future guarded submit — checked before any state or
    network mutation. A token in the environment is never consent and can never
    flip these switches; enabling requires an explicit coordinated config change."""
    guards = _z0902_guards(config)
    if guards.get('network_enabled') is not True:
        _refuse('Z0902_NETWORK_DISABLED', 'guards keep network_enabled=false; '
                'submission refuses before any state or network mutation')
    if guards.get('generation_enabled') is not True:
        _refuse('Z0902_GENERATION_DISABLED', 'guards keep generation_enabled=false; '
                'submission refuses before any state or network mutation')
    if guards.get('dry_run') is not False:
        _refuse('Z0902_DRY_RUN_LOCKED', 'guards keep dry_run=true; configuring a '
                'token or an authorization file alone never enables execution')


def _validate_z0902_prompt(prompt):
    if not isinstance(prompt, str) or isinstance(prompt, bool):
        _refuse('Z0902_PROMPT_INVALID', 'prompt must be a plain string')
    low, high = Z0902_PROMPT_RANGE
    if len(prompt.strip()) < low or len(prompt) > high:
        _refuse('Z0902_PROMPT_LENGTH_INVALID', 'prompt must be %d..%d characters '
                'and not blank (user contract)' % (low, high))
    return prompt            # serialized exactly as authored — never rewritten


def _validate_z0902_duration(duration):
    if duration is None:
        return Z0902_DURATION_DEFAULT
    if type(duration) is not int:            # rejects bool and float exactly
        _refuse('Z0902_DURATION_NOT_INTEGER', '%r — the user contract requires a '
                'plain integer (bool/float refused, never rounded silently)'
                % (duration,))
    low, high = Z0902_DURATION_RANGE
    if not low <= duration <= high:
        _refuse('Z0902_DURATION_OUT_OF_RANGE', '%d outside the user contract range '
                '%d..%d' % (duration, low, high))
    return duration


def _validate_z0902_seed(seed):
    """Optional plain integer. Absent → the field is OMITTED entirely from the
    request. The user contract declares no range; no 32-bit or other limit is
    invented here (only the exact type is enforced)."""
    if seed is None:
        return None
    if type(seed) is not int:                # rejects bool and float exactly
        _refuse('Z0902_SEED_NOT_INTEGER', '%r — seed must be a plain integer or '
                'omitted (bool/float refused)' % (seed,))
    return seed


def _validate_z0902_resolution(resolution):
    if resolution is None:
        return Z0902_DEFAULT_RESOLUTION       # explicit serialization of default
    if resolution not in Z0902_RESOLUTION_OPTIONS:
        _refuse('Z0902_RESOLUTION_INVALID', '%r is not one of the 8 exact user '
                'contract options; no silent substitution' % (resolution,))
    return resolution


def _validate_z0902_image_url(url, slot):
    if not isinstance(url, str) or isinstance(url, bool) or not url.strip():
        _refuse('Z0902_IMAGE_URL_INVALID', slot + ' must be a non-blank URL string')
    lowered = url.lower()
    if lowered.startswith(('data:', 'file:')):
        _refuse('Z0902_IMAGE_URL_SCHEME_UNSUPPORTED', slot + ' must be an https '
                'URL; data/file URIs are not the URL type of this contract')
    if 'data:video' in lowered or VIDEO_REF_PATTERN.search(lowered):
        _refuse('Z0902_VIDEO_REFERENCE_FORBIDDEN', slot + ' looks like a video — '
                'the z0902 contract has no video input of any kind')
    if 'data:audio' in lowered or AUDIO_REF_PATTERN.search(lowered):
        _refuse('Z0902_AUDIO_REFERENCE_FORBIDDEN', slot + ' looks like an audio '
                'file — the z0902 contract has no audio input of any kind')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname \
            or parsed.username or parsed.password:
        _refuse('Z0902_IMAGE_URL_INVALID', slot + ' must be an https URL with a '
                'hostname and no userinfo (mirrors the pinned runtime remote-asset '
                'rule); signed/no-extension URLs are accepted WITHOUT fetching — '
                'format compatibility stays unverified, no server validation is '
                'invented')
    return url


def _validate_z0902_images(images):
    """Named image slots. ref_image_0 is required; each of ref_image_1..5 is
    independently optional (TaskSpec revision 2: gaps such as image0+image5 are
    allowed); the caller's exact slot names and numbering are preserved — never
    renumbered, never filled with placeholder images."""
    if images is None:
        images = {}
    if not isinstance(images, dict):
        _refuse('Z0902_IMAGES_NOT_MAPPING', 'images must map exact slot names '
                + ', '.join(Z0902_IMAGE_SLOTS) + ' to URL strings')
    for slot in images:
        if slot not in Z0902_IMAGE_SLOTS:
            _refuse('Z0902_IMAGE_SLOT_UNKNOWN', str(slot) + ' — only '
                    + ', '.join(Z0902_IMAGE_SLOTS) + ' exist; ref_image_6+ and any '
                    'other field are rejected')
    if Z0902_REQUIRED_IMAGE_SLOT not in images:
        _refuse('Z0902_IMAGE0_REQUIRED', 'ref_image_0 is the single required image '
                'slot of the z0902 contract')
    for slot in sorted(images):
        _validate_z0902_image_url(images[slot], slot)
    return images


def _assert_z0902_body_keys(body):
    unknown = set(body) - Z0902_ALLOWED_BODY_KEYS
    if unknown:
        _refuse('Z0902_FIELD_FORBIDDEN', 'unknown fields ' + ', '.join(sorted(unknown))
                + ' — the z0902 request accepts only prompt/duration/resolution/'
                'seed and ref_image_0..5; audio inputs, video inputs, official '
                'MiniMax content/model fields and any extra field are forbidden')


def _canonical_encode(obj):
    """Deterministic canonical JSON, replicating the pinned runtime's encode()
    expression (laox_h3.py line 20) so z0902 fingerprints use the same scheme."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _validate_z0902_prepared_body(body):
    """Strict revalidation of a LOADED prepared body (submit path): hand-edited
    or drifted files must fail closed — required fields must be explicitly
    present and well-typed; defaults are never silently re-applied to stored
    requests and null seed is malformed (the field must be omitted entirely)."""
    if not isinstance(body, dict):
        _refuse('Z0902_REQUEST_SCHEMA_INVALID', 'prepared request carries no body')
    _assert_z0902_body_keys(body)
    missing = [k for k in ('prompt', 'duration', 'resolution') if k not in body]
    if missing:
        _refuse('Z0902_REQUEST_SCHEMA_INVALID', 'prepared body lacks required '
                'fields: ' + ', '.join(missing))
    if type(body['duration']) is not int \
            or not Z0902_DURATION_RANGE[0] <= body['duration'] <= Z0902_DURATION_RANGE[1]:
        _refuse('Z0902_DURATION_INVALID', 'prepared duration must be an explicit '
                'plain integer %d..%d — defaults are never re-applied to stored '
                'requests' % Z0902_DURATION_RANGE)
    if 'seed' in body and body['seed'] is None:
        _refuse('Z0902_REQUEST_SCHEMA_INVALID', 'seed must be a plain integer or '
                'omitted entirely — null is malformed')
    _validate_z0902_prompt(body['prompt'])
    # R1 (repair-1): a STORED body must already carry an explicit valid
    # resolution. The builder helper below defaults None to 1088p竖 for NEW
    # caller inputs and its return value is ignored here, so without this
    # guard a stored null resolution would pass through untouched.
    stored_resolution = body['resolution']
    if not isinstance(stored_resolution, str) \
            or stored_resolution not in Z0902_RESOLUTION_OPTIONS:
        _refuse('Z0902_RESOLUTION_INVALID', 'stored resolution %r must be an exact '
                'string member of the 8 user contract options — stored bodies never '
                'receive builder defaults; null/malformed refuse before authorization, '
                'state or network' % (stored_resolution,))
    _validate_z0902_resolution(body['resolution'])
    if 'seed' in body:
        _validate_z0902_seed(body['seed'])
    images = {slot: body[slot] for slot in Z0902_IMAGE_SLOTS if slot in body}
    _validate_z0902_images(images)
    return body


def compute_z0902_fingerprint(body):
    """Bind workflow + canonical request body (resolution included) into one
    fingerprint. The workflow id inside the digest keeps this fingerprint space
    structurally separate from every historical z0903 fingerprint, so z0903-era
    run records, locks and authorization artifacts can never match a z0902
    request."""
    _assert_z0902_body_keys(body)
    return hashlib.sha256(_canonical_encode(
        {'workflow': Z0902_WORKFLOW, 'body': body})).hexdigest()


def build_z0902_request(prompt, images=None, duration=None, seed=None,
                        resolution=None):
    """Serialize ONE z0902 request body from the actual caller inputs — no
    test-frame extraction, no blank.wav, no fabricated local PASS, no runtime
    instance, no network. Returns (body, fingerprint)."""
    load_z0902_config()                          # fail-closed consistency check
    prompt = _validate_z0902_prompt(prompt)
    images = _validate_z0902_images(images)
    duration = _validate_z0902_duration(duration)
    seed = _validate_z0902_seed(seed)
    resolution = _validate_z0902_resolution(resolution)
    body = {'prompt': prompt, 'duration': duration, 'resolution': resolution}
    body.update(images)                          # exact caller slot names kept
    if seed is not None:
        body['seed'] = seed                      # omitted entirely when absent
    _assert_z0902_body_keys(body)
    return body, compute_z0902_fingerprint(body)


def prepare_z0902(prompt, images=None, duration=None, seed=None, resolution=None,
                  request_id=None, write_evidence=True):
    """LOCAL preparation of ONE z0902 request: validate → serialize → fingerprint
    → persist the prepared request for a future guarded submission. Zero network,
    zero runtime loading, zero paid calls, zero uploads."""
    config = load_z0902_config()
    guards = _z0902_guards(config)
    body, fingerprint = build_z0902_request(prompt=prompt, images=images,
                                            duration=duration, seed=seed,
                                            resolution=resolution)
    rid = request_id or ('z0902-' + fingerprint[:16])
    rid = re.sub(r'[^A-Za-z0-9_-]', '-', rid)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', rid):
        _refuse('Z0902_REQUEST_ID_INVALID', 'request id must match the runtime '
                'slug rules (letter/digit start, then A-Za-z0-9_-)')
    out_dir = Z0902_PREPARED_DIR / rid
    out = out_dir / 'request.json'
    prepared = {
        'schema': Z0902_REQUEST_SCHEMA,
        'request_id': rid,
        'created_utc': _utc(),
        'workflow': Z0902_WORKFLOW,
        'contract_source': 'USER_SUPPLIED_NOT_SERVER_VERIFIED',
        'runtime_verification': 'NOT_RUN_USER_PROHIBITED',
        'fingerprint': fingerprint,
        'body': body,
        'image_slot_policy': 'ref_image_0 required; ref_image_1..5 independently '
                             'optional (gaps allowed, revision 2); caller slot '
                             'names preserved, never renumbered or filled',
        'url_format_compatibility': 'unverified — URLs are never fetched during '
                                    'prepare; no server-side validation invented',
        'guards_at_prepare': {'network_enabled': guards.get('network_enabled'),
                              'generation_enabled': guards.get('generation_enabled'),
                              'dry_run': guards.get('dry_run')},
        'paid_calls': 0, 'uploads': 0, 'network_calls': 0,
    }
    if out.is_file():
        try:
            existing = json.loads(out.read_text(encoding='utf-8'))
        except ValueError:
            _refuse('Z0902_PREPARED_CORRUPT', str(out))
        if existing.get('fingerprint') != fingerprint:
            _refuse('Z0902_REQUEST_ID_COLLISION', str(out) + ' already holds a '
                    'different fingerprint; use a new request id')
        prepared['created_utc'] = existing.get('created_utc', prepared['created_utc'])
    _atomic_write_json(out, prepared)
    prepared['request_path'] = str(out)
    if write_evidence:
        Z0902_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        evidence = dict(prepared)
        evidence['evidence_of'] = 'prepare (local serialization only)'
        stamp = _utc().replace(':', '').replace('+', '_')
        path = Z0902_EVIDENCE_DIR / ('prepare-%s-%s.json' % (rid, stamp))
        _atomic_write_json(path, evidence)
        prepared['evidence_file'] = str(path)
    return prepared


def doctor_z0902(write_evidence=True):
    """OFFLINE-ONLY contract/config report for the active route: no GET, no
    schema fetch, no tool resolution, no runtime instance — the historical z0903
    live doctor is frozen with the legacy route. Credential ENV presence is
    reported as booleans only; values are never read, echoed or logged."""
    config = load_z0902_config()
    guards = _z0902_guards(config)
    report = {
        'utc': _utc(),
        'mode': 'offline_only',
        'workflow': Z0902_WORKFLOW,
        'contract_source': 'USER_SUPPLIED_NOT_SERVER_VERIFIED',
        'runtime_verification': 'NOT_RUN_USER_PROHIBITED',
        'live_schema': 'not_run — no GET performed; --live is refused on this route',
        'project_default_resolution': Z0902_DEFAULT_RESOLUTION,
        'provider_declared_default_resolution':
            config.get('provider_declared_default_resolution'),
        'resolution_options': list(Z0902_RESOLUTION_OPTIONS),
        'request_contract': config.get('request_contract'),
        'guards': {'network_enabled': guards.get('network_enabled'),
                   'generation_enabled': guards.get('generation_enabled'),
                   'dry_run': guards.get('dry_run'),
                   'max_calls_per_authorization':
                       guards.get('max_calls_per_authorization'),
                   'max_budget_cny': guards.get('max_budget_cny'),
                   'token_is_not_consent': True},
        'credential_env_presence': {
            'AUTODL_ART_TOKEN': bool(os.environ.get('AUTODL_ART_TOKEN')),
            'AUTODL_ART_BASE_URL': bool(os.environ.get('AUTODL_ART_BASE_URL'))},
        'credential_note': 'presence booleans only — values never read here; '
                           'presence is not authorization and not consent',
        'transport': 'future guarded submit reuses the pinned oldX runtime '
                     'http_json via the private lab-rooted instance only; AutoDL '
                     'config resolves from AUTODL_ART_BASE_URL/AUTODL_ART_TOKEN '
                     'env vars (official autodl.art https domains only)',
        'legacy_z0903': 'frozen (LEGACY_Z0903_ENABLED=False); historical evidence '
                        'under runtime/evidence/h3-autodl-v1/',
        'paid_calls': 0, 'uploads': 0, 'network_calls': 0,
    }
    if write_evidence:
        Z0902_EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = report['utc'].replace(':', '').replace('+', '_')
        _atomic_write_json(Z0902_EVIDENCE_DIR / ('doctor-%s.json' % stamp), report)
    return report


def _z0902_settings_env():
    """ENV-ONLY AutoDL configuration for the z0902 route. The oldX
    app/api/autodl.env file and the runtime settings() resolver are z0903-locked
    and are never consulted here; only the two environment variables below are
    read, and the official HTTPS domains are the only accepted bases."""
    base = os.environ.get('AUTODL_ART_BASE_URL', 'https://autodl.art').rstrip('/')
    if base not in Z0902_OFFICIAL_BASES:
        _refuse('Z0902_BASE_UNOFFICIAL', 'AUTODL_ART_BASE_URL must be an official '
                'HTTPS AutoDL domain (' + ' or '.join(sorted(Z0902_OFFICIAL_BASES))
                + '); got an unaccepted value — refuse, no fallback')
    return {'base': base, 'token': os.environ.get('AUTODL_ART_TOKEN', '')}


def _validate_z0902_cost_estimate(record, fingerprint):
    """Structured quote contract (same standard as legacy repair-1) re-bound to
    z0902: a finite positive CNY estimate covering exactly ONE call, with human
    confirmed platform quotation provenance. Shape/binding validation only — it
    cannot establish what the platform will actually charge; unknown pricing
    refuses fail-closed. No estimate is generated by this module."""
    est = record.get('cost_estimate')
    if not isinstance(est, dict):
        _refuse('AUTH_ESTIMATE_INVALID', 'a structured cost_estimate is required — '
                'unknown pricing must refuse fail-closed')
    if est.get('status') != ESTIMATE_STATUS_CONFIRMED:
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.status must be '
                + ESTIMATE_STATUS_CONFIRMED)
    if est.get('currency') != 'CNY':
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.currency must be CNY — no '
                'conversion is performed')
    if est.get('unit') != ESTIMATE_UNIT:
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.unit must be ' + ESTIMATE_UNIT)
    value = est.get('estimate_cny')
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.estimate_cny must be a '
                'plain finite number > 0 (bool/str/NaN/inf/zero/negative/null all '
                'refuse; null means unknown cost and must not submit)')
    if est.get('workflow_id') != Z0902_WORKFLOW:
        _refuse('AUTH_ESTIMATE_MISBOUND', 'cost_estimate.workflow_id must be '
                + Z0902_WORKFLOW)
    if est.get('request_fingerprint') != fingerprint:
        _refuse('AUTH_ESTIMATE_MISBOUND', 'cost_estimate is not bound to this '
                'exact z0902 request fingerprint')
    prov = est.get('provenance')
    if not isinstance(prov, dict) \
            or not isinstance(prov.get('recorded_by'), str) or not prov['recorded_by'].strip() \
            or not isinstance(prov.get('source'), str) or not prov['source'].strip() \
            or not isinstance(prov.get('recorded_utc'), str) or not prov['recorded_utc'].strip():
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.provenance must state '
                'recorded_by/source/recorded_utc — the platform quotation a human '
                'actually confirmed')
    return est


def _validate_z0902_authorization(record, fingerprint, resolution):
    """Strict-shape z0902 authorization gate. New schema id + workflow +
    resolution + fingerprint bindings mean historical z0903 v1 authorization
    records are structurally unable to authorize a z0902 submission."""
    if not isinstance(record, dict) or record.get('schema') != Z0902_AUTH_SCHEMA:
        _refuse('AUTH_RECORD_INVALID', 'schema must be ' + Z0902_AUTH_SCHEMA
                + ' — historical z0903 v1 records cannot authorize z0902')
    if record.get('workflow') != Z0902_WORKFLOW:
        _refuse('AUTH_RECORD_INVALID', 'workflow must be ' + Z0902_WORKFLOW)
    if record.get('authorized_by_human') is not True:
        _refuse('AUTH_RECORD_INVALID', 'authorized_by_human must be true (CLI '
                'flags and token presence are not consent)')
    consent = record.get('consent_statement')
    if not isinstance(consent, str) or isinstance(consent, bool) \
            or len(consent.strip()) < 10:
        _refuse('AUTH_RECORD_INVALID', 'consent_statement must carry the human '
                'consent text')
    if not isinstance(record.get('request_fingerprint'), str) \
            or record['request_fingerprint'] != fingerprint:
        _refuse('AUTH_FINGERPRINT_MISMATCH', 'authorization is bound to a different '
                'request')
    if record.get('request_resolution') != resolution:
        _refuse('AUTH_FINGERPRINT_MISMATCH', 'authorization is bound to a different '
                'resolution (' + str(record.get('request_resolution')) + ' != '
                + resolution + ')')
    if type(record.get('max_calls')) is not int or record['max_calls'] != 1:
        _refuse('AUTH_RECORD_INVALID', 'max_calls must be exactly integer 1')
    if type(record.get('calls_used')) is not int or not 0 <= record['calls_used'] < 1:
        _refuse('AUTH_CALLS_EXHAUSTED' if record.get('calls_used') == 1
                else 'AUTH_RECORD_INVALID',
                'calls_used must be integer 0 for a fresh single-call authorization')
    budget = record.get('budget')
    if not isinstance(budget, dict) or budget.get('currency') != 'CNY':
        _refuse('AUTH_BUDGET_INVALID', 'budget.currency must be CNY')
    amount = budget.get('max_total')
    if type(amount) not in (int, float) or not math.isfinite(amount) \
            or not 0 < amount <= MAX_BUDGET_CNY:
        _refuse('AUTH_BUDGET_INVALID', 'budget.max_total must be a plain finite '
                'number, positive and <= %.0f CNY' % MAX_BUDGET_CNY)
    waiver = record.get('cost_waiver')
    if waiver is not None:
        # explicit human waiver of the platform quote: the price is UNKNOWN, not estimated.
        # The budget ceiling above still applies; nothing here fabricates a quote.
        if not isinstance(waiver, dict) or waiver.get('waived_by_human') is not True \
                or not isinstance(waiver.get('statement'), str) or len(waiver['statement'].strip()) < 4 \
                or waiver.get('price_known') is not False:
            _refuse('AUTH_WAIVER_INVALID', 'cost_waiver needs waived_by_human=true, '
                    'price_known=false and the human statement')
        return
    estimate = _validate_z0902_cost_estimate(record, fingerprint)
    if estimate['estimate_cny'] > amount:
        _refuse('AUTH_BUDGET_INSUFFICIENT', 'cost estimate %.2f CNY exceeds the '
                'authorized budget %.2f CNY — refused before any network or state '
                'mutation' % (estimate['estimate_cny'], amount))


def submit_guarded_z0902(request_path, authorization_path, runtime=None):
    """Future single paid z0902 submission — STATIC DESIGN, disabled by default.
    Order of gates: (1) config guards (network/generation/dry_run) BEFORE any
    state or network mutation; (2) prepared request schema + fingerprint drift
    check; (3) authorization containment under Z0902_AUTH_DIR, strict record
    validation (single call, bounded CNY budget, structured quote ≤ budget,
    z0902-bound); (4) ENV-only settings; (5) exclusive lock + run record; then
    exactly one POST through the pinned runtime's http_json via the private
    lab-rooted instance — no retry, uncertain outcomes persisted, duplicate
    fingerprints refused. This function was NEVER executed in the integration
    round (NOT_RUN_USER_PROHIBITED)."""
    config = load_z0902_config()
    _assert_z0902_execution_allowed(config)
    request_path = Path(request_path)
    if not request_path.is_file():
        _refuse('Z0902_REQUEST_NOT_FOUND', str(request_path))
    try:
        prepared = json.loads(request_path.read_text(encoding='utf-8'))
    except ValueError:
        _refuse('Z0902_REQUEST_CORRUPT', str(request_path))
    if not isinstance(prepared, dict) or prepared.get('schema') != Z0902_REQUEST_SCHEMA:
        _refuse('Z0902_REQUEST_SCHEMA_INVALID', 'expected schema ' + Z0902_REQUEST_SCHEMA)
    if prepared.get('workflow') != Z0902_WORKFLOW:
        _refuse('Z0902_WORKFLOW_MISMATCH', 'prepared request workflow must be '
                + Z0902_WORKFLOW + ' — fail closed, no fallback')
    body = _validate_z0902_prepared_body(prepared.get('body'))
    fingerprint = compute_z0902_fingerprint(body)
    if prepared.get('fingerprint') != fingerprint:
        _refuse('Z0902_FINGERPRINT_DRIFT', 'stored fingerprint does not match the '
                'recomputed canonical request — refuse, never resubmit drifted '
                'content')
    auth_path = Path(authorization_path).resolve()
    if not auth_path.is_relative_to(Z0902_AUTH_DIR.resolve()):
        _refuse('Z0902_AUTH_RECORD_PATH_UNAUTHORIZED', 'authorization records must '
                'live inside ' + str(Z0902_AUTH_DIR) + '; outside path refused '
                'before any read/write/call')
    if not auth_path.is_file():
        _refuse('Z0902_AUTH_RECORD_MISSING', str(auth_path)
                + ' — paid submission requires an explicit human authorization '
                  'record (' + Z0902_AUTH_SCHEMA + ')')
    try:
        record = json.loads(auth_path.read_text(encoding='utf-8'))
    except ValueError:
        _refuse('AUTH_RECORD_INVALID', 'authorization record is not valid JSON')
    _validate_z0902_authorization(record, fingerprint, body['resolution'])
    cfg = _z0902_settings_env()
    if not cfg['token']:
        _refuse('Z0902_TOKEN_MISSING', 'AUTODL_ART_TOKEN is not set in the '
                'environment (env-only channel; the oldX autodl.env resolver is '
                'never read on this route)')
    transport_runtime = runtime if runtime is not None else runtime_exec()
    run_dir = Z0902_RUNS_DIR / fingerprint[:16]
    if run_dir.exists():
        _refuse('Z0902_RUN_ALREADY_EXISTS', 'this exact request already has a run '
                'record at ' + str(run_dir) + '; query it — duplicate submission is '
                'forbidden')
    Z0902_LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with (Z0902_LOCKS_DIR / (fingerprint + '.lock')).open('x', encoding='utf-8') as f:
            json.dump({'run': str(run_dir), 'created_at': _utc()}, f)
    except FileExistsError:
        _refuse('Z0902_SUBMIT_LOCK_EXISTS', 'an identical request already holds a '
                'submit lock; no automatic resubmission')
    run_dir.mkdir(parents=True, exist_ok=False)
    _atomic_write_json(run_dir / 'run.json', {
        'schema': 'lab-h3-z0902-run/v1', 'workflow': Z0902_WORKFLOW,
        'request_id': prepared.get('request_id'), 'base': cfg['base'],
        'fingerprint': fingerprint, 'created_utc': _utc(),
        'expected_duration': body['duration'], 'resolution': body['resolution'],
        'request_path': str(request_path)})
    _atomic_write_json(run_dir / 'request.json', body)
    _atomic_write_json(run_dir / 'state.json',
                       {'state': 'SUBMITTING', 'started_at': _utc()})
    try:
        response = transport_runtime.http_json(
            'POST', cfg['base'] + '/api/v1/comfyui/comfyui_workflow/' + Z0902_WORKFLOW,
            cfg['token'], body)
    except Exception as exc:  # noqa: BLE001 — recorded, never retried
        # http_json failures already carry a token-scrubbed .diagnostic (built by
        # the pinned runtime with THIS token); persist it without re-reading any
        # raw transport data and never retry.
        _atomic_write_json(run_dir / 'state.json',
                           {'state': 'SUBMISSION_UNCERTAIN', 'checked_at': _utc(),
                            'automatic_retry': False})
        _atomic_write_json(run_dir / 'failure.json',
                           {'at': _utc(), 'exception_type': type(exc).__name__,
                            'diagnostic': getattr(exc, 'diagnostic', None),
                            'automatic_retry': False})
        raise H3AdapterError('Z0902_SUBMIT_UNCERTAIN: outcome unconfirmed; record '
                             'kept at ' + str(run_dir) + ' — verify the receipt '
                             'before any retry') from None
    # every persisted receipt is scrubbed with the pinned runtime's own redaction
    # (token substring + authorization/token/api_key keys) BEFORE it touches disk
    scrubbed = transport_runtime.scrub(response, cfg['token'])
    task_id = response.get('data', {}).get('task_id') \
        if isinstance(response.get('data'), dict) else None
    if response.get('code') != 'Success' or not isinstance(task_id, str) or not task_id:
        _atomic_write_json(run_dir / 'submission.response.json', scrubbed)
        _atomic_write_json(run_dir / 'state.json',
                           {'state': 'SUBMISSION_UNCERTAIN', 'checked_at': _utc(),
                            'automatic_retry': False})
        _atomic_write_json(run_dir / 'failure.json',
                           {'at': _utc(),
                            'exception_type': 'Z0902_SUBMIT_UNCONFIRMED',
                            'automatic_retry': False})
        raise H3AdapterError('Z0902_SUBMIT_UNCONFIRMED: response carried no confirmed '
                             'task_id; scrubbed receipt kept at ' + str(run_dir)
                             + ' — check it, never auto-resubmit')
    _atomic_write_json(run_dir / 'submission.response.json', scrubbed)
    _atomic_write_json(run_dir / 'state.json',
                       {'state': 'SUBMITTED', 'task_id': task_id,
                        'submitted_at': _utc(), 'media_verified': False})
    record['calls_used'] = record.get('calls_used', 0) + 1
    record['last_call_utc'] = _utc()
    _atomic_write_json(auth_path, record)
    return {'state': 'SUBMITTED', 'task_id': task_id, 'workflow': Z0902_WORKFLOW,
            'run': str(run_dir), 'fingerprint': fingerprint}


# ---------------------------------------------------------------- z0902 query / download
# User permission 2026-10-11 ("允许只读 laox_h3.py 中任务查询和下载的函数"): the result endpoint and
# response shape below were read from that file's poll()/download() only and are reproduced here;
# the transport is still the pinned runtime's http_json. Status words mirror its SUCCESS/FAILED sets.
Z0902_SUCCESS = frozenset({'SUCCESS', 'COMPLETED', 'SUCCEEDED'})
Z0902_FAILED = frozenset({'FAILED', 'ERROR', 'CANCELLED', 'CANCELED'})
Z0902_FFPROBE = Path('E:/software/mini/MiniMax Design/current/resources/ffmpeg/ffprobe.exe')


def _z0902_run_path(run_dir):
    path = Path(run_dir).resolve()
    if not path.is_relative_to(Z0902_RUNS_DIR.resolve()) or not (path / 'run.json').is_file():
        _refuse('Z0902_RUN_UNKNOWN', 'only run records created by submit_guarded_z0902 can be queried')
    return path


def poll_z0902(run_dir, runtime=None):
    """ONE bounded read-only GET of the task result (no loop, no cancel, no resubmit)."""
    path = _z0902_run_path(run_dir)
    meta = json.loads((path / 'run.json').read_text(encoding='utf-8'))
    state = json.loads((path / 'state.json').read_text(encoding='utf-8'))
    task_id = state.get('task_id')
    if not task_id:
        _refuse('Z0902_NO_TASK_ID', 'no confirmed task_id; never resubmit')
    cfg = _z0902_settings_env()
    if not cfg['token']:
        _refuse('Z0902_TOKEN_MISSING', 'AUTODL_ART_TOKEN is not set')
    if cfg['base'] != meta.get('base'):
        _refuse('Z0902_BASE_CHANGED', 'run record was created against a different base')
    rt = runtime if runtime is not None else runtime_exec()
    value = rt.http_json('GET', cfg['base'] + '/api/v1/comfyui/comfyui_workflow/result/'
                         + urllib.parse.quote(task_id, safe=''), cfg['token'])
    scrubbed = rt.scrub(value, cfg['token'])
    stamp = _utc().replace(':', '').replace('+', '_')
    _atomic_write_json(path / ('status_%s.json' % stamp), scrubbed)
    if value.get('code') != 'Success':
        _refuse('Z0902_QUERY_ERROR', 'query returned a non-success code; run record unchanged')
    data = value.get('data') or {}
    if data.get('task_id') and data['task_id'] != task_id:
        _refuse('Z0902_TASK_ID_MISMATCH', 'query result belongs to another task')
    status = str(data.get('status', 'UNKNOWN')).upper()
    _atomic_write_json(path / 'latest.response.json', scrubbed)
    _atomic_write_json(path / 'state.json', {'state': status, 'task_id': task_id,
                                             'checked_at': _utc(), 'media_verified': False})
    return {'state': status, 'task_id': task_id, 'run': str(path),
            'terminal': status in Z0902_SUCCESS or status in Z0902_FAILED}


def _z0902_probe(path):
    if not Z0902_FFPROBE.is_file():
        return {'probed': False, 'reason': 'ffprobe not found'}
    proc = subprocess.run([str(Z0902_FFPROBE), '-v', 'error', '-print_format', 'json',
                           '-show_streams', '-show_format', str(path)],
                          capture_output=True, timeout=30)
    if proc.returncode != 0:
        return {'probed': False, 'reason': 'ffprobe exit %d' % proc.returncode}
    info = json.loads(proc.stdout.decode('utf-8', errors='replace'))
    video = next((x for x in info.get('streams', []) if x.get('codec_type') == 'video'), {})
    audio = [x for x in info.get('streams', []) if x.get('codec_type') == 'audio']
    return {'probed': True, 'width': video.get('width'), 'height': video.get('height'),
            'codec': video.get('codec_name'), 'duration_sec': float(info.get('format', {}).get('duration') or 0),
            'has_audio_stream': bool(audio)}


Z0902_BLACK_LUMA = 10.0      # mean luma below this on every sampled frame = blank output
Z0902_DARK_LUMA = 70.0       # below this = low-key warning (recorded, human decides)


def _z0902_content_check(path):
    """Sample ~12 frames and measure luma. Catches blank (black) results that the
    duration/resolution checks cannot. Needs cv2; without it the check is reported as not run."""
    try:
        import cv2
    except ImportError:
        return {'checked': False, 'reason': 'cv2 not available'}
    cap = cv2.VideoCapture(str(path))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        return {'checked': True, 'readable': False, 'blank': True}
    values = []
    for k in range(0, total, max(1, total // 12)):
        cap.set(cv2.CAP_PROP_POS_FRAMES, k)
        ok, frame = cap.read()
        if ok:
            values.append(float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()))
    cap.release()
    if not values:
        return {'checked': True, 'readable': False, 'blank': True}
    mean = sum(values) / len(values)
    return {'checked': True, 'readable': True, 'luma_mean': round(mean, 1),
            'luma_min': round(min(values), 1), 'luma_max': round(max(values), 1),
            'blank': max(values) < Z0902_BLACK_LUMA, 'low_key_warning': mean < Z0902_DARK_LUMA}


def download_z0902(run_dir):
    """Download the single video result (HTTPS, no token attached) and probe it locally."""
    path = _z0902_run_path(run_dir)
    meta = json.loads((path / 'run.json').read_text(encoding='utf-8'))
    state = json.loads((path / 'state.json').read_text(encoding='utf-8'))
    target = path / 'video.mp4'
    receipt = path / 'download.json'
    if receipt.is_file() and target.is_file():
        prior = json.loads(receipt.read_text(encoding='utf-8'))
        if hashlib.sha256(target.read_bytes()).hexdigest() == prior['sha256']:
            if 'content' not in prior:               # receipt predates the content check: apply it now
                prior['content'] = _z0902_content_check(target)
                prior['checks']['not_blank'] = bool(prior['content'].get('checked')
                                                    and not prior['content'].get('blank'))
                _atomic_write_json(receipt, prior)
                ok = all(prior['checks'].values())
                _atomic_write_json(path / 'state.json', {
                    'state': 'MEDIA_VERIFIED' if ok else 'MEDIA_CHECK_FAILED',
                    'task_id': state.get('task_id'), 'media_verified': ok,
                    'content_review_required': True})
            return prior
    if state.get('state') not in Z0902_SUCCESS:
        _refuse('Z0902_NOT_SUCCEEDED', 'state is %s; query to SUCCESS first' % state.get('state'))
    value = json.loads((path / 'latest.response.json').read_text(encoding='utf-8'))
    videos = [v for v in (value.get('data') or {}).get('results', [])
              if v.get('type') == 'video' and v.get('url')]
    if len(videos) != 1:
        _refuse('Z0902_RESULT_NOT_SINGLE_VIDEO', 'inspect the receipt manually')
    parsed = urllib.parse.urlsplit(videos[0]['url'])
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username:
        _refuse('Z0902_RESULT_URL_INVALID', 'result download must be https')
    partial = target.with_suffix('.partial')
    req = urllib.request.Request(videos[0]['url'], headers={'User-Agent': 'lab-z0902/1'})
    with urllib.request.urlopen(req, timeout=120) as src, partial.open('wb') as out:
        while True:
            block = src.read(1024 * 1024)
            if not block:
                break
            out.write(block)
    if partial.stat().st_size == 0:
        _refuse('Z0902_DOWNLOAD_EMPTY', 'downloaded file is empty')
    partial.replace(target)
    media = _z0902_probe(target)
    expected = meta.get('expected_duration')
    content = _z0902_content_check(target)
    checks = {'not_blank': bool(content.get('checked') and not content.get('blank')),
              'duration_within_1s': bool(media.get('probed') and expected
                                         and abs(media['duration_sec'] - expected) <= 1.0),
              'resolution_1088x1920': bool(media.get('probed') and media.get('width') == 1088
                                           and media.get('height') == 1920)}
    item = {'task_id': state['task_id'], 'path': str(target), 'bytes': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'downloaded_utc': _utc(),
            'expected_duration': expected, 'media': media, 'content': content, 'checks': checks,
            'content_review_required': True}
    _atomic_write_json(receipt, item)
    verified = all(checks.values())
    _atomic_write_json(path / 'state.json', {'state': 'MEDIA_VERIFIED' if verified else 'MEDIA_CHECK_FAILED',
                                             'task_id': state['task_id'], 'media_verified': verified,
                                             'content_review_required': True})
    return item


# =====================================================================
# HISTORICAL ROUTE — z0903 silent TEST B-roll chain (h3-autodl-v1,
# 2026-10-11). FROZEN: LEGACY_Z0903_ENABLED=False and every public legacy
# entry refuses first. Retained verbatim for provenance; its receipts and
# evidence stay under runtime/evidence/h3-autodl-v1/. The z0902 route above
# never calls into anything below (fail closed instead of routing an
# image-only request through this audio-locked serializer — see EK-23).
# Re-enabling requires an explicit coordinated source change plus a fresh
# authorization review; it is not reachable from the active CLI.
# =====================================================================

LEGACY_Z0903_ENABLED = False


def _legacy_z0903_refuse(caller):
    if not LEGACY_Z0903_ENABLED:
        raise H3AdapterError(
            'LEGACY_Z0903_DISABLED: ' + caller + ' belongs to the frozen historical '
            'z0903 route (audio-locked serializer; see EK-23). The active AutoDL '
            'route is minimax_h3_z0902 image-only; z0902 requests must never be '
            'routed through this function. Re-enabling requires an explicit '
            'coordinated source change plus fresh authorization review.')


EVIDENCE_DIR = EXEC_ROOT / 'evidence' / 'h3-autodl-v1'
PROJECTS_DIR = EXEC_ROOT / 'h3-autodl-v1' / 'projects'
AUTH_DIR = EXEC_ROOT / 'h3-autodl-v1' / 'authorization'
WORKFLOW = 'minimax_h3_z0903'
RESOLUTION = '768p竖(768*1376)'
# lab operational ceiling mirrors oldX h3_policy cloud.max_segment_seconds=12; the
# provider's real integer range (1..15) is validated against the fetched live schema
LAB_DURATION_RANGE = (4, 12)
LIVE_GET_TIMEOUT_S = 20.0
FFMPEG_TIMEOUT_S = 30.0
MAX_FRAME_AT_S = 30.0
# The ONLY authorized read-only TEST frame source this round: the exact a1 clip bound
# in the dragonfruit case (coordinator follow-up 2026-10-11: no implicit extraction
# from arbitrary case refs / user videos; those are VIDEO_REFERENCE_UNSUPPORTED).
AUTHORIZED_FRAME_SOURCE = {
    'path': 'F:/codex/laox_account/video_postprod/artifacts/imports/eb/'
            'ebba26902592edba75027de82e2d11d383e05511ddb24a046c5fd4da868c000e.mp4',
    'sha256': 'ebba26902592edba75027de82e2d11d383e05511ddb24a046c5fd4da868c000e',
}
AUTH_SCHEMA = 'lab-h3-submit-authorization/v1'


# ---------------------------------------------------------------- live doctor

def fetch_live_schema(timeout_s=LIVE_GET_TIMEOUT_S):
    """HISTORICAL (z0903): real GET of the official workflow definition via the
    ORIGINAL runtime client. Frozen — the active z0902 route performs no GET at
    all (runtime verification NOT_RUN_USER_PROHIBITED)."""
    _legacy_z0903_refuse('fetch_live_schema')
    resolver = runtime_resolver()
    cfg = resolver.settings()
    box = {}

    def _run():
        try:
            box['schema'] = resolver.fetch_schema(cfg)
        except BaseException as exc:  # noqa: BLE001 — recorded, never retried
            box['error'] = exc

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive() or 'schema' not in box:
        if worker.is_alive():
            raise H3AdapterError('LIVE_SCHEMA_WATCHDOG_TIMEOUT: no result within %.0fs; '
                                 'automatic_retry=false' % timeout_s)
        exc = box.get('error')
        raise H3AdapterError('LIVE_SCHEMA_GET_FAILED: ' + type(exc).__name__
                             + ' (original runtime client, no retry, sanitized)')
    return box['schema'], cfg


def doctor(live=True):
    """HISTORICAL (z0903): safe connectivity/contract report. Frozen — use
    doctor_z0902() (offline-only) for the active route."""
    _legacy_z0903_refuse('doctor')
    resolver = runtime_resolver()
    cfg = resolver.settings()
    report = {
        'utc': _utc(), 'workflow': WORKFLOW, 'base': cfg['base'],
        'credential_configured': bool(cfg['token']),
        'credential_source': 'original runtime resolver; value never returned',
        'auth_verification': 'not_run',
        'automatic_retry': False, 'redirect_followed': False,
        'paid_calls': 0, 'uploads': 0,
    }
    try:
        report['tools'] = {'ffmpeg': resolver.tool('ffmpeg'), 'ffprobe': resolver.tool('ffprobe')}
    except Exception as exc:  # noqa: BLE001 — tool resolution is reported, not fatal
        report['tools'] = {'error': 'resolution failed: ' + type(exc).__name__}
    if live:
        schema, _ = fetch_live_schema()
        report['live_schema'] = summarize_schema(schema)
    return report


def summarize_schema(schema):
    """Field-by-field source-bound summary of the fetched definition (public data
    only; no token, no inline raw response dump)."""
    data = schema.get('data', {})
    rules = data.get('input_rules', {})
    fields = {}
    for key, rule in sorted(rules.items()):
        entry = {'type': rule.get('type'), 'required': bool(rule.get('required'))}
        for opt in ('node_id', 'field', 'default', 'min', 'max', 'min_length', 'max_length'):
            if opt in rule:
                entry[opt] = rule[opt]
        if rule.get('type') == 'enum':
            entry['options'] = [o.get('label') for o in rule.get('options', [])]
        fields[key] = entry
    video_inputs = [k for k, v in rules.items()
                    if v.get('type') == 'video' or k.startswith('ref_video')]
    audio0 = rules.get('ref_audio_0', {})
    # semantic: video_reference_supported is True only when the workflow actually
    # declares a video input; the live z0903 schema declares none (empty list → False)
    resolution_labels = [o.get('label') for o in rules.get('resolution', {}).get('options', [])]
    return {
        'fetched_via': 'original runtime fetch_schema (GET, 20s watchdog, no retry)',
        'uuid': data.get('uuid'), 'version': data.get('version'),
        'updated_at': data.get('updated_at'), 'price_type': data.get('price_type'),
        'input_fields': fields,
        'required_fields': sorted(k for k, v in rules.items() if v.get('required')),
        'video_reference_supported': bool(video_inputs),
        'video_inputs_found': video_inputs,
        'resolution_default': rules.get('resolution', {}).get('default'),
        'lab_resolution_allowed': RESOLUTION in resolution_labels,
        'blank_audio_default': audio0.get('default'),
        'endpoints': [e.get('name') for e in data.get('endpoint_info', {}).get('endpoints', [])],
    }


# ------------------------------------------------------- case binding guards

def load_case(case_dir):
    case_dir = Path(case_dir)
    payloads = {}
    for name in ('case.json', 'storyboards.json', 'review.json'):
        path = case_dir / name
        if not path.is_file():
            raise H3AdapterError('CASE_FILE_MISSING: ' + str(path))
        payloads[name[:-5]] = json.loads(path.read_text(encoding='utf-8'))
    return payloads


def verify_pass_shot(case_dir, shot_id):
    """Only a PASS shot whose content hash AND version still match the review record
    is preparable. RETURN / stale hash / unknown duration / 3D-dependent action /
    missing ref media all fail closed."""
    data = load_case(case_dir)
    plan, review = data['storyboards'], data['review']
    shot = next((s for s in plan['shots'] if s['shot_id'] == shot_id), None)
    if shot is None:
        raise H3AdapterError('SHOT_NOT_FOUND: ' + shot_id)
    entry = next((s for s in review['review']['shots'] if s['shot_id'] == shot_id), None)
    if entry is None:
        raise H3AdapterError('SHOT_REVIEW_MISSING: ' + shot_id)
    if entry['verdict'] != 'PASS' or shot_id not in review['final']['pass']:
        raise H3AdapterError('SHOT_NOT_PASS: ' + shot_id + ' verdict=' + str(entry['verdict']))
    if entry['version'] != shot['version']:
        raise H3AdapterError('SHOT_REVIEW_STALE: version drift (shot=%s review=%s)'
                             % (shot['version'], entry['version']))
    current_hash = ps.shot_content_hash(shot)
    if current_hash != entry['content_sha256']:
        raise H3AdapterError('SHOT_REVIEW_STALE: content hash mismatch for ' + shot_id)
    if shot.get('duration_sec') is None:
        raise H3AdapterError('SHOT_DURATION_UNKNOWN: ' + shot_id)
    if shot.get('needs_3d'):
        raise H3AdapterError('SHOT_REQUIRES_3D: 3D-dependent unreviewed action; '
                             'full-action 3D check unavailable this round')
    for ref in shot['refs']:
        declared = next((r for r in plan['refs'] if r['ref_id'] == ref['ref_id']), None)
        if declared is None:
            raise H3AdapterError('MISSING_REF_MEDIA: ref ' + ref['ref_id'] + ' not declared')
        if not Path(declared['path']).is_file():
            raise H3AdapterError('MISSING_REF_MEDIA: ' + declared['path'])
        if _sha256_file(Path(declared['path'])) != declared['sha256']:
            raise H3AdapterError('REF_MEDIA_HASH_MISMATCH: ' + ref['ref_id'])
    return data, shot


def assert_no_video_reference(items):
    """z0903 has no video inputs (verified against the live schema). Any attempt to
    bind a video file/URL (including URL forms with query strings) as a generation
    reference is rejected clearly — never an implicit frame extraction or transcoding
    of user video inputs."""
    for item in items:
        name = str(item)
        lowered = name.lower()
        if 'data:video' in lowered or VIDEO_REF_PATTERN.search(lowered):
            raise H3AdapterError('VIDEO_REFERENCE_UNSUPPORTED: the live z0903 workflow '
                                 'has no video input field; refusing ' + name)


def validate_test_duration(duration):
    if type(duration) is not int:
        raise H3AdapterError('DURATION_NOT_INTEGER: %r — provider requires an integer '
                             '1..15; planned shot durations are never rounded silently'
                             % (duration,))
    low, high = LAB_DURATION_RANGE
    if not low <= duration <= high:
        raise H3AdapterError('DURATION_OUT_OF_LAB_RANGE: %d outside lab operational '
                             'ceiling %d..%d (provider real range 1..15 is validated '
                             'separately against the live schema)' % (duration, low, high))
    return duration


def validate_seed(seed):
    if type(seed) is not int or not 1 <= seed <= 999999999999999:
        raise H3AdapterError('SEED_INVALID: must be a plain integer within the workflow '
                             'range 1..999999999999999 (got %r)' % (seed,))
    return seed


# ---------------------------------------------------------- TEST frame (a1)

def extract_frame(ref_path, expected_sha256, at_sec, out_png):
    """HISTORICAL (z0903): one READ-ONLY frame from the EXACT authorized a1 clip.
    Frozen — the z0902 route performs no frame extraction of any kind."""
    _legacy_z0903_refuse('extract_frame')
    name = str(ref_path)
    if name.lower().startswith(('http://', 'https://', 'data:')):
        raise H3AdapterError('TEST_FRAME_SOURCE_NOT_AUTHORIZED: URLs/data URIs are not '
                             'an authorized TEST frame source; only the exact pinned a1 '
                             'local clip may be read')
    if type(at_sec) not in (int, float) or not math.isfinite(at_sec) \
            or not 0 <= at_sec <= MAX_FRAME_AT_S:
        raise H3AdapterError('FRAME_TIMESTAMP_INVALID: must be a finite number within '
                             '0..%.0fs' % MAX_FRAME_AT_S)
    ref = Path(ref_path).resolve()
    authorized = Path(AUTHORIZED_FRAME_SOURCE['path']).resolve()
    if ref != authorized or expected_sha256 != AUTHORIZED_FRAME_SOURCE['sha256']:
        raise H3AdapterError('TEST_FRAME_SOURCE_NOT_AUTHORIZED: read-only TEST frame '
                             'extraction is pinned to the exact authorized a1 clip '
                             '(SHA ' + AUTHORIZED_FRAME_SOURCE['sha256'][:16] + '…); '
                             'other videos are VIDEO_REFERENCE_UNSUPPORTED, never '
                             'auto-extracted')
    if not ref.is_file():
        raise H3AdapterError('MISSING_REF_MEDIA: ' + str(ref))
    actual = _sha256_file(ref)
    if actual != expected_sha256:
        raise H3AdapterError('REF_MEDIA_HASH_MISMATCH: ' + str(ref))
    out = Path(out_png).resolve()
    if out == ref:
        raise H3AdapterError('TEST_FRAME_OUTPUT_ESCAPE_REFUSED: output must never be '
                             'the read-only source itself')
    if out.suffix.lower() != '.png' or not out.is_relative_to(EVIDENCE_DIR.resolve()):
        raise H3AdapterError('TEST_FRAME_OUTPUT_ESCAPE_REFUSED: derived PNG must live '
                             'under ' + str(EVIDENCE_DIR))
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = runtime_resolver().tool('ffmpeg')
    cmd = [ffmpeg, '-ss', '%.3f' % at_sec, '-i', str(ref),
           '-frames:v', '1', '-y', str(out)]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=0x08000000, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        if out.is_file():
            out.unlink()                      # drop any partial frame; bounded runtime
        raise H3AdapterError('FRAME_EXTRACTION_TIMEOUT: ffmpeg exceeded %.0fs'
                             % FFMPEG_TIMEOUT_S)
    if proc.returncode or not out.is_file() or out.stat().st_size == 0:
        raise H3AdapterError('FRAME_EXTRACTION_FAILED: ffmpeg exit=%d' % proc.returncode)
    return {
        'source_path': str(ref), 'source_sha256': actual,
        'source_read_only': True, 'extracted_at_sec': at_sec,
        'png_path': str(out), 'png_sha256': _sha256_file(out),
        'png_bytes': out.stat().st_size,
        'command_display': 'ffmpeg -ss {t} -i <authorized-a1> -frames:v 1 -y <out> '
                           '(timeout {d:.0f}s)'.format(t='%.3f' % at_sec, d=FFMPEG_TIMEOUT_S),
        'purpose': 'TEST visual reference only; not a real product image or identity '
                   'proof; never passed to the API as a video reference',
    }


# --------------------------------------------------- independent TEST intent

def build_test_intent(data, shot, duration, frame_meta, intent_id=None):
    """HISTORICAL (z0903): independent 5s silent-B-roll TEST generation intent.
    Frozen — kept verbatim for provenance."""
    _legacy_z0903_refuse('build_test_intent')
    duration = validate_test_duration(duration)
    case, plan = data['case'], data['storyboards']
    product = plan['product']
    new_shot_id = (intent_id or (shot['shot_id'] + '-h3test%ds' % duration))
    if any(existing['shot_id'] == new_shot_id for existing in plan['shots']):
        raise H3AdapterError('TEST_INTENT_ID_COLLISION: ' + new_shot_id
                             + ' must be a NEW id, never an existing storyboard shot id')
    ref = shot['refs'][0]
    declared = next(r for r in plan['refs'] if r['ref_id'] == ref['ref_id'])
    narration = shot['narration']
    intent_shot = {
        'shot_id': new_shot_id, 'product_id': shot['product_id'], 'version': 1,
        'purpose': shot['purpose'], 'narration': narration,
        'refs': [{'ref_id': ref['ref_id'], 'role': ref['role']}],
        'action': {k: (list(v) if isinstance(v, list) else v) for k, v in shot['action'].items()},
        'action_source': 'h3_test_intent(authored from PASS shot ' + shot['shot_id'] + ' v'
                         + str(shot['version']) + ')',
        'ref_fit': 'fitting', 'framing': shot['framing'], 'camera': shot['camera'],
        'movement': shot['movement'],
        # explicit approved generation intent, deliberately separate from the
        # storyboard's planned duration_sec (which is preserved below untouched)
        'duration_sec': float(duration),
        'model_capability': dict(shot['model_capability']),
        'must': list(shot['must']) + ['无口播静默片段：不得出现台词、字幕、角标或水印'],
        'must_not': list(shot['must_not']),
        'risk_flags': [], 'needs_3d': False, 'needs_3d_reason': None,
        'complex_grip': False, 'overlay_request': None, 'reuse': 'one_off_test_intent',
        'audio': {'has_original': False, 'must_preserve': False, 'policy': 'silent_placeholder'},
        'generated': {'status': 'to_be_generated', 'verified_real_product_test': False,
                      'provider': 'h3_autodl_test'},
        'counterexample_of': None,
    }
    mini_plan = {
        'schema_version': 'product-storyboards/v1',
        'case_id': case['case_id'] + '-h3test-intent',
        'created_utc': _utc(), 'policy_version': plan['policy_version'],
        'planning': {
            'engine': 'deterministic-local-v1', 'llm_invoked': False,
            'distinct_use_purposes': [shot['purpose']],
            'action_provenance': 'independent 5s TEST generation intent reusing the case '
                                 'action/refs/provenance; original PASS storyboard untouched',
            'native_storyboard_team_context': plan['planning'].get('native_storyboard_team_context'),
            'h3_test_intent': {
                'generation_duration_sec': duration,
                'storyboard_shot_id': shot['shot_id'],
                'storyboard_version': shot['version'],
                'storyboard_content_sha256': ps.shot_content_hash(shot),
                'storyboard_planned_duration_sec': shot['duration_sec'],
                'silent': True, 'spoken': False, 'rerender_of_accepted_ref': False,
                'blank_audio_placeholder_not_voice': True,
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
        raise H3AdapterError('TEST_INTENT_SEGMENT_MISSING: narration segment for '
                             + shot['shot_id'] + ' not found')
    review = ps.review_plan(mini_plan)
    verdict = review['shots'][0]
    if verdict['verdict'] != 'PASS':
        raise H3AdapterError('TEST_INTENT_NOT_PASS: local reviewer refused the intent: '
                             + json.dumps(verdict['problems'], ensure_ascii=False))
    return {
        'schema': 'lab-h3-test-intent/v1',
        'intent_id': new_shot_id,
        'created_utc': _utc(),
        'case_id': case['case_id'],
        'storyboard_binding': {'shot_id': shot['shot_id'], 'version': shot['version'],
                               'content_sha256': ps.shot_content_hash(shot),
                               'planned_duration_sec': shot['duration_sec']},
        'generation_intent': {'duration_sec': duration, 'silent': True, 'spoken': False,
                              'not_a_rerender_of': declared['ref_id']},
        'frame': frame_meta,
        'mini_plan': mini_plan,
        'review': review,
        'review_content_sha256': verdict['content_sha256'],
    }


# ------------------------------------------------------------- H3 mapping

def _zh(v):
    return str(v or '').strip()


def render_six_section_prompt(intent, product_name, subject_term):
    """HISTORICAL (z0903): authored six-section prompt (runtime X route: full
    sections, silent, no <d>). Kept verbatim for provenance."""
    shot = intent['mini_plan']['shots'][0]
    binding = intent['storyboard_binding']
    dur = intent['generation_intent']['duration_sec']
    action = shot['action']
    process = '，'.join(_zh(s) for s in action['process'])
    sha16 = intent['frame']['source_sha256'][:16]
    at = intent['frame']['extracted_at_sec']
    lines = [
        'subject_definitions:',
        '<Subject 1> 是本 TEST 生成意图展示的商品（%s，%s）。商品身份未经验证，画面不作为产品实测或身份证明。'
        % (product_name, subject_term),
        '<Picture 1> 是从既有演示素材（ref 绑定源，SHA256 %s…）在 %.1f 秒处只读抽取的单帧 TEST 参考图：'
        '仅提供商品外观、桌面场景与竖屏构图参考；不是实测产品照片，不保证首帧一致，禁止复刻源画面中的任何文字或水印。'
        % (sha16, at),
        '<Audio 1> 是官方工作流对必填 ref_audio_0 声明的 blank.wav 静音占位 URL；不是声音参考、不是口播授权。'
        '本片段为无口播静默 B-roll，生成的音频轨道在下游一律丢弃。',
        'summary:',
        '[reference generation] 目标视频是一段 %d 秒的无口播商品静默 B-roll TEST 片段（用途 %s）：画面呈现「%s」。'
        '以 <Picture 1> 为外观与构图参考、<Audio 1> 为必填静音占位；不是对已验收参考素材的重渲染，'
        '不包含任何台词、字幕、固定角标或水印。' % (dur, shot['purpose'], shot['narration']),
        'retention_analysis:',
        '<Subject 1>（出现在 [Shot 1]）: partially_preserved - 保留该商品类别的整体外观与色彩关系；'
        'TEST 参考非产品实测，允许生成画面在细节上不同于参考帧。',
        '<Picture 1> ([Shot 1] 构图参考): weak_reference - 仅保留桌面商品演示的景别、构图与光感方向；'
        '不要求逐像素一致，不作为首帧锚点。',
        '<Audio 1>: weak_reference - 仅作接口必填占位，不提供任何可听内容。',
        'detailed_description:',
        '竖屏 768x1376 自然写实桌面商品演示画面，光线均匀。全程无人物出镜（动作需要时仅允许持物的手部入画，'
        '不出现人脸）。整个片段一镜到底，无切换、无字幕、无角标、无水印、无可读文字。',
        '[Shot 1] %s，%s，%s。取景：%s；机位：%s；运动：%s。动作在片段内自然完成并保持到画面结束。'
        % (_zh(action['start']), process, _zh(action['end']),
           _zh(shot['framing']), _zh(shot['camera']), _zh(shot['movement'])),
        'overall_soundscape:',
        '无口播。整体为安静的室内环境底噪；<Audio 1> 仅是必填静音占位，不贡献任何可听内容，'
        '生成的音频轨道在下游被丢弃。',
        'non_diegetic_music:',
        'N/A（本 TEST 片段不配乐）。',
    ]
    prompt = '\n'.join(lines)
    if '<d>' in prompt or '</d>' in prompt:
        raise H3AdapterError('SILENT_INTENT_CONTAINS_DIALOGUE_TAG')
    return prompt


def map_intent_to_project(intent, seed, blank_audio_url):
    """HISTORICAL (z0903): map the independent TEST intent onto the runtime's
    template_pack + X-silent compatibility route. Kept verbatim for provenance."""
    mini = intent['mini_plan']
    shot = mini['shots'][0]
    product = mini['product']
    prompt = render_six_section_prompt(intent, product['name'], product.get('subject_term', ''))
    dur = intent['generation_intent']['duration_sec']
    frame_rel = str(Path(intent['frame']['png_path']).relative_to(EXEC_ROOT)).replace('\\', '/')
    project_id = ('laox_lab_h3test_' + re.sub(r'[^A-Za-z0-9_-]', '-', mini['case_id'])
                  + '_' + intent['intent_id'])[:80]
    assert_no_video_reference([frame_rel, blank_audio_url])
    project = {
        'schema': 'laox-h3-template/v1', 'account': 'laoX', 'project_id': project_id,
        'title': 'lab h3 silent TEST — ' + intent['intent_id'],
        'resolution': RESOLUTION, 'creation_mode': 'x', 'seed': seed,
        'template_pack': True,
        'editorial_status': 'TEMPLATE_PACK_LAB_TEST_SILENT_BROLL_NOT_FOR_PUBLICATION',
        'style_en': 'Natural realistic tabletop product demonstration with even lighting '
                    'and a clean vertical composition; no faces, no captions, no logos, '
                    'no watermarks, no readable interface text.',
        'assets': {
            'test_frame': {
                'kind': 'image', 'path': frame_rel, 'role': 'test_visual_reference',
                'role_en': 'TEST-only single-frame appearance/composition reference '
                           'extracted read-only from the declared existing demo clip; '
                           'not a real product photo or identity proof; not a guaranteed '
                           'first frame.'},
            'blank_audio': {
                'kind': 'audio', 'url': blank_audio_url, 'role': 'mandatory_silent_placeholder',
                'role_en': 'Official blank.wav URL declared by the live workflow schema as '
                           'the required ref_audio_0 default; this silent B-roll request '
                           'carries no voiceover and discards generated audio downstream.'},
        },
        'segments': [{
            'id': intent['intent_id'],
            'duration': dur,
            'prompt_zh': prompt,
            'image_assets': ['test_frame'], 'audio_assets': ['blank_audio'],
            'shots': [{
                'start': 0, 'end': dur, 'person_visible': False, 'delivery': 'silent',
                'dialogue': '',
                'visual_en': 'Tabletop product B-roll of %s — %s (from storyboard).'
                             % (product['name'], _zh(shot['framing'])),
                'camera_en': '%s / %s — single continuous take, vertical 768x1376.'
                             % (_zh(shot['camera']), _zh(shot['movement'])),
                'sound_en': 'No voiceover; silent B-roll TEST; generated audio is '
                            'discarded downstream.',
                'transition_en': 'No cuts, no overlays, no captions or watermarks.',
            }],
        }],
        'lab_h3_test_metadata': {
            'schema': 'lab-h3-test-metadata/v1',
            'intent_id': intent['intent_id'],
            'storyboard_binding': intent['storyboard_binding'],
            'generation_intent': intent['generation_intent'],
            'route_explanation': 'template_pack=true + creation_mode=x reuses the runtime\'s '
                                 'existing authored-six-section + silent-delivery validation '
                                 'path for fragment packs; it is NOT an activation of the '
                                 'oldX X creative workflow.',
            'spoken_locked': True, 'spoken': False,
            'blank_audio_placeholder_not_voice': True,
            'generated_audio_discarded_downstream': True,
            'test_flag': True, 'for_publication': False,
            'local_review': {'engine': intent['review']['engine'],
                             'verdict': 'PASS',
                             'content_sha256': intent['review_content_sha256']},
            'created_utc': _utc(),
        },
    }
    return project


def _scrub_body(body):
    safe = {}
    for k, v in body.items():
        if isinstance(v, str) and v.startswith('data:'):
            safe[k] = '[local asset encoded as data URI at submit time; %d chars omitted]' % len(v)
        else:
            safe[k] = v
    return safe


def prepare(case_dir, shot_id, duration=5, frame_at=1.0, seed=20261011,
            blank_audio_url=None, live=True, write_evidence=True):
    """HISTORICAL (z0903): end-to-end local preparation of ONE 5s silent TEST
    request via the original runtime. Frozen — use prepare_z0902() for the
    active image-only route."""
    _legacy_z0903_refuse('prepare')
    duration = validate_test_duration(duration)
    seed = validate_seed(seed)
    data, shot = verify_pass_shot(case_dir, shot_id)
    ref = shot['refs'][0]
    declared = next(r for r in data['storyboards']['refs'] if r['ref_id'] == ref['ref_id'])
    png = EVIDENCE_DIR / ('test-frame-%s-at%.1fs.png' % (ref['ref_id'], frame_at))
    frame = extract_frame(declared['path'], declared['sha256'], frame_at, png)
    intent = build_test_intent(data, shot, duration, frame)
    if live:
        schema, _ = fetch_live_schema()
    else:
        schema = json.loads(Path('F:/codex/laox_account/app/api/'
                                 'workflow_schema_z0903.json').read_text(encoding='utf-8'))
    if blank_audio_url is None:
        # declared official silent placeholder from the workflow definition itself
        blank_audio_url = schema['data']['input_rules']['ref_audio_0']['default'].split(';')[-1]
    runtime = runtime_exec()
    project = map_intent_to_project(intent, seed, blank_audio_url)
    project_path = PROJECTS_DIR / project['project_id'] / 'project.json'
    runtime.save(project_path, project)
    loaded, qa = runtime.load_project(str(project_path))
    preview = runtime.previews(str(project_path))
    seg = loaded['segments'][0]
    body, audit, fingerprint = runtime.prepare_bundle(loaded, seg)
    runtime.validate_schema(body, schema)
    evidence = {
        'schema': 'lab-h3-prepare-evidence/v1', 'utc': _utc(),
        'case_dir': str(Path(case_dir)), 'shot_id': shot_id,
        'storyboard_binding': intent['storyboard_binding'],
        'generation_intent': intent['generation_intent'],
        'frame': frame, 'intent_review': {'verdict': 'PASS',
                                          'content_sha256': intent['review_content_sha256']},
        'project_path': str(project_path), 'preview_folder': preview['folder'],
        'segment_id': seg['id'], 'fingerprint': fingerprint,
        'request_preview_scrubbed': _scrub_body(body),
        'assets_audit': [{'asset_id': a['asset_id'], 'kind': a['kind'],
                          'remote': a.get('remote', False), 'role_en': a['role_en'],
                          'request_field': a['request_field'],
                          'sha256': a.get('sha256')} for a in audit],
        'live_schema_source': {'uuid': schema['data'].get('uuid'),
                               'updated_at': schema['data'].get('updated_at'),
                               'required_fields': sorted(
                                   k for k, v in schema['data']['input_rules'].items()
                                   if v.get('required'))},
        'guards': {'paid_calls': 0, 'uploads': 0, 'video_reference_inputs': 0,
                   'base64_in_evidence': False, 'secrets_in_evidence': False},
        'runtime_validation': {'load_project': 'PASS', 'previews': preview['status'],
                               'validate_schema_live': bool(live),
                               'segment_qa': {'total_seconds': qa.get('total_seconds'),
                                              'person_seconds': qa.get('person_seconds')}},
    }
    if write_evidence:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        out = EVIDENCE_DIR / ('prepare-%s-%s.json' % (shot_id, _utc().replace(':', '').replace('+', '_')))
        out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
        intent_out = EVIDENCE_DIR / ('test-intent-%s.json' % intent['intent_id'])
        intent_out.write_text(json.dumps(intent, ensure_ascii=False, indent=2),
                              encoding='utf-8')
        evidence['evidence_files'] = [str(out), str(intent_out)]
    return evidence


# ------------------------------------------------------------ guarded submit

def compute_fingerprint(project_path, segment_id, runtime=None):
    """HISTORICAL (z0903): fingerprint of a mapped template project. Frozen — use
    compute_z0902_fingerprint() for the active route."""
    _legacy_z0903_refuse('compute_fingerprint')
    runtime = runtime or runtime_exec()
    data, _ = runtime.load_project(str(project_path))
    seg = next((s for s in data['segments'] if s['id'] == segment_id), None)
    if seg is None:
        raise H3AdapterError('SEGMENT_NOT_FOUND: ' + segment_id)
    _, _, fingerprint = runtime.prepare_bundle(data, seg)
    return fingerprint


def _validate_cost_estimate(record, fingerprint):
    """HISTORICAL (z0903, repair-1 standard): structured, request-bound, finite
    positive CNY estimate covering exactly ONE call."""
    est = record.get('cost_estimate')
    if not isinstance(est, dict):
        _refuse('AUTH_ESTIMATE_INVALID',
                'a structured cost_estimate is required — unknown pricing must refuse '
                'fail-closed; the workflow schema price field is NOT proof of free')
    if est.get('status') != ESTIMATE_STATUS_CONFIRMED:
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.status must be '
                + ESTIMATE_STATUS_CONFIRMED + ' (a human-confirmed platform quotation '
                'recorded by the trusted coordinator; unverified/assumed statuses refuse)')
    if est.get('currency') != 'CNY':
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.currency must be CNY — the live '
                'billing_config currency is unverified, so no conversion is performed')
    if est.get('unit') != ESTIMATE_UNIT:
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.unit must be ' + ESTIMATE_UNIT
                + ' — billing granularity is unverified; per-second/per-video guesses '
                'are refused, never inferred')
    value = est.get('estimate_cny')
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.estimate_cny must be a plain '
                'finite number > 0 (bool/str/NaN/inf/zero/negative all refuse; a null '
                'estimate means the cost is unknown and must not submit)')
    if est.get('workflow_id') != WORKFLOW:
        _refuse('AUTH_ESTIMATE_MISBOUND', 'cost_estimate.workflow_id must be ' + WORKFLOW)
    if est.get('request_fingerprint') != fingerprint:
        _refuse('AUTH_ESTIMATE_MISBOUND',
                'cost_estimate is not bound to this exact request fingerprint')
    prov = est.get('provenance')
    if not isinstance(prov, dict) \
            or not isinstance(prov.get('recorded_by'), str) or not prov['recorded_by'].strip() \
            or not isinstance(prov.get('source'), str) or not prov['source'].strip() \
            or not isinstance(prov.get('recorded_utc'), str) or not prov['recorded_utc'].strip():
        _refuse('AUTH_ESTIMATE_INVALID', 'cost_estimate.provenance must state '
                'recorded_by/source/recorded_utc — the platform quotation the human '
                'actually confirmed; validation cannot invent or verify pricing itself')
    return est


def _validate_authorization(record, fingerprint, segment_id):
    """HISTORICAL (z0903, repair-1): strict-shape gate for v1 authorization
    records. Frozen — the z0902 route uses _validate_z0902_authorization."""
    if not isinstance(record, dict) or record.get('schema') != AUTH_SCHEMA:
        _refuse('AUTH_RECORD_INVALID', 'schema must be ' + AUTH_SCHEMA)
    if record.get('authorized_by_human') is not True:
        _refuse('AUTH_RECORD_INVALID', 'authorized_by_human must be true (CLI flags are '
                                     'not consent)')
    consent = record.get('consent_statement')
    if not isinstance(consent, str) or isinstance(consent, bool) or len(consent.strip()) < 10:
        _refuse('AUTH_RECORD_INVALID', 'consent_statement must carry the human consent text')
    basis = record.get('cost_estimate_basis')
    if not isinstance(basis, str) or isinstance(basis, bool) or len(basis.strip()) < 8:
        _refuse('AUTH_RECORD_INVALID', 'cost_estimate_basis must state the verifiable cost '
                'estimate the human relied on; the workflow schema price field (0) is NOT '
                'proof of free — billing_config tariff values are unverified (currency/'
                'unit/granularity unknown, never converted here)')
    if not isinstance(record.get('request_fingerprint'), str) \
            or record['request_fingerprint'] != fingerprint:
        _refuse('AUTH_FINGERPRINT_MISMATCH', 'authorization is bound to a different request')
    if record.get('segment_id') != segment_id:
        _refuse('AUTH_FINGERPRINT_MISMATCH', 'authorization is bound to a different segment')
    if type(record.get('max_calls')) is not int or record['max_calls'] != 1:
        _refuse('AUTH_RECORD_INVALID', 'max_calls must be exactly integer 1')
    if type(record.get('calls_used')) is not int or not 0 <= record['calls_used'] < 1:
        # negative / float / bool / already-used values are all malformed or exhausted
        _refuse('AUTH_CALLS_EXHAUSTED' if record.get('calls_used') == 1
                else 'AUTH_RECORD_INVALID',
                'calls_used must be integer 0 for a fresh single-call authorization')
    budget = record.get('budget')
    if not isinstance(budget, dict) or budget.get('currency') != 'CNY':
        _refuse('AUTH_BUDGET_INVALID', 'budget.currency must be CNY')
    amount = budget.get('max_total')
    if type(amount) not in (int, float) or not math.isfinite(amount) \
            or not 0 < amount <= MAX_BUDGET_CNY:
        _refuse('AUTH_BUDGET_INVALID', 'budget.max_total must be a plain finite number, '
                'positive and <= %.0f CNY' % MAX_BUDGET_CNY)
    estimate = _validate_cost_estimate(record, fingerprint)
    if estimate['estimate_cny'] > amount:
        _refuse('AUTH_BUDGET_INSUFFICIENT',
                'cost estimate %.2f CNY exceeds the authorized budget %.2f CNY — '
                'refused before any network or state mutation'
                % (estimate['estimate_cny'], amount))


def guarded_submit(project_path, segment_id, authorization_path, runtime=None):
    """HISTORICAL (z0903): future single paid submission, delegated to the
    ORIGINAL runtime. Frozen — the active route is submit_guarded_z0902()."""
    _legacy_z0903_refuse('guarded_submit')
    runtime = runtime or runtime_exec()
    project_path = Path(project_path)
    if not project_path.is_file():
        _refuse('PROJECT_NOT_FOUND', str(project_path))
    auth_path = Path(authorization_path).resolve()
    if not auth_path.is_relative_to(AUTH_DIR.resolve()):
        _refuse('AUTH_RECORD_PATH_UNAUTHORIZED', 'authorization records must live inside '
                + str(AUTH_DIR) + '; outside path refused before any read/write/call')
    fingerprint = compute_fingerprint(project_path, segment_id, runtime)
    if not auth_path.is_file():
        _refuse('AUTH_RECORD_MISSING', str(auth_path)
                + ' — paid submission requires an explicit human authorization record')
    record = json.loads(auth_path.read_text(encoding='utf-8'))
    _validate_authorization(record, fingerprint, segment_id)
    result = runtime.submit(str(project_path), segment_id)
    record['calls_used'] = record.get('calls_used', 0) + 1
    record['last_call_utc'] = _utc()
    auth_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
