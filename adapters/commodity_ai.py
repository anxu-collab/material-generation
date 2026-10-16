"""commodity_ai — core adapter, immutable audit store and contracts for
commodity-material-ai-v1 (MODULE.md core interface section is the exact contract;
authoritative business field sets come from the user's original request, see
runtime/evidence/commodity-material-ai-v1/user-original-request.txt).

Design boundaries (AGENTS.md + docs/modules/commodity-material-ai-v1/MODULE.md):

- ClaudeCLIAdapter drives the OFFICIAL local Claude CLI only: fixed model
  claude-opus-5-5, -p prompt via stdin, structured output validated strictly against
  SCHEMAS[role], restricted / dontAsk / permission-prompts none / empty tools / MCP
  disabled / no session persistence, argv array without shell, isolated cwd inside the
  project runtime (junction-safe), auth-override env channels refused (presence
  recorded, values never), bounded stream-read stdout/stderr with prompt kill on
  overflow, hard deadline, single-flight concurrency guard, no automatic auth retry /
  model downgrade / API fallback. Three independent role prompts: Director designs the
  shot intent, Writer converts it into the H3-executable spec + reference requirements,
  Reviewer produces the repair decision record. Zero H3 anywhere.
- ClefAdapter speaks the REAL local service protocol (F:/ai_services/clef_flash
  scripts/adapter.py, read-only reference): GET /health for service identity /
  Clef-Flash-Q4_K_M.gguf / llama.cpp backend / nvidia-smi cuda facts, and the native
  POST /v1/systemone state+questions decision. A verified record needs validated REAL
  probabilities (noul p_true with raw cross-check because the service clamps, choice
  probability map, score label-keyed probabilities) AND ok health with CUDA available
  — GPU presence alone is never treated as executed-inference proof; anything less is
  an honest blocked record. Text-only service: B-phase visuals stay UNVERIFIED and
  continuous motion NEEDS_REVIEW; filenames/descriptions are never fed as visual
  evidence. HTTP uses no environment proxy and refuses redirects; endpoints stay
  loopback.
- MaterialStore is an append-only SQLite audit store under
  <root>/runtime/commodity-material-ai-v1: every version is preserved (broken /
  rejected / locked originals included and queryable), duplicate shot versions are
  refused, media must live inside the project root with no symlink/junction escape,
  true duration/decode metadata only (ffprobe; never invented), shadow reviews append
  observations without touching the approval binding, usage supports cross-shot and
  locked reuse, APPROVED requires real (non-mock) review evidence plus hash-bound
  human visual/temporal confirmation and a material description block, and export
  revalidates freshness (stale state or media drift refuses). The manifest carries the
  full user-required field list (asset_id … review_record_id) plus audit extras.
- run_material_case / run_dry_run drive the FULL per-case chain Director ->
  Writer -> A review -> Mock H3 (no asset) -> B review -> per-shot Reviewer with
  every stage persisted (stage_records table, run_id-scoped). No real model
  inference ever happens in the default path: Claude stages carry the explicit
  known-blocked AUTH_EXPIRED record (proven this round, never retried), Clef
  stages fail closed without an owned inference lifecycle, B reviews without an
  eligible video stay NEEDS_REVIEW, and model gaps are covered by clearly-Mock
  fallbacks normalized to the full NEW shot contract. UNIT clients can be
  injected to exercise the complete happy/failure chains; per-shot failure is
  isolated (no whole-batch regeneration, max 2 repairs per shot, paid budget 0).

UNIT fakes in tests/test_commodity_ai.py prove behavior only; they are never real
model calls or production assets. Known live blockers (2026-10-11 evidence): Claude
exact-model OAuth expired; Clef port 8088 refused; VBench scoring not_run (lightweight
weights cached; Human_Anatomy blocked on mmcv, Human_Identity blocked on retinaface).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = 'claude-opus-5-5'
MANIFEST_SCHEMA_VERSION = 'commodity-material-manifest/v2'
ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
MEDIA_SUFFIXES = {'.mp4', '.mov', '.webm', '.mkv', '.avi', '.m4v'}
FFPROBE_TIMEOUT_S = 30.0
_CLEF_MAX_RESPONSE = 262144
_CLEF_HEALTH_TTL_S = 30.0
# only these roles are model-invocable; 'manifest' is a contract, never a prompt
_MODEL_ROLES = ('director', 'writer', 'reviewer')
# bounded constructor argument ranges: no zero/negative/non-finite/unbounded values
_TIMEOUT_BOUNDS = (0.05, 3600.0)
_OUTPUT_BYTES_BOUNDS = (1024, 33554432)
_DRIVE_RELATIVE_RE = re.compile(r'^[A-Za-z]:[^\\/]')

# Official OAuth path only: any override channel in the ambient environment is refused
# (presence recorded as variable NAMES, values are never read, echoed or logged).
_AUTH_OVERRIDE_EXACT = {
    'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
    'ANTHROPIC_CUSTOM_HEADERS', 'ANTHROPIC_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL',
    'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX',
}
_AUTH_OVERRIDE_PREFIXES = ('ANTHROPIC_', 'CLAUDE_CODE_')

_CLAUDE_CANDIDATES = [
    Path.home() / '.local' / 'bin' / 'claude.exe',
    Path.home() / '.local' / 'bin' / 'claude.cmd',
]


class AIError(RuntimeError):
    """Fail-closed refusal; the message never contains credentials."""

    def __init__(self, code, message=None):
        super().__init__('%s: %s' % (code, message) if message else code)
        self.code = code


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _utc():
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def _canonical_hash(value):
    return hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _stream_snippet(stdout, stderr):
    return {'stdout_tail': (stdout or b'')[-1000:].decode('utf-8', 'replace'),
            'stderr_tail': (stderr or b'')[-1000:].decode('utf-8', 'replace')}


def _auth_override_names():
    return sorted(k for k in os.environ
                  if k in _AUTH_OVERRIDE_EXACT or k.startswith(_AUTH_OVERRIDE_PREFIXES))


def _scrubbed_env():
    return {k: v for k, v in os.environ.items()
            if not (k in _AUTH_OVERRIDE_EXACT or k.startswith(_AUTH_OVERRIDE_PREFIXES))}


def _sanitize_refs(refs):
    """Refs travel as text identifiers only; external paths never leave the process."""
    if not refs:
        return []
    out = []
    for ref in refs:
        if isinstance(ref, dict) and isinstance(ref.get('ref_id'), str):
            item = {'ref_id': ref['ref_id'],
                    'role': ref.get('role') if isinstance(ref.get('role'), str) else None}
            if isinstance(ref.get('sha256'), str):
                item['sha256'] = ref['sha256']
            out.append(item)
    return out


def _reject_nonfinite(value):
    """Raise ValueError when NaN/Infinity appears anywhere (strict JSON rule used
    by _canonical_json; applied to inputs BEFORE schema/cache and to outputs
    BEFORE caching so serialization can never blow up uncaught)."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('non-finite float value')
    if isinstance(value, bool) or value is None or isinstance(value, (str, int, float)):
        return
    if isinstance(value, dict):
        for item in value.values():
            _reject_nonfinite(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_nonfinite(item)
        return
    raise ValueError('value is not JSON-serializable: %s' % type(value).__name__)


def _reject_json_constant(name):
    raise ValueError('non-finite JSON constant: %s' % name)


def _strict_json_loads(text):
    """json.loads, but NaN/Infinity/-Infinity literals are rejected (the stdlib
    parser accepts them by default, which would smuggle non-compliant floats
    into schema validation and later serialization)."""
    return json.loads(text, parse_constant=_reject_json_constant)


def _is_inside(candidate, root):
    left = os.path.normcase(str(candidate))
    right = os.path.normcase(str(root))
    return left == right or left.startswith(right.rstrip('\\/') + os.sep)


def _guard_path_inside(raw, root, code):
    """Lexical boundary guard, evaluated BEFORE any resolve()/stat()/open():
    rejects UNC/device, drive-relative, parent-traversal and external literals
    so hostile paths are refused without a single filesystem metadata access."""
    text = str(raw)
    if text.startswith('\\\\') or text.startswith('\\\\.\\') or text.startswith('\\??\\'):
        raise AIError(code, 'UNC/device path literal refused before filesystem access')
    if _DRIVE_RELATIVE_RE.match(text):
        raise AIError(code, 'drive-relative path refused before filesystem access')
    candidate = os.path.normcase(os.path.abspath(text))
    base = os.path.normcase(os.path.abspath(str(root)))
    try:
        rel = os.path.relpath(candidate, base)
    except ValueError:  # different drives on Windows
        raise AIError(code, 'path on a different drive refused before filesystem access')
    if rel == os.pardir or rel.startswith(os.pardir + os.sep) or os.path.isabs(rel):
        raise AIError(code, 'path outside project root refused before filesystem access')
    return Path(candidate)


def _reject_link_escape(root, target, code):
    """Refuse when the real (link-resolved) location of target escapes root —
    catches symlink/junction chains under runtime/db/isolation ancestors
    BEFORE mkdir or sqlite open. realpath() never creates anything, so this
    stays a pre-flight check."""
    root_real = os.path.realpath(str(root))
    target_real = os.path.realpath(str(target))
    if not _is_inside(target_real, root_real):
        raise AIError(code, 'symlink/junction chain escapes the project root')


def _validate_root_argument(root):
    """Constructor root guard: absolute, lexically inside the canonical module
    ROOT, and link-resolution stays inside it too."""
    raw = Path(root)
    if not raw.is_absolute():
        raise AIError('ROOT_MUST_BE_ABSOLUTE')
    _guard_path_inside(raw, ROOT, 'ROOT_OUTSIDE_PROJECT')
    resolved = raw.resolve()
    _reject_link_escape(ROOT, resolved, 'ROOT_OUTSIDE_PROJECT')
    return resolved


def _validate_bounded_number(value, bounds, code):
    low, high = bounds
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)) or not (low <= float(value) <= high):
        raise AIError(code, 'value must be a finite number within [%s, %s]' % bounds)
    return float(value)


# ---------------------------------------------------------------------------
# per-project single-flight across instances AND CLI processes. The byte-range
# lock is owned by the OS on behalf of the acquiring process: when that process
# exits (even killed), the OS releases it, so stale lock state can never
# permanently block the project. release() always runs in finally, including
# after timeout/overflow kills.
# ---------------------------------------------------------------------------

