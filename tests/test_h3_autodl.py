"""h3-autodl-v1 tests (UNIT ONLY — no real network POST, no paid path executes here).

Covers: private dual-instance runtime loading with lab ROOT binding + source-hash pin,
PASS-shot binding verification (RETURN/stale-hash/unknown-duration/3D/missing-media
fail closed), video-reference rejection, duration honesty (no silent rounding),
independent 5s TEST intent + local PASS re-review, template_pack/X-silent mapping that
the ORIGINAL runtime itself validates end-to-end (load_project → prepare_bundle →
validate_schema against the local z0903 schema), and the guarded-submit authorization
chain delegating to a stub runtime transport (clearly labelled, never the real client).
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import h3_autodl as h3  # noqa: E402
from agents import product_storyboards as ps  # noqa: E402

CASE_DIR = ROOT / 'cases' / 'koc-dragonfruit-dryrun'
LOCAL_SCHEMA = json.loads(Path('F:/codex/laox_account/app/api/'
                               'workflow_schema_z0903.json').read_text(encoding='utf-8'))
BLANK_WAV = LOCAL_SCHEMA['data']['input_rules']['ref_audio_0']['default'].split(';')[-1]
PNG_1X1 = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
           b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
           b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')


def _refuse(fn, code):
    with pytest.raises(h3.H3AdapterError, match=code):
        fn()


def _tampered_case(tmp_path, mutate, rebind_hash=False):
    """Copy of the real case with one storyboard mutation. rebind_hash=True fakes a
    hash-consistent record (the exact adversary class: binding looks fresh but the
    content is semantically blocked)."""
    d = tmp_path / 'case'
    d.mkdir()
    payloads = h3.load_case(CASE_DIR)
    shot = next(s for s in payloads['storyboards']['shots'] if s['shot_id'] == 'shot-007')
    mutate(shot)
    if rebind_hash:
        entry = next(e for e in payloads['review']['review']['shots']
                     if e['shot_id'] == 'shot-007')
        entry['content_sha256'] = ps.shot_content_hash(shot)
    (d / 'case.json').write_text(json.dumps(payloads['case'], ensure_ascii=False),
                                 encoding='utf-8')
    (d / 'storyboards.json').write_text(json.dumps(payloads['storyboards'], ensure_ascii=False),
                                        encoding='utf-8')
    (d / 'review.json').write_text(json.dumps(payloads['review'], ensure_ascii=False),
                                   encoding='utf-8')
    return d


def _frame_meta(png_path):
    return {'source_path': 'F:/codex/laox_account/video_postprod/artifacts/imports/eb/'
                           'ebba26902592edba75027de82e2d11d383e05511ddb24a046c5fd4da868c000e.mp4',
            'source_sha256': 'ebba' * 16, 'source_read_only': True,
            'extracted_at_sec': 1.0, 'png_path': str(png_path),
            'png_sha256': 'unit', 'png_bytes': len(PNG_1X1), 'command_display': 'unit',
            'purpose': 'TEST visual reference only'}


def _fixture_png():
    p = h3.EXEC_ROOT / 'tmp' / 'pytest' / 'frame.png'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(PNG_1X1)
    return p


# --------------------------------------------------------------- runtime reuse

def test_private_instances_root_bound_and_pinned():
    exec_mod = h3.runtime_exec()
    resolver_mod = h3.runtime_resolver()
    assert exec_mod.ROOT.resolve() == h3.EXEC_ROOT.resolve()
    assert resolver_mod.ROOT.resolve() == Path('F:/codex/laox_account').resolve()
    assert exec_mod is not resolver_mod
    for name in ('lab_h3_exec_private_1', 'lab_h3_resolver_private_1'):
        assert name not in sys.modules          # private instances, never shared
    assert h3._sha256_file(h3.RUNTIME_SOURCE) == h3.RUNTIME_SHA256


def test_runtime_source_drift_refuses_load(monkeypatch):
    monkeypatch.setattr(h3, 'RUNTIME_SHA256', '0' * 64)
    _refuse(lambda: h3._load_runtime('unit_drift_probe'),
            'H3ADAPTER_RUNTIME_DRIFT')




# ------------------------------------------------------------ binding guards

def test_verify_pass_shot_real_case():
    _, shot = h3.verify_pass_shot(CASE_DIR, 'shot-007')
    assert shot['duration_sec'] == 2.8 and shot['purpose'] == 'detail'


def test_return_shot_refused():
    _refuse(lambda: h3.verify_pass_shot(CASE_DIR, 'ce-007'), 'SHOT_NOT_PASS')


def test_unknown_shot_refused():
    _refuse(lambda: h3.verify_pass_shot(CASE_DIR, 'shot-999'), 'SHOT_NOT_FOUND')


def test_stale_content_hash_refused(tmp_path):
    case = _tampered_case(tmp_path, lambda s: s.update(narration='被篡改的旁白。'))
    _refuse(lambda: h3.verify_pass_shot(case, 'shot-007'), 'SHOT_REVIEW_STALE')


def test_hash_consistent_unknown_duration_still_refused(tmp_path):
    case = _tampered_case(tmp_path, lambda s: s.update(duration_sec=None), rebind_hash=True)
    _refuse(lambda: h3.verify_pass_shot(case, 'shot-007'), 'SHOT_DURATION_UNKNOWN')


def test_hash_consistent_needs3d_still_refused(tmp_path):
    case = _tampered_case(tmp_path, lambda s: s.update(needs_3d=True), rebind_hash=True)
    _refuse(lambda: h3.verify_pass_shot(case, 'shot-007'), 'SHOT_REQUIRES_3D')


def test_missing_ref_media_refused(tmp_path):
    # mutate ref path inside storyboards.json copy (refs live at plan level)
    d = tmp_path / 'case2'
    d.mkdir()
    payloads = h3.load_case(CASE_DIR)
    for r in payloads['storyboards']['refs']:
        r['path'] = 'F:/unit-only/gone.mp4'
    (d / 'storyboards.json').write_text(json.dumps(payloads['storyboards'],
                                                   ensure_ascii=False), encoding='utf-8')
    (d / 'case.json').write_text(json.dumps(payloads['case'], ensure_ascii=False),
                                 encoding='utf-8')
    (d / 'review.json').write_text(json.dumps(payloads['review'], ensure_ascii=False),
                                   encoding='utf-8')
    _refuse(lambda: h3.verify_pass_shot(d, 'shot-007'), 'MISSING_REF_MEDIA')


def test_video_references_rejected_clearly():
    _refuse(lambda: h3.assert_no_video_reference(['F:/x/a1.mp4']),
            'VIDEO_REFERENCE_UNSUPPORTED')
    _refuse(lambda: h3.assert_no_video_reference(['data:video/mp4;base64,QUJD']),
            'VIDEO_REFERENCE_UNSUPPORTED')
    # URL forms with query strings must not slip past a naive extension check
    _refuse(lambda: h3.assert_no_video_reference(
        ['https://cdn.example.com/clip.MOV?token=x&range=0-1']),
        'VIDEO_REFERENCE_UNSUPPORTED')
    _refuse(lambda: h3.assert_no_video_reference(['https://x/y.mp4#t=1']),
            'VIDEO_REFERENCE_UNSUPPORTED')
    h3.assert_no_video_reference(['evidence/x.png', BLANK_WAV])  # image/audio fine




def test_seed_validated_before_use():
    assert h3.validate_seed(20261011) == 20261011
    for bad in (0, -1, 10 ** 15 + 1, 1.0, True, '7', float('nan')):
        _refuse(lambda: h3.validate_seed(bad), 'SEED_INVALID')


def test_duration_never_silently_rounded():
    assert h3.validate_test_duration(5) == 5
    assert h3.validate_test_duration(4) == 4 and h3.validate_test_duration(12) == 12
    _refuse(lambda: h3.validate_test_duration(2.8), 'DURATION_NOT_INTEGER')  # storyboard 2.8s
    _refuse(lambda: h3.validate_test_duration(5.0), 'DURATION_NOT_INTEGER')
    _refuse(lambda: h3.validate_test_duration(3), 'DURATION_OUT_OF_LAB_RANGE')
    _refuse(lambda: h3.validate_test_duration(13), 'DURATION_OUT_OF_LAB_RANGE')


# ------------------------------------------------- independent TEST intent





# ------------------------------------------- mapping validated by the runtime





# ------------------------------------------------- guarded submit (stub transport)

class StubRuntime:
    """UNIT ONLY transport stub standing in for the ORIGINAL runtime instance:
    records delegation, never touches the network or the filesystem run tree."""

    def __init__(self, fingerprint='f' * 64):
        self.fingerprint = fingerprint
        self.submit_calls = []

    def load_project(self, path):
        seg = {'id': 'seg-x', 'duration': 5}
        return {'segments': [seg], 'assets': {}, 'resolution': h3.RESOLUTION, 'seed': 1}, {}

    def prepare_bundle(self, data, seg):
        return ({}, [], self.fingerprint)

    def submit(self, project_path, segment_id):
        self.submit_calls.append((project_path, segment_id))
        return {'state': 'STUB_SUBMITTED', 'task_id': 'unit-stub'}


def _estimate(over=None):
    """Structured synthetic quote (UNIT ONLY — clearly labelled synthetic, never real
    pricing evidence, never provider-verified; the coordinator probe class is
    reproduced with these values, see coordinator-budget-findings.json)."""
    est = {'status': h3.ESTIMATE_STATUS_CONFIRMED, 'currency': 'CNY',
           'unit': h3.ESTIMATE_UNIT, 'estimate_cny': 100,
           'workflow_id': h3.WORKFLOW, 'request_fingerprint': 'f' * 64,
           'provenance': {'recorded_by': 'trusted-coordinator (UNIT SYNTHETIC)',
                          'source': 'UNIT SYNTHETIC human-confirmed platform quotation — '
                                    'not real pricing evidence, not provider-verified',
                          'recorded_utc': '2026-10-11T00:00:00+00:00'}}
    if over:
        est.update(over)
    return est


def _auth_record(tmp_path, **over):
    """Authorization records live ONLY inside AUTH_DIR (guarded_submit containment);
    tmp_path is used for out-of-dir probes."""
    record = {'schema': h3.AUTH_SCHEMA, 'request_fingerprint': 'f' * 64,
              'segment_id': 'seg-x', 'max_calls': 1, 'calls_used': 0,
              'budget': {'currency': 'CNY', 'max_total': 100},
              'cost_estimate': _estimate(),
              'authorized_by_human': True,
              'consent_statement': '用户已明确授权本次单次付费提交（UNIT TEST）',
              'cost_estimate_basis': 'UNIT TEST basis：按 768p 档 billing_config 人工核对估价',
              'created_utc': '2026-10-11T00:00:00+00:00'}
    record.update(over)
    h3.AUTH_DIR.mkdir(parents=True, exist_ok=True)
    name = 'unit-auth-%s.json' % abs(hash(json.dumps(over, sort_keys=True, default=str)))
    p = h3.AUTH_DIR / name
    p.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    return p


def _auth_outside(tmp_path, **over):
    record = {'schema': h3.AUTH_SCHEMA, 'request_fingerprint': 'f' * 64,
              'segment_id': 'seg-x', 'max_calls': 1, 'calls_used': 0,
              'budget': {'currency': 'CNY', 'max_total': 100},
              'cost_estimate': _estimate(),
              'authorized_by_human': True,
              'consent_statement': '用户已明确授权本次单次付费提交（UNIT TEST）',
              'cost_estimate_basis': 'UNIT TEST basis', 'created_utc': '2026-10-11T00:00:00+00:00'}
    record.update(over)
    p = tmp_path / 'outside-auth.json'
    p.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    return p


def _project_file(tmp_path):
    p = tmp_path / 'project.json'
    p.write_text('{}', encoding='utf-8')
    return p


def _lock_tree_untouched():
    locks = h3.EXEC_ROOT / 'runs' / 'h3' / 'h3_locks'
    runs = h3.EXEC_ROOT / 'runs' / 'h3' / 'h3_runs'
    return (list(locks.glob('*')) if locks.is_dir() else []) + \
           (list(runs.glob('*')) if runs.is_dir() else [])


















def test_exec_instance_delegates_settings_and_tool_to_resolver(monkeypatch):
    exec_mod = h3.runtime_exec()
    resolver = h3.runtime_resolver()
    assert exec_mod.settings is resolver.settings      # explicit private-instance bridge
    assert exec_mod.tool is resolver.tool
    # env-override path of the ORIGINAL resolver still works with precedence
    # (synthetic unit value only — no real credential is ever read or echoed here)
    monkeypatch.setenv('AUTODL_ART_TOKEN', 'unit-synthetic-token')
    assert resolver.settings()['token'] == 'unit-synthetic-token'
    assert exec_mod.settings(True)['token'] == 'unit-synthetic-token'
    monkeypatch.delenv('AUTODL_ART_TOKEN')
    cfg = resolver.settings()                          # non-env path: real autodl.env file
    assert isinstance(cfg['token'], str)               # presence only, value never asserted