if sys.platform == 'win32':
    import msvcrt

    def _lock_region(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock_region(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_region(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_region(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)


class _ProjectFlight:
    def __init__(self, lock_path):
        self._mutex = threading.Lock()
        self._path = Path(lock_path)
        self._fd = None

    def acquire(self):
        if not self._mutex.acquire(blocking=False):
            return False
        fd = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o600)
            _lock_region(fd)
        except OSError:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._mutex.release()
            return False
        self._fd = fd
        return True

    def release(self):
        fd, self._fd = self._fd, None
        try:
            if fd is not None:
                _unlock_region(fd)
        except OSError:
            pass
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._mutex.release()


_FLIGHT_REGISTRY = {}
_FLIGHT_REGISTRY_GUARD = threading.Lock()


def _project_flight(root, name):
    key = (os.path.normcase(os.path.abspath(str(root))), str(name))
    with _FLIGHT_REGISTRY_GUARD:
        flight = _FLIGHT_REGISTRY.get(key)
        if flight is None:
            flight = _ProjectFlight(Path(root) / 'runtime' / 'commodity-material-ai-v1'
                                    / (str(name) + '.lock'))
            _FLIGHT_REGISTRY[key] = flight
        return flight


# ---------------------------------------------------------------------------
# embedded strict JSON schemas — authoritative field sets from the user's original
# request (§5 shot standard, §4 writer role, §9 reviewer record, §11 manifest)
# ---------------------------------------------------------------------------

_REF_ITEM_DEF = {'type': 'object', 'additionalProperties': False,
                 'required': ['ref_id', 'role'],
                 'properties': {'ref_id': {'type': 'string'},
                                'role': {'type': 'string'},
                                'sha256': {'type': 'string'}}}

_ISSUE_DEF = {'type': 'object', 'additionalProperties': False,
              'required': ['code', 'evidence'],
              'properties': {'code': {'type': 'string'}, 'evidence': {'type': 'string'}}}

# §5 商品素材分镜标准 — the independent new shot contract (legacy KOC fields stay
# accepted by the audit store but can never substitute for this generation contract)
SHOT_SPEC_DEF = {
    'type': 'object', 'additionalProperties': False,
    'title': 'commodity material shot spec (user §5 full field list)',
    'required': ['material_id', 'shot_id', 'product_id', 'source_refs',
                 'narration_target', 'visual_purpose', 'scene', 'character_refs',
                 'product_refs', 'action_start', 'action_process', 'action_end',
                 'camera_and_framing', 'duration_sec', 'required_visuals',
                 'forbidden_visuals', 'audio_policy', 'generation_prompt'],
    'properties': {
        'material_id': {'type': 'string', 'pattern': ID_RE.pattern},
        'shot_id': {'type': 'string', 'pattern': ID_RE.pattern},
        'product_id': {'type': 'string', 'minLength': 1},
        'source_refs': {'type': 'array', 'items': _REF_ITEM_DEF},
        'narration_target': {'type': 'string'},
        'visual_purpose': {'type': 'string', 'minLength': 1},
        'scene': {'type': 'string'},
        'character_refs': {'type': 'array', 'items': _REF_ITEM_DEF},
        'product_refs': {'type': 'array', 'items': _REF_ITEM_DEF},
        'action_start': {'type': ['string', 'null']},
        'action_process': {'type': 'array', 'items': {'type': 'string'}},
        'action_end': {'type': ['string', 'null']},
        'camera_and_framing': {'type': 'string', 'minLength': 1},
        'duration_sec': {'type': ['number', 'null']},
        'required_visuals': {'type': 'array', 'items': {'type': 'string'}},
        'forbidden_visuals': {'type': 'array', 'items': {'type': 'string'}},
        'audio_policy': {'type': 'string', 'minLength': 1},
        'generation_prompt': {'type': ['string', 'null']},
    },
}

_WRITER_SPEC_DEF = {
    'type': 'object', 'additionalProperties': False,
    'title': 'writer H3-executable shot spec (director intent converted)',
    'required': ['role', 'material_id', 'shot_id', 'product_id', 'source_refs',
                 'narration_target', 'visual_purpose', 'scene', 'character_refs',
                 'product_refs', 'action_start', 'action_process', 'action_end',
                 'camera_and_framing', 'duration_sec', 'required_visuals',
                 'forbidden_visuals', 'audio_policy', 'generation_prompt',
                 'reference_requirements', 'notes'],
    'properties': {
        'role': {'const': 'writer'},
        'material_id': {'type': 'string', 'pattern': ID_RE.pattern},
        'shot_id': {'type': 'string', 'pattern': ID_RE.pattern},
        'product_id': {'type': 'string', 'minLength': 1},
        'source_refs': {'type': 'array', 'items': _REF_ITEM_DEF},
        'narration_target': {'type': 'string'},
        'visual_purpose': {'type': 'string', 'minLength': 1},
        'scene': {'type': 'string'},
        'character_refs': {'type': 'array', 'items': _REF_ITEM_DEF},
        'product_refs': {'type': 'array', 'items': _REF_ITEM_DEF},
        'action_start': {'type': ['string', 'null']},
        'action_process': {'type': 'array', 'items': {'type': 'string'}},
        'action_end': {'type': ['string', 'null']},
        'camera_and_framing': {'type': 'string', 'minLength': 1},
        'duration_sec': {'type': ['number', 'null']},
        'required_visuals': {'type': 'array', 'items': {'type': 'string'}},
        'forbidden_visuals': {'type': 'array', 'items': {'type': 'string'}},
        'audio_policy': {'type': 'string', 'minLength': 1},
        'generation_prompt': {'type': 'string', 'minLength': 1},
        'reference_requirements': {'type': 'array', 'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['ref_id', 'requirement'],
            'properties': {'ref_id': {'type': 'string'},
                           'requirement': {'type': 'string'}}}},
        'notes': {'type': ['string', 'null']},
    },
}

_REVIEW_RECORD_DEF = {
    'type': 'object', 'additionalProperties': False,
    'title': 'stored review record (claude/clef/unit, append-only)',
    'required': ['review_id', 'take_id', 'mode', 'created_at'],
    'properties': {
        'review_id': {'type': 'string'}, 'take_id': {'type': 'string'},
        'mode': {'enum': ['dry_run', 'shadow_review']}, 'created_at': {'type': 'string'},
        'source': {'type': 'string'}, 'status': {'type': 'string'},
        'error_code': {'type': ['string', 'null']}, 'error': {'type': ['string', 'null']},
        'phase': {'type': ['string', 'null']}, 'shot_id': {'type': ['string', 'null']},
        'take_id_out': {'type': ['string', 'null']},
        'verdict': {'type': ['string', 'null']}, 'engine': {'type': ['string', 'null']},
        'review_status': {'type': ['string', 'null']},
        'input_scope': {'type': ['string', 'null']},
        'input_modality': {'type': ['string', 'null']},
        'questions_digest': {'type': ['string', 'null']},
        'mock': {'type': ['boolean', 'null']}, 'llm_invoked': {'type': ['boolean', 'null']},
        'problems': {'type': 'array', 'items': {}},
        'evidence': {'type': 'array', 'items': {}},
        'probabilities': {}, 'capability_proof': {}, 'raw_result': {},
        'structured_output': {}, 'visual_status': {'type': ['string', 'null']},
        'continuous_motion_status': {'type': ['string', 'null']},
        'review_record_id': {'type': ['string', 'null']},
        'cached': {'type': ['boolean', 'null']},
        'model': {'type': ['string', 'null']}, 'model_id': {'type': ['string', 'null']},
        'call_id': {'type': ['string', 'null']},
        'duration_sec': {'type': ['number', 'null']},
        'latency_ms': {'type': ['integer', 'null']},
        # reviewer §9 decision fields may be carried directly by stored records
        'confirmed_issues': {'type': 'array', 'items': {}},
        'unverified_issues': {'type': 'array', 'items': {}},
        'repair_action': {'type': ['string', 'null']},
        'prompt_revision': {'type': ['string', 'null']},
        'reference_changes': {'type': 'array', 'items': {}},
        'regenerate_required': {'type': ['boolean', 'null']},
        # store/adapter stamps and honest capability flags
        'binding_stamp': {
            'type': 'object', 'additionalProperties': False,
            'required': ['take_id', 'shot_id', 'shot_sha256', 'video_sha256',
                         'stamped_at'],
            'properties': {'take_id': {'type': 'string'},
                           'shot_id': {'type': ['string', 'null']},
                           'shot_sha256': {'type': ['string', 'null']},
                           'video_sha256': {'type': ['string', 'null']},
                           'stamped_at': {'type': 'string'}}},
        'refs_status': {'type': 'array', 'items': {}},
        'input_bounds': {
            'type': 'object', 'additionalProperties': False,
            'properties': {'state_sha256': {'type': 'string'},
                           'questions_sha256': {'type': 'string'},
                           'input_scope': {'type': 'string'},
                           'policy': {'type': 'string'}}},
        'lifecycle_status': {'type': ['string', 'null']},
        'cuda_execution_status': {'type': ['string', 'null']},
        'joint_head_status': {'type': ['string', 'null']},
        'known_blocked': {'type': ['boolean', 'null']},
        'approval_changed': {'type': ['boolean', 'null']},
        'returncode': {'type': ['integer', 'null']},
        'schema_error': {'type': ['string', 'null']},
        'auth_overrides_present': {'type': 'array', 'items': {'type': 'string'}},
        'retries': {'type': ['integer', 'null']}, 'role': {'type': ['string', 'null']},
        'run_id': {'type': ['string', 'null']}, 'stage': {'type': ['string', 'null']},
        'provenance': {'type': ['string', 'null']},
        'reason': {'type': ['string', 'null']}, 'note': {'type': ['string', 'null']},
    },
}

_USAGE_RECORD_DEF = {
    'type': 'object', 'additionalProperties': False,
    'required': ['usage_id', 'take_id', 'target_shot_id', 'purpose', 'created_at'],
    'properties': {'usage_id': {'type': 'string'}, 'take_id': {'type': 'string'},
                   'target_shot_id': {'type': 'string'}, 'purpose': {'type': 'string'},
                   'created_at': {'type': 'string'}},
}

_RANGE_DEF = {'type': 'object', 'additionalProperties': False,
              'required': ['start_sec', 'end_sec'],
              'properties': {'start_sec': {'type': 'number', 'minimum': 0},
                             'end_sec': {'type': 'number', 'minimum': 0}}}

_HUMAN_DEF = {
    'type': 'object', 'additionalProperties': False,
    'required': ['confirmed_by', 'visual_scope', 'temporal_scope', 'confirmed_at'],
    'properties': {'confirmed_by': {'type': 'string', 'minLength': 1},
                   'visual_scope': {'type': 'string', 'minLength': 1},
                   'temporal_scope': {'type': 'string', 'minLength': 1},
                   'confirmed_at': {'type': 'string', 'minLength': 1},
                   # pre-confirmed exact-context binding token (get_approval_context)
                   'binding_token': {'type': 'string'},
                   # human-confirmed ranges, must equal evidence.usable_ranges
                   'confirmed_ranges': {'type': 'array', 'items': _RANGE_DEF},
                   # explicit human resolution for NEEDS_REVIEW evidence
                   'review_resolution': {'type': 'string'},
                   # hash-bearing evidence set the human actually checked
                   'resolution_evidence': {'type': 'array', 'items': {}}},
}

_EVIDENCE_DEF = {
    'type': 'object', 'additionalProperties': False,
    'required': ['usable_ranges', 'refs'],
    'properties': {'usable_ranges': {'type': 'array', 'minItems': 1, 'items': _RANGE_DEF},
                   'refs': {'type': 'array', 'items': {}},
                   'refs_none_required': {'type': 'boolean'},
                   'refs_none_required_reason': {'type': 'string'}},
}

_MATERIAL_DEF = {
    'type': 'object', 'additionalProperties': False,
    'required': ['visual_description', 'actions', 'narration_targets', 'tags',
                 'audio_policy', 'scene'],
    'properties': {'visual_description': {'type': 'string', 'minLength': 1},
                   'actions': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
                   'narration_targets': {'type': 'array', 'items': {'type': 'string'}},
                   'tags': {'type': 'array', 'items': {'type': 'string'}},
                   'audio_policy': {'type': 'string', 'minLength': 1},
                   'scene': {'type': 'string'}},
}

_BINDING_DEF = {
    'type': 'object', 'additionalProperties': False,
    'required': ['take_sha256', 'review_sha256', 'video_sha256'],
    'properties': {'take_sha256': {'type': 'string', 'pattern': SHA_RE.pattern},
                   'review_sha256': {'type': 'array',
                                     'items': {'type': 'string', 'pattern': SHA_RE.pattern}},
                   'video_sha256': {'type': ['string', 'null']},
                   'media_sha256': {'type': 'string'},
                   'refs_sha256': {'type': 'string'},
                   'evidence_sha256': {'type': 'string'},
                   'shot_id': {'type': 'string'},
                   'evidence_review_ids': {'type': 'array',
                                           'items': {'type': ['string', 'null']}}},
}

SCHEMAS = {
    'director': {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'commodity-material-ai-v1 director structured output',
        'type': 'object', 'additionalProperties': False,
        'required': ['role', 'case_id', 'shots', 'concerns'],
        'properties': {
            'role': {'const': 'director'},
            'case_id': {'type': 'string', 'minLength': 1},
            'shots': {'type': 'array', 'minItems': 1, 'items': SHOT_SPEC_DEF},
            'concerns': {'type': 'array', 'items': {'type': 'string'}},
        },
    },
    'writer': {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'commodity-material-ai-v1 writer H3-executable spec',
        'type': 'object', 'additionalProperties': False,
        'required': list(_WRITER_SPEC_DEF['required']),
        'properties': _WRITER_SPEC_DEF['properties'],
    },
    'reviewer': {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'commodity-material-ai-v1 reviewer decision record',
        'type': 'object', 'additionalProperties': False,
        'required': ['role', 'shot_id', 'take_id', 'review_status',
                     'confirmed_issues', 'unverified_issues', 'evidence',
                     'repair_action', 'prompt_revision', 'reference_changes',
                     'regenerate_required'],
        'properties': {
            'role': {'const': 'reviewer'},
            'shot_id': {'type': 'string', 'pattern': ID_RE.pattern},
            'take_id': {'type': ['string', 'null']},
            'review_status': {'enum': ['PASS', 'RETURN', 'NEEDS_REVIEW',
                                       'TECHNICAL_ERROR']},
            'confirmed_issues': {'type': 'array', 'items': _ISSUE_DEF},
            'unverified_issues': {'type': 'array', 'items': _ISSUE_DEF},
            'evidence': {'type': 'array', 'items': {'type': 'string'}},
            'repair_action': {'type': ['string', 'null']},
            'prompt_revision': {'type': ['string', 'null']},
            'reference_changes': {'type': 'array', 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['ref_id', 'change'],
                'properties': {'ref_id': {'type': 'string'},
                               'change': {'type': 'string'}}}},
            'regenerate_required': {'type': 'boolean'},
        },
    },
    'manifest': {
        '$schema': 'http://json-schema.org/draft-07/schema#',
        'title': 'commodity-material-ai-v1 approved material manifest',
        'type': 'object', 'additionalProperties': False,
        'required': ['schema_version', 'manifest_type', 'exported_at', 'asset_id',
                     'shot_id', 'product_id', 'version', 'video_path', 'file_hash',
                     'duration_sec', 'usable_ranges', 'visual_description', 'actions',
                     'scene', 'narration_targets', 'tags', 'audio_policy',
                     'review_status', 'review_record_id', 'batch_id', 'provenance',
                     'mock', 'reviews', 'usage', 'approval', 'binding', 'h3_invoked',
                     'paid_h3_calls', 'vbench'],
        'properties': {
            'schema_version': {'const': MANIFEST_SCHEMA_VERSION},
            'manifest_type': {'const': 'approved_material'},
            'exported_at': {'type': 'string'},
            'asset_id': {'type': 'string', 'pattern': ID_RE.pattern},
            'shot_id': {'type': 'string', 'pattern': ID_RE.pattern},
            'product_id': {'type': 'string'},
            'version': {'type': 'integer', 'minimum': 1},
            'video_path': {'type': 'string'},
            'file_hash': {'type': 'string', 'pattern': SHA_RE.pattern},
            'duration_sec': {'type': 'number'},
            'usable_ranges': {'type': 'array', 'minItems': 1, 'items': _RANGE_DEF},
            'visual_description': {'type': 'string', 'minLength': 1},
            'actions': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}},
            'scene': {'type': 'string'},
            'narration_targets': {'type': 'array', 'items': {'type': 'string'}},
            'tags': {'type': 'array', 'items': {'type': 'string'}},
            'audio_policy': {'type': 'string', 'minLength': 1},
            'review_status': {'const': 'APPROVED'},
            'review_record_id': {'type': 'string', 'pattern': ID_RE.pattern},
            'batch_id': {'type': 'string'},
            'provenance': {'type': 'string'},
            'mock': {'const': False},
            'reviews': {'type': 'array', 'items': _REVIEW_RECORD_DEF},
            'usage': {'type': 'array', 'items': _USAGE_RECORD_DEF},
            'approval': {
                'type': 'object', 'additionalProperties': False,
                'required': ['approval_id', 'take_id', 'approved_at', 'approval_sha256',
                             'human', 'evidence', 'material', 'binding'],
                'properties': {
                    'approval_id': {'type': 'string'}, 'take_id': {'type': 'string'},
                    'approved_at': {'type': 'string'},
                    'approval_sha256': {'type': 'string', 'pattern': SHA_RE.pattern},
                    'human': _HUMAN_DEF, 'evidence': _EVIDENCE_DEF,
                    'material': _MATERIAL_DEF, 'binding': _BINDING_DEF,
                    'contract': {
                        'type': 'object', 'additionalProperties': False,
                        'properties': {
                            'manifest_schema': {'type': 'string'},
                            'review_contract': {'type': 'string'},
                            'store_policy': {'type': 'string'}}},
                },
            },
            'binding': _BINDING_DEF,
            'h3_invoked': {'const': False},
            'paid_h3_calls': {'const': 0},
            'vbench': {
                'type': 'object', 'additionalProperties': False,
                'required': ['status', 'note'],
                'properties': {'status': {'const': 'not_run'},
                               'note': {'type': 'string'}},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# bounded real process execution (no shell, capped stream reads, hard deadline,
# prompt termination on overflow, no orphaned output threads)
# ---------------------------------------------------------------------------

def _bounded_spawn_run(argv, stdin_bytes, cwd, env, timeout_sec, max_output_bytes):
    """Spawn argv (array, never a shell string), feed stdin, stream-read stdout/stderr
    with a byte cap, kill promptly when the cap is exceeded or the deadline hits, and
    always reap the process and reader threads."""
    cap = int(max_output_bytes) if max_output_bytes else 262144
    out_buf = bytearray()
    err_buf = bytearray()
    truncated = threading.Event()
    timed_out = False
    overflow_killed = False

    proc = subprocess.Popen(
        [str(a) for a in argv],
        stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=cwd if cwd else None, env=env if env is not None else None)

    def pump(pipe, buf):
        try:
            while True:
                chunk = pipe.read(65536)
                if not chunk:
                    break
                remaining = (cap + 1) - len(buf)
                if remaining <= 0:
                    truncated.set()
                    break
                buf.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated.set()
                    break
        except (OSError, ValueError):
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    def feed():
        try:
            if stdin_bytes is not None:
                proc.stdin.write(stdin_bytes)
        except (OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (OSError, ValueError, AttributeError):
                pass

    readers = [threading.Thread(target=pump, args=(stream, buf), daemon=True)
               for stream, buf in ((proc.stdout, out_buf), (proc.stderr, err_buf))]
    feeder = threading.Thread(target=feed, daemon=True)
    for thread in readers + [feeder]:
        thread.start()
    deadline = (time.monotonic() + float(timeout_sec)) if timeout_sec else None
    while True:
        try:
            proc.wait(timeout=0.05)
            break
        except subprocess.TimeoutExpired:
            if truncated.is_set():  # terminate promptly on stream overflow
                overflow_killed = True
                proc.kill()
                _wait_reap(proc)
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                proc.kill()
                _wait_reap(proc)
                break
    for thread in readers + [feeder]:
        thread.join(timeout=5)
    returncode = proc.returncode if isinstance(proc.returncode, int) else -1
    return {'returncode': returncode, 'stdout': bytes(out_buf), 'stderr': bytes(err_buf),
            'timed_out': timed_out, 'truncated': truncated.is_set(),
            'overflow_killed': overflow_killed}


def _wait_reap(proc):
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _resolve_executable(explicit=None):
    if explicit:
        return str(explicit)
    found = shutil.which('claude')
    if found:
        return found
    for candidate in _CLAUDE_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return None


_ROLE_PROMPTS = {
    'director': (
        '你是商品素材流水线的导演（Director）。任务：根据商品资料、人物形象与创作要求，'
        '设计每个独立素材镜头的实际目的、画面与动作。输出镜头必须使用用户标准字段'
        '（material_id/shot_id/product_id/source_refs/narration_target/visual_purpose/scene/'
        'character_refs/product_refs/action_start/action_process/action_end/'
        'camera_and_framing/duration_sec/required_visuals/forbidden_visuals/audio_policy/'
        'generation_prompt）。原则：动作有起点、过程和结束状态；人物身份与商品真实参考优先；'
        '商品包装、文字及外观不得编造；背景或产品文字易变形时优先保留真实商品图或交后期；'
        '不默认生成完整成片；不强制三段结构；时长遵守 H3 真实接口范围；证据不足写入 concerns '
        '如实说明，禁止虚构解释。只输出符合随附 JSON Schema 的 structured_output。'),
    'writer': (
        '你是商品素材流水线的写手（Writer）。任务：把导演意图转换为 H3 可执行的分镜规格：'
        '补全用户标准字段（与导演同一字段集）、给出每个参考的具体要求'
        '（reference_requirements）和最终 generation_prompt。规则：生成提示词只能使用'
        'PAYLOAD 与 REFS 标识中给出的真实信息；商品外观、包装、文字不得编造；音频策略必须'
        '明确；不虚构参考能力。证据不足时在 notes 如实说明。只输出符合随附 JSON Schema 的 '
        'structured_output。'),
    'reviewer': (
        '你是商品素材流水线的审核员（Reviewer）。任务：依据 Clef/VBench/参考资料与实际检查'
        '证据判断通过、人工复核、局部返修或技术错误。输出字段：shot_id/take_id/'
        'review_status/confirmed_issues/unverified_issues/evidence/repair_action/'
        'prompt_revision/reference_changes/regenerate_required。规则：confirmed_issues 只列'
        '有具体证据的问题；文本无法验证的视觉/时序事项必须放入 unverified_issues；没有足够'
        '证据时必须写明不能确认，不为给出解释而虚构故障原因；只能返修失败镜头（每镜头最多 '
        '2 次修复预算），不因单条素材问题重生成整批；禁止自动无限重试。只输出符合随附 JSON '
        'Schema 的 structured_output。'),
}


def _build_prompt(role, payload, refs):
    sections = [
        _ROLE_PROMPTS[role],
        'ROLE: ' + role,
        '固定模型合同: 仅 ' + MODEL_ID + '；不得更换模型、不得降级、不得转 API、不得重试认证。',
        '输出必须是严格符合以下 JSON Schema 的 structured_output（additionalProperties=false）:',
        _canonical_json(SCHEMAS[role]),
        'REFS（仅文字标识；禁止读取、访问或推断任何文件路径内容）: ' + _canonical_json(refs),
        'PAYLOAD（唯一事实来源；证据不足时如实说明）: ' + _canonical_json(payload),
        '直接输出 JSON，不要输出其它内容。',
    ]
    return '\n\n'.join(sections)


class ClaudeCLIAdapter:
    """Official Claude CLI client with fixed secure parameters (see module docstring)."""

    def __init__(self, root=ROOT, timeout_sec=60, max_output_bytes=262144,
                 executable=None, runner=None):
        self.root = _validate_root_argument(root)
        self.timeout_sec = _validate_bounded_number(timeout_sec, _TIMEOUT_BOUNDS,
                                                    'INVALID_TIMEOUT_SEC')
        if isinstance(max_output_bytes, bool) or not isinstance(max_output_bytes, int) \
                or not (_OUTPUT_BYTES_BOUNDS[0] <= max_output_bytes <= _OUTPUT_BYTES_BOUNDS[1]):
            raise AIError('INVALID_MAX_OUTPUT_BYTES',
                          'max_output_bytes must be an int within [%s, %s]'
                          % _OUTPUT_BYTES_BOUNDS)
        self.max_output_bytes = int(max_output_bytes)
        self.executable = executable
        self._runner = runner or _bounded_spawn_run
        self._cache = {}
        self._isolated_cwd = (self.root / 'runtime' / 'commodity-material-ai-v1'
                              / 'claude-isolated')

    # -- public API ---------------------------------------------------------

    def preflight(self):
        """Local-only readiness: executable resolution + --version + override scan.
        Never performs a model call and never revalidates auth (E-01)."""
        result = {'status': 'blocked', 'error_code': None, 'model_id': MODEL_ID,
                  'auth_overrides_present': [], 'executable': None, 'cli_version': None}
        overrides = _auth_override_names()
        if overrides:
            result.update(error_code='AUTH_OVERRIDE_PRESENT', auth_overrides_present=overrides)
            return result
        executable = _resolve_executable(self.executable)
        if executable is None:
            result.update(error_code='CLI_NOT_FOUND')
            return result
        result['executable'] = executable
        try:
            _reject_link_escape(self.root, self._isolated_cwd, 'ISOLATED_CWD_UNAVAILABLE')
            self._isolated_cwd.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.update(error_code='ISOLATED_CWD_UNAVAILABLE', error=str(exc))
            return result
        record = self._runner([executable, '--version'], None, str(self._isolated_cwd),
                              _scrubbed_env(), min(self.timeout_sec, 15.0), 4096)
        if record.get('timed_out'):
            result.update(error_code='TIMEOUT')
            return result
        if record.get('returncode') != 0:
            result.update(error_code='CLI_VERSION_CHECK_FAILED',
                          error=_stream_snippet(record.get('stdout'), record.get('stderr')))
            return result
        lines = (record.get('stdout') or b'').decode('utf-8', 'replace').strip().splitlines()
        result.update(status='ready', error_code=None,
                      cli_version=lines[0] if lines else '')
        return result

    def invoke(self, role, payload, refs=None):
        """One bounded exact-model CLI invocation. Always returns a call record
        {status, error_code, structured_output, model_id, duration_sec, raw_result,
        call_id, cached, role, retries}; failures are honest and never retried here.
        A nonzero CLI exit code is NEVER success, even when the stream carries a
        parseable structured payload (independently reproduced defect 2026-10-11)."""
        call_id = 'call-' + uuid.uuid4().hex[:16]
        record = {'status': 'blocked', 'error_code': None, 'structured_output': None,
                  'model_id': MODEL_ID, 'duration_sec': 0.0, 'raw_result': None,
                  'call_id': call_id, 'cached': False, 'role': role, 'retries': 0}
        if role not in _MODEL_ROLES:
            raise AIError('ROLE_UNKNOWN',
                          'role must be one of ' + ', '.join(_MODEL_ROLES))
        try:
            resolved_refs, refs_status = self._resolve_refs(refs)
        except AIError as exc:
            record.update(error_code=exc.code, error=str(exc))
            return record
        record['refs_status'] = refs_status
        try:
            _reject_nonfinite(payload)
            _reject_nonfinite(resolved_refs)
            _canonical_json(payload)
        except (ValueError, TypeError):
            record.update(error_code='PAYLOAD_NOT_STRICT_JSON',
                          error='payload/refs must be strict JSON (no NaN/Infinity)')
            return record
        # auth overrides are checked BEFORE the cache: a fresh refusal can never be
        # masked by a previously cached verified result
        overrides = _auth_override_names()
        if overrides:
            record.update(error_code='AUTH_OVERRIDE_PRESENT', auth_overrides_present=overrides)
            return record
        cache_key = _canonical_hash({'adapter': 'claude-cli', 'role': role,
                                     'model': MODEL_ID, 'schema': SCHEMAS[role],
                                     'payload': payload, 'refs': resolved_refs})
        hit = self._cache.get(cache_key)
        if hit is not None:
            replayed = json.loads(_canonical_json(hit))
            replayed.update(cached=True)
            return replayed
        executable = _resolve_executable(self.executable)
        if executable is None:
            record.update(error_code='CLI_NOT_FOUND')
            return record
        # canonical link validation BEFORE the single-flight lock (whose parent
        # dirs it may create) and before any isolated-cwd write: a junction under
        # runtime/ must never be followed out of the project
        try:
            _reject_link_escape(self.root,
                                self.root / 'runtime' / 'commodity-material-ai-v1',
                                'ISOLATED_CWD_UNAVAILABLE')
            _reject_link_escape(self.root, self._isolated_cwd, 'ISOLATED_CWD_UNAVAILABLE')
        except AIError:
            record.update(error_code='ISOLATED_CWD_UNAVAILABLE',
                          error='link/junction chain escapes the project root '
                                '(rejected before lock/cwd creation)')
            return record
        flight = _project_flight(self.root, 'claude')
        if not flight.acquire():
            record.update(error_code='CONCURRENCY_BLOCKED',
                          error='another Claude invocation holds the per-project '
                                'single-flight lock (in-process or cross-process)')
            return record
        started = time.monotonic()
        try:
            try:
                self._isolated_cwd.mkdir(parents=True, exist_ok=True)
                run = self._runner(self._argv(executable, role),
                                   _build_prompt(role, payload, resolved_refs).encode('utf-8'),
                                   str(self._isolated_cwd), _scrubbed_env(),
                                   self.timeout_sec, self.max_output_bytes)
            except (OSError, ValueError, RuntimeError) as exc:
                # boundary failures of the runner itself (spawn OSError etc.) are
                # honest blocked records; only the exception TYPE is recorded so a
                # secret-bearing message value can never leak into the record
                record.update(error_code='RUNNER_ERROR',
                              error='runner raised %s' % type(exc).__name__)
                return record
        finally:
            flight.release()  # always released: success, error, timeout or overflow
        record['duration_sec'] = round(time.monotonic() - started, 3)
        stdout = run.get('stdout') or b''
        stderr = run.get('stderr') or b''
        snippet = _stream_snippet(stdout, stderr)
        if run.get('timed_out'):
            record.update(error_code='TIMEOUT', raw_result=snippet)
            return record
        if run.get('truncated') or len(stdout) > self.max_output_bytes:
            record.update(error_code='OUTPUT_LIMIT_EXCEEDED', raw_result=snippet)
            return record
        text = stdout.decode('utf-8', 'replace').strip()
        parsed = None
        if text:
            try:
                parsed = json.loads(text)  # accepts NaN literals; checked below
            except ValueError:
                parsed = None
        if run.get('returncode') != 0:
            # nonzero exit is always blocked; the parsed stream (kept as the raw
            # record) may still map AUTH_EXPIRED/MODEL_ERROR precisely
            record.update(returncode=run.get('returncode'), raw_result=parsed or snippet)
            if isinstance(parsed, dict) and parsed.get('is_error'):
                lowered = str(parsed.get('result') or '').lower()
                if 'oauth' in lowered and 'expired' in lowered:
                    record.update(error_code='AUTH_EXPIRED')
                    return record
                record.update(error_code='MODEL_ERROR')
                return record
            record.update(error_code='CLI_NONZERO_EXIT',
                          error='claude CLI exited with returncode %s'
                                % run.get('returncode'))
            return record
        if not isinstance(parsed, dict):
            record.update(error_code='UNPARSEABLE_OUTPUT', raw_result=snippet)
            return record
        try:
            _reject_nonfinite(parsed)
        except ValueError:
            record.update(error_code='NONFINITE_OUTPUT', raw_result=snippet)
            return record
        record['raw_result'] = parsed
        model = parsed.get('model')
        if isinstance(model, str) and model != MODEL_ID:
            record.update(error_code='MODEL_MISMATCH')
            return record
        # the official modelUsage block is runtime accounting, not self-report:
        # a different model there is a mismatch even if the JSON label looks right
        usage = parsed.get('modelUsage')
        if isinstance(usage, dict) and isinstance(usage.get('model'), str) \
                and usage['model'] != MODEL_ID:
            record.update(error_code='MODEL_MISMATCH')
            return record
        if parsed.get('is_error'):
            result_text = str(parsed.get('result') or '')
            lowered = result_text.lower()
            if 'oauth' in lowered and 'expired' in lowered:
                record.update(error_code='AUTH_EXPIRED')
            else:
                record.update(error_code='MODEL_ERROR')
            return record
        if 'structured_output' not in parsed or parsed.get('structured_output') is None:
            record.update(error_code='STRUCTURED_OUTPUT_MISSING')
            return record
        structured = parsed.get('structured_output')
        try:
            _reject_nonfinite(structured)
            jsonschema.Draft7Validator(SCHEMAS[role]).validate(structured)
        except ValueError:
            record.update(error_code='NONFINITE_OUTPUT')
            return record
        except jsonschema.ValidationError as exc:
            record.update(error_code='SCHEMA_INVALID', schema_error=str(exc.message)[:300])
            return record
        record.update(status='verified', error_code=None, structured_output=structured)
        # only verified results are cacheable; technical errors never enter the cache
        self._cache[cache_key] = json.loads(_canonical_json(record))
        return record

    # -- internals ----------------------------------------------------------

    def _resolve_refs(self, refs):
        """Sanitize refs to text identifiers with honest per-ref status. Supplied
        SHA formats are validated. Explicitly supplied paths get the lexical
        boundary guard BEFORE any resolve/open (external paths fail closed and
        are never read); project-local refs are re-digested FRESH so a changed
        file content can never be served from a stale verified cache hit."""
        sanitized = []
        status = []
        for ref in refs or []:
            if not (isinstance(ref, dict) and isinstance(ref.get('ref_id'), str)):
                continue
            item = {'ref_id': ref['ref_id'],
                    'role': ref.get('role') if isinstance(ref.get('role'), str) else None}
            st = {'ref_id': ref['ref_id'], 'available': None,
                  'digest_source': 'none', 'status': 'UNVERIFIED_NO_DIGEST'}
            sha = ref.get('sha256')
            if sha is not None and (not isinstance(sha, str) or not SHA_RE.fullmatch(sha)):
                raise AIError('REF_SHA_FORMAT_INVALID',
                              'ref %s: sha256 must be 64 lowercase hex characters'
                              % ref['ref_id'])
            path = ref.get('path')
            if isinstance(path, str) and path:
                guarded = _guard_path_inside(path, self.root, 'REF_PATH_OUTSIDE_PROJECT')
                resolved = guarded.resolve()
                if not _is_inside(resolved, self.root):
                    raise AIError('REF_PATH_OUTSIDE_PROJECT',
                                  'ref %s resolves outside the project root' % ref['ref_id'])
                if not resolved.is_file():
                    st.update(status='UNVERIFIED_MISSING', available=False)
                else:
                    item['sha256'] = _sha256_file(resolved)
                    st.update(status='VERIFIED_FRESH_DIGEST', available=True,
                              digest_source='computed_fresh')
            elif sha is not None:
                item['sha256'] = sha
                st.update(status='SUPPLIED_SHA_CONTENT_UNVERIFIED', digest_source='supplied')
            sanitized.append(item)
            status.append(st)
        return sanitized, status

    def _argv(self, executable, role):
        schema_json = _canonical_json(SCHEMAS[role])
        return [executable, '-p',
                '--model', MODEL_ID,
                '--output-format', 'json',
                '--permission-mode', 'dontAsk',
                '--permission-prompts', 'none',
                '--tools', '',
                '--disallowedTools', 'mcp__*',
                '--max-turns', '1',
                '--json-schema', schema_json,
                '--restricted',
                '--strict-mcp-config',
                '--mcp-config', '{"mcpServers":{}}',
                '--no-session-persistence']


# ---------------------------------------------------------------------------
# Clef — REAL local service protocol: GET /health + POST /v1/systemone
# (reference: F:/ai_services/clef_flash/scripts/adapter.py, read-only)
# ---------------------------------------------------------------------------

SYSTEMONE_QUESTIONS = {
    'clear': {'type': 'noul', 'instructions': '镜头动作链是否明确（start-process-end 完整）',
              'criteria': {'true': '动作链明确', 'false': '动作链不明确'}},
    'route': {'type': 'choice', 'instructions': '审核处置选择',
              'criteria': {'pass': '镜头要素完整可放行', 'return': '存在需退回修复的问题'}},
    'quality': {'type': 'score', 'instructions': '信息传达清晰度',
                'criteria': ['low', 'medium', 'high']},
}

# Distinct A/B question sets reflecting the user's specific checks per phase: A
# inspects the pre-generation spec, B inspects the generated result against the
# technical evidence actually supplied (text-only service: visuals stay with humans).
_PHASE_QUESTIONS = {
    'A': {
        'clear': {'type': 'noul',
                  'instructions': '生成前检查：分镜动作链是否明确（start-process-end 完整、无缺环）',
                  'criteria': {'true': '动作链明确可进入生成', 'false': '动作链不明确需先修正规格'}},
        'route': {'type': 'choice',
                  'instructions': '生成前处置：分镜规格是否完整可进入 H3 生成',
                  'criteria': {'pass': '规格完整可进入生成', 'return': '规格缺陷需退回修复'}},
        'quality': {'type': 'score', 'instructions': '生成前规格信息传达清晰度',
                    'criteria': ['low', 'medium', 'high']},
    },
    'B': {
        'clear': {'type': 'noul',
                  'instructions': '生成后检查：素材动作链与时序是否明确可判（基于文本与技术元数据）',
                  'criteria': {'true': '动作链明确', 'false': '动作链不明确'}},
        'route': {'type': 'choice',
                  'instructions': '生成后处置：文本与技术元数据是否存在阻断问题（画面本体仍需人工核验）',
                  'criteria': {'pass': '文本与技术证据无阻断问题', 'return': '存在需退回修复的问题'}},
        'quality': {'type': 'score', 'instructions': '生成后信息传达清晰度（仅文本证据范围）',
                    'criteria': ['low', 'medium', 'high']},
    },
}


def _validate_loopback_endpoint(endpoint):
    """Strict endpoint contract via urlsplit: exact loopback host, no userinfo,
    valid port, no query/fragment, empty-or-root path. Regex prefix matching is
    NOT acceptable ('http://127.0.0.1:8088@evil.invalid' must be refused) and
    validation happens in the constructor so invalid endpoints do zero network."""
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise AIError('ENDPOINT_NOT_LOOPBACK', 'endpoint must be a loopback http(s) URL')
    parts = urllib.parse.urlsplit(endpoint.strip())
    if parts.scheme not in ('http', 'https'):
        raise AIError('ENDPOINT_NOT_LOOPBACK', 'scheme must be http or https')
    if '@' in (parts.netloc or ''):
        raise AIError('ENDPOINT_USERINFO_REFUSED',
                      'userinfo in endpoint netloc is refused')
    host = (parts.hostname or '').lower()
    if host not in ('127.0.0.1', 'localhost'):
        raise AIError('ENDPOINT_NOT_LOOPBACK', 'host must be exactly 127.0.0.1 or localhost')
    try:
        port = parts.port
    except ValueError:
        raise AIError('ENDPOINT_INVALID_PORT', 'port out of range')
    if port is not None and not 1 <= port <= 65535:
        raise AIError('ENDPOINT_INVALID_PORT', 'port out of range')
    if parts.query or parts.fragment:
        raise AIError('ENDPOINT_QUERY_FRAGMENT_REFUSED',
                      'query/fragment in endpoint is refused')
    if parts.path not in ('', '/'):
        raise AIError('ENDPOINT_INVALID_PATH', 'only an empty or / path is allowed')
    canonical = '%s://%s' % (parts.scheme, host)
    if port is not None:
        canonical += ':%d' % port
    return canonical


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # any redirect surfaces as HTTPError and is refused


# no environment proxy, no redirects — loopback decision traffic only
_CLEF_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _http_json(url, payload, timeout_sec):
    """Bounded GET (payload None) / POST to the local Clef adapter; no proxy, no
    redirects. Returns {'http_status', 'body'}; transport errors raise."""
    data = _canonical_json(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/json'} if data else {},
        method='POST' if data is not None else 'GET')
    with _CLEF_OPENER.open(request, timeout=max(0.1, float(timeout_sec))) as response:
        body = response.read(_CLEF_MAX_RESPONSE + 1)
        if len(body) > _CLEF_MAX_RESPONSE:
            raise AIError('TECHNICAL_ERROR', 'RESPONSE_TOO_LARGE')
        return {'http_status': response.status, 'body': body.decode('utf-8', 'replace')}


def _probability(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AIError('PROBABILITY_NOT_NUMERIC')
    if not (math.isfinite(value) and 0.0 <= value <= 1.0):
        raise AIError('PROBABILITY_RANGE_INVALID')
    return float(value)


def _raw_noul_probabilities(raw):
    """Mirror the service's own extraction surface (adapter.normalize_answer): the
    native response can carry the true-probability in several keys. The service
    CLAMPS to [0,1], so EVERY numeric candidate in the raw answer must be
    re-validated — a valid normalized p_true can otherwise mask a broken raw head
    output."""
    found = []
    if isinstance(raw, dict):
        probs = raw.get('probabilities')
        if isinstance(probs, dict):
            for key in ('true', 'yes'):
                value = probs.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    found.append(float(value))
        for key in ('noul', 'p_true', 'true', 'probability', 'prob_true'):
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                found.append(float(value))
    return found


def _validate_health(data):
    """Fail-closed health check: real service identity, Clef-Flash Q4_K_M model,
    llama.cpp backend up, CUDA available. nvidia-smi GPU presence alone is NOT
    executed-inference proof — that only comes from a validated native decision."""
    if not isinstance(data, dict):
        raise AIError('TECHNICAL_ERROR', 'HEALTH_RESPONSE_NOT_OBJECT')
    if data.get('service') != 'clef-flash-systemone':
        raise AIError('SERVICE_IDENTITY_INVALID')
    model = data.get('model') or {}
    model_file = str(model.get('file') or '')
    if 'clef' not in model_file.lower():
        raise AIError('MODEL_NOT_CLEF')
    if model.get('quantization') != 'Q4_K_M':
        raise AIError('QUANTIZATION_MISMATCH')
    llama = data.get('llama_server') or {}
    if llama.get('status') != 'ok':
        raise AIError('LLAMA_BACKEND_DOWN')
    cuda = data.get('cuda') or {}
    if not cuda.get('available'):
        raise AIError('CUDA_UNAVAILABLE')
    return {'service': data.get('service'), 'service_version': data.get('service_version'),
            'backend': data.get('backend'), 'model': {'file': model.get('file'),
                                                      'quantization': model.get('quantization')},
            'cuda': cuda, 'llama_server': llama,
            'cuda_note': 'cuda.available reflects nvidia-smi GPU presence; execution '
                         'proof requires a validated native decision call'}


_EXPECTED_ANSWER_TYPES = {'clear': 'noul', 'route': 'choice', 'quality': 'score'}
_RAW_TOLERANCE = 0.05


def _raw_probability_map(raw):
    if isinstance(raw, dict) and isinstance(raw.get('probabilities'), dict):
        return raw['probabilities']
    return None


def _validate_decision(data):
    """Validate the normalized /v1/systemone response (adapter.normalize_response
    shape): {model, answers: {qid: {type, raw, scores/p_true ...}}}. Real probability
    shapes/ranges required; the service-side clamp on noul is cross-checked against
    the raw native value; each answer must carry its own question_id and type
    consistently with what was asked; and the NATIVE RAW probabilities must be
    present and consistent with the normalized values for every head — a
    normalized/invented score alone is never sufficient (independently reproduced:
    raw missing, or raw .01 vs normalized .87, all used to pass)."""
    if not isinstance(data, dict):
        raise AIError('TECHNICAL_ERROR', 'DECISION_RESPONSE_NOT_OBJECT')
    answers = data.get('answers')
    if not isinstance(answers, dict):
        raise AIError('TECHNICAL_ERROR', 'ANSWERS_MISSING')
    for qid, qtype in _EXPECTED_ANSWER_TYPES.items():
        answer = answers.get(qid)
        if not isinstance(answer, dict):
            raise AIError('TECHNICAL_ERROR', '%s_ANSWER_MISSING' % qid.upper())
        if answer.get('question_id') != qid:
            raise AIError('ANSWER_QUESTION_ID_MISMATCH',
                          'answer %r declares question_id %r' % (qid, answer.get('question_id')))
        if answer.get('type') != qtype:
            raise AIError('ANSWER_TYPE_MISMATCH',
                          'answer %r declares type %r, expected %r'
                          % (qid, answer.get('type'), qtype))
    model = data.get('model')
    if not isinstance(model, str) or not model.strip():
        raise AIError('DECISION_MODEL_IDENTITY_MISSING',
                      'decision response must identify the actual decision model')
    probabilities = {}
    clear = answers.get('clear')
    if not isinstance(clear.get('p_true'), (int, float)) or isinstance(clear.get('p_true'), bool):
        raise AIError('NOUL_ANSWER_MISSING')
    p_true = _probability(clear.get('p_true'))
    # the native raw noul must EXIST and every numeric candidate must be a valid,
    # consistent probability — the service clamps, so the normalized value can
    # mask a broken raw head output
    raw_nouls = _raw_noul_probabilities(clear.get('raw'))
    if not raw_nouls:
        raise AIError('RAW_NOUL_PROBABILITY_MISSING')
    for raw_p in raw_nouls:
        if not (math.isfinite(raw_p) and 0.0 <= raw_p <= 1.0):
            raise AIError('RAW_NOUL_PROBABILITY_INVALID')
        if abs(raw_p - p_true) > _RAW_TOLERANCE:
            raise AIError('RAW_NOUL_PROBABILITY_MISMATCH')
    probabilities['noul'] = {'p_true': p_true,
                             'choice': clear.get('choice'),
                             'confidence': clear.get('confidence')}
    route = answers.get('route')
    route_scores = route.get('scores') if isinstance(route, dict) else None
    if not isinstance(route_scores, dict) or set(route_scores) != {'pass', 'return'}:
        raise AIError('CHOICE_ANSWER_SHAPE_INVALID')
    probabilities['choice'] = {k: _probability(v) for k, v in route_scores.items()}
    if abs(sum(probabilities['choice'].values()) - 1.0) > 0.05:
        raise AIError('CHOICE_PROBABILITIES_NOT_NORMALIZED')
    raw_choice = _raw_probability_map(route.get('raw'))
    if not isinstance(raw_choice, dict) or set(raw_choice) != {'pass', 'return'}:
        raise AIError('RAW_CHOICE_PROBABILITY_MISSING')
    for key, value in raw_choice.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise AIError('RAW_CHOICE_PROBABILITY_INVALID')
        if abs(float(value) - probabilities['choice'][key]) > _RAW_TOLERANCE:
            raise AIError('RAW_CHOICE_PROBABILITY_MISMATCH')
    quality = answers.get('quality')
    quality_scores = quality.get('scores') if isinstance(quality, dict) else None
    if not isinstance(quality_scores, dict) or set(quality_scores) != {'low', 'medium', 'high'}:
        raise AIError('SCORE_ANSWER_SHAPE_INVALID')
    probabilities['score'] = {k: _probability(v) for k, v in quality_scores.items()}
    if abs(sum(probabilities['score'].values()) - 1.0) > 0.05:
        raise AIError('SCORE_PROBABILITIES_NOT_NORMALIZED')
    raw_score = _raw_probability_map(quality.get('raw'))
    if not isinstance(raw_score, dict) or set(raw_score) != {'0', '1', '2'}:
        raise AIError('RAW_SCORE_PROBABILITY_MISSING')
    for index, label in (('0', 'low'), ('1', 'medium'), ('2', 'high')):
        value = raw_score[index]
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise AIError('RAW_SCORE_PROBABILITY_INVALID')
        if abs(float(value) - probabilities['score'][label]) > _RAW_TOLERANCE:
            raise AIError('RAW_SCORE_PROBABILITY_MISMATCH')
    return {'model': model,
            'probabilities': probabilities,
            'latency_ms': data.get('latency_ms'),
            'best': answers.get('quality', {}).get('best'),
            'raw': data}


def _shot_state_text(shot, evidence_block=None):
    """Structured shot fields plus — when supplied — VERIFIED structured technical
    evidence only. The full NEW-contract identity fields travel with the shot
    (source/character/product refs as text identifiers, required/forbidden
    visuals, generation prompt); never file paths or filenames — they are not
    visual proof. The input scope is recorded so the decision can never be
    presented as more than what was actually sent."""
    state = {key: shot.get(key) for key in (
        'shot_id', 'material_id', 'product_id', 'narration', 'narration_target',
        'visual_purpose', 'scene', 'action_start', 'action_process', 'action_end',
        'action', 'camera_and_framing', 'framing', 'camera', 'movement',
        'duration_sec', 'risk_flags', 'must', 'must_not',
        'required_visuals', 'forbidden_visuals', 'audio_policy',
        'generation_prompt')}
    for key in ('refs', 'source_refs', 'character_refs', 'product_refs'):
        refs = shot.get(key)
        if isinstance(refs, list):
            state[key] = [{'ref_id': r.get('ref_id'),
                           'role': r.get('role') if isinstance(r, dict) else None}
                          for r in refs if isinstance(r, dict)]
    if evidence_block is not None:
        state['technical_evidence'] = evidence_block
    return _canonical_json(state)


def _structured_evidence(evidence):
    """Extract only verifiable structured technical facts from a take/review
    record: probe/decode metadata, content hashes, recorded review statuses and
    text-only reference identifiers. video_path / filenames / any path string is
    dropped — a filename proves nothing about pixels."""
    if not isinstance(evidence, dict):
        return None, 'text_only'
    media = evidence.get('media') if isinstance(evidence.get('media'), dict) else {}
    block = {}
    for key in ('duration_sec', 'width', 'height', 'container', 'metadata_status',
                'probe_status', 'decode_status', 'sha256'):
        if media.get(key) is not None:
            block[key] = media[key]
    for key in ('take_id', 'shot_id', 'version', 'status', 'review_status'):
        if evidence.get(key) is not None:
            block['take_' + key] = evidence[key]
    refs = []
    for ref in evidence.get('refs', []) if isinstance(evidence.get('refs'), list) else []:
        if isinstance(ref, dict) and isinstance(ref.get('ref_id'), str):
            item = {'ref_id': ref['ref_id'],
                    'role': ref.get('role') if isinstance(ref.get('role'), str) else None}
            if isinstance(ref.get('sha256'), str) and SHA_RE.fullmatch(ref['sha256']):
                item['sha256'] = ref['sha256']
            refs.append(item)
    if refs:
        block['registered_refs'] = refs
    shot = evidence.get('shot') if isinstance(evidence.get('shot'), dict) else {}
    for key in ('visual_status', 'continuous_motion_status', 'input_modality'):
        value = evidence.get(key) or shot.get(key)
        if isinstance(value, str):
            block['recorded_' + key] = value
    if not block:
        return None, 'text_only'
    return block, 'text+structured_technical_metadata'


class ClefAdapter:
    """Real-protocol Clef decision client: GET /health + POST /v1/systemone only.

    Inference policy (user model-lifecycle rule 2026-10-11): inspections/doctor
    never infer or load. probe() is health-only by default; a real decision
    happens ONLY through an explicit opt-in carrying a caller-owned exclusive
    lifecycle control (acquire/release, released in finally on every ending),
    under the per-project single-flight. GPU presence in /health is a fact about
    the machine, not about executed inference: without lifecycle-held runtime
    proof the record stays 'partial'/'blocked' — never 'verified'."""

    def __init__(self, endpoint='http://127.0.0.1:8088', timeout_sec=15):
        self.endpoint = _validate_loopback_endpoint(endpoint)
        self.timeout_sec = _validate_bounded_number(timeout_sec, _TIMEOUT_BOUNDS,
                                                    'INVALID_TIMEOUT_SEC')
        self._cache = {}
        self._health_cache = {'at': 0.0, 'data': None}

    # -- capability ----------------------------------------------------------

    def _capability(self):
        """GET /health (short TTL cache) validated fail-closed."""
        now = time.monotonic()
        if self._health_cache['data'] is not None and \
                now - self._health_cache['at'] < _CLEF_HEALTH_TTL_S:
            return self._health_cache['data']
        result = self._request(self.endpoint + '/health', None)
        if result['status'] != 'ok':
            return result
        try:
            raw_health = _strict_json_loads(result['body'])
            capability = _validate_health(raw_health)
        except ValueError:
            return {'status': 'blocked', 'error_code': 'TECHNICAL_ERROR',
                    'error': 'UNPARSEABLE_HEALTH_RESPONSE'}
        except AIError as exc:
            return {'status': 'blocked', 'error_code': exc.code, 'error': str(exc)}
        capability['raw_health'] = raw_health  # audit: keep the real response
        self._health_cache = {'at': now, 'data': capability}
        return capability

    def _honest_flags(self, lifecycle_status='not_owned'):
        return {'lifecycle_status': lifecycle_status,
                'cuda_execution_status': 'unverified',
                'joint_head_status': 'unverified'}

    def probe(self, infer=False, lifecycle=None):
        """Health-only capability check by default: GET /health, NO decision,
        NO model load. Explicit infer=True runs one real state+questions probe
        decision, and only under a caller-owned exclusive lifecycle control that
        is always released in finally; without a valid lifecycle the probe fails
        closed and no POST happens."""
        base = {'service_endpoint': self.endpoint, 'probe': True,
                'input_modality': 'text_only', 'visual_status': 'UNVERIFIED',
                'image_input_support': 'UNVERIFIED'}
        capability = self._capability()
        if capability.get('status') == 'blocked':
            return dict(base, status='blocked', error_code=capability.get('error_code'),
                        error=capability.get('error'),
                        **self._honest_flags())
        if not infer:
            return dict(base, status='partial', error_code=None,
                        reason='health_only_no_inference: identity/backend/CUDA facts '
                               'verified, decision capability NOT proven (no load, no POST)',
                        health=capability,
                        raw_result={'health': capability.get('raw_health')},
                        **self._honest_flags())
        held, record = _acquire_inference_lifecycle(lifecycle)
        if not held:
            return dict(base, status='blocked', error_code=record['error_code'],
                        error=record.get('error'), health=capability, **record.get('flags', {}))
        try:
            decision = self._inference_call(
                'Preflight probe: a product material shot has a clear start, '
                'visible process and a stable end. No generation is authorized.',
                capability)
        finally:
            lifecycle.release()  # released on success, failure, timeout and cancel
        if decision.get('status') != 'ok':
            return dict(base, status='blocked', error_code=decision.get('error_code'),
                        error=decision.get('error'), health=capability,
                        **self._honest_flags())
        try:
            validated = _validate_decision(_strict_json_loads(decision['body']))
            _check_decision_model_identity(validated, capability)
        except ValueError:
            return dict(base, status='blocked', error_code='TECHNICAL_ERROR',
                        error='UNPARSEABLE_SYSTEMONE_RESPONSE', health=capability,
                        **self._honest_flags())
        except AIError as exc:
            return dict(base, status='blocked', error_code=exc.code, error=str(exc),
                        health=capability, **self._honest_flags())
        flags = _lifecycle_runtime_flags(lifecycle)
        status = 'verified' if (flags['cuda_execution_status'] == 'verified'
                                and flags['joint_head_status'] == 'verified'
                                and flags['lifecycle_status'] == 'owned_exclusive') \
            else 'partial'
        return dict(base, status=status, error_code=None,
                    health=capability,
                    decision_model=validated['model'],
                    probabilities=validated['probabilities'],
                    raw_result={'health': capability.get('raw_health'),
                                'decision': validated['raw']},
                    **flags,
                    note='nvidia-smi CUDA availability + native probabilities do NOT '
                         'prove executed CUDA; joint-head runtime proof is separate')

    # -- decisions -----------------------------------------------------------

    def review(self, phase, shot, evidence=None, lifecycle=None):
        if phase not in ('A', 'B'):
            raise AIError('INVALID_PHASE',
                          'phase must be A (pre-generation) or B (post-generation)')
        record_id = 'rev-' + uuid.uuid4().hex[:12]
        record = {'status': 'blocked', 'error_code': None, 'source': 'clef',
                  'phase': phase,
                  'shot_id': shot.get('shot_id') if isinstance(shot, dict) else None,
                  'verdict': None, 'probabilities': None, 'input_modality': 'text_only',
                  'visual_status': ('UNVERIFIED' if phase == 'B'
                                    else 'not_applicable_pre_generation'),
                  'continuous_motion_status': ('NEEDS_REVIEW' if phase == 'B' else None),
                  'review_status': 'TECHNICAL_ERROR',
                  'review_record_id': record_id, 'cached': False,
                  'model': None, 'latency_ms': None, 'raw_result': None,
                  **self._honest_flags()}
        if not isinstance(shot, dict):
            record.update(error_code='SHOT_CONTRACT_INVALID')
            return record
        evidence_block, input_scope = _structured_evidence(evidence)
        record['input_scope'] = input_scope
        questions = _PHASE_QUESTIONS[phase]
        record['questions_digest'] = hashlib.sha256(
            _canonical_json(questions).encode('utf-8')).hexdigest()
        cache_key = _canonical_hash({
            'adapter': 'clef-systemone', 'phase': phase, 'shot': shot,
            'evidence': evidence_block, 'input_scope': input_scope,
            'questions': questions, 'policy': 'commodity-material-ai-v1/clef-review/v2'})
        hit = self._cache.get(cache_key)
        if hit is not None:
            replayed = json.loads(_canonical_json(hit))
            replayed.update(cached=True, review_record_id=record_id)
            return replayed
        capability = self._capability()
        if capability.get('status') == 'blocked':
            record.update(error_code=capability.get('error_code'),
                          error=capability.get('error'))
            return record
        held, gate = _acquire_inference_lifecycle(lifecycle)
        if not held:
            record.update(error_code=gate['error_code'], error=gate.get('error'))
            record.update(gate.get('flags', {}))
            return record
        try:
            decision = self._inference_call(_shot_state_text(shot, evidence_block),
                                            capability, questions=questions)
        finally:
            lifecycle.release()  # released on every ending
        if decision.get('status') != 'ok':
            record.update(error_code=decision.get('error_code'),
                          error=decision.get('error'))
            return record
        try:
            validated = _validate_decision(_strict_json_loads(decision['body']))
            _check_decision_model_identity(validated, capability)
        except ValueError:
            record.update(error_code='TECHNICAL_ERROR',
                          error='UNPARSEABLE_SYSTEMONE_RESPONSE')
            return record
        except AIError as exc:
            record.update(error_code=exc.code, error=str(exc))
            return record
        choice = validated['probabilities']['choice']
        verdict = 'pass' if choice.get('pass', 0.0) >= choice.get('return', 0.0) \
            else 'return'
        flags = _lifecycle_runtime_flags(lifecycle)
        delivery = 'verified' if (flags['cuda_execution_status'] == 'verified'
                                  and flags['joint_head_status'] == 'verified'
                                  and flags['lifecycle_status'] == 'owned_exclusive') \
            else 'partial'
        # uppercase review_status is the review verdict, distinct from technical
        # delivery status: a text-only B review can never PASS on visuals
        if verdict == 'return':
            review_status = 'RETURN'
        elif phase == 'B':
            review_status = 'NEEDS_REVIEW'
        else:
            review_status = 'PASS'
        record.update(status=delivery, error_code=None, verdict=verdict,
                      review_status=review_status,
                      probabilities=validated['probabilities'],
                      model=validated['model'], latency_ms=validated['latency_ms'],
                      raw_result={'health_model': capability['model'],
                                  'decision': validated['raw']},
                      **flags)
        self._cache[cache_key] = json.loads(_canonical_json(record))
        return record

    # -- inference plumbing ----------------------------------------------------

    def _inference_call(self, state_text, capability, questions=None):
        """Single real POST /v1/systemone under the per-project single-flight
        (bounded: released in finally, including on timeout)."""
        flight = _project_flight(ROOT, 'clef')
        if not flight.acquire():
            return {'status': 'blocked', 'error_code': 'CONCURRENCY_BLOCKED',
                    'error': 'another Clef inference holds the per-project '
                             'single-flight lock'}
        try:
            return self._request(
                self.endpoint + '/v1/systemone',
                {'state': state_text,
                 'questions': questions or SYSTEMONE_QUESTIONS})
        finally:
            flight.release()

    def _request(self, url, payload):
        try:
            response = _http_json(url, payload, self.timeout_sec)
            return {'status': 'ok', 'http_status': response['http_status'],
                    'body': response['body']}
        except urllib.error.HTTPError as exc:
            return {'status': 'blocked', 'error_code': 'HTTP_%d' % exc.code,
                    'error': str(exc.reason)}
        except urllib.error.URLError as exc:
            return {'status': 'blocked', 'error_code': 'SERVICE_UNREACHABLE',
                    'error': str(exc)}
        except AIError as exc:
            return {'status': 'blocked', 'error_code': exc.code, 'error': str(exc)}
        except (OSError, ValueError) as exc:
            return {'status': 'blocked', 'error_code': 'SERVICE_UNREACHABLE',
                    'error': str(exc)}


def _check_decision_model_identity(validated, capability):
    """The decision response must identify the SAME model the service health
    declared; a normalized answer from an unknown head is not evidence."""
    health_model = ((capability.get('model') or {}).get('file')
                    if isinstance(capability, dict) else None)
    decision_model = validated['model']
    if not isinstance(health_model, str) \
            or decision_model.strip().lower() != health_model.strip().lower():
        raise AIError('DECISION_MODEL_MISMATCH',
                      'decision model identity does not match service health')


def _acquire_inference_lifecycle(lifecycle):
    """Fail-closed gate for real local inference: without a caller-owned
    exclusive lifecycle control (acquire() -> bool / release() -> None) no
    decision POST is ever made."""
    if lifecycle is None:
        return False, {'error_code': 'INFERENCE_LIFECYCLE_REQUIRED',
                       'error': 'real inference requires a caller-owned exclusive '
                                'lifecycle control released in finally; '
                                'inspections never infer'}
    if not callable(getattr(lifecycle, 'acquire', None)) \
            or not callable(getattr(lifecycle, 'release', None)):
        return False, {'error_code': 'INFERENCE_LIFECYCLE_INVALID',
                       'error': 'lifecycle must provide acquire() and release()'}
    try:
        acquired = lifecycle.acquire()
    except Exception as exc:  # lifecycle seam is caller-owned; never crash on it
        return False, {'error_code': 'INFERENCE_LIFECYCLE_INVALID',
                       'error': 'acquire raised %s' % type(exc).__name__}
    if acquired is not True:
        try:
            lifecycle.release()
        except Exception:
            pass
        return False, {'error_code': 'INFERENCE_LIFECYCLE_BUSY',
                       'error': 'exclusive lifecycle could not be acquired'}
    return True, {}


def _lifecycle_runtime_flags(lifecycle):
    """Honest runtime flags from the lifecycle seam. nvidia-smi visibility and
    a normalized probability value are NOT CUDA execution proof; only the
    lifecycle's own runtime evidence can raise these, and absence keeps them
    unverified (record stays partial/blocked, never verified)."""
    evidence = {}
    try:
        fn = getattr(lifecycle, 'runtime_evidence', None)
        if callable(fn):
            value = fn()
            if isinstance(value, dict):
                evidence = value
    except Exception:
        evidence = {}
    return {'lifecycle_status': evidence.get('lifecycle_status', 'owned_exclusive'),
            'cuda_execution_status': evidence.get('cuda_execution_status', 'unverified'),
            'joint_head_status': evidence.get('joint_head_status', 'unverified')}


# ---------------------------------------------------------------------------
# MaterialStore — append-only SQLite audit store
# ---------------------------------------------------------------------------

REVIEW_MODES = ('dry_run', 'shadow_review')  # production recording stays blocked this round
REAL_REVIEW_SOURCES = ('claude', 'clef')
_APPROVAL_COMPATIBLE_STATUSES = ('PASS', 'NEEDS_REVIEW')
# user §9 reviewer decision fields — a claude reviewer record carries them
# either nested in structured_output or directly at the record top level
_REVIEWER_DECISION_FIELDS = ('role', 'shot_id', 'take_id', 'review_status',
                             'confirmed_issues', 'unverified_issues', 'evidence',
                             'repair_action', 'prompt_revision',
                             'reference_changes', 'regenerate_required')


def _approval_status_value(rec):
    """Explicit review verdict on a stored review record, normalized uppercase:
    the record's own review_status first, then a claude reviewer structured
    decision, then a legacy lowercase verdict. A B-phase text verdict 'pass' can
    never surface as PASS (text-only reviews cannot confirm visuals) — it maps
    to NEEDS_REVIEW. None when the record carries no verdict at all."""
    value = rec.get('review_status')
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    structured = rec.get('structured_output')
    if isinstance(structured, dict):
        value = structured.get('review_status')
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    value = rec.get('verdict')
    if isinstance(value, str) and value.strip():
        lowered = value.strip().lower()
        if lowered == 'return':
            return 'RETURN'
        if lowered == 'needs_review':
            return 'NEEDS_REVIEW'
        if lowered == 'pass':
            return 'NEEDS_REVIEW' if rec.get('phase') == 'B' else 'PASS'
    return None


def _required_ref_ids(shot):
    """Every ref_id the shot contract declares as required identity input."""
    ids = []
    for key in ('source_refs', 'character_refs', 'product_refs', 'refs'):
        for ref in (shot.get(key) or []) if isinstance(shot, dict) else []:
            if isinstance(ref, dict) and isinstance(ref.get('ref_id'), str) \
                    and ref['ref_id'] not in ids:
                ids.append(ref['ref_id'])
    return ids


# fields the store itself stamps onto stored review rows are kept ONLY in the
# stamped envelope; the immutable source payload columns preserve the caller's
# payload verbatim, so export re-hashes exact bytes and never guesses
_REVIEW_STAMP_KEYS = ('review_id', 'take_id', 'mode', 'created_at',
                      'binding_stamp', 'approval_changed')


def _probe_media(path):
    """Honest two-layer media evidence:

    - probe layer (ffprobe): container/duration/stream facts only;
    - decode layer (ffmpeg): a bounded REAL frame decode (one frame, null muxer).

    ``metadata_status`` is 'decoded' ONLY when probe parsed AND a video stream
    with nonzero dimensions exists AND the real frame decode succeeded. An
    audio-only file (no video stream, dims null), a corrupt-coded payload, or a
    missing ffmpeg decode check is never 'decoded' and can never be approved —
    but the file/record is always retained, never deleted."""
    probe = {'duration_sec': None, 'width': None, 'height': None, 'container': None,
             'video_stream': False,
             'probe_status': 'probe_unavailable', 'probe_error': None,
             'decode_status': 'not_run', 'decode_error': None,
             'metadata_status': 'probe_unavailable'}
    ffprobe = shutil.which('ffprobe')
    if ffprobe is None:
        probe['probe_error'] = 'ffprobe not resolvable on PATH; no invented metadata'
        return probe
    try:
        record = subprocess.run(
            [ffprobe, '-v', 'error', '-print_format', 'json',
             '-show_format', '-show_streams', str(path)],
            capture_output=True, timeout=FFPROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        probe['probe_error'] = 'ffprobe failed: %s' % type(exc).__name__
        return probe
    if record.returncode != 0:
        probe.update(probe_status='probe_error', metadata_status='probe_error',
                     probe_error=record.stderr.decode('utf-8', 'replace')[:300])
        return probe
    try:
        data = _strict_json_loads(record.stdout.decode('utf-8', 'replace'))
        fmt = data.get('format') or {}
        duration = float(fmt.get('duration'))
        if not (math.isfinite(duration) and duration >= 0):
            raise ValueError('duration not finite')
        probe['duration_sec'] = duration
        probe['container'] = (fmt.get('format_name') or '').split(',')[0] or None
        for stream in data.get('streams') or []:
            if isinstance(stream, dict) and stream.get('codec_type') == 'video':
                width, height = stream.get('width'), stream.get('height')
                if isinstance(width, int) and isinstance(height, int) \
                        and width > 0 and height > 0:
                    probe['width'], probe['height'] = width, height
                    probe['video_stream'] = True
                break
        probe['probe_status'] = 'ok'
    except (ValueError, TypeError, AttributeError):
        probe.update(probe_status='probe_error', metadata_status='probe_error',
                     probe_error='ffprobe output not parseable as real media metadata')
        return probe
    if not probe['video_stream']:
        probe.update(metadata_status='no_video_stream',
                     decode_status='not_run',
                     decode_error='audio-only or stream-less payload has no decodable '
                                  'video frames; never approvable')
        return probe
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg is None:
        probe.update(metadata_status='decode_check_unavailable',
                     decode_error='ffmpeg not resolvable on PATH; real frame decode '
                                  'unverified, approval stays blocked')
        return probe
    try:
        decoded = subprocess.run(
            [ffmpeg, '-v', 'error', '-i', str(path), '-frames:v', '1',
             '-f', 'null', '-'],
            capture_output=True, timeout=FFPROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        probe.update(metadata_status='decode_failed',
                     decode_error='ffmpeg decode check failed: %s' % type(exc).__name__)
        return probe
    if decoded.returncode != 0:
        probe.update(metadata_status='decode_failed',
                     decode_error=decoded.stderr.decode('utf-8', 'replace')[:300])
        return probe
    probe.update(decode_status='verified', metadata_status='decoded')
    return probe


class MaterialStore:
    """Append-only material audit store; every version and every failed take stays."""

    def __init__(self, root=ROOT):
        self.root = _validate_root_argument(root)
        self.dir = self.root / 'runtime' / 'commodity-material-ai-v1'
        # reject junction/symlink escapes on the runtime/db ancestors BEFORE any
        # mkdir or sqlite open; re-verify after creation (TOCTOU hardening)
        _reject_link_escape(self.root, self.dir, 'STORE_PATH_LINK_ESCAPE')
        self.dir.mkdir(parents=True, exist_ok=True)
        _reject_link_escape(self.root, self.dir, 'STORE_PATH_LINK_ESCAPE')
        self.db_path = self.dir / 'store.sqlite3'
        self._init_db()

    # -- schema -------------------------------------------------------------

    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS takes ('
                    ' take_id TEXT PRIMARY KEY, shot_id TEXT NOT NULL,'
                    ' product_id TEXT NOT NULL, version INTEGER NOT NULL,'
                    ' batch_id TEXT NOT NULL, provenance TEXT NOT NULL,'
                    ' mock INTEGER NOT NULL, status TEXT NOT NULL,'
                    ' video_path TEXT, shot_json TEXT NOT NULL,'
                    ' shot_sha256 TEXT NOT NULL, media_json TEXT NOT NULL,'
                    ' registered_at TEXT NOT NULL,'
                    ' UNIQUE(shot_id, version))')
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS reviews ('
                    ' review_id TEXT PRIMARY KEY, take_id TEXT NOT NULL,'
                    ' mode TEXT NOT NULL, review_json TEXT NOT NULL,'
                    ' review_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)')
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS usages ('
                    ' usage_id TEXT PRIMARY KEY, take_id TEXT NOT NULL,'
                    ' target_shot_id TEXT NOT NULL, purpose TEXT NOT NULL,'
                    ' created_at TEXT NOT NULL)')
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS approvals ('
                    ' approval_id TEXT PRIMARY KEY, take_id TEXT NOT NULL UNIQUE,'
                    ' approval_json TEXT NOT NULL, approved_at TEXT NOT NULL)')
                conn.execute(
                    'CREATE TABLE IF NOT EXISTS stage_records ('
                    ' record_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,'
                    ' stage TEXT NOT NULL, shot_id TEXT, mode TEXT NOT NULL,'
                    ' record_json TEXT NOT NULL, record_sha256 TEXT NOT NULL,'
                    ' created_at TEXT NOT NULL)')
                # additive migration: immutable caller source payloads so export
                # re-hashes the exact bytes that were reviewed (never guesses
                # which keys were store stamps); legacy NULL rows stay explicit
                columns = {row[1] for row in conn.execute('PRAGMA table_info(reviews)')}
                if 'source_payload' not in columns:
                    conn.execute('ALTER TABLE reviews ADD COLUMN source_payload TEXT')
                if 'source_sha256' not in columns:
                    conn.execute('ALTER TABLE reviews ADD COLUMN source_sha256 TEXT')
        finally:
            conn.close()

    # -- registration -------------------------------------------------------

    def _resolve_media(self, video_path):
        text = str(video_path)
        # reject UNC/device and drive-relative LITERALS before any pathlib join
        # (a same-drive relative would otherwise be folded into cwd) and before
        # any resolve()/stat()
        if text.startswith('\\\\') or text.startswith('\\\\.\\') \
                or text.startswith('\\??\\'):
            raise AIError('MEDIA_OUTSIDE_PROJECT',
                          'UNC/device path literal refused before filesystem access')
        if _DRIVE_RELATIVE_RE.match(text):
            raise AIError('MEDIA_OUTSIDE_PROJECT',
                          'drive-relative path refused before filesystem access')
        raw = Path(video_path)
        if not raw.is_absolute():
            raw = self.root / raw
        # lexical boundary guard BEFORE any resolve()/stat(): external, UNC,
        # drive-relative and parent-traversal literals are refused untouched
        guarded = _guard_path_inside(raw, self.root, 'MEDIA_OUTSIDE_PROJECT')
        resolved = guarded.resolve()
        if not _is_inside(resolved, self.root):
            raise AIError('MEDIA_OUTSIDE_PROJECT',
                          'media path resolves (link chain) outside the project root')
        if not resolved.is_file():
            raise AIError('MEDIA_MISSING')
        if resolved.suffix.lower() not in MEDIA_SUFFIXES:
            raise AIError('MEDIA_TYPE_UNSUPPORTED')
        return resolved

    def register_take(self, shot, video_path, version=1, batch_id='local',
                      provenance='existing_local'):
        if not isinstance(shot, dict):
            raise AIError('SHOT_CONTRACT_INVALID')
        shot_id = shot.get('shot_id')
        if not isinstance(shot_id, str) or not ID_RE.fullmatch(shot_id):
            raise AIError('INVALID_SHOT_ID')
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise AIError('INVALID_VERSION')
        if not isinstance(batch_id, str) or not ID_RE.fullmatch(batch_id):
            raise AIError('INVALID_BATCH_ID')
        if not isinstance(provenance, str) or not provenance.strip():
            raise AIError('INVALID_PROVENANCE')
        mock = bool(shot.get('test_flag')) or 'mock' in provenance.lower()
        media = {'video_path': None, 'sha256': None, 'duration_sec': None,
                 'width': None, 'height': None, 'container': None,
                 'video_stream': False, 'probe_status': 'not_run',
                 'probe_error': None, 'decode_status': 'not_run',
                 'decode_error': None, 'metadata_status': 'no_media'}
        if video_path is None:
            if not mock:
                raise AIError('MEDIA_REQUIRED_FOR_REAL_TAKE')
            status = 'mock_no_video'
        else:
            resolved = self._resolve_media(video_path)
            media['video_path'] = str(resolved)
            media['sha256'] = _sha256_file(resolved)
            media.update(_probe_media(resolved))
            # 'registered' claims full honest decode evidence; anything less keeps
            # the version (retain broken originals) but marks it media_broken
            status = 'registered' if media['metadata_status'] == 'decoded' \
                else 'media_broken'
        take_id = '{}.v{}.{}'.format(shot_id, version, batch_id)
        if not ID_RE.fullmatch(take_id):
            raise AIError('INVALID_TAKE_ID')
        shot_json = _canonical_json(shot)
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    'INSERT INTO takes (take_id, shot_id, product_id, version, batch_id,'
                    ' provenance, mock, status, video_path, shot_json, shot_sha256,'
                    ' media_json, registered_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (take_id, shot_id, str(shot.get('product_id') or ''), version,
                     batch_id, provenance, 1 if mock else 0, status,
                     media['video_path'], shot_json,
                     hashlib.sha256(shot_json.encode('utf-8')).hexdigest(),
                     _canonical_json(media), _utc()))
        except sqlite3.IntegrityError:
            raise AIError('DUPLICATE_TAKE_VERSION',
                          'shot %s version %s already registered; versions are immutable'
                          % (shot_id, version))
        finally:
            conn.close()
        return self.get_take(take_id)

    # -- queries ------------------------------------------------------------

    def _take_row(self, take_id, conn=None):
        owns = conn is None
        conn = conn or self._connect()
        try:
            return conn.execute('SELECT * FROM takes WHERE take_id = ?',
                                (take_id,)).fetchone()
        finally:
            if owns:
                conn.close()

    def _assemble(self, row, conn=None):
        shot = json.loads(row['shot_json'])
        media = json.loads(row['media_json'])
        owns = conn is None
        conn = conn or self._connect()
        try:
            review_rows = conn.execute(
                'SELECT * FROM reviews WHERE take_id = ? ORDER BY created_at, review_id',
                (row['take_id'],)).fetchall()
            usage_rows = conn.execute(
                'SELECT * FROM usages WHERE take_id = ? ORDER BY created_at, usage_id',
                (row['take_id'],)).fetchall()
            approval_row = conn.execute(
                'SELECT * FROM approvals WHERE take_id = ?', (row['take_id'],)).fetchone()
        finally:
            if owns:
                conn.close()
        reviews = [json.loads(r['review_json']) for r in review_rows]
        bound = [r for r in reviews if r.get('mode') != 'shadow_review']
        approval = json.loads(approval_row['approval_json']) if approval_row else None
        return {
            'take_id': row['take_id'], 'shot_id': row['shot_id'],
            'product_id': row['product_id'], 'version': row['version'],
            'batch_id': row['batch_id'], 'provenance': row['provenance'],
            'mock': bool(row['mock']), 'status': row['status'],
            'video_path': row['video_path'], 'media': media, 'shot': shot,
            'registered_at': row['registered_at'], 'reviews': bound,
            'usage': [dict(u) for u in usage_rows], 'approval': approval,
            'review_status': bound[-1].get('verdict') if bound else None,
            'approval_status': 'approved' if approval else None,
        }

    def list_takes(self):
        conn = self._connect()
        try:
            rows = conn.execute('SELECT * FROM takes ORDER BY shot_id, version').fetchall()
            return [self._assemble(row, conn=conn) for row in rows]
        finally:
            conn.close()

    def get_take(self, take_id):
        row = self._take_row(take_id)
        if row is None:
            return None
        return self._assemble(row)

    def history(self, take_id):
        conn = self._connect()
        try:
            row = self._take_row(take_id, conn=conn)
            if row is None:
                raise AIError('TAKE_NOT_FOUND', take_id)
            version_rows = conn.execute(
                'SELECT take_id, version, batch_id, provenance, mock, status,'
                ' registered_at FROM takes WHERE shot_id = ? ORDER BY version',
                (row['shot_id'],)).fetchall()
            review_rows = conn.execute(
                'SELECT * FROM reviews WHERE take_id = ? ORDER BY created_at, review_id',
                (take_id,)).fetchall()
            usage_rows = conn.execute(
                'SELECT * FROM usages WHERE take_id = ? ORDER BY created_at, usage_id',
                (take_id,)).fetchall()
            approval_row = conn.execute(
                'SELECT * FROM approvals WHERE take_id = ?', (take_id,)).fetchone()
        finally:
            conn.close()
        reviews = [json.loads(r['review_json']) for r in review_rows]
        # per-run + per-shot stage records (Director/Writer/A/B/H3/Reviewer),
        # linked through the run_ids the appended reviews carry, so raw model
        # records and errors stay visible in the take's history; the shared
        # Director record of each run is included via its run association
        run_ids = sorted({r.get('run_id') for r in reviews
                          if isinstance(r.get('run_id'), str)})
        stage_records = [rec for rec in self.stage_history()
                         if rec['run_id'] in run_ids
                         and rec['shot_id'] in (None, row['shot_id'])]
        return {
            'take_id': take_id, 'shot_id': row['shot_id'],
            'versions': [dict(v) for v in version_rows],
            'reviews': reviews,
            'shadow_observations': [r for r in reviews
                                    if r.get('mode') == 'shadow_review'],
            'usage': [dict(u) for u in usage_rows],
            'approval': json.loads(approval_row['approval_json']) if approval_row else None,
            'stage_records': stage_records,
            'stage_runs': sorted({rec['run_id'] for rec in stage_records}),
        }

    # -- append-only writes ---------------------------------------------------

    def append_review(self, take_id, review, mode='dry_run'):
        if mode not in REVIEW_MODES:
            raise AIError('MODE_INVALID',
                          'mode must be one of %s (production recording stays blocked '
                          'this round)' % ', '.join(REVIEW_MODES))
        if not isinstance(review, dict):
            raise AIError('REVIEW_CONTRACT_INVALID')
        # a direct full reviewer record may carry its own take_id: it must match
        # the take being appended to (no silent rebinding)
        if review.get('take_id') is not None and review.get('take_id') != take_id:
            raise AIError('REVIEW_TAKE_MISMATCH',
                          'review declares take_id %r but is appended to %r'
                          % (review.get('take_id'), take_id))
        try:
            _reject_nonfinite(review)
            source_payload = _canonical_json(review)  # immutable, verbatim
            source_sha = _canonical_hash(review)
        except (ValueError, TypeError) as exc:
            raise AIError('REVIEW_NOT_STRICT_JSON', str(exc))
        stored = json.loads(source_payload)
        stored.update(review_id='rev-' + uuid.uuid4().hex[:12], take_id=take_id,
                      mode=mode, created_at=_utc())
        conn = self._connect()
        try:
            with conn:
                row = self._take_row(take_id, conn=conn)
                if row is None:
                    raise AIError('TAKE_NOT_FOUND', take_id)
                # the store stamps CURRENT take/shot/file hashes so every stored
                # review is bound to the exact version it reviewed; it never
                # manufactures or upgrades any inference status field
                media = json.loads(row['media_json'])
                stored['binding_stamp'] = {
                    'take_id': take_id, 'shot_id': row['shot_id'],
                    'shot_sha256': row['shot_sha256'],
                    'video_sha256': media.get('sha256'),
                    'stamped_at': _utc()}
                conn.execute(
                    'INSERT INTO reviews (review_id, take_id, mode, review_json,'
                    ' review_sha256, source_payload, source_sha256, created_at)'
                    ' VALUES (?,?,?,?,?,?,?,?)',
                    (stored['review_id'], take_id, mode, _canonical_json(stored),
                     source_sha, source_payload, source_sha, stored['created_at']))
        finally:
            conn.close()
        stored['approval_changed'] = False  # append-only: reviews never touch approval
        return stored

    def append_stage_record(self, run_id, stage, record, shot_id=None,
                            mode='dry_run'):
        """Append-only per-stage orchestration records (Director/Writer/A/B/H3/
        Reviewer). Strict JSON only; the stored hash covers exactly the caller's
        payload so any later drift is detectable."""
        if mode not in REVIEW_MODES:
            raise AIError('MODE_INVALID', 'mode must be one of %s'
                          % ', '.join(REVIEW_MODES))
        if not isinstance(record, dict):
            raise AIError('STAGE_RECORD_CONTRACT_INVALID')
        if not isinstance(run_id, str) or not ID_RE.fullmatch(run_id):
            raise AIError('INVALID_RUN_ID')
        if not isinstance(stage, str) or not stage.strip():
            raise AIError('INVALID_STAGE')
        if shot_id is not None and (not isinstance(shot_id, str)
                                    or not ID_RE.fullmatch(shot_id)):
            raise AIError('INVALID_SHOT_ID')
        try:
            _reject_nonfinite(record)
            payload_json = _canonical_json(record)
        except (ValueError, TypeError) as exc:
            raise AIError('STAGE_RECORD_NOT_STRICT_JSON', str(exc))
        stored = {'record_id': 'stg-' + uuid.uuid4().hex[:12], 'run_id': run_id,
                  'stage': stage, 'shot_id': shot_id, 'mode': mode,
                  'record': json.loads(payload_json), 'created_at': _utc(),
                  'record_sha256': _canonical_hash(record)}
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    'INSERT INTO stage_records (record_id, run_id, stage, shot_id,'
                    ' mode, record_json, record_sha256, created_at)'
                    ' VALUES (?,?,?,?,?,?,?,?)',
                    (stored['record_id'], run_id, stage, shot_id, mode,
                     payload_json, stored['record_sha256'], stored['created_at']))
        finally:
            conn.close()
        return stored

    def stage_history(self, run_id=None):
        conn = self._connect()
        try:
            if run_id is None:
                rows = conn.execute(
                    'SELECT * FROM stage_records ORDER BY created_at, record_id'
                ).fetchall()
            else:
                rows = conn.execute(
                    'SELECT * FROM stage_records WHERE run_id = ?'
                    ' ORDER BY created_at, record_id', (run_id,)).fetchall()
        finally:
            conn.close()
        return [{'record_id': r['record_id'], 'run_id': r['run_id'],
                 'stage': r['stage'], 'shot_id': r['shot_id'], 'mode': r['mode'],
                 'record': json.loads(r['record_json']),
                 'record_sha256': r['record_sha256'],
                 'created_at': r['created_at']} for r in rows]

    def append_usage(self, take_id, target_shot_id, purpose='reuse'):
        if not isinstance(target_shot_id, str) or not target_shot_id.strip():
            raise AIError('INVALID_TARGET_SHOT')
        usage = {'usage_id': 'use-' + uuid.uuid4().hex[:12], 'take_id': take_id,
                 'target_shot_id': target_shot_id,
                 'purpose': purpose if isinstance(purpose, str) and purpose else 'reuse',
                 'created_at': _utc()}
        conn = self._connect()
        try:
            with conn:
                if self._take_row(take_id, conn=conn) is None:
                    raise AIError('TAKE_NOT_FOUND', take_id)
                conn.execute(
                    'INSERT INTO usages (usage_id, take_id, target_shot_id, purpose,'
                    ' created_at) VALUES (?,?,?,?,?)',
                    (usage['usage_id'], take_id, usage['target_shot_id'],
                     usage['purpose'], usage['created_at']))
        finally:
            conn.close()
        return usage

    # -- approval + manifest -------------------------------------------------

    def _bound_reviews(self, conn, take_id):
        rows = conn.execute(
            'SELECT * FROM reviews WHERE take_id = ? ORDER BY created_at, review_id',
            (take_id,)).fetchall()
        parsed = [(json.loads(r['review_json']), r['review_sha256']) for r in rows]
        return [(rec, sha) for rec, sha in parsed
                if rec.get('mode') != 'shadow_review']

    def _approval_evidence(self, row, bound, media):
        """Approval evidence = current bound REAL (non-mock) review results with
        ACTUAL adapter proof, all bound to THIS take version via binding_stamp:
        - a phase-B clef record must carry validated native raw probabilities
          (a source/status/PASS label alone proves nothing);
        - a claude reviewer record must carry a structured_output that strictly
          validates against SCHEMAS['reviewer'] and binds to this shot/take.
        The LATEST record of each required kind wins: a latest RETURN /
        TECHNICAL_ERROR can never be outrun by an earlier PASS. Mock, UNIT,
        planner or unbound records are never evidence."""
        mock_seen = False
        binding_failed = False
        schema_invalid = False
        unverified = False
        candidates = {'clef': [], 'claude': []}
        for rec, sha in bound:
            if rec.get('mock') is True or \
                    str(rec.get('provenance') or '').lower().startswith('mock'):
                mock_seen = True
                continue
            if rec.get('source') not in REAL_REVIEW_SOURCES:
                continue
            if rec.get('status') not in ('verified', 'partial'):
                continue
            stamp = rec.get('binding_stamp')
            if not isinstance(stamp, dict) \
                    or stamp.get('take_id') != row['take_id'] \
                    or stamp.get('shot_sha256') != row['shot_sha256'] \
                    or stamp.get('video_sha256') != media.get('sha256'):
                binding_failed = True
                continue
            structured = rec.get('structured_output')
            decision = None
            if isinstance(structured, dict) and structured.get('role') == 'reviewer':
                decision = structured
            elif rec.get('role') == 'reviewer':
                # direct full reviewer record: the §9 decision fields sit at the
                # record top level; everything else (source/status/stamps) stays out
                decision = {field: rec.get(field) for field in _REVIEWER_DECISION_FIELDS}
            if decision is not None:
                try:
                    jsonschema.Draft7Validator(SCHEMAS['reviewer']).validate(decision)
                except jsonschema.ValidationError:
                    schema_invalid = True
                    continue
                if decision.get('shot_id') != row['shot_id'] \
                        or decision.get('take_id') not in (None, row['take_id']):
                    binding_failed = True
                    continue
                candidates['claude'].append((rec, sha,
                                             _approval_status_value(rec), 'reviewer'))
                continue
            if rec.get('phase') != 'B' or rec.get('shot_id') not in (None, row['shot_id']):
                continue
            probabilities = rec.get('probabilities')
            if not (isinstance(probabilities, dict) and probabilities
                    and isinstance(probabilities.get('choice'), dict)):
                unverified = True  # no raw adapter proof: just a stub with labels
                continue
            candidates['clef'].append((rec, sha, _approval_status_value(rec), 'B'))
        if not any(candidates.values()):
            if mock_seen:
                raise AIError('REVIEW_SOURCE_UNVERIFIED',
                              'mock/self-labelled records can never be approval '
                              'evidence')
            if unverified:
                raise AIError('REVIEW_SOURCE_UNVERIFIED',
                              'review record carries no validated raw adapter '
                              'evidence (probabilities/structured output)')
            if schema_invalid:
                raise AIError('REVIEW_SCHEMA_INVALID',
                              'reviewer structured_output fails the reviewer schema')
            if binding_failed:
                raise AIError('REVIEW_BINDING_MISMATCH',
                              'real review record is not bound to the current '
                              'take version (binding_stamp/shot mismatch)')
            raise AIError('REAL_REVIEW_EVIDENCE_MISSING',
                          'approval requires a current bound real B/reviewer '
                          'result with adapter proof')
        # latest non-shadow rejection priority per required kind
        for kind, group in candidates.items():
            if not group:
                continue
            latest = group[-1]
            if latest[2] not in _APPROVAL_COMPATIBLE_STATUSES:
                raise AIError('CURRENT_REVIEW_REJECTED',
                              'the latest %s review is %r; an earlier PASS cannot '
                              'outrun the current rejection'
                              % (kind, latest[2]))
        evidence_reviews = []
        for kind in ('clef', 'claude'):
            if candidates[kind]:
                rec, sha, status_value, _phase = candidates[kind][-1]
                evidence_reviews.append((rec, sha, status_value))
        return evidence_reviews

    def _validate_approval_refs(self, evidence, shot):
        """Reference currentness gate. Only a LOCALLY VERIFIED fresh digest
        (project-local path, re-hashed at approval time) counts as covering a
        declared required ref — a supplied sha without a verifiable source is
        recorded as unverified and is never identity evidence. External paths
        are refused unread. Declared required refs cannot be waived by
        refs_none_required: that escape exists only for shots that declare no
        required refs at all (with a recorded reason)."""
        refs = evidence.get('refs')
        if not isinstance(refs, list):
            raise AIError('APPROVAL_CONTRACT_INVALID',
                          'evidence.refs must be a list')
        reason = evidence.get('refs_none_required_reason')
        none_required = evidence.get('refs_none_required') is True and \
            isinstance(reason, str) and reason.strip()
        validated = []
        for ref in refs:
            if not (isinstance(ref, dict) and isinstance(ref.get('ref_id'), str)):
                raise AIError('REF_EVIDENCE_INVALID')
            sha = ref.get('sha256')
            if sha is not None and (not isinstance(sha, str) or not SHA_RE.fullmatch(sha)):
                raise AIError('REF_EVIDENCE_SHA_INVALID',
                              'ref %s: sha256 must be 64 lowercase hex' % ref['ref_id'])
            path = ref.get('path')
            if isinstance(path, str) and path:
                guarded = _guard_path_inside(path, self.root,
                                             'REF_EVIDENCE_PATH_OUTSIDE_PROJECT')
                resolved = guarded.resolve()
                if not _is_inside(resolved, self.root):
                    raise AIError('REF_EVIDENCE_PATH_OUTSIDE_PROJECT')
                if not resolved.is_file():
                    raise AIError('REF_EVIDENCE_MISSING',
                                  'ref %s file missing at approval time' % ref['ref_id'])
                fresh = _sha256_file(resolved)
                if sha is not None and fresh != sha:
                    raise AIError('REF_DRIFT',
                                  'ref %s content changed since its registered SHA'
                                  % ref['ref_id'])
                validated.append({'ref_id': ref['ref_id'], 'sha256': fresh,
                                  'digest_source': 'computed_fresh'})
            elif sha is not None:
                validated.append({'ref_id': ref['ref_id'], 'sha256': sha,
                                  'digest_source': 'supplied_unverified'})
            else:
                validated.append({'ref_id': ref['ref_id'], 'sha256': None,
                                  'digest_source': 'none'})
        required = _required_ref_ids(shot)
        if required:
            if none_required:
                raise AIError('REF_COVERAGE_MISSING',
                              'refs_none_required cannot waive declared required '
                              'refs %s' % ', '.join(required))
            covered = {r['ref_id'] for r in validated
                       if r['digest_source'] == 'computed_fresh'}
            missing = [rid for rid in required if rid not in covered]
            if missing:
                raise AIError('REF_COVERAGE_MISSING',
                              'shot requires refs %s covered by locally verified '
                              'fresh-digest evidence' % ', '.join(missing))
        return validated

    def get_approval_context(self, take_id, approval):
        """Read-only pre-confirmation helper: validates the CURRENT approval
        context (take/media digests, latest bound reviews, fresh verified refs,
        usable ranges, material digest) and returns the exact ``binding_token``
        a human must echo back in ``approval.human.binding_token``. It never
        infers, never creates an approval and adds no HTTP surface."""
        conn = self._connect()
        try:
            row = self._take_row(take_id, conn=conn)
            if row is None:
                raise AIError('TAKE_NOT_FOUND', take_id)
            ctx = self._approval_context(conn, row, approval)
            return {'status': 'verified', 'take_id': take_id,
                    'binding_token': ctx['token'], 'context': ctx['context'],
                    'needs_human_resolution': ctx['needs_resolution']}
        finally:
            conn.close()

    def _approval_context(self, conn, row, approval):
        """Shared validation of the current approval context. Returns digests,
        the binding token, evidence reviews and whether the latest evidence still
        needs explicit human resolution. Raises the specific blueprint failure
        codes on any gap (fail-closed, no partial context)."""
        if not isinstance(approval, dict):
            raise AIError('APPROVAL_CONTRACT_INVALID')
        media = json.loads(row['media_json'])
        evidence = approval.get('evidence')
        material = approval.get('material')
        if not isinstance(evidence, dict):
            raise AIError('APPROVAL_CONTRACT_INVALID')
        if not isinstance(material, dict):
            raise AIError('MATERIAL_DESCRIPTION_MISSING',
                          'manifest needs visual_description/actions/scene/'
                          'narration_targets/tags/audio_policy')
        duration = media.get('duration_sec')
        width, height = media.get('width'), media.get('height')
        # honest decode gate: ffprobe facts + nonzero video dimensions + a real
        # verified ffmpeg frame decode; audio-only/corrupt payloads stop here
        if media.get('metadata_status') != 'decoded' \
                or media.get('decode_status') != 'verified' \
                or isinstance(duration, bool) or not isinstance(duration, (int, float)) \
                or not math.isfinite(float(duration)) \
                or not isinstance(width, int) or not isinstance(height, int) \
                or width <= 0 or height <= 0:
            raise AIError('MEDIA_METADATA_UNVERIFIED',
                          'approval requires true decoded video metadata: video '
                          'stream with nonzero dimensions and a verified real '
                          'frame decode (audio-only/corrupt payloads never approve)')
        ranges = evidence.get('usable_ranges')
        if not isinstance(ranges, list) or not ranges:
            raise AIError('USABLE_RANGE_INVALID')
        for rng in ranges:
            if not isinstance(rng, dict):
                raise AIError('USABLE_RANGE_INVALID')
            start, end = rng.get('start_sec'), rng.get('end_sec')
            for value in (start, end):
                if isinstance(value, bool) or not isinstance(value, (int, float)) \
                        or not math.isfinite(value) or value < 0:
                    raise AIError('USABLE_RANGE_INVALID')
            if not start < end <= duration + 1e-6:
                raise AIError('USABLE_RANGE_INVALID',
                              'usable range must fit the true decoded duration')
        if not isinstance(evidence.get('refs'), list):
            raise AIError('APPROVAL_CONTRACT_INVALID')
        shot = json.loads(row['shot_json'])
        validated_refs = self._validate_approval_refs(evidence, shot)
        bound = self._bound_reviews(conn, row['take_id'])
        evidence_reviews = self._approval_evidence(row, bound, media)
        if row['video_path']:
            if _sha256_file(Path(row['video_path'])) != media.get('sha256'):
                raise AIError('MEDIA_DRIFT', 'media file changed since registration')
        context = {
            'take': {'take_id': row['take_id'], 'shot_id': row['shot_id'],
                     'shot_sha256': row['shot_sha256'],
                     'video_sha256': media.get('sha256'),
                     'media_sha256': _canonical_hash(media)},
            'reviews': [{'review_id': rec.get('review_id', rec.get('review_record_id')),
                         'source_sha256': sha,
                         'status': rec.get('status'),
                         'review_status': status}
                        for rec, sha, status in evidence_reviews],
            'refs': validated_refs,
            'usable_ranges': ranges,
            'material_sha256': _canonical_hash(material),
        }
        return {'context': context,
                'token': _canonical_hash(context),
                'needs_resolution': any(status == 'NEEDS_REVIEW'
                                        for _rec, _sha, status in evidence_reviews),
                'media': media, 'bound': bound, 'evidence_reviews': evidence_reviews,
                'validated_refs': validated_refs, 'ranges': ranges}

    def approve(self, take_id, approval):
        conn = self._connect()
        try:
            conn.isolation_level = None
            # the write transaction opens BEFORE the bound-review read and the
            # approval-exists check, so two concurrent approvals cannot both pass
            conn.execute('BEGIN IMMEDIATE')
            try:
                record = self._approve_locked(conn, take_id, approval)
                conn.commit()
                return record
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()

    def _approve_locked(self, conn, take_id, approval):
        row = self._take_row(take_id, conn=conn)
        if row is None:
            raise AIError('TAKE_NOT_FOUND', take_id)
        if row['mock'] or 'mock' in (row['provenance'] or '').lower():
            raise AIError('MOCK_NOT_APPROVABLE',
                          'mock/test units can never produce APPROVED')
        if conn.execute('SELECT 1 FROM approvals WHERE take_id = ?',
                        (take_id,)).fetchone():
            raise AIError('APPROVAL_EXISTS', 'approvals are immutable')
        ctx = self._approval_context(conn, row, approval)
        human = approval.get('human')
        if not isinstance(human, dict) or not all(
                isinstance(human.get(k), str) and human.get(k, '').strip()
                for k in ('confirmed_by', 'visual_scope', 'temporal_scope',
                          'confirmed_at')):
            raise AIError('HUMAN_CONFIRMATION_MISSING',
                          'human visual/temporal scope confirmation is mandatory')
        # confirmed_at must be a parseable ISO timestamp WITH timezone
        try:
            stamp = datetime.fromisoformat(human['confirmed_at'].replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('naive')
        except ValueError:
            raise AIError('HUMAN_CONFIRMATION_MISSING',
                          'confirmed_at must be a timezone-aware ISO timestamp')
        # the human must echo the EXACT pre-confirmed binding token and ranges:
        # approve() never fabricates or repairs the confirmation for the caller
        token = human.get('binding_token')
        if not isinstance(token, str) or not SHA_RE.fullmatch(token):
            raise AIError('HUMAN_BINDING_REQUIRED',
                          'human.binding_token must be the 64-hex token from '
                          'get_approval_context for the current state')
        if token != ctx['token']:
            raise AIError('HUMAN_BINDING_MISMATCH',
                          'binding token does not match the current take/review/'
                          'ref/range/material context')
        if human.get('confirmed_ranges') != ctx['ranges']:
            raise AIError('HUMAN_SCOPE_MISSING',
                          'human.confirmed_ranges must exactly equal the '
                          'evidence usable_ranges being approved')
        if ctx['needs_resolution']:
            if human.get('review_resolution') != 'HUMAN_RESOLVED_NEEDS_REVIEW':
                raise AIError('HUMAN_RESOLUTION_REQUIRED',
                              'NEEDS_REVIEW evidence requires an explicit human '
                              "resolution (human.review_resolution = "
                              "'HUMAN_RESOLVED_NEEDS_REVIEW')")
            resolution = human.get('resolution_evidence')
            if not isinstance(resolution, list) or not resolution:
                raise AIError('HUMAN_RESOLUTION_REQUIRED',
                              'resolution must carry the actual hash-bearing '
                              'evidence set a human checked')
            for item in resolution:
                if not (isinstance(item, dict) and isinstance(item.get('sha256'), str)
                        and SHA_RE.fullmatch(item['sha256'])):
                    raise AIError('HUMAN_RESOLUTION_REQUIRED',
                                  'each resolution_evidence entry needs a valid '
                                  'sha256 of what was actually checked')
        material = approval.get('material')
        if not isinstance(material.get('visual_description'), str) \
                or not material.get('visual_description', '').strip():
            raise AIError('MATERIAL_DESCRIPTION_MISSING')
        actions = material.get('actions')
        if not isinstance(actions, list) or not actions or \
                not all(isinstance(a, str) and a for a in actions):
            raise AIError('MATERIAL_DESCRIPTION_MISSING')
        for key in ('narration_targets', 'tags'):
            if not isinstance(material.get(key), list) or \
                    not all(isinstance(v, str) for v in material.get(key, [])):
                raise AIError('MATERIAL_DESCRIPTION_MISSING')
        if not isinstance(material.get('audio_policy'), str) \
                or not material.get('audio_policy', '').strip():
            raise AIError('MATERIAL_DESCRIPTION_MISSING')
        if not isinstance(material.get('scene'), str):
            raise AIError('MATERIAL_DESCRIPTION_MISSING')
        media = ctx['media']
        validated_refs = ctx['validated_refs']
        bound = ctx['bound']
        # the binding pins exact take/video/media/refs/evidence hashes so the
        # human confirmation is anchored to immutable content, not strings
        binding = {'take_sha256': row['shot_sha256'],
                   'review_sha256': [sha for _rec, sha in bound],
                   'video_sha256': media.get('sha256'),
                   'media_sha256': _canonical_hash(media),
                   'refs_sha256': _canonical_hash(validated_refs),
                   'evidence_sha256': _canonical_hash(
                       {'usable_ranges': ctx['ranges'],
                        'refs': [{'ref_id': r['ref_id'], 'sha256': r['sha256']}
                                 for r in validated_refs]}),
                   'shot_id': row['shot_id'],
                   'evidence_review_ids': [rec.get('review_id', rec.get('review_record_id'))
                                           for rec, _sha, _status
                                           in ctx['evidence_reviews']]}
        record = {'approval_id': 'apr-' + uuid.uuid4().hex[:12],
                  'take_id': take_id, 'approved_at': _utc(),
                  'approval_sha256': _canonical_hash({
                      'human': human, 'evidence': approval.get('evidence'),
                      'material': material, 'binding': binding}),
                  'human': human, 'evidence': approval.get('evidence'),
                  'material': material,
                  'binding': binding,
                  'contract': {'manifest_schema': MANIFEST_SCHEMA_VERSION,
                               'review_contract': 'user-original-request §9 reviewer/v1',
                               'store_policy': 'commodity-material-ai-v1/approval/v2'}}
        # the FULL manifest candidate is built and strictly schema-validated
        # BEFORE the approval row is inserted: a failure leaves no broken pointer
        candidate = self._manifest_record(conn, row, record)
        try:
            jsonschema.Draft7Validator(SCHEMAS['manifest']).validate(candidate)
        except jsonschema.ValidationError as exc:
            raise AIError('APPROVAL_MANIFEST_INVALID', str(exc.message)[:300])
        conn.execute(
            'INSERT INTO approvals (approval_id, take_id, approval_json,'
            ' approved_at) VALUES (?,?,?,?)',
            (record['approval_id'], take_id, _canonical_json(record),
             record['approved_at']))
        return record

    def _manifest_record(self, conn, row, approval):
        """Build the full approved-material manifest record from the CURRENT
        store state (shared by approve's pre-insert validation and export)."""
        approval = approval if not isinstance(approval, str) else json.loads(approval)
        binding = approval['binding']
        media = json.loads(row['media_json'])
        bound = self._bound_reviews(conn, row['take_id'])
        usage_rows = conn.execute(
            'SELECT * FROM usages WHERE take_id = ? ORDER BY created_at, usage_id',
            (row['take_id'],)).fetchall()
        compatible = [rec for rec, _sha in bound
                      if _approval_status_value(rec) in _APPROVAL_COMPATIBLE_STATUSES
                      and rec.get('source') in REAL_REVIEW_SOURCES
                      and rec.get('status') in ('verified', 'partial')]
        review_record_id = (compatible[-1].get('review_id')
                            or compatible[-1].get('review_record_id')
                            or bound[-1][0].get('review_id'))
        material = approval['material']
        return {
            'schema_version': MANIFEST_SCHEMA_VERSION,
            'manifest_type': 'approved_material',
            'exported_at': _utc(),
            'asset_id': row['take_id'], 'shot_id': row['shot_id'],
            'product_id': row['product_id'], 'version': row['version'],
            'video_path': media['video_path'], 'file_hash': media['sha256'],
            'duration_sec': media['duration_sec'],
            'usable_ranges': approval['evidence']['usable_ranges'],
            'visual_description': material['visual_description'],
            'actions': material['actions'], 'scene': material['scene'],
            'narration_targets': material['narration_targets'],
            'tags': material['tags'], 'audio_policy': material['audio_policy'],
            'review_status': 'APPROVED', 'review_record_id': review_record_id,
            'batch_id': row['batch_id'], 'provenance': row['provenance'],
            'mock': False,
            'reviews': [rec for rec, _sha in bound],
            'usage': [dict(u) for u in usage_rows], 'approval': approval,
            'binding': binding, 'h3_invoked': False, 'paid_h3_calls': 0,
            'vbench': {'status': 'not_run',
                       'note': 'VBench scoring not executed this round '
                               '(lightweight weights cached, two 7B models absent); '
                               'no fake scores'},
        }

    def export_manifest(self, take_id):
        conn = self._connect()
        try:
            row = self._take_row(take_id, conn=conn)
            if row is None:
                raise AIError('TAKE_NOT_FOUND', take_id)
            approval_row = conn.execute(
                'SELECT * FROM approvals WHERE take_id = ?', (take_id,)).fetchone()
            if approval_row is None:
                raise AIError('EXPORT_REQUIRES_APPROVAL', take_id)
            approval = json.loads(approval_row['approval_json'])
            binding = approval['binding']
            shot_sha = hashlib.sha256(row['shot_json'].encode('utf-8')).hexdigest()
            if shot_sha != binding['take_sha256']:
                raise AIError('STALE_APPROVAL', 'take content changed after approval')
            # every stored review row is re-hashed against its IMMUTABLE source
            # payload: legacy rows without one are explicitly blocked, tampered
            # payloads refuse, and store stamps are never guessed away
            source_rows = conn.execute(
                'SELECT review_id, review_sha256, source_payload, source_sha256'
                ' FROM reviews WHERE take_id = ? ORDER BY created_at, review_id',
                (take_id,)).fetchall()
            for source_row in source_rows:
                if source_row['source_payload'] is None \
                        or source_row['source_sha256'] is None:
                    raise AIError('REVIEW_RECORD_DRIFT',
                                  'legacy review record without an immutable '
                                  'source payload cannot be re-verified')
                if hashlib.sha256(source_row['source_payload'].encode('utf-8')
                                  ).hexdigest() != source_row['source_sha256'] \
                        or source_row['source_sha256'] != source_row['review_sha256']:
                    raise AIError('REVIEW_RECORD_DRIFT',
                                  'stored review source payload no longer matches '
                                  'its recorded hash')
            bound = self._bound_reviews(conn, take_id)
            current_hashes = [sha for _rec, sha in bound]
            if current_hashes != binding['review_sha256']:
                raise AIError('STALE_APPROVAL',
                              'bound review set changed after approval')
            # approval payload re-hash: approval_json fields vs approval_sha256
            if _canonical_hash({'human': approval.get('human'),
                                'evidence': approval.get('evidence'),
                                'material': approval.get('material'),
                                'binding': binding}) != approval.get('approval_sha256'):
                raise AIError('APPROVAL_RECORD_DRIFT',
                              'stored approval payload no longer matches its '
                              'approval_sha256')
            media = json.loads(row['media_json'])
            if media.get('sha256') != binding.get('video_sha256'):
                raise AIError('MEDIA_DRIFT', 'registered media hash diverged from the '
                                              'approval binding')
            # the FULL normalized media record is pinned, not just the video hash
            if binding.get('media_sha256') is not None \
                    and _canonical_hash(media) != binding['media_sha256']:
                raise AIError('MEDIA_RECORD_DRIFT',
                              'registered media metadata changed after approval')
            if row['video_path']:
                if _sha256_file(Path(row['video_path'])) != binding['video_sha256']:
                    raise AIError('MEDIA_DRIFT', 'media file changed after approval')
            # reference currentness: ALWAYS recompute the fresh normalized refs
            # and compare to the pinned refs_sha256 binding (with or without a
            # supplied sha) — drift anywhere refuses
            if binding.get('refs_sha256') is not None:
                revalidated = self._validate_approval_refs(approval.get('evidence') or {},
                                                           json.loads(row['shot_json']))
                if _canonical_hash(revalidated) != binding['refs_sha256']:
                    raise AIError('REF_DRIFT',
                                  'reference set/content changed after approval')
            manifest = self._manifest_record(conn, row, approval)
        finally:
            conn.close()
        try:
            jsonschema.Draft7Validator(SCHEMAS['manifest']).validate(manifest)
        except jsonschema.ValidationError as exc:
            raise AIError('MANIFEST_SCHEMA_INVALID', str(exc.message)[:300])
        return manifest

# ---------------------------------------------------------------------------
# dry-run orchestration (zero H3, zero external reads, explicit Mock chain)
# ---------------------------------------------------------------------------

def _vbench_summary():
    caps_path = ROOT / 'config' / 'vbench_capabilities.json'
    try:
        caps = json.loads(caps_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'status': 'not_run', 'reason': 'capability record unavailable'}
    metrics = []
    for metric in caps.get('priority_metrics', []):
        raw = str(metric.get('execution_status', ''))
        metrics.append({'name': metric.get('name'),
                        'execution_status': raw.split(' ', 1)[0] or 'not_run',
                        'detail': raw})
    return {
        'status': 'not_run',
        'metrics': metrics,
        'weights': 'lightweight set cached 2026-10-11; two 7B models not downloaded',
        'weights_downloads': 0,
        'note': 'env import-ready is not scoring readiness; scoring stays not_run with '
                'no CPU fallback and no fake scores (Human_Anatomy blocked on mmcv, '
                'Human_Identity blocked on retinaface per capability record)',
    }


_DRYRUN_REPAIR_GATE = {'max_repairs_per_shot': 2, 'regenerate_whole_batch': False,
                       'paid_budget': 0}


def _known_blocked_claude(role, stage):
    """The real exact-model call was already attempted and recorded this round
    (OAuth expired). Per the no-auth-retry policy it is NOT re-issued: stages
    carry this explicit known-blocked record instead of a fabricated output."""
    return {'status': 'blocked', 'error_code': 'AUTH_EXPIRED', 'source': 'claude',
            'role': role, 'stage': stage, 'model_id': MODEL_ID,
            'known_blocked': True, 'llm_invoked': False,
            'evidence_ref': 'runtime/evidence/commodity-material-ai-v1/'
                            'claude-minimal-precheck.json',
            'note': 'real exact-model call already attempted this round (OAuth '
                    'expired, recorded evidence); no automatic auth retry'}


def _normalize_new_contract_shot(shot, case):
    """Deterministic normalization of a legacy planner shot into the user §5 NEW
    shot contract (full field list) for the Mock fallback chain. Normalizes
    fields only — it never invents evidence or model output, and the Mock
    provenance is carried by the record around it, not hidden inside it."""
    product = case.get('product') or {}
    refs = [{'ref_id': r.get('ref_id'), 'role': r.get('role'), 'sha256': r.get('sha256')}
            for r in case.get('refs', [])
            if isinstance(r, dict) and isinstance(r.get('ref_id'), str)]
    action = shot.get('action') if isinstance(shot.get('action'), dict) else {}
    person = case.get('person_appearance') \
        if isinstance(case.get('person_appearance'), dict) else {}
    character_refs = []
    if isinstance(person.get('ref_id'), str):
        character_refs.append({'ref_id': person['ref_id'],
                               'role': person.get('role') or 'person_appearance'})
    product_refs = [r for r in refs
                    if 'product' in str(r.get('role') or '').lower()
                    or 'demo' in str(r.get('role') or '').lower()]
    audio = shot.get('audio') if isinstance(shot.get('audio'), dict) else {}
    return {
        'material_id': 'mat-' + str(shot.get('shot_id') or 'unknown'),
        'shot_id': str(shot.get('shot_id') or ''),
        'product_id': str(shot.get('product_id') or product.get('product_id') or ''),
        'source_refs': refs,
        'narration_target': str(shot.get('narration') or ''),
        'visual_purpose': str(shot.get('purpose') or ''),
        'scene': str(case.get('location_note') or ''),
        'character_refs': character_refs,
        'product_refs': product_refs,
        'action_start': action.get('start'),
        'action_process': [str(step) for step in (action.get('process') or [])],
        'action_end': action.get('end'),
        'camera_and_framing': '；'.join(
            str(part) for part in (shot.get('framing'), shot.get('camera'),
                                   shot.get('movement')) if part),
        'duration_sec': shot.get('duration_sec'),
        'required_visuals': [str(item) for item in (shot.get('must') or [])],
        'forbidden_visuals': [str(item) for item in (shot.get('must_not') or [])],
        'audio_policy': str(audio.get('policy')
                            or (case.get('audio_defaults') or {}).get('policy')
                            or 'discard_allowed'),
        'generation_prompt': None,
    }


def _mock_writer_output(normalized):
    """Deterministic Mock-fallback writer spec: full NEW contract shape with an
    explicit mock generation prompt and honest placeholder reference
    requirements. Clearly Mock at the record level; never a real model output."""
    writer = dict(normalized)
    writer['role'] = 'writer'
    writer['generation_prompt'] = (
        '【Mock 数据链路回退】竖屏桌面演示：%s：%s。此提示词由确定性规则生成，'
        '仅演示字段流转，非真实模型输出。'
        % (normalized.get('visual_purpose') or '商品演示',
           '；'.join(normalized.get('action_process') or []) or '完整展示商品'))
    writer['reference_requirements'] = [
        {'ref_id': ref['ref_id'],
         'requirement': 'Mock 链路占位：真实参考要求需 Writer 模型产出'}
        for ref in normalized.get('source_refs', [])]
    writer['notes'] = 'Mock fallback：Writer 模型本轮不可用（AUTH_EXPIRED 已证明），' \
                      '确定性归一化输出，非真实模型输出'
    return writer


def _client_record(raw, stage, default_code='CLIENT_ERROR'):
    """Defensive normalization of an injected client's return value: dict + strict
    JSON only; an exception type name is recorded, never its value."""
    if isinstance(raw, dict):
        try:
            _reject_nonfinite(raw)
            return json.loads(_canonical_json(raw))
        except (ValueError, TypeError):
            return {'status': 'blocked', 'error_code': 'CLIENT_RECORD_NOT_STRICT_JSON',
                    'stage': stage}
    return {'status': 'blocked', 'error_code': default_code, 'stage': stage}


def _next_free_version(store, shot_id):
    """First unused version number for a shot: legacy rows are never touched;
    an identical spec already registered is reused, a changed contract registers
    the NEXT version instead of colliding with the old one."""
    conn = store._connect()
    try:
        row = conn.execute('SELECT MAX(version) AS v FROM takes WHERE shot_id = ?',
                           (shot_id,)).fetchone()
    finally:
        conn.close()
    return (row['v'] or 0) + 1


def _register_or_reuse(store, executed_spec, batch_id='dryrun',
                       provenance='mock_h3_dry_run'):
    """Idempotent Mock take registration for the EXECUTED (writer) spec: when a
    take row with byte-identical shot content already exists it is reused; a
    changed contract takes the next free version. Existing rows are never
    modified or deleted (append-only root store with legacy rows preserved)."""
    shot_id = executed_spec.get('shot_id')
    spec_json = _canonical_json(executed_spec)
    spec_sha = hashlib.sha256(spec_json.encode('utf-8')).hexdigest()
    conn = store._connect()
    try:
        row = conn.execute(
            'SELECT take_id, shot_sha256, version FROM takes WHERE shot_id = ?'
            ' ORDER BY version', (shot_id,)).fetchall()
    finally:
        conn.close()
    for existing in row:
        if existing['shot_sha256'] == spec_sha:
            return existing['take_id'], True  # identical spec: idempotent reuse
    version = _next_free_version(store, shot_id)
    take = store.register_take(shot=executed_spec, video_path=None, version=version,
                               batch_id=batch_id, provenance=provenance)
    return take['take_id'], False


def run_material_case(root=ROOT, clients=None):
    """Full per-case chain with per-stage persistence and real dataflow:
    Director -> Writer -> A review -> Mock H3 (no asset) -> B review -> Reviewer.

    clients is an OPTIONAL injection seam for UNIT fakes only (keys: director,
    writer, review_a, review_b, reviewer, bridge). The default performs NO real
    model calls at all: Claude stages carry the explicit known-blocked
    AUTH_EXPIRED record (already proven this round, never retried), Clef stages
    fail closed without an owned inference lifecycle, B reviews without an
    eligible video are NEEDS_REVIEW, and every model gap is covered by clearly
    marked Mock fallbacks normalized to the full NEW shot contract. When clients
    ARE injected, model outputs actually flow: verified Director shots drive the
    chain, the verified Writer spec is what A reviews, Mock H3, B reviews, the
    registered take and the Reviewer see — and the Reviewer additionally
    receives the raw A/B decisions, VBench state, references and take history.
    Zero H3, zero paid budget, no approvals; per-shot failure is isolated (a
    single shot never regenerates the batch, max 2 repairs per shot)."""
    root = _validate_root_argument(root)  # canonical guard BEFORE any case read
    generated_at = _utc()
    run_id = 'run-' + uuid.uuid4().hex[:12]
    case_path = root / 'cases' / 'koc-dragonfruit-dryrun' / 'case.json'
    case = json.loads(case_path.read_text(encoding='utf-8'))
    from agents import product_storyboards as ps  # deterministic local engine (no LLM)
    pipeline = ps.run_pipeline(case)
    verdicts = {r['shot_id']: r for r in pipeline['review']['shots']}
    store = MaterialStore(root=root)
    injected = dict(clients) if isinstance(clients, dict) else {}

    def client(name):
        fn = injected.get(name)
        return fn if callable(fn) else None

    def call_client(name, *args, **kwargs):
        fn = client(name)
        if fn is None:
            return None
        try:
            return _client_record(fn(*args, **kwargs), name)
        except Exception as exc:  # injected seam may raise; type only, never value
            return {'status': 'blocked', 'error_code': 'CLIENT_ERROR',
                    'error': '%s raised %s' % (name, type(exc).__name__), 'stage': name}

    def persist(stage, record, shot_id=None):
        stored = store.append_stage_record(run_id, stage, record, shot_id=shot_id,
                                           mode='dry_run')
        return stored['record']

    sanitized_case_refs = [{'ref_id': r.get('ref_id'), 'role': r.get('role'),
                            'sha256': r.get('sha256')}
                           for r in case.get('refs', []) if isinstance(r, dict)]

    # ---- Director (one record for the whole case) ---------------------------
    director_payload = {
        'case_id': case.get('case_id'),
        'product': {'product_id': (case.get('product') or {}).get('product_id'),
                    'name': (case.get('product') or {}).get('name')},
        'refs': sanitized_case_refs,
        'note': 'dry-run Director stage; UNIT injection seam only'}
    director_record = call_client('director', director_payload)
    if director_record is None:
        director_record = _known_blocked_claude('director', 'director')
    director_shots = None
    if director_record.get('status') == 'verified':
        structured = director_record.get('structured_output')
        try:
            jsonschema.Draft7Validator(SCHEMAS['director']).validate(structured)
            director_shots = list(structured.get('shots') or [])
            director_record['shots_accepted'] = len(director_shots)
        except jsonschema.ValidationError as exc:
            # the REAL failure record is kept — never overwritten by a generic
            # known-blocked stub — and the chain falls back to the local plan
            director_record.update(status='blocked', error_code='SCHEMA_INVALID',
                                   schema_error=str(exc.message)[:200])
    director_record['run_id'] = run_id
    director_record = persist('director', director_record)
    director_ok = director_record.get('status') == 'verified' and bool(director_shots)

    # the iteration source follows the dataflow: verified Director shots drive
    # the chain; otherwise the deterministic legacy plan (clearly normalized)
    if director_ok:
        shot_sources = [('director', shot) for shot in director_shots]
    else:
        shot_sources = [('legacy', shot) for shot in pipeline['plan']['shots']
                        if not shot.get('counterexample_of')]

    shots_summary = []
    registered = []
    failures = []
    executed_specs = []
    stage_counts = {'writer_model': 0, 'writer_mock_fallback': 0, 'review_a': 0,
                    'h3_mock': 0, 'review_b': 0, 'reviewer_model': 0,
                    'reviewer_mock_fallback': 0, 'skipped': 0}
    for source_kind, shot in shot_sources:
        shot_id = shot.get('shot_id')
        if source_kind == 'legacy' and shot.get('counterexample_of'):
            continue
        entry = {'shot_id': shot_id, 'purpose': shot.get('purpose'),
                 'version': shot.get('version', verdicts.get(shot_id, {}).get('version', 1)),
                 'verdict': verdicts.get(shot_id, {}).get('verdict'),
                 'source': source_kind,
                 'spec_provenance': None,
                 'mock': True, 'take_id': None,
                 'media': {'video_path': None, 'status': 'no_video'},
                 'h3_invoked': False, 'h3_linkage': 'mock_h3_dry_run',
                 'schema': 'user-§5-new-shot-contract (mock fallback normalization)'}
        try:
            if source_kind == 'director':
                normalized = dict(shot)  # already the full NEW contract
                jsonschema.Draft7Validator(SHOT_SPEC_DEF).validate(normalized)
            else:
                normalized = _normalize_new_contract_shot(shot, case)
                jsonschema.Draft7Validator(SHOT_SPEC_DEF).validate(normalized)

            # ---- Writer -----------------------------------------------------
            # a blocked Director propagates: the model Writer stage is explicitly
            # SKIPPED (never called, no fabricated model output) and the chain
            # continues on the deterministic Mock fallback
            if not director_ok:
                writer_record = {'stage': 'writer', 'status': 'skipped',
                                 'reason': 'Director blocked: no fabricated model '
                                           'output; deterministic Mock fallback '
                                           'used instead',
                                 'run_id': run_id, 'shot_id': shot_id}
            else:
                writer_record = call_client('writer', dict(normalized))
            executed_spec = None
            if director_ok and writer_record is not None                     and writer_record.get('status') == 'verified':
                structured = writer_record.get('structured_output')
                try:
                    jsonschema.Draft7Validator(SCHEMAS['writer']).validate(structured)
                    executed_spec = structured  # the Writer spec IS what runs
                    writer_record['shot_id'] = shot_id
                    stage_counts['writer_model'] += 1
                except jsonschema.ValidationError as exc:
                    # keep the real SCHEMA_INVALID record; fall back to Mock
                    writer_record.update(status='blocked',
                                         error_code='SCHEMA_INVALID',
                                         schema_error=str(exc.message)[:200])
            if executed_spec is None:
                if writer_record is None:
                    writer_record = _known_blocked_claude('writer', 'writer')
                elif writer_record.get('status') != 'skipped':
                    writer_record.setdefault('shot_id', shot_id)
                persist('writer', writer_record, shot_id=shot_id)
                if writer_record.get('status') == 'skipped':
                    stage_counts['skipped'] += 1
                executed_spec = _mock_writer_output(normalized)
                fallback = {'stage': 'writer_fallback', 'status': 'mock', 'mock': True,
                            'source': 'deterministic-local-v1',
                            'engine': 'deterministic-rules-v1', 'llm_invoked': False,
                            'provenance': 'mock_fallback', 'shot_id': shot_id,
                            'structured_output': executed_spec}
                persist('writer_fallback', fallback, shot_id=shot_id)
                stage_counts['writer_mock_fallback'] += 1
                entry['spec_provenance'] = 'mock_fallback'
            else:
                persist('writer', writer_record, shot_id=shot_id)
                entry['spec_provenance'] = 'model_writer'
            executed_specs.append(executed_spec)

            # ---- register the Mock take on the EXECUTED spec (no asset) -------
            take_id, reused = _register_or_reuse(store, executed_spec)
            entry['take_id'] = take_id
            entry['already_registered'] = reused
            if not reused:
                registered.append(take_id)

            # ---- A review (pre-generation) -----------------------------------
            questions = _PHASE_QUESTIONS['A']
            bounds = {'input_bounds': {
                'state_sha256': _canonical_hash({'spec': executed_spec, 'phase': 'A'}),
                'questions_sha256': _canonical_hash(questions),
                'input_scope': 'text_only',
                'policy': 'commodity-material-ai-v1/clef-review/v2'}}
            review_a = call_client('review_a', dict(executed_spec))
            if review_a is None:
                review_a = {'status': 'blocked', 'source': 'clef', 'phase': 'A',
                            'shot_id': shot_id, 'error_code':
                            'INFERENCE_LIFECYCLE_REQUIRED', 'llm_invoked': False,
                            'visual_status': 'not_applicable_pre_generation',
                            'review_status': 'TECHNICAL_ERROR',
                            'reason': 'dry-run performs no real inference: real A '
                                      'review requires a caller-owned exclusive '
                                      'lifecycle', **bounds}
            else:
                review_a.setdefault('phase', 'A')
                review_a.setdefault('source', 'clef')
                review_a.setdefault('shot_id', shot_id)
                review_a.update(**bounds)
            review_a = persist('review_a', review_a, shot_id=shot_id)
            store.append_review(take_id, {
                'source': review_a.get('source', 'clef'),
                'status': review_a.get('status', 'blocked'),
                'error_code': review_a.get('error_code'),
                'phase': 'A', 'shot_id': shot_id,
                'mock': False, 'llm_invoked': bool(review_a.get('llm_invoked')),
                'review_status': review_a.get('review_status', 'TECHNICAL_ERROR'),
                'input_scope': bounds['input_bounds']['input_scope'],
                'input_bounds': bounds['input_bounds'],
                'run_id': run_id}, mode='dry_run')
            stage_counts['review_a'] += 1

            # ---- Mock H3 (explicitly no asset, no paid call) -------------------
            h3_record = {'stage': 'h3', 'status': 'mock', 'mock': True,
                         'h3_invoked': False, 'paid_h3_calls': 0, 'asset': None,
                         'shot_id': shot_id, 'provenance': 'mock_h3_dry_run',
                         'input_spec_sha256': _canonical_hash(executed_spec),
                         'note': 'Mock H3 数据链路：仅演示字段流转，无真实生成、'
                                 '无外部读取、无资产'}
            persist('h3', h3_record, shot_id=shot_id)
            stage_counts['h3_mock'] += 1

            # ---- B review (post-generation): no eligible video ----------------
            review_b = call_client('review_b', dict(executed_spec),
                                   {'take_id': take_id, 'mock': True})
            if review_b is None:
                review_b = {'status': 'blocked', 'source': 'local-policy',
                            'phase': 'B', 'shot_id': shot_id,
                            'error_code': 'NO_ELIGIBLE_VIDEO',
                            'review_status': 'NEEDS_REVIEW',
                            'visual_status': 'UNVERIFIED',
                            'continuous_motion_status': 'NEEDS_REVIEW',
                            'llm_invoked': False,
                            'reason': 'no eligible video this round (Mock H3 chain '
                                      'produces no asset); text review can never '
                                      'confirm visuals'}
            else:
                review_b.setdefault('phase', 'B')
                review_b.setdefault('shot_id', shot_id)
                review_b.setdefault('review_status', 'NEEDS_REVIEW')
            review_b = persist('review_b', review_b, shot_id=shot_id)
            store.append_review(take_id, {
                'source': review_b.get('source', 'local-policy'),
                'status': review_b.get('status', 'blocked'),
                'error_code': review_b.get('error_code'),
                'phase': 'B', 'shot_id': shot_id, 'mock': False,
                'llm_invoked': bool(review_b.get('llm_invoked')),
                'review_status': review_b.get('review_status'),
                'visual_status': 'UNVERIFIED',
                'continuous_motion_status': 'NEEDS_REVIEW',
                'input_scope': 'text+structured_technical_metadata',
                'run_id': run_id}, mode='dry_run')
            stage_counts['review_b'] += 1

            # ---- Reviewer (per shot, with the full evidence context) ----------
            reviewer_evidence = {
                'take_id': take_id, 'shot_id': shot_id, 'mock': True,
                'spec': executed_spec,
                'a_decision': {k: review_a.get(k) for k in
                               ('status', 'error_code', 'review_status', 'verdict',
                                'probabilities', 'input_scope')},
                'b_decision': {k: review_b.get(k) for k in
                               ('status', 'error_code', 'review_status', 'verdict',
                                'probabilities', 'input_scope')},
                'vbench': _vbench_summary(),
                'refs': sanitized_case_refs,
                'history': [{'review_id': r.get('review_id'),
                             'phase': r.get('phase'),
                             'review_status': r.get('review_status')}
                            for r in store.history(take_id)['reviews']]}
            reviewer_record = call_client('reviewer', dict(executed_spec),
                                          reviewer_evidence)
            if reviewer_record is not None \
                    and reviewer_record.get('status') == 'verified':
                structured = reviewer_record.get('structured_output')
                try:
                    jsonschema.Draft7Validator(SCHEMAS['reviewer']).validate(structured)
                    stage_counts['reviewer_model'] += 1
                except jsonschema.ValidationError as exc:
                    # keep the real SCHEMA_INVALID record, then Mock fallback
                    reviewer_record.update(status='blocked',
                                           error_code='SCHEMA_INVALID',
                                           schema_error=str(exc.message)[:200])
                    persist('reviewer', reviewer_record, shot_id=shot_id)
                    reviewer_record = None
            if reviewer_record is None:
                reviewer_record = _known_blocked_claude('reviewer', 'reviewer')
                reviewer_record['shot_id'] = shot_id
                persist('reviewer', reviewer_record, shot_id=shot_id)
                fallback = {'source': 'deterministic-local-v1', 'status': 'mock',
                            'mock': True, 'engine': 'deterministic-rules-v1',
                            'llm_invoked': False, 'phase': 'B', 'shot_id': shot_id,
                            'verdict': entry['verdict'],
                            'review_status': entry['verdict'],
                            'problems': verdicts.get(shot_id, {}).get('problems', []),
                            'provenance': 'mock_fallback',
                            'run_id': run_id}
                persist('reviewer_fallback', fallback, shot_id=shot_id)
                store.append_review(take_id, {
                    'source': 'deterministic-local-v1', 'status': 'mock',
                    'mock': True, 'engine': 'deterministic-rules-v1',
                    'llm_invoked': False, 'phase': 'B', 'shot_id': shot_id,
                    'verdict': entry['verdict'], 'review_status': entry['verdict'],
                    'problems': verdicts.get(shot_id, {}).get('problems', []),
                    'run_id': run_id}, mode='dry_run')
                stage_counts['reviewer_mock_fallback'] += 1
            else:
                reviewer_record.setdefault('shot_id', shot_id)
                persist('reviewer', reviewer_record, shot_id=shot_id)
                store.append_review(take_id, {
                    'source': reviewer_record.get('source', 'claude'),
                    'status': reviewer_record.get('status'),
                    'llm_invoked': bool(reviewer_record.get('llm_invoked')),
                    'phase': 'B', 'shot_id': shot_id,
                    'structured_output': reviewer_record.get('structured_output'),
                    'review_status': (reviewer_record.get('structured_output')
                                      or {}).get('review_status'),
                    'run_id': run_id}, mode='dry_run')
            shots_summary.append(entry)
        except (AIError, jsonschema.ValidationError, ValueError, OSError,
                KeyError, TypeError) as exc:
            # per-shot failure isolation: record and continue, never regenerate
            # the whole batch; the repair gate stays max-2 per shot, budget 0
            failures.append({'shot_id': shot_id,
                             'error_code': getattr(exc, 'code', type(exc).__name__)})
            entry['error_code'] = getattr(exc, 'code', type(exc).__name__)
            shots_summary.append(entry)
            continue
    # ---- optional external-node bridge (default: explicitly not_run) ---------
    bridge_client = client('bridge')
    if bridge_client is None:
        bridge_record = {'stage': 'bridge', 'status': 'not_run',
                         'run_id': run_id,
                         'reason': 'bridge callback not supplied; dry-run performs '
                                   'no external node mutations by default'}
    else:
        bridge_record = call_client('bridge', {'run_id': run_id,
                                               'shots': executed_specs}) or             {'stage': 'bridge', 'status': 'blocked', 'error_code': 'CLIENT_ERROR'}
        bridge_record.setdefault('stage', 'bridge')
    persist('bridge', bridge_record)
    counterexamples = [
        {'shot_id': ce['shot_id'], 'exercising': ce['exercising'],
         'verdict': verdicts.get(ce['shot_id'], {}).get('verdict'),
         'problems': [p.get('code') for p in
                      verdicts.get(ce['shot_id'], {}).get('problems', [])]}
        for ce in case.get('counterexamples', [])]
    claude_section = {'status': 'blocked', 'model_id': MODEL_ID,
                      'real_call': 'known_blocked_not_reissued',
                      'known_blocked': True,
                      'director': {'status': director_record.get('status'),
                                   'error_code': director_record.get('error_code'),
                                   'known_blocked': director_record.get('known_blocked', True)},
                      'contracts': {'director': 'shot-spec §5 (new field set)',
                                    'writer': 'H3-executable spec + reference '
                                              'requirements',
                                    'reviewer': 'review decision record §9'},
                      'note': 'AUTH_EXPIRED 已在本轮真实调用中证明（recorded '
                              'evidence）；按不重试认证策略不再发起真实调用，'
                              '各阶段携带显式 known_blocked 记录与 Mock 回退'}
    clef_section = {'status': 'blocked', 'error_code': 'NO_INFERENCE_IN_DRY_RUN',
                    'probe_policy': 'probe() is health-only by default; decisions '
                                    'require an owned exclusive lifecycle',
                    'a_reviews': stage_counts['review_a'],
                    'b_reviews': stage_counts['review_b'],
                    'note': 'dry-run 不做任何 Clef 推理：A 记录为 lifecycle 缺失的'
                            '显式 blocked；B 无合格视频为 NEEDS_REVIEW'}
    product = case.get('product') or {}
    refs = [{'ref_id': r.get('ref_id'), 'role': r.get('role'),
             'sha256': r.get('sha256'), 'test_only': bool(r.get('test_only')),
             'external_access': False} for r in case.get('refs', [])
            if isinstance(r, dict)]
    return {
        'schema': 'commodity-material-ai-v1/dry-run-summary/v3',
        'status': 'partial', 'run_mode': 'dry_run', 'generated_at': generated_at,
        'run_id': run_id,
        'chain': ['director', 'writer', 'review_a', 'h3_mock', 'review_b',
                  'reviewer'],
        'director_source': 'model_director_shots' if director_ok else 'legacy_plan_normalized',
        'stage_counts': stage_counts,
        'repair_gate': dict(_DRYRUN_REPAIR_GATE),
        'h3_invoked': False, 'paid_h3_calls': 0,
        'h3': {'invoked': False, 'paid_calls': 0, 'status': 'mock',
               'linkage': 'mock_h3_dry_run',
               'note': 'Mock H3 数据链路：仅演示字段流转，无真实生成、无外部读取'},
        'claude': claude_section, 'clef': clef_section,
        'vbench': _vbench_summary(),
        'case': {'case_id': case.get('case_id'),
                 'test_flag': bool(case.get('test_flag')),
                 'product': {'product_id': product.get('product_id'),
                             'name': product.get('name'),
                             'identity_verified': bool(product.get('identity_verified'))}},
        'refs': refs, 'shots': shots_summary, 'counterexamples': counterexamples,
        'registered_take_ids': registered, 'registration_failures': failures,
        'notes': [
            '外部 refs 仅作为不读取的文字标识（ref_id/sha256），未访问任何外部路径',
            '无本轮允许读取的原视频：不伪造视频，media.status=no_video，B 审核为 '
            'NEEDS_REVIEW（无合格视频）',
            'Mock 链路素材不可批准（MOCK_NOT_APPROVABLE）',
            '每阶段记录（Director/Writer/A/B/H3/Reviewer/bridge）已按 run_id 持久化到 '
            'stage_records（mode=dry_run 非影子）并进入 history(take_id)；影子模式仍经 '
            'CLI shadow_review 追加（mode=shadow_review，不改批准绑定）',
            '单镜头失败隔离：失败镜头记录后继续，不重生成整批；每镜头最多 2 次修复，'
            '付费预算 0',
            '真实 Claude/Clef 状态独立记录；本轮 dry-run 不发起任何真实推理',
            'Mock 回退镜头使用完整用户新合同（material_id…generation_prompt）字段集，'
            'provenance=mock_fallback 与真实输出严格分离；变更后的新合同镜头登记 '
            '下一个空闲版本，绝不改写既有行',
        ],
    }


def run_dry_run(root=ROOT, clients=None):
    """Backward-compatible wrapper: the CLI dry-run entry drives the full
    run_material_case chain. Zero H3, zero real model inference, no approvals."""
    return run_material_case(root=root, clients=clients)
