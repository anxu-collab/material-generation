"""commodity-material-ai-v1 core adapter safety tests (UNIT ONLY).

Red-first safety tests pinning the authoritative user contracts
(runtime/evidence/commodity-material-ai-v1/user-original-request.txt §4/§5/§9/§11)
plus the repair-1 hardening (2026-10-11 independently reproduced defects):

- MODULE.md core interface (exact names / signatures / defaults);
- the fixed secure official Claude CLI invocation: exact model claude-opus-5-5,
  structured output strictly validated against SCHEMAS[role] (director shot-spec /
  writer H3-executable spec / reviewer decision record), restricted / no permission
  prompts / no tools / no MCP / no session persistence, stdin prompt, argv array
  without shell, bounded output with prompt overflow kill, deadline, per-project
  single-flight ACROSS instances and CLI processes, auth-override refusal
  (presence only, checked BEFORE the cache), no auto retry / downgrade / fallback,
  and repair-1 gates: nonzero CLI exit is never success, strict JSON in and out
  (NaN/Infinity refused), runner boundary exceptions become honest records;
- the REAL Clef local protocol: GET /health identity/Clef-Flash-Q4_K_M/llama/CUDA
  facts plus POST /v1/systemone native decision with validated real probabilities
  (noul p_true cross-checked against the raw value, choice/score maps,
  question_id/type and decision-model identity consistency); probe is health-only
  by default (no load, no POST); real inference only under a caller-owned
  exclusive lifecycle seam released in finally; text-only visuals stay UNVERIFIED
  with uppercase review_status distinct from technical delivery; strict loopback
  endpoint validation with zero network on invalid endpoints;
- the append-only SQLite material store: all versions preserved, broken originals
  kept, duplicate versions refused, mock never approvable, honest probe-vs-decode
  media evidence (video dimensions + real ffmpeg frame decode), approval gated on
  current bound B/reviewer real results with approval-compatible status (RETURN
  never; text-clef NEEDS_REVIEW needs an explicit human resolution token),
  reference coverage/currentness with drift refusal, transactional approval, and
  export revalidation of every stored hash;
- zero-H3 dry-run: run_material_case drives the full Director -> Writer -> A ->
  Mock H3 -> B -> Reviewer chain with per-stage persisted records, injectable
  UNIT clients, per-shot failure isolation, explicit known-blocked real-call
  records and NO real model inference.

All Claude/Clef/HTTP interactions here are fakes or bounded local processes: they
prove unit behavior only and never count as real model calls or production assets.
"""
import hashlib
import importlib.util
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
from pathlib import Path

import pytest
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import commodity_ai as core  # noqa: E402
from agents import product_storyboards as ps  # noqa: E402

CASE = json.loads((ROOT / 'cases' / 'koc-dragonfruit-dryrun' / 'case.json').read_text(encoding='utf-8'))
PLAN_SHOT = ps.build_plan(CASE)['shots'][0]  # legacy KOC shot (audit store keeps these)
ID_RE = r'^[A-Za-z0-9_.:-]{1,128}$'
# user §5 authoritative shot field list (legacy KOC fields can never substitute)
NEW_SHOT_FIELDS = {
    'material_id', 'shot_id', 'product_id', 'source_refs', 'narration_target',
    'visual_purpose', 'scene', 'character_refs', 'product_refs', 'action_start',
    'action_process', 'action_end', 'camera_and_framing', 'duration_sec',
    'required_visuals', 'forbidden_visuals', 'audio_policy', 'generation_prompt',
}
# user §9 reviewer decision fields
REVIEWER_FIELDS = {
    'shot_id', 'take_id', 'review_status', 'confirmed_issues', 'unverified_issues',
    'evidence', 'repair_action', 'prompt_revision', 'reference_changes',
    'regenerate_required',
}
# user §11 manifest field list
USER_MANIFEST_FIELDS = {
    'asset_id', 'shot_id', 'product_id', 'version', 'video_path', 'file_hash',
    'duration_sec', 'usable_ranges', 'visual_description', 'actions', 'scene',
    'narration_targets', 'tags', 'audio_policy', 'review_status', 'review_record_id',
}

NEW_SHOT = {
    'material_id': 'mat-001',
    'shot_id': 'shot-101',
    'product_id': 'koc-dragonfruit-demo',
    'source_refs': [{'ref_id': 'a1', 'role': 'demo_footage_reference'}],
    'narration_target': '果子的外观先看一遍。',
    'visual_purpose': '展示果子完整外观与颜色，服务开箱观感',
    'scene': '桌面演示场景',
    'character_refs': [{'ref_id': 'hands-only', 'role': 'hand_appearance'}],
    'product_refs': [{'ref_id': 'a1', 'role': 'product_appearance_demo'}],
    'action_start': '果子完整入画',
    'action_process': ['展示果子外观与颜色'],
    'action_end': '果子外观完整可见',
    'camera_and_framing': '果子正面中景，果子居中，固定机位',
    'duration_sec': 2.4,
    'required_visuals': ['果子完整可见', '果皮颜色真实一致'],
    'forbidden_visuals': ['固定角标与水印', '虚构产品功效文字'],
    'audio_policy': 'discard_allowed',
    'generation_prompt': '竖屏桌面演示：果子完整入画居中，展示外观与颜色，画面连续不截断动作',
}

VALID_DIRECTOR_OUTPUT = {
    'role': 'director', 'case_id': CASE['case_id'],
    'shots': [json.loads(json.dumps(NEW_SHOT))],
    'concerns': ['fixture 时长证据有限，duration 上限保守'],
}
VALID_WRITER_OUTPUT = {
    **json.loads(json.dumps(NEW_SHOT)),
    'role': 'writer',
    'generation_prompt': '竖屏 768x1376 桌面演示：手持果子入画，缓慢旋转展示果皮与外观，'
                         '画面连续、产品完整可见、无角标水印',
    'reference_requirements': [{'ref_id': 'a1', 'requirement': '果皮颜色与外观必须与参考一致'}],
    'notes': None,
}
VALID_REVIEWER_OUTPUT = {
    'role': 'reviewer', 'shot_id': 'shot-101', 'take_id': None,
    'review_status': 'NEEDS_REVIEW',
    'confirmed_issues': [{'code': 'DURATION_OUTSIDE_VERIFIED_LIMITS',
                          'evidence': 'duration 2.4s 依据 fixture probe 上限'}],
    'unverified_issues': [{'code': 'VISUAL_UNVERIFIED',
                           'evidence': '文本审核无法验证实际画面'}],
    'evidence': ['规则核对：叙述与可见信息一致'],
    'repair_action': '人工核对画面后决定是否放行',
    'prompt_revision': None,
    'reference_changes': [],
    'regenerate_required': False,
}


def claude_stdout(structured=..., model=..., is_error=False, result='ok'):
    payload = {'is_error': is_error, 'result': result, 'session_id': 'test-session',
               'duration_ms': 120, 'total_cost_usd': 0}
    if structured is not ...:
        payload['structured_output'] = structured
    if model is not ...:
        payload['model'] = model
    return json.dumps(payload).encode('utf-8')


def make_runner(stdout=b'', stderr=b'', returncode=0, sleep=0.0, emit=None,
                script=None, timed_out=False, truncated=False, raise_exc=None,
                entered=None):
    """Fake bounded-process runner: captures the exact spawn contract, never spawns."""
    calls = []

    def runner(argv, stdin_bytes, cwd, env, timeout_sec, max_output_bytes):
        calls.append({'argv': list(argv), 'stdin': stdin_bytes, 'cwd': str(cwd),
                      'env': dict(env or {}), 'timeout_sec': timeout_sec,
                      'max_output_bytes': max_output_bytes})
        if entered is not None:
            entered.set()
        if raise_exc is not None:
            raise raise_exc
        if script is not None and len(calls) <= len(script):
            behavior = script[len(calls) - 1]
            return {'returncode': behavior.get('returncode', 0),
                    'stdout': behavior.get('stdout', b''),
                    'stderr': behavior.get('stderr', b''),
                    'timed_out': behavior.get('timed_out', False),
                    'truncated': behavior.get('truncated', False)}
        if sleep:
            time.sleep(sleep)
        out = stdout
        if emit is not None:
            out = emit(max_output_bytes)
        return {'returncode': returncode, 'stdout': out, 'stderr': stderr,
                'timed_out': timed_out, 'truncated': truncated}

    runner.calls = calls
    return runner


# --- real Clef protocol fixtures (adapter.py normalize_response/build_health shape)

VALID_HEALTH = {
    'status': 'ok', 'service': 'clef-flash-systemone', 'service_version': '1.1',
    'listen': 'http://127.0.0.1:8088',
    'backend': {'name': 'llama.cpp', 'build': 'b9999', 'variant': 'cuda-x64',
                'endpoint': 'http://127.0.0.1:8089/v1/systemone'},
    'model': {'file': 'Clef-Flash-Q4_K_M.gguf',
              'path': 'F:/models/clef-flash/Clef-Flash-Q4_K_M.gguf',
              'quantization': 'Q4_K_M'},
    'cuda': {'available': True, 'gpu_name': 'NVIDIA GeForce RTX 3080',
             'vram_total_mb': 10240, 'vram_used_mb': 512, 'vram_free_mb': 9728},
    'llama_server': {'status': 'ok', 'http': 200},
    'timestamp': '2026-10-11T00:00:00',
}

VALID_DECISION = {
    'model': 'Clef-Flash-Q4_K_M.gguf',
    'answers': {
        'clear': {'question_id': 'clear', 'type': 'noul', 'choice': 'yes',
                  'confidence': 0.74, 'scores': {'yes': 0.87, 'no': 0.13},
                  'p_true': 0.87,
                  'raw': {'noul': 0.87, 'probabilities': {'true': 0.87}}},
        'route': {'question_id': 'route', 'type': 'choice', 'choice': 'pass',
                  'confidence': 0.82, 'scores': {'pass': 0.91, 'return': 0.09},
                  'raw': {'choice': 'pass', 'probabilities': {'pass': 0.91, 'return': 0.09}}},
        'quality': {'question_id': 'quality', 'type': 'score', 'score': 2.0,
                    'legend': ['low', 'medium', 'high'], 'best': 'high',
                    'confidence': 0.5,
                    'scores': {'low': 0.02, 'medium': 0.23, 'high': 0.75},
                    'raw': {'score': 2.0, 'legend': ['low', 'medium', 'high'],
                            'probabilities': {'0': 0.02, '1': 0.23, '2': 0.75}}},
    },
    'latency_ms': 123,
}


class OwnedLifecycle:
    """UNIT-OWNED inference lifecycle seam: exclusive acquire/release with
    release proven on every ending (success, failure, timeout, cancel)."""

    def __init__(self, evidence=None, fail_acquire=False):
        self.held = 0
        self.released = 0
        self.fail_acquire = fail_acquire
        self.evidence = evidence or {}

    def acquire(self):
        if self.fail_acquire:
            return False
        self.held += 1
        return True

    def release(self):
        self.released += 1

    def runtime_evidence(self):
        return self.evidence


PROVEN_RUNTIME = {'lifecycle_status': 'owned_exclusive',
                  'cuda_execution_status': 'verified',
                  'joint_head_status': 'verified'}


def deep_update(data, dotted, value):
    node = data
    keys = dotted.split('.')
    for key in keys[:-1]:
        node = node[key]
    if value is ...:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value
    return data


def clef_fixture(health=VALID_HEALTH, decision=VALID_DECISION, health_error=None,
                 decision_error=None):
    calls = []

    def _http_json(url, payload, timeout_sec):
        calls.append({'url': url, 'payload': payload, 'timeout_sec': timeout_sec})
        if url.endswith('/health'):
            if health_error is not None:
                raise health_error
            body = health if isinstance(health, str) else json.dumps(health)
            return {'http_status': 200, 'body': body}
        if decision_error is not None:
            raise decision_error
        body = decision if isinstance(decision, str) else json.dumps(decision)
        return {'http_status': 200, 'body': body}

    _http_json.calls = calls
    return _http_json


def post_calls(http):
    return [c for c in http.calls if c['payload'] is not None]


@pytest.fixture()
def tmp_root():
    """Always project-internal temp root (never the OS temp outside the lab)."""
    forced = ROOT / 'runtime' / 'tmp' / 'commodity-zcode' / 'unit'
    forced.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix='core-', dir=str(forced)))


@pytest.fixture()
def store(tmp_root):
    return core.MaterialStore(root=tmp_root)


# --------------------------------------------------------------------------
# A0 — MODULE.md interface contract (names, signatures, defaults)
# --------------------------------------------------------------------------

def test_module_constants_and_error_type():
    assert core.MODEL_ID == 'claude-opus-5-5'
    assert isinstance(core.ROOT, Path)
    assert set(core.SCHEMAS) == {'director', 'writer', 'reviewer', 'manifest'}
    exc = core.AIError('SOME_CODE', 'detail')
    assert exc.code == 'SOME_CODE' and 'SOME_CODE' in str(exc)


def test_core_interface_signatures_match_module_md():
    p = inspect.signature(core.ClaudeCLIAdapter.__init__).parameters
    assert list(p)[:4] == ['self', 'root', 'timeout_sec', 'max_output_bytes']
    assert p['timeout_sec'].default == 60 and p['max_output_bytes'].default == 262144
    assert list(inspect.signature(core.ClaudeCLIAdapter.invoke).parameters) == \
        ['self', 'role', 'payload', 'refs']
    assert callable(core.ClaudeCLIAdapter.preflight)

    p = inspect.signature(core.ClefAdapter.__init__).parameters
    assert list(p) == ['self', 'endpoint', 'timeout_sec']
    assert p['endpoint'].default == 'http://127.0.0.1:8088'
    assert p['timeout_sec'].default == 15
    # repair-1 optional keyword seams: review lifecycle, probe infer/lifecycle
    assert list(inspect.signature(core.ClefAdapter.review).parameters) == \
        ['self', 'phase', 'shot', 'evidence', 'lifecycle']
    assert list(inspect.signature(core.ClefAdapter.probe).parameters) == \
        ['self', 'infer', 'lifecycle']
    assert inspect.signature(core.ClefAdapter.probe).parameters['infer'].default \
        is False

    p = inspect.signature(core.MaterialStore.__init__).parameters
    assert list(p) == ['self', 'root'] and p['root'].default == core.ROOT
    expected = {
        'register_take': ['self', 'shot', 'video_path', 'version', 'batch_id', 'provenance'],
        'append_review': ['self', 'take_id', 'review', 'mode'],
        'append_usage': ['self', 'take_id', 'target_shot_id', 'purpose'],
        'append_stage_record': ['self', 'run_id', 'stage', 'record', 'shot_id', 'mode'],
        'stage_history': ['self', 'run_id'],
    }
    for name, params in expected.items():
        assert list(inspect.signature(getattr(core.MaterialStore, name)).parameters) == params, name
    for name in ('list_takes', 'get_take', 'approve', 'export_manifest', 'history'):
        assert callable(getattr(core.MaterialStore, name)), name
    assert list(inspect.signature(core.run_dry_run).parameters) == ['root', 'clients']
    assert list(inspect.signature(core.run_material_case).parameters) == ['root', 'clients']


# --------------------------------------------------------------------------
# A1 — fixed secure Claude CLI invocation
# --------------------------------------------------------------------------

def test_claude_invoke_fixed_secure_argv_and_stdin_prompt(tmp_root):
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'verified', record
    assert len(runner.calls) == 1
    call = runner.calls[0]
    argv = call['argv']
    assert isinstance(argv, list) and argv[0] == 'C:/fake/claude.exe'
    flags = set()
    pairs = {}
    tokens = argv[1:]
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if index + 1 < len(tokens) and not tokens[index + 1].startswith('-'):
            pairs[token] = tokens[index + 1]
            index += 2
        else:
            flags.add(token)
            index += 1
    assert pairs['--model'] == 'claude-opus-5-5'
    assert pairs['--output-format'] == 'json'
    assert pairs['--permission-mode'] == 'dontAsk'
    assert pairs['--permission-prompts'] == 'none'
    assert pairs['--tools'] == ''
    assert pairs['--disallowedTools'] == 'mcp__*'
    assert pairs['--max-turns'] == '1'
    assert pairs['--mcp-config'] == '{"mcpServers":{}}'
    for flag in ('-p', '--restricted', '--strict-mcp-config', '--no-session-persistence'):
        assert flag in flags, flag
    assert json.loads(pairs['--json-schema']) == core.SCHEMAS['reviewer']
    # prompt travels via stdin, never via shell string
    assert isinstance(call['stdin'], bytes) and b'shot-001' in call['stdin']
    # isolated cwd inside project runtime
    assert Path(call['cwd']).resolve().is_relative_to((tmp_root / 'runtime').resolve())
    # scrubbed child environment: no auth override channels
    assert not [k for k in call['env'] if k.startswith('ANTHROPIC_') or k.startswith('CLAUDE_CODE_')]


def test_claude_invoke_record_shape_and_exact_model(tmp_root):
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT,
                                              model='claude-opus-5-5'))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    for key in ('status', 'error_code', 'structured_output', 'model_id', 'duration_sec',
                'raw_result', 'call_id', 'cached'):
        assert key in record, key
    assert record['status'] == 'verified' and record['error_code'] is None
    assert record['model_id'] == 'claude-opus-5-5'
    assert record['structured_output'] == VALID_REVIEWER_OUTPUT
    assert record['cached'] is False and record['raw_result']['is_error'] is False


def test_claude_invoke_model_mismatch_refused(tmp_root):
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT,
                                              model='claude-sonnet-4-5'))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'MODEL_MISMATCH'


@pytest.mark.parametrize('structured', [
    {'role': 'reviewer', 'review_status': 'PASS'},                       # missing required
    {**VALID_REVIEWER_OUTPUT, 'invented_field': 'x'},                    # extra property
    {**VALID_REVIEWER_OUTPUT, 'review_status': 'APPROVED'},              # bad enum
    {**VALID_REVIEWER_OUTPUT, 'regenerate_required': 'maybe'},           # bad type
    None,                                                                 # absent
])
def test_claude_invoke_strict_schema_failure(tmp_root, structured):
    runner = make_runner(stdout=claude_stdout(structured=structured))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked'
    assert record['error_code'] in ('SCHEMA_INVALID', 'STRUCTURED_OUTPUT_MISSING')
    assert record['structured_output'] is None


def test_claude_invoke_output_limit_enforced(tmp_root):
    runner = make_runner(stdout=b'x' * 300000)
    adapter = core.ClaudeCLIAdapter(root=tmp_root, max_output_bytes=262144,
                                    executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'OUTPUT_LIMIT_EXCEEDED'


def test_claude_invoke_timeout_enforced(tmp_root):
    runner = make_runner(timed_out=True, returncode=-1)
    adapter = core.ClaudeCLIAdapter(root=tmp_root, timeout_sec=5,
                                    executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'TIMEOUT'


def test_claude_invoke_concurrency_guard(tmp_root):
    entered = threading.Event()
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT),
                         sleep=0.6, entered=entered)
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    results = {}

    def worker():
        results['a'] = adapter.invoke('reviewer', {'shot_id': 'shot-001'})

    thread = threading.Thread(target=worker)
    thread.start()
    assert entered.wait(5)
    results['b'] = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    thread.join(10)
    assert results['a']['status'] == 'verified'
    assert results['b']['status'] == 'blocked' and results['b']['error_code'] == 'CONCURRENCY_BLOCKED'


def test_claude_invoke_auth_override_refused_without_spawn(tmp_root, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-unit-secret-value')
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'AUTH_OVERRIDE_PRESENT'
    assert runner.calls == []  # fail-closed before any spawn
    assert 'ANTHROPIC_API_KEY' in record.get('auth_overrides_present', [])
    assert 'sk-ant-unit-secret-value' not in json.dumps(record)  # presence only


def test_claude_invoke_real_expired_oauth_mapped_no_retry(tmp_root):
    evidence = json.loads((ROOT / 'runtime' / 'evidence' / 'commodity-material-ai-v1' /
                           'claude-minimal-precheck.json').read_text(encoding='utf-8'))
    runner = make_runner(stdout=evidence['stdout'].encode('utf-8'),
                         returncode=evidence['exit_code'])
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'AUTH_EXPIRED'
    assert len(runner.calls) == 1  # no automatic auth retry
    assert record['raw_result']  # raw record preserved for audit


def test_claude_invoke_unparseable_output(tmp_root):
    runner = make_runner(stdout=b'this is not json at all')
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'UNPARSEABLE_OUTPUT'


def test_claude_invoke_unknown_role(tmp_root):
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe',
                                    runner=make_runner())
    for role in ('producer', 'manifest'):  # manifest is a contract, never a prompt
        with pytest.raises(core.AIError) as exc:
            adapter.invoke(role, {'shot_id': 'shot-001'})
        assert exc.value.code == 'ROLE_UNKNOWN'


def test_claude_role_prompts_independent_and_refs_sanitized(tmp_root):
    outs = {role: claude_stdout(structured=valid) for role, valid in (
        ('director', VALID_DIRECTOR_OUTPUT), ('writer', VALID_WRITER_OUTPUT),
        ('reviewer', VALID_REVIEWER_OUTPUT))}
    runner = make_runner(script=[{'stdout': outs['director']},
                                 {'stdout': outs['writer']},
                                 {'stdout': outs['reviewer']}])
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    ref_file = tmp_root / 'unit-ref.bin'
    ref_file.write_bytes(b'project-local ref content')
    refs = [{'ref_id': 'a1', 'role': 'demo_footage_reference',
             'sha256': core._sha256_file(ref_file), 'path': str(ref_file)}]
    payload = {'shot_id': 'shot-101', 'narration_target': '果子的外观先看一遍。'}
    for role in ('director', 'writer', 'reviewer'):
        assert adapter.invoke(role, payload, refs=refs)['status'] == 'verified', role
    stdins = [c['stdin'].decode('utf-8') for c in runner.calls]
    assert len(set(stdins)) == 3  # three independent prompts
    for role, text in zip(('director', 'writer', 'reviewer'), stdins):
        assert role in text
    joined = '\n'.join(stdins)
    assert 'a1' in joined and 'demo_footage_reference' in joined
    assert 'unit-ref.bin' not in joined  # paths never travel to the model
    assert str(tmp_root).replace('\\', '/') not in joined


def test_claude_cache_hits_only_verified_results(tmp_root):
    ok = claude_stdout(structured=VALID_REVIEWER_OUTPUT)
    expired = json.loads((ROOT / 'runtime' / 'evidence' / 'commodity-material-ai-v1' /
                          'claude-minimal-precheck.json').read_text(encoding='utf-8'))
    runner = make_runner(script=[{'stdout': expired['stdout'].encode('utf-8'),
                                  'returncode': 1},
                                 {'stdout': ok},
                                 {'stdout': ok}])
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    first = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert first['status'] == 'blocked' and first['cached'] is False
    second = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert second['status'] == 'verified' and second['cached'] is False
    third = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert third['status'] == 'verified' and third['cached'] is True  # error was not cached
    assert third['structured_output'] == VALID_REVIEWER_OUTPUT
    assert len(runner.calls) == 2
    changed = adapter.invoke('reviewer', {'shot_id': 'shot-002'})
    assert changed['cached'] is False and len(runner.calls) == 3  # key binds payload


def test_claude_preflight_ready_blocked_and_override(tmp_root, monkeypatch):
    runner = make_runner(stdout=b'2.1.283 (Claude Code)\n')
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    ready = adapter.preflight()
    assert ready['status'] == 'ready' and ready['cli_version'].startswith('2.1.283')
    assert ready['model_id'] == 'claude-opus-5-5'

    monkeypatch.setattr(core, '_CLAUDE_CANDIDATES', [])
    monkeypatch.setattr(core.shutil, 'which', lambda _name: None)
    missing = core.ClaudeCLIAdapter(root=tmp_root, runner=runner).preflight()
    assert missing['status'] == 'blocked' and missing['error_code'] == 'CLI_NOT_FOUND'

    monkeypatch.setenv('ANTHROPIC_BASE_URL', 'https://unit-only.example')
    override = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe',
                                     runner=runner).preflight()
    assert override['status'] == 'blocked' and override['error_code'] == 'AUTH_OVERRIDE_PRESENT'
    assert 'ANTHROPIC_BASE_URL' in override['auth_overrides_present']
    assert 'unit-only.example' not in json.dumps(override)


def test_bounded_spawn_run_real_timeout_and_cap():
    rec = core._bounded_spawn_run([sys.executable, '-c', 'import time; time.sleep(5)'],
                                  None, None, None, 0.5, 4096)
    assert rec['timed_out'] is True and rec['returncode'] != 0
    # child still alive after overflowing the cap: must be terminated promptly
    rec = core._bounded_spawn_run([sys.executable, '-c',
                                   'print("A"*100000, flush=True); import time; time.sleep(5)'],
                                  None, None, None, 30, 512)
    assert len(rec['stdout']) <= 513 and rec['truncated'] is True
    assert rec['overflow_killed'] is True and rec['returncode'] != 0
    rec = core._bounded_spawn_run([sys.executable, '-c',
                                   'import sys; sys.stdout.write(sys.stdin.read().upper())'],
                                  b'hello', None, None, 30, 4096)
    assert rec['stdout'] == b'HELLO' and rec['returncode'] == 0


# --------------------------------------------------------------------------
# A1-repair — reproduced false-verification defects (2026-10-11 audits)
# --------------------------------------------------------------------------

def test_claude_nonzero_exit_with_valid_structured_never_verified(tmp_root):
    """Luna repro: returncode 7 + perfectly valid structured payload. A nonzero
    CLI exit is NEVER success no matter how parseable the stream looks."""
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT),
                         returncode=7)
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked', record
    assert record['error_code'] == 'CLI_NONZERO_EXIT' and record['returncode'] == 7
    assert record['structured_output'] is None
    assert record['raw_result']  # raw stream record preserved for audit
    assert record['cached'] is False  # and never cached


def test_claude_nonzero_exit_error_mappings_preserved(tmp_root):
    """Nonzero exit with an is_error/oauth stream still maps to the precise
    error code and is never retried."""
    runner = make_runner(stdout=claude_stdout(is_error=True,
                                              result='OAuth token has expired'),
                         returncode=1)
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'AUTH_EXPIRED'
    assert len(runner.calls) == 1


def test_claude_runner_exception_recorded_without_secret_values(tmp_root):
    """Runner OSError (and friends) become honest blocked records with elapsed
    time; the exception VALUE never enters the record (it may carry secrets)."""
    runner = make_runner(raise_exc=OSError('spawn failed token=sk-ant-SECRET'))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'RUNNER_ERROR'
    assert record['error'] == 'runner raised OSError'
    assert 'sk-ant-SECRET' not in json.dumps(record)
    assert 'duration_sec' in record  # elapsed recorded even on boundary failure


def test_claude_auth_override_checked_before_cache(tmp_root, monkeypatch):
    """A fresh auth-override presence can never be masked by a cached verified
    result: the override check precedes the cache lookup."""
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    assert adapter.invoke('reviewer', {'shot_id': 'shot-001'})['status'] == 'verified'
    monkeypatch.setenv('ANTHROPIC_AUTH_TOKEN', 'unit-secret')
    replay = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert replay['status'] == 'blocked'
    assert replay['error_code'] == 'AUTH_OVERRIDE_PRESENT'
    assert replay.get('cached') is False
    assert len(runner.calls) == 1  # no second spawn under an override channel


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_claude_nonfinite_payload_rejected_as_record(tmp_root, bad):
    """NaN/Infinity in the payload used to escape as an uncaught ValueError;
    now it is an explicit blocked record before schema/cache."""
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001', 'score': bad})
    assert record['status'] == 'blocked'
    assert record['error_code'] == 'PAYLOAD_NOT_STRICT_JSON'
    assert runner.calls == []


def test_claude_nonfinite_output_rejected(tmp_root):
    """NaN literals in the model stream (json.loads accepts them by default)
    never reach schema validation or the cache."""
    payload = b'{"model": "claude-opus-5-5", "is_error": false, ' \
              b'"structured_output": {"role": "reviewer", "x": NaN}}'
    runner = make_runner(stdout=payload)
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked'
    assert record['error_code'] == 'NONFINITE_OUTPUT'


@pytest.mark.parametrize('kwargs', [
    {'timeout_sec': 0}, {'timeout_sec': -5}, {'timeout_sec': float('nan')},
    {'timeout_sec': float('inf')}, {'timeout_sec': 10 ** 9},
    {'max_output_bytes': 0}, {'max_output_bytes': -1},
    {'max_output_bytes': True}, {'max_output_bytes': 10 ** 10},
])
def test_claude_constructor_rejects_invalid_bounds(tmp_root, kwargs):
    with pytest.raises(core.AIError) as exc:
        core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', **kwargs)
    assert exc.value.code in ('INVALID_TIMEOUT_SEC', 'INVALID_MAX_OUTPUT_BYTES')


def test_claude_single_flight_across_instances_and_cli_processes(tmp_root):
    """Per-PROJECT single-flight across adapter instances AND separate CLI
    processes; lock state cannot permanently block after the owner exits."""
    entered = threading.Event()
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT),
                         sleep=0.8, entered=entered)
    first = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe',
                                  runner=runner)
    results = {}
    thread = threading.Thread(target=lambda: results.__setitem__(
        'a', first.invoke('reviewer', {'shot_id': 'shot-001'})))
    thread.start()
    assert entered.wait(5)
    second = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe',
                                   runner=make_runner(stdout=claude_stdout(
                                       structured=VALID_REVIEWER_OUTPUT)))
    results['b'] = second.invoke('reviewer', {'shot_id': 'shot-001'})
    thread.join(10)
    assert results['a']['status'] == 'verified'
    assert results['b']['status'] == 'blocked'
    assert results['b']['error_code'] == 'CONCURRENCY_BLOCKED'
    # lock released after completion: the very next call goes through
    follow = second.invoke('reviewer', {'shot_id': 'shot-002'})
    assert follow['status'] == 'verified'


def test_claude_cross_process_lock_released_after_owner_exit(tmp_root):
    """A killed external process that held the per-project lock must not
    permanently block us: the OS releases byte-range locks on owner exit."""
    holder_script = (
        "import sys, time; sys.path.insert(0, %r); "
        "from adapters.commodity_ai import _project_flight; "
        "f = _project_flight(%r, 'claude'); "
        "assert f.acquire(); print('HELD', flush=True); time.sleep(60)"
        % (str(ROOT), str(tmp_root)))
    holder = subprocess.Popen([sys.executable, '-c', holder_script],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 20
        held = False
        while time.time() < deadline:
            line = holder.stdout.readline()
            if b'HELD' in line:
                held = True
                break
        assert held, 'lock holder process never signaled'
        runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
        adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe',
                                        runner=runner)
        blocked = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
        assert blocked['status'] == 'blocked'
        assert blocked['error_code'] == 'CONCURRENCY_BLOCKED'
    finally:
        holder.kill()
        holder.wait(timeout=10)
    # owner exited: the same project lock must be usable again immediately
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe',
                                    runner=runner)
    after = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert after['status'] == 'verified', after


def test_claude_refs_cache_invalidated_by_content_change(tmp_root):
    """Supplied ref content changes must invalidate a verified cache hit: the
    digest is computed FRESH from the project-local file, never trusted from
    stale metadata."""
    ref_file = tmp_root / 'ref-cache.bin'
    ref_file.write_bytes(b'content-v1')
    refs_v1 = [{'ref_id': 'r1', 'role': 'demo',
                'sha256': core._sha256_file(ref_file), 'path': str(ref_file)}]
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    first = adapter.invoke('reviewer', {'shot_id': 'shot-001'}, refs=refs_v1)
    assert first['status'] == 'verified'
    assert first['refs_status'][0]['status'] == 'VERIFIED_FRESH_DIGEST'
    cached = adapter.invoke('reviewer', {'shot_id': 'shot-001'}, refs=refs_v1)
    assert cached['cached'] is True
    ref_file.write_bytes(b'content-v2-content-changed')  # same sha claim, new bytes
    stale = adapter.invoke('reviewer', {'shot_id': 'shot-001'}, refs=refs_v1)
    assert stale['cached'] is False, 'changed ref content was served from cache'
    assert stale['status'] == 'verified'
    assert stale['refs_status'][0]['digest_source'] == 'computed_fresh'
    assert len(runner.calls) == 2


def test_claude_refs_sha_format_validated_and_external_paths_refused(tmp_root):
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    bad_sha = adapter.invoke('reviewer', {'shot_id': 'shot-001'},
                             refs=[{'ref_id': 'r1', 'sha256': 'NOT-HEX'}])
    assert bad_sha['status'] == 'blocked'
    assert bad_sha['error_code'] == 'REF_SHA_FORMAT_INVALID'
    external = adapter.invoke('reviewer', {'shot_id': 'shot-001'},
                              refs=[{'ref_id': 'r2', 'path': 'F:/protected/outside/ref.mp4'}])
    assert external['status'] == 'blocked'
    assert external['error_code'] == 'REF_PATH_OUTSIDE_PROJECT'
    missing = adapter.invoke('reviewer', {'shot_id': 'shot-001'},
                             refs=[{'ref_id': 'r3', 'path': str(tmp_root / 'gone.bin')}])
    assert missing['status'] == 'verified'  # call proceeds honestly
    assert missing['refs_status'][0]['status'] == 'UNVERIFIED_MISSING'
    assert missing['refs_status'][0]['available'] is False
    assert runner.calls, 'external/invalid refs must still be fail-closed records'


def test_claude_refs_fresh_digest_recorded_on_cache_miss(tmp_root):
    ref_file = tmp_root / 'ref-fresh.bin'
    ref_file.write_bytes(b'fresh-digest-content')
    refs = [{'ref_id': 'r1', 'role': 'demo', 'path': str(ref_file)}]
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe', runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'}, refs=refs)
    assert record['status'] == 'verified'
    assert record['refs_status'][0]['digest_source'] == 'computed_fresh'
    ref_file.write_bytes(b'changed-again')
    changed = adapter.invoke('reviewer', {'shot_id': 'shot-001'}, refs=refs)
    assert changed['cached'] is False  # no path/sha claim can freeze the key


# --------------------------------------------------------------------------
# A2 — Clef REAL protocol: GET /health + POST /v1/systemone (lifecycle-gated)
# --------------------------------------------------------------------------

def test_clef_probe_health_only_by_default_no_decision_no_load(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    probe = core.ClefAdapter().probe()
    assert probe['status'] == 'partial'  # health verified, decision NOT proven
    assert probe['error_code'] is None
    assert probe['health']['model'] == {'file': 'Clef-Flash-Q4_K_M.gguf',
                                        'quantization': 'Q4_K_M'}
    assert probe['health']['cuda']['available'] is True
    assert probe['visual_status'] == 'UNVERIFIED'  # text-only service
    # doctor/inspection policy: NO decision POST, no model load
    assert post_calls(http) == []
    assert [c['url'] for c in http.calls].count(http.calls[0]['url']) >= 1
    assert probe['lifecycle_status'] == 'not_owned'
    assert probe['cuda_execution_status'] == 'unverified'
    assert probe['joint_head_status'] == 'unverified'


def test_clef_probe_infer_fails_closed_without_lifecycle(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    probe = core.ClefAdapter().probe(infer=True)
    assert probe['status'] == 'blocked'
    assert probe['error_code'] == 'INFERENCE_LIFECYCLE_REQUIRED'
    assert post_calls(http) == []  # zero network for the decision


def test_clef_probe_infer_under_owned_lifecycle_partial_and_proven(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    lifecycle = OwnedLifecycle()  # held, but no runtime proof
    probe = core.ClefAdapter().probe(infer=True, lifecycle=lifecycle)
    assert probe['status'] == 'partial'  # real probabilities, no CUDA proof
    assert probe['probabilities']['noul']['p_true'] == pytest.approx(0.87)
    assert probe['decision_model'] == 'Clef-Flash-Q4_K_M.gguf'
    assert probe['cuda_execution_status'] == 'unverified'
    assert lifecycle.released == 1  # always released in finally
    proven = core.ClefAdapter().probe(infer=True,
                                      lifecycle=OwnedLifecycle(PROVEN_RUNTIME))
    assert proven['status'] == 'verified'
    assert proven['joint_head_status'] == 'verified'
    assert any(c['url'].endswith('/v1/systemone') for c in http.calls)


def test_clef_probe_lifecycle_released_on_every_ending(monkeypatch):
    lifecycle = OwnedLifecycle()
    monkeypatch.setattr(core, '_http_json',
                        clef_fixture(decision_error=urllib.error.HTTPError(
                            'u', 500, 'boom', {}, None)))
    probe = core.ClefAdapter().probe(infer=True, lifecycle=lifecycle)
    assert probe['status'] == 'blocked' and probe['error_code'] == 'HTTP_500'
    assert lifecycle.released == 1
    busy = core.ClefAdapter().probe(infer=True, lifecycle=OwnedLifecycle(fail_acquire=True))
    assert busy['status'] == 'blocked' and busy['error_code'] == 'INFERENCE_LIFECYCLE_BUSY'


def test_clef_probe_service_off_records_honest_blocked(monkeypatch):
    monkeypatch.setattr(core, '_http_json',
                        clef_fixture(health_error=urllib.error.URLError('refused 10061')))
    probe = core.ClefAdapter().probe()
    assert probe['status'] == 'blocked' and probe['error_code'] == 'SERVICE_UNREACHABLE'
    assert 'refused' in probe['error']


@pytest.mark.parametrize('kwargs,expected_code', [
    ({'health': deep_update(json.loads(json.dumps(VALID_HEALTH)),
                            'model.quantization', 'Q8_0')}, 'QUANTIZATION_MISMATCH'),
    ({'health': deep_update(json.loads(json.dumps(VALID_HEALTH)),
                            'cuda.available', False)}, 'CUDA_UNAVAILABLE'),
    ({'health': deep_update(json.loads(json.dumps(VALID_HEALTH)),
                            'llama_server.status', 'down')}, 'LLAMA_BACKEND_DOWN'),
    ({'health': deep_update(json.loads(json.dumps(VALID_HEALTH)),
                            'service', 'other-service')}, 'SERVICE_IDENTITY_INVALID'),
    ({'health': 'not json'}, 'TECHNICAL_ERROR'),
])
def test_clef_probe_negative_health_honestly_blocked(monkeypatch, kwargs, expected_code):
    monkeypatch.setattr(core, '_http_json', clef_fixture(**kwargs))
    probe = core.ClefAdapter().probe()
    assert probe['status'] == 'blocked', probe
    assert probe['error_code'] == expected_code, probe


@pytest.mark.parametrize('kwargs,expected_code', [
    ({'decision': 'not json'}, 'TECHNICAL_ERROR'),
    ({'health': VALID_HEALTH,
      'decision': deep_update(json.loads(json.dumps(VALID_DECISION)),
                              'answers.clear', {'question_id': 'clear',
                                                'type': 'noul',
                                                'raw': {'noul': 0.4}})},
     'NOUL_ANSWER_MISSING'),
    ({'health': VALID_HEALTH,
      'decision': deep_update(json.loads(json.dumps(VALID_DECISION)),
                              'answers.clear.raw.noul', 1.2)}, 'RAW_NOUL_PROBABILITY_INVALID'),
    ({'health': VALID_HEALTH,
      'decision': deep_update(json.loads(json.dumps(VALID_DECISION)),
                              'answers.route.scores', {'pass': 0.5, 'return': 0.1})},
     'CHOICE_PROBABILITIES_NOT_NORMALIZED'),
    ({'health': VALID_HEALTH,
      'decision': deep_update(json.loads(json.dumps(VALID_DECISION)),
                              'answers.quality.scores', {'0': 1.0})},
     'SCORE_ANSWER_SHAPE_INVALID'),
    ({'health': VALID_HEALTH,
      'decision': deep_update(json.loads(json.dumps(VALID_DECISION)),
                              'answers.route.scores', {'pass': 1.5, 'return': -0.5})},
     'PROBABILITY_RANGE_INVALID'),
    ({'health': VALID_HEALTH,
      'decision': deep_update(json.loads(json.dumps(VALID_DECISION)),
                              'answers.route.question_id', 'other')},
     'ANSWER_QUESTION_ID_MISMATCH'),
    ({'health': VALID_HEALTH,
      'decision': deep_update(json.loads(json.dumps(VALID_DECISION)),
                              'answers.route.type', 'noul')},
     'ANSWER_TYPE_MISMATCH'),
    ({'health': VALID_HEALTH,
      'decision': deep_update(json.loads(json.dumps(VALID_DECISION)),
                              'model', 'SomeOther-Q4.gguf')},
     'DECISION_MODEL_MISMATCH'),
    ({'health': VALID_HEALTH,
      'decision_error': urllib.error.HTTPError(
          'http://127.0.0.1:8088/v1/systemone', 500, 'boom', {}, None)}, 'HTTP_500'),
])
def test_clef_inference_negative_shapes_honestly_blocked(monkeypatch, kwargs, expected_code):
    monkeypatch.setattr(core, '_http_json', clef_fixture(**kwargs))
    probe = core.ClefAdapter().probe(infer=True, lifecycle=OwnedLifecycle())
    assert probe['status'] == 'blocked', probe
    assert probe['error_code'] == expected_code, probe


@pytest.mark.parametrize('endpoint,code', [
    ('http://10.0.0.5:8088', 'ENDPOINT_NOT_LOOPBACK'),
    ('http://127.0.0.1:8088@evil.invalid', 'ENDPOINT_USERINFO_REFUSED'),
    ('http://127.0.0.1:8088:pw@evil.invalid', 'ENDPOINT_USERINFO_REFUSED'),
    ('http://localhost@evil.invalid/', 'ENDPOINT_USERINFO_REFUSED'),
    ('http://127.0.0.1:99999', 'ENDPOINT_INVALID_PORT'),
    ('http://127.0.0.1:0', 'ENDPOINT_INVALID_PORT'),
    ('http://127.0.0.1:8088/v1/systemone', 'ENDPOINT_INVALID_PATH'),
    ('http://127.0.0.1:8088?action=x', 'ENDPOINT_QUERY_FRAGMENT_REFUSED'),
    ('http://127.0.0.1:8088#frag', 'ENDPOINT_QUERY_FRAGMENT_REFUSED'),
    ('ftp://127.0.0.1:8088', 'ENDPOINT_NOT_LOOPBACK'),
    ('http://[::1]:8088', 'ENDPOINT_NOT_LOOPBACK'),
])
def test_clef_endpoint_strict_validation_zero_network(endpoint, code):
    with pytest.raises(core.AIError) as exc:
        core.ClefAdapter(endpoint=endpoint)
    assert exc.value.code == code
    # canonical loopback endpoints stay accepted and normalized
    assert core.ClefAdapter(endpoint='http://localhost:8088/').endpoint == \
        'http://localhost:8088'
    assert core.ClefAdapter().endpoint == 'http://127.0.0.1:8088'


def test_clef_timeout_validation():
    for bad in (0, -1, float('nan'), float('inf'), 10 ** 9):
        with pytest.raises(core.AIError) as exc:
            core.ClefAdapter(timeout_sec=bad)
        assert exc.value.code == 'INVALID_TIMEOUT_SEC'


def test_clef_review_requires_lifecycle_no_inference(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    review = core.ClefAdapter().review('B', NEW_SHOT)
    assert review['status'] == 'blocked'
    assert review['error_code'] == 'INFERENCE_LIFECYCLE_REQUIRED'
    assert review['review_status'] == 'TECHNICAL_ERROR'
    assert post_calls(http) == []  # no POST without an owned lifecycle


def test_clef_review_phase_b_visual_unverified_needs_review(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    review = core.ClefAdapter().review('B', NEW_SHOT, lifecycle=OwnedLifecycle())
    assert review['status'] == 'partial' and review['phase'] == 'B'
    assert review['verdict'] == 'pass'  # raw argmax from real probabilities
    assert review['review_status'] == 'NEEDS_REVIEW'  # text-only B can never PASS
    assert review['visual_status'] == 'UNVERIFIED'
    assert review['continuous_motion_status'] == 'NEEDS_REVIEW'
    assert review['input_modality'] == 'text_only'
    assert review['probabilities']['choice']['pass'] == pytest.approx(0.91)
    assert review['model'] == 'Clef-Flash-Q4_K_M.gguf'
    assert review['questions_digest']  # question set recorded per phase
    assert review['lifecycle_status'] == 'owned_exclusive'


def test_clef_review_phase_a_pass_and_return_mapping(monkeypatch):
    http = clef_fixture()  # pass-leaning fixture
    monkeypatch.setattr(core, '_http_json', http)
    a_pass = core.ClefAdapter().review('A', NEW_SHOT, lifecycle=OwnedLifecycle())
    assert a_pass['review_status'] == 'PASS'  # A-phase spec check may pass on text
    returning = json.loads(json.dumps(VALID_DECISION))
    returning['answers']['route']['scores'] = {'pass': 0.09, 'return': 0.91}
    returning['answers']['route']['raw']['probabilities'] = {'pass': 0.09, 'return': 0.91}
    http_returning = clef_fixture(decision=returning)
    monkeypatch.setattr(core, '_http_json', http_returning)
    b_return = core.ClefAdapter().review('B', NEW_SHOT, lifecycle=OwnedLifecycle())
    assert b_return['verdict'] == 'return'
    assert b_return['review_status'] == 'RETURN'  # RETURN never approval-compatible
    # A and B send distinct question sets reflecting their specific checks
    payloads = [c['payload'] for c in post_calls(http)]
    q_a = json.loads(json.dumps(core._PHASE_QUESTIONS['A'], sort_keys=True))
    q_b = json.loads(json.dumps(core._PHASE_QUESTIONS['B'], sort_keys=True))
    assert q_a != q_b
    assert json.loads(json.dumps(payloads[0]['questions'], sort_keys=True)) == q_a
    b_payloads = [c['payload'] for c in post_calls(http_returning)]
    assert json.loads(json.dumps(b_payloads[0]['questions'], sort_keys=True)) == q_b


def test_clef_review_phase_a_cache_isolation(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    adapter = core.ClefAdapter()
    first = adapter.review('A', NEW_SHOT, lifecycle=OwnedLifecycle())
    assert first['cached'] is False and first['visual_status'] == 'not_applicable_pre_generation'
    second = adapter.review('A', NEW_SHOT, lifecycle=OwnedLifecycle())
    assert second['cached'] is True and len(post_calls(http)) == 1  # unchanged spec reuses
    changed_shot = json.loads(json.dumps(NEW_SHOT))
    changed_shot['narration_target'] = '完全不同的旁白。'
    adapter.review('A', changed_shot, lifecycle=OwnedLifecycle())
    adapter.review('B', NEW_SHOT, lifecycle=OwnedLifecycle())  # A/B isolated cache keys
    assert len(post_calls(http)) == 3


def test_clef_review_service_off_and_bad_phase(monkeypatch):
    monkeypatch.setattr(core, '_http_json',
                        clef_fixture(health_error=urllib.error.URLError('refused 10061')))
    review = core.ClefAdapter().review('B', NEW_SHOT, lifecycle=OwnedLifecycle())
    assert review['status'] == 'blocked' and review['error_code'] == 'SERVICE_UNREACHABLE'
    assert review.get('verdict') is None  # no fabricated pass on technical failure
    with pytest.raises(core.AIError) as exc:
        core.ClefAdapter().review('C', NEW_SHOT)
    assert exc.value.code == 'INVALID_PHASE'


def test_clef_review_evidence_structured_only_never_paths(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    evidence = {'take_id': 't-1', 'shot_id': 'shot-101',
                'media': {'video_path': 'F:/somewhere/take_a1_secret_name.mp4',
                          'duration_sec': 2.4, 'width': 768, 'height': 1376,
                          'metadata_status': 'decoded', 'sha256': 'ab' * 32},
                'refs': [{'ref_id': 'a1', 'role': 'demo', 'sha256': 'cd' * 32}]}
    review = core.ClefAdapter().review('B', NEW_SHOT, evidence=evidence,
                                       lifecycle=OwnedLifecycle())
    assert review['input_scope'] == 'text+structured_technical_metadata'
    sent = json.dumps([c['payload'] for c in post_calls(http)], ensure_ascii=False)
    assert 'take_a1_secret_name' not in sent and 'video_path' not in sent
    assert '2.4' in sent and '768' in sent  # verified technical facts DO travel
    no_evidence = core.ClefAdapter().review('B', NEW_SHOT, lifecycle=OwnedLifecycle())
    assert no_evidence['input_scope'] == 'text_only'  # scope stated accurately


def test_clef_review_lifecycle_released_and_single_flight(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    lifecycle = OwnedLifecycle()
    core.ClefAdapter().review('A', NEW_SHOT, lifecycle=lifecycle)
    assert lifecycle.released == 1
    # invalid lifecycle contract fails closed
    broken = core.ClefAdapter().review('A', NEW_SHOT, lifecycle=object())
    assert broken['status'] == 'blocked'
    assert broken['error_code'] == 'INFERENCE_LIFECYCLE_INVALID'


# --------------------------------------------------------------------------
# A3 — append-only SQLite material store
# --------------------------------------------------------------------------

def _write_media(root, name='clip.mp4', payload=b'\x00\x00\x00\x18ftypmp42not-a-real-video'):
    path = root / name
    path.write_bytes(payload)
    return path


def test_store_root_and_media_path_guards(tmp_root):
    # constructor root must be absolute and inside the canonical project ROOT
    with pytest.raises(core.AIError) as exc:
        core.MaterialStore(root='F:/outside-the-lab/store')
    assert exc.value.code == 'ROOT_OUTSIDE_PROJECT'
    with pytest.raises(core.AIError) as exc:
        core.MaterialStore(root=tmp_root / '..' / '..' / '..' / '..' / '..' / '..'
                           / 'elsewhere')
    assert exc.value.code == 'ROOT_OUTSIDE_PROJECT'

    store = core.MaterialStore(root=tmp_root)
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, str(tmp_root.parent / 'escape.mp4'))
    assert exc.value.code == 'MEDIA_OUTSIDE_PROJECT'
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, str(tmp_root / '..' / 'escape.mp4'))
    assert exc.value.code == 'MEDIA_OUTSIDE_PROJECT'
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, r'\\server\share\escape.mp4')
    assert exc.value.code == 'MEDIA_OUTSIDE_PROJECT'
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, 'F:drive-relative.mp4')
    assert exc.value.code == 'MEDIA_OUTSIDE_PROJECT'
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, str(tmp_root / 'missing.mp4'))
    assert exc.value.code == 'MEDIA_MISSING'


def test_store_rejects_junction_escape_before_open(tmp_root):
    """A junction planted under the store runtime dir must be refused BEFORE
    mkdir/sqlite open; the escape target stays untouched."""
    outside = tmp_root.parent / ('outside-' + tmp_root.name)
    outside.mkdir(parents=True, exist_ok=True)
    booby = tmp_root / 'runtime'
    booby.mkdir(parents=True, exist_ok=True)
    junction = tmp_root / 'runtime' / 'commodity-material-ai-v1'
    made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip('junction creation unavailable in this environment')
    with pytest.raises(core.AIError) as exc:
        core.MaterialStore(root=tmp_root)
    assert exc.value.code == 'STORE_PATH_LINK_ESCAPE'


def test_register_take_media_must_be_inside_root(store, tmp_root):
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, str(tmp_root.parent / 'escape.mp4'))
    assert exc.value.code == 'MEDIA_OUTSIDE_PROJECT'
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, str(tmp_root / 'missing.mp4'))
    assert exc.value.code == 'MEDIA_MISSING'


def test_register_take_real_media_requires_video(store):
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, None, provenance='existing_local')
    assert exc.value.code == 'MEDIA_REQUIRED_FOR_REAL_TAKE'


def test_register_take_new_contract_shot_and_legacy_shot(store, tmp_root):
    path = _write_media(tmp_root)
    new_take = store.register_take(NEW_SHOT, str(path))  # user §5 contract registers fine
    assert new_take['take_id'] == 'shot-101.v1.local'
    assert set(NEW_SHOT_FIELDS) <= set(new_take['shot'])
    legacy = dict(PLAN_SHOT)
    legacy['shot_id'] = 'shot-202'
    legacy_take = store.register_take(legacy, str(path))  # legacy KOC shots stay auditable
    assert legacy_take['take_id'] == 'shot-202.v1.local'
    assert len(store.list_takes()) == 2


def test_register_take_broken_media_preserved_and_queryable(store, tmp_root):
    payload = b'\x00\x00\x00\x18ftypmp42broken-junk'
    path = _write_media(tmp_root, payload=payload)
    take = store.register_take(PLAN_SHOT, str(path))
    assert take['status'] == 'media_broken'
    media = take['media']
    # probe vs decode evidence are named honestly and separately; never 'decoded'
    assert media['metadata_status'] != 'decoded'
    assert media['metadata_status'] in ('probe_error', 'decode_failed',
                                        'no_video_stream', 'decode_check_unavailable',
                                        'probe_unavailable')
    assert media['probe_status'] in ('probe_error', 'probe_unavailable')
    assert media['duration_sec'] is None  # true metadata only, never invented
    assert path.read_bytes() == payload  # original preserved byte-for-byte
    fetched = store.get_take(take['take_id'])
    assert fetched['take_id'] == take['take_id'] and fetched['version'] == 1


def test_register_take_duplicate_version_refused(store, tmp_root):
    path = _write_media(tmp_root)
    first = store.register_take(PLAN_SHOT, str(path))
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, str(path))
    assert exc.value.code == 'DUPLICATE_TAKE_VERSION'
    with pytest.raises(core.AIError) as exc:
        store.register_take(PLAN_SHOT, str(path), batch_id='other-batch')
    assert exc.value.code == 'DUPLICATE_TAKE_VERSION'
    assert len(store.list_takes()) == 1 and store.get_take(first['take_id'])['version'] == 1


def test_store_preserves_all_versions_including_failed(store, tmp_root):
    path = _write_media(tmp_root)
    take_v1 = store.register_take(PLAN_SHOT, str(path))
    store.append_review(take_v1['take_id'], {'source': 'unit', 'status': 'verified',
                                             'phase': 'A', 'verdict': 'return'})
    shot_v2 = json.loads(json.dumps(PLAN_SHOT))
    shot_v2['version'] = 2
    shot_v2['narration'] = '改进后的旁白。'
    take_v2 = store.register_take(shot_v2, str(path), version=2)
    shot_v3 = json.loads(json.dumps(PLAN_SHOT))
    shot_v3['version'] = 3
    take_v3 = store.register_take(shot_v3, None, version=3, provenance='mock_h3_dry_run')
    takes = store.list_takes()
    assert {t['take_id'] for t in takes} == {take_v1['take_id'], take_v2['take_id'],
                                             take_v3['take_id']}
    history = store.history(take_v2['take_id'])
    assert [v['version'] for v in history['versions']] == [1, 2, 3]
    history_v1 = store.history(take_v1['take_id'])
    assert history_v1['reviews'] and history_v1['reviews'][0]['verdict'] == 'return'


def test_append_review_modes_unknown_take_and_hash_stamp(store, tmp_root):
    path = _write_media(tmp_root)
    take = store.register_take(NEW_SHOT, str(path))
    stored = store.append_review(take['take_id'], {'source': 'clef', 'status': 'verified',
                                                   'phase': 'B', 'review_status': 'NEEDS_REVIEW'})
    assert stored['mode'] == 'dry_run' and stored['approval_changed'] is False
    # the store stamps current take/shot/file hashes for binding ...
    stamp = stored['binding_stamp']
    assert stamp['take_id'] == take['take_id']
    assert stamp['shot_id'] == 'shot-101'
    assert stamp['shot_sha256'] == hashlib.sha256(
        core._canonical_json(NEW_SHOT).encode('utf-8')).hexdigest()
    assert stamp['video_sha256'] == take['media']['sha256']
    # ... but never manufactures an inference status the caller did not supply
    assert 'llm_invoked' not in stored
    shadow = store.append_review(take['take_id'], {'source': 'clef', 'status': 'verified',
                                                   'phase': 'B', 'review_status': 'PASS'},
                                 mode='shadow_review')
    assert shadow['mode'] == 'shadow_review' and shadow['approval_changed'] is False
    with pytest.raises(core.AIError) as exc:
        store.append_review('nope.v1.local', {'phase': 'A'})
    assert exc.value.code == 'TAKE_NOT_FOUND'
    with pytest.raises(core.AIError) as exc:
        store.append_review(take['take_id'], {'phase': 'A'}, mode='production')
    assert exc.value.code == 'MODE_INVALID'


def test_stage_records_append_strict_and_run_scoped(store):
    run = 'run-unit0001'
    stored = store.append_stage_record(run, 'director', {'status': 'blocked',
                                                         'error_code': 'AUTH_EXPIRED'})
    assert stored['record_id'].startswith('stg-') and stored['mode'] == 'dry_run'
    assert store.stage_history(run)[0]['record']['error_code'] == 'AUTH_EXPIRED'
    store.append_stage_record(run, 'h3', {'mock': True, 'asset': None}, shot_id='shot-001')
    assert [r['stage'] for r in store.stage_history(run)] == ['director', 'h3']
    assert len(store.stage_history()) >= 2  # full history without run filter
    with pytest.raises(core.AIError) as exc:
        store.append_stage_record(run, 'writer', {'bad': float('nan')})
    assert exc.value.code == 'STAGE_RECORD_NOT_STRICT_JSON'
    with pytest.raises(core.AIError) as exc:
        store.append_stage_record('bad run!', 'writer', {})
    assert exc.value.code == 'INVALID_RUN_ID'


def _ffmpeg_available():
    return shutil.which('ffmpeg') is not None


def _make_real_video(root, name='real.mp4', seconds='0.4'):
    path = root / name
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', f'color=c=red:s=64x64:d={seconds}', '-r', '10', str(path)],
                   capture_output=True, timeout=60, check=True)
    return path


def _write_ref_files(tmp_root):
    """Fresh project-local reference files covering every ref NEW_SHOT declares."""
    refs = {}
    for ref_id, payload in (('a1', b'ref-a1-identity-content'),
                            ('hands-only', b'ref-hands-identity-content')):
        path = tmp_root / ('ref-%s.bin' % ref_id)
        path.write_bytes(payload)
        refs[ref_id] = path
    return refs


def _approval_request(store, take, tmp_root, ref_files, ranges=None,
                      human_extra=None, skip_token=False, resolution=None):
    """Build a complete approval request through the honest flow: read the
    CURRENT context via get_approval_context, then echo its binding_token and
    confirm the exact ranges (approve never repairs a lazy confirmation)."""
    duration = take['media']['duration_sec']
    ranges = ranges or [{'start_sec': 0.0, 'end_sec': round(duration / 2, 3)}]
    material = {
        'visual_description': '竖屏桌面演示：手持果子展示外观与剖开切面',
        'actions': ['拿出果子', '对半切开', '展示切面'],
        'narration_targets': ['果子的外观先看一遍。'],
        'tags': ['火龙果', '演示'],
        'audio_policy': 'discard_allowed',
        'scene': '桌面演示场景',
    }
    evidence = {'usable_ranges': ranges, 'refs': [
        {'ref_id': rid, 'sha256': core._sha256_file(path), 'path': str(path)}
        for rid, path in sorted(ref_files.items())]}
    human = {'confirmed_by': 'unit-reviewer', 'visual_scope': '全画面人工核对',
             'temporal_scope': '0.0-结束 全时段',
             'confirmed_at': '2026-10-11T00:00:00+00:00'}
    approval = {'human': human, 'evidence': evidence, 'material': material}
    if not skip_token:
        ctx = store.get_approval_context(take['take_id'], approval)
        human['binding_token'] = ctx['binding_token']
        human['confirmed_ranges'] = ranges
        needs = resolution if resolution is not None else ctx['needs_human_resolution']
        if needs:
            human['review_resolution'] = 'HUMAN_RESOLVED_NEEDS_REVIEW'
            human['resolution_evidence'] = [
                {'sha256': take['media']['sha256'], 'note': 'full-frame visual check'},
                {'sha256': core._sha256_file(ref_files['a1']), 'note': 'identity ref'}]
    if human_extra:
        human.update(human_extra)
    return approval


CLEF_PROBABILITIES = {'noul': {'p_true': 0.87, 'choice': 'yes', 'confidence': 0.7},
                      'choice': {'pass': 0.91, 'return': 0.09},
                      'score': {'low': 0.02, 'medium': 0.23, 'high': 0.75}}


def _register_decoded_take(store, tmp_root, version=1, batch_id='local',
                           review_status='NEEDS_REVIEW'):
    if not _ffmpeg_available():
        pytest.skip('ffmpeg unavailable; decoded-media approval path needs a real clip')
    path = _make_real_video(tmp_root)
    shot = dict(NEW_SHOT)
    take = store.register_take(shot, str(path), version=version, batch_id=batch_id)
    assert take['media']['metadata_status'] == 'decoded', take['media']
    assert take['media']['decode_status'] == 'verified'
    assert take['media']['width'] > 0 and take['media']['height'] > 0
    store.append_review(take['take_id'],
                        {'source': 'clef', 'status': 'partial', 'phase': 'B',
                         'shot_id': 'shot-101', 'review_status': review_status,
                         'visual_status': 'UNVERIFIED',
                         'probabilities': CLEF_PROBABILITIES}, mode='dry_run')
    return take, path


def _approve_decoded_take(store, tmp_root, ref_files=None, approval=None, version=1):
    take, path = _register_decoded_take(store, tmp_root, version=version)
    ref_files = ref_files or _write_ref_files(tmp_root)
    record = store.approve(take['take_id'],
                           approval or _approval_request(store, take, tmp_root, ref_files))
    return take, path, record


def test_approve_refusals_missing_take_and_mock(store, tmp_root):
    with pytest.raises(core.AIError) as exc:
        store.approve('missing.v1.local', {'human': {}, 'evidence': {}, 'material': {}})
    assert exc.value.code == 'TAKE_NOT_FOUND'
    mock_take = store.register_take(PLAN_SHOT, None, provenance='mock_h3_dry_run')
    with pytest.raises(core.AIError) as exc:
        store.approve(mock_take['take_id'],
                      {'human': {}, 'evidence': {}, 'material': {}})
    assert exc.value.code == 'MOCK_NOT_APPROVABLE'  # mock never produces APPROVED


@pytest.mark.skipif(not _ffmpeg_available(), reason='decoded-media refusal path needs a real clip')
def test_approve_refusals_evidence_shadow_and_metadata(tmp_root):
    store = core.MaterialStore(root=tmp_root)
    path = _make_real_video(tmp_root, 'real2.mp4')

    no_review = store.register_take(NEW_SHOT, str(path))
    with pytest.raises(core.AIError) as exc:
        store.approve(no_review['take_id'],
                      _approval_request(store, no_review, tmp_root,
                                        _write_ref_files(tmp_root)))
    assert exc.value.code == 'REAL_REVIEW_EVIDENCE_MISSING'

    shadow_shot = dict(NEW_SHOT)
    shadow_shot['shot_id'] = 'shot-102'
    shadow_only = store.register_take(shadow_shot, str(path))
    store.append_review(shadow_only['take_id'], {'source': 'clef', 'status': 'verified',
                                                 'phase': 'B', 'review_status': 'NEEDS_REVIEW'},
                        mode='shadow_review')  # shadow observations are not approval evidence
    with pytest.raises(core.AIError) as exc:
        store.approve(shadow_only['take_id'],
                      _approval_request(store, shadow_only, tmp_root,
                                        _write_ref_files(tmp_root)))
    assert exc.value.code == 'REAL_REVIEW_EVIDENCE_MISSING'

    broken_shot = dict(NEW_SHOT)
    broken_shot['shot_id'] = 'shot-103'
    broken = store.register_take(broken_shot, str(_write_media(tmp_root, 'broken.mp4')))
    store.append_review(broken['take_id'], {'source': 'clef', 'status': 'verified',
                                            'phase': 'B', 'review_status': 'NEEDS_REVIEW'},
                        mode='dry_run')
    with pytest.raises(core.AIError) as exc:
        store.approve(broken['take_id'],
                      {'human': {}, 'evidence': {}, 'material': {}})
    assert exc.value.code == 'MEDIA_METADATA_UNVERIFIED'  # no real decode -> no approval


@pytest.mark.skipif(not _ffmpeg_available(), reason='audio-only gate needs ffmpeg')
def test_audio_only_media_never_decoded_or_approvable(tmp_root):
    """ffprobe metadata on an audio-only file used to be labeled 'decoded' and
    approved; now a real video stream with dimensions + verified frame decode
    is required, and the record is retained as media_broken."""
    store = core.MaterialStore(root=tmp_root)
    audio_path = tmp_root / 'audio_only.mp4'
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'lavfi',
                    '-i', 'anullsrc=r=44100:cl=mono', '-t', '0.4', str(audio_path)],
                   capture_output=True, timeout=60, check=True)
    shot = dict(NEW_SHOT)
    shot['shot_id'] = 'shot-AUD'
    take = store.register_take(shot, str(audio_path))
    assert take['status'] == 'media_broken'
    assert take['media']['metadata_status'] == 'no_video_stream'
    assert take['media']['video_stream'] is False
    assert take['media']['width'] is None and take['media']['height'] is None
    store.append_review(take['take_id'], {'source': 'clef', 'status': 'verified',
                                          'phase': 'B', 'review_status': 'PASS'},
                        mode='dry_run')
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'],
                      _approval_request(store, take, tmp_root,
                                        _write_ref_files(tmp_root)))
    assert exc.value.code == 'MEDIA_METADATA_UNVERIFIED'
    assert audio_path.exists()  # retained, never deleted


@pytest.mark.skipif(not _ffmpeg_available(), reason='approval guard repro needs a real clip')
@pytest.mark.parametrize('review_payload,expected_code', [
    ({'source': 'clef', 'status': 'verified', 'phase': 'B', 'verdict': 'pass',
      'mock': True, 'probabilities': CLEF_PROBABILITIES},
     'REVIEW_SOURCE_UNVERIFIED'),                            # coordinator repro case 1
    ({'source': 'clef', 'status': 'verified', 'phase': 'B',
      'review_status': 'PASS'}, 'REVIEW_SOURCE_UNVERIFIED'),  # stub: no raw proof
    ({'source': 'clef', 'status': 'verified', 'phase': 'B',
      'review_status': 'RETURN', 'probabilities': CLEF_PROBABILITIES},
     'CURRENT_REVIEW_REJECTED'),                              # RETURN never approvable
    ({'source': 'claude', 'status': 'verified', 'phase': 'A',
      'shot_id': 'different-shot', 'verdict': 'pass'},
     'REAL_REVIEW_EVIDENCE_MISSING'),                        # unbound A-phase review
    ({'source': 'clef', 'status': 'verified'}, 'REAL_REVIEW_EVIDENCE_MISSING'),  # no verdict
    ({'source': 'claude', 'status': 'verified',
      'structured_output': {'role': 'reviewer', 'shot_id': 'other-shot',
                            'review_status': 'RETURN'}},
     'REVIEW_SCHEMA_INVALID'),                               # incomplete §9 decision
    ({'source': 'claude', 'status': 'verified',
      'structured_output': {'role': 'reviewer', 'take_id': 'different.version',
                            **VALID_REVIEWER_OUTPUT}},
     'REVIEW_BINDING_MISMATCH'),                             # nested reviewer, other take
])
def test_approve_rejects_false_evidence_shapes(tmp_root, review_payload, expected_code):
    """Independently reproduced approval-guard defects: mock/RETURN/unbound/
    verdict-less review records can never produce APPROVED."""
    store = core.MaterialStore(root=tmp_root)
    path = _make_real_video(tmp_root)
    shot = dict(NEW_SHOT)
    shot['shot_id'] = 'shot-GUARD'
    take = store.register_take(shot, str(path))
    store.append_review(take['take_id'], review_payload, mode='dry_run')
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'],
                      _approval_request(store, take, tmp_root, _write_ref_files(tmp_root)))
    assert exc.value.code == expected_code, review_payload
    assert store.get_take(take['take_id'])['approval'] is None  # nothing stored


@pytest.mark.skipif(not _ffmpeg_available(), reason='resolution gate needs a real clip')
@pytest.mark.skipif(not _ffmpeg_available(), reason='ref coverage gate needs a real clip')
def test_approve_reference_coverage_and_currentness(tmp_root):
    store = core.MaterialStore(root=tmp_root)
    path = _make_real_video(tmp_root)
    ref_files = _write_ref_files(tmp_root)
    take = store.register_take(NEW_SHOT, str(path))
    store.append_review(take['take_id'],
                        {'source': 'claude', 'status': 'verified', 'phase': 'B',
                         'structured_output': VALID_REVIEWER_OUTPUT}, mode='dry_run')

    # empty ref list cannot approve a shot that declares refs
    base = _approval_request(store, take, tmp_root, ref_files)
    empty = json.loads(json.dumps(base))
    empty['evidence']['refs'] = []
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], empty)
    assert exc.value.code == 'REF_COVERAGE_MISSING'

    # supplied-only shas (no verifiable local source) never cover a required ref
    supplied = json.loads(json.dumps(base))
    supplied['evidence']['refs'] = [
        {'ref_id': 'a1', 'sha256': 'aa' * 32},
        {'ref_id': 'hands-only', 'sha256': 'bb' * 32}]
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], supplied)
    assert exc.value.code == 'REF_COVERAGE_MISSING'

    # declared required refs cannot be waived by refs_none_required
    waived = json.loads(json.dumps(base))
    waived['evidence']['refs'] = []
    waived['evidence']['refs_none_required'] = True
    waived['evidence']['refs_none_required_reason'] = 'stub trying to waive declared refs'
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], waived)
    assert exc.value.code == 'REF_COVERAGE_MISSING'

    # invalid evidence sha format refused
    bad_sha = json.loads(json.dumps(base))
    bad_sha['evidence']['refs'][0]['sha256'] = 'zz'
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], bad_sha)
    assert exc.value.code == 'REF_EVIDENCE_SHA_INVALID'

    # external ref paths are refused unread (never read, never approve)
    external = json.loads(json.dumps(base))
    external['evidence']['refs'][0]['path'] = 'F:/outside/lab/ref.mp4'
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], external)
    assert exc.value.code == 'REF_EVIDENCE_PATH_OUTSIDE_PROJECT'

    # local ref with drifted content refuses (fresh rehash vs claimed sha)
    drifted = json.loads(json.dumps(base))
    ref_files['a1'].write_bytes(b'ref-a1-drifted-content')
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], drifted)
    assert exc.value.code == 'REF_DRIFT'
    ref_files['a1'].write_bytes(b'ref-a1-identity-content')
    record = store.approve(take['take_id'], base)  # current fresh content approves
    assert record['binding']['refs_sha256']
    assert record['binding']['media_sha256']  # full media record pinned


@pytest.mark.skipif(not _ffmpeg_available(), reason='approval contract path needs a real clip')
def test_approve_requires_full_human_range_and_material_contract(tmp_root):
    store = core.MaterialStore(root=tmp_root)
    ref_files = _write_ref_files(tmp_root)
    take, _path = _register_decoded_take(store, tmp_root)
    duration = take['media']['duration_sec']
    valid = _approval_request(store, take, tmp_root, ref_files)
    cases = [
        ({'human': {}, 'evidence': valid['evidence'], 'material': valid['material']},
         'HUMAN_CONFIRMATION_MISSING'),
        ({'human': valid['human'], 'evidence': {'usable_ranges': [], 'refs': []},
          'material': valid['material']}, 'USABLE_RANGE_INVALID'),
        ({'human': valid['human'],
          'evidence': {'usable_ranges': [{'start_sec': 0.0, 'end_sec': duration + 5.0}],
                       'refs': valid['evidence']['refs']},
          'material': valid['material']}, 'USABLE_RANGE_INVALID'),
        ({'human': {**valid['human'], 'confirmed_at': 'not-a-date'},
          'evidence': valid['evidence'], 'material': valid['material']},
         'HUMAN_CONFIRMATION_MISSING'),
        ({'human': {k: v for k, v in valid['human'].items() if k != 'binding_token'},
          'evidence': valid['evidence'], 'material': valid['material']},
         'HUMAN_BINDING_REQUIRED'),
        ({'human': valid['human'], 'evidence': valid['evidence']},
         'MATERIAL_DESCRIPTION_MISSING'),
        # altered material with a STALE token: the context changed, so the
        # confirmation no longer binds (HUMAN_BINDING_MISMATCH fires first)
        ({'human': valid['human'], 'evidence': valid['evidence'],
          'material': {**valid['material'], 'visual_description': ' '}},
         'HUMAN_BINDING_MISMATCH'),
        (None, 'APPROVAL_CONTRACT_INVALID'),
    ]
    for approval, code in cases:
        with pytest.raises(core.AIError) as exc:
            store.approve(take['take_id'], approval)
        assert exc.value.code == code, (code, approval)
    # blank visual description refuses even with a correctly refreshed token
    blank = json.loads(json.dumps(valid))
    blank['material']['visual_description'] = ' '
    blank['human']['binding_token'] = store.get_approval_context(
        take['take_id'], blank)['binding_token']
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], blank)
    assert exc.value.code == 'MATERIAL_DESCRIPTION_MISSING'
    assert store.get_take(take['take_id'])['approval'] is None  # nothing inserted
    record = store.approve(take['take_id'], valid)  # valid one succeeds
    assert record['approval_sha256'] and record['binding']['take_sha256']
    assert record['contract']['manifest_schema'] == core.MANIFEST_SCHEMA_VERSION
    assert record['contract']['store_policy']  # schema/policy identifiers pinned


@pytest.mark.skipif(not _ffmpeg_available(), reason='manifest freshness path needs a real clip')
def test_export_manifest_full_contract_and_freshness(tmp_root):
    store = core.MaterialStore(root=tmp_root)
    ref_files = _write_ref_files(tmp_root)
    take, path, approval = _approve_decoded_take(store, tmp_root,
                                                 ref_files=ref_files)
    manifest = store.export_manifest(take['take_id'])
    jsonschema.Draft7Validator(core.SCHEMAS['manifest']).validate(manifest)
    assert USER_MANIFEST_FIELDS <= set(manifest)  # full user §11 contract
    assert manifest['asset_id'] == take['take_id'] and manifest['mock'] is False
    assert manifest['review_status'] == 'APPROVED'
    assert manifest['file_hash'] == take['media']['sha256']
    assert manifest['duration_sec'] == take['media']['duration_sec']
    assert manifest['usable_ranges'] == approval['evidence']['usable_ranges']
    assert manifest['actions'] and manifest['visual_description']
    assert manifest['review_record_id'].startswith('rev-')
    assert manifest['h3_invoked'] is False and manifest['vbench']['status'] == 'not_run'
    assert manifest['approval']['approval_sha256'] == approval['approval_sha256']

    # shadow review is append-only: it must NOT mutate approval or change the manifest
    before = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    store.append_review(take['take_id'], {'source': 'clef', 'status': 'verified',
                                          'phase': 'B', 'verdict': 'return'},
                        mode='shadow_review')
    again = store.export_manifest(take['take_id'])
    assert json.dumps(again, sort_keys=True, ensure_ascii=False).replace(
        again['exported_at'], '') == before.replace(manifest['exported_at'], '')

    # a NEW bound (non-shadow) review after approval is a real change: export must refuse
    store.append_review(take['take_id'], {'source': 'clef', 'status': 'verified',
                                          'phase': 'B', 'verdict': 'return'},
                        mode='dry_run')
    with pytest.raises(core.AIError) as exc:
        store.export_manifest(take['take_id'])
    assert exc.value.code == 'STALE_APPROVAL'

    # media drift after approval also refuses export
    store2_root = tmp_root / 'drift'
    store2 = core.MaterialStore(root=store2_root)
    store2_root.mkdir(parents=True, exist_ok=True)
    take2, path2, _ = _approve_decoded_take(store2, store2_root,
                                            ref_files=_write_ref_files(store2_root))
    with open(path2, 'ab') as handle:
        handle.write(b'drift')
    with pytest.raises(core.AIError) as exc:
        store2.export_manifest(take2['take_id'])
    assert exc.value.code == 'MEDIA_DRIFT'


@pytest.mark.skipif(not _ffmpeg_available(), reason='tamper seams need a real clip')
def test_export_refuses_tampered_store_records(tmp_root):
    """Re-hashing seams: the immutable review source payload, the approval
    payload hash, the full media record digest and fresh reference digests are
    all re-verified at export; any drift refuses."""
    store = core.MaterialStore(root=tmp_root)
    ref_files = _write_ref_files(tmp_root)
    take, _path, _approval = _approve_decoded_take(store, tmp_root,
                                                   ref_files=ref_files)
    assert store.export_manifest(take['take_id'])['review_status'] == 'APPROVED'

    # ref content drift after approval refuses the export (fresh rehash vs the
    # pinned refs_sha256 binding — with or without a supplied sha)
    ref_files['a1'].write_bytes(b'drifted')
    with pytest.raises(core.AIError) as exc:
        store.export_manifest(take['take_id'])
    assert exc.value.code == 'REF_DRIFT'
    ref_files['a1'].write_bytes(b'ref-a1-identity-content')

    # full media metadata record tampered (duration) with identical video bytes:
    # the pinned media_sha256 binding refuses it
    conn = store._connect()
    with conn:
        row = conn.execute('SELECT media_json FROM takes WHERE take_id = ?',
                           (take['take_id'],)).fetchone()
        media = json.loads(row['media_json'])
        media['duration_sec'] = 99.0
        conn.execute('UPDATE takes SET media_json = ? WHERE take_id = ?',
                     (core._canonical_json(media), take['take_id']))
    conn.close()
    with pytest.raises(core.AIError) as exc:
        store.export_manifest(take['take_id'])
    assert exc.value.code == 'MEDIA_RECORD_DRIFT'
    conn = store._connect()
    with conn:
        conn.execute('UPDATE takes SET media_json = ? WHERE take_id = ?',
                     (core._canonical_json(json.loads(
                         core._canonical_json(take['media']))),
                      take['take_id']))
    conn.close()

    # approval payload edited without fixing approval_sha256 refuses
    conn = store._connect()
    with conn:
        row = conn.execute('SELECT approval_json FROM approvals WHERE take_id = ?',
                           (take['take_id'],)).fetchone()
        approval = json.loads(row['approval_json'])
        approval['material']['visual_description'] = 'tampered'
        conn.execute('UPDATE approvals SET approval_json = ? WHERE take_id = ?',
                     (core._canonical_json(approval), take['take_id']))
    conn.close()
    with pytest.raises(core.AIError) as exc:
        store.export_manifest(take['take_id'])
    assert exc.value.code == 'APPROVAL_RECORD_DRIFT'

    # review source payload tampered while its hash columns stay untouched
    conn = store._connect()
    with conn:
        row = conn.execute('SELECT source_payload FROM reviews WHERE take_id = ?',
                           (take['take_id'],)).fetchone()
        tampered = json.loads(row['source_payload'])
        tampered['review_status'] = 'PASS'
        conn.execute('UPDATE reviews SET source_payload = ? WHERE take_id = ?',
                     (core._canonical_json(tampered), take['take_id']))
    conn.close()
    with pytest.raises(core.AIError) as exc:
        store.export_manifest(take['take_id'])
    assert exc.value.code == 'REVIEW_RECORD_DRIFT'

    # legacy review rows without an immutable source payload are explicit blocks
    conn = store._connect()
    with conn:
        conn.execute(
            "INSERT INTO reviews (review_id, take_id, mode, review_json, "
            "review_sha256, source_payload, source_sha256, created_at) VALUES "
            "(?,?,?,?,?,?,?,?)",
            ('rev-legacy', take['take_id'], 'dry_run', '{"legacy": true}',
             '00' * 32, None, None, core._utc()))
    conn.close()
    with pytest.raises(core.AIError) as exc:
        store.export_manifest(take['take_id'])
    assert exc.value.code in ('REVIEW_RECORD_DRIFT', 'STALE_APPROVAL')


@pytest.mark.skipif(not _ffmpeg_available(), reason='approval gate path needs a real clip')
def test_export_requires_approval_and_double_approve_refused(tmp_root):
    store = core.MaterialStore(root=tmp_root)
    take, path = _register_decoded_take(store, tmp_root)
    with pytest.raises(core.AIError) as exc:
        store.export_manifest(take['take_id'])
    assert exc.value.code == 'EXPORT_REQUIRES_APPROVAL'
    store.approve(take['take_id'],
                  _approval_request(store, take, tmp_root, _write_ref_files(tmp_root)))
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'],
                      _approval_request(store, take, tmp_root, _write_ref_files(tmp_root)))
    assert exc.value.code == 'APPROVAL_EXISTS'


def test_usage_cross_shot_and_locked_reuse(tmp_root):
    store = core.MaterialStore(root=tmp_root)
    path = _write_media(tmp_root)
    take = store.register_take(NEW_SHOT, str(path))
    usage = store.append_usage(take['take_id'], 'shot-elsewhere-01', purpose='reuse')
    assert usage['target_shot_id'] == 'shot-elsewhere-01'
    _approve_decoded_take(store, tmp_root, version=2)
    locked_usage = store.append_usage(take['take_id'], 'shot-locked-02')
    assert locked_usage['purpose'] == 'reuse'  # locked/approved take stays reusable
    with pytest.raises(core.AIError) as exc:
        store.append_usage('nope.v1.local', 'shot-x')
    assert exc.value.code == 'TAKE_NOT_FOUND'


def test_append_review_rejects_foreign_take_id(tmp_root):
    store = core.MaterialStore(root=tmp_root)
    path = _write_media(tmp_root)
    take = store.register_take(NEW_SHOT, str(path))
    with pytest.raises(core.AIError) as exc:
        store.append_review(take['take_id'],
                            {'source': 'claude', 'status': 'verified', 'role': 'reviewer',
                             'take_id': 'some-other.v1.local',
                             'review_status': 'PASS'})
    assert exc.value.code == 'REVIEW_TAKE_MISMATCH'


def test_approve_human_binding_token_and_resolution_flow(tmp_root):
    """The human confirmation must echo the CURRENT context token and exact
    ranges; NEEDS_REVIEW evidence additionally needs the resolution token plus
    a hash-bearing evidence set."""
    store = core.MaterialStore(root=tmp_root)
    ref_files = _write_ref_files(tmp_root)
    take, _path = _register_decoded_take(store, tmp_root)  # NEEDS_REVIEW evidence
    wrong_token = _approval_request(store, take, tmp_root, ref_files,
                                    human_extra={'binding_token': 'cd' * 32})
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], wrong_token)
    assert exc.value.code == 'HUMAN_BINDING_MISMATCH'
    no_token = _approval_request(store, take, tmp_root, ref_files, skip_token=True)
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], no_token)
    assert exc.value.code == 'HUMAN_BINDING_REQUIRED'
    no_ranges = _approval_request(store, take, tmp_root, ref_files,
                                  human_extra={'confirmed_ranges': None})
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], no_ranges)
    assert exc.value.code == 'HUMAN_SCOPE_MISSING'
    naive_time = _approval_request(store, take, tmp_root, ref_files,
                                   human_extra={'confirmed_at': 'not-a-date'})
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], naive_time)
    assert exc.value.code == 'HUMAN_CONFIRMATION_MISSING'
    no_resolution = _approval_request(store, take, tmp_root, ref_files,
                                      resolution=False)
    with pytest.raises(core.AIError) as exc:
        store.approve(take['take_id'], no_resolution)
    assert exc.value.code == 'HUMAN_RESOLUTION_REQUIRED'
    good = _approval_request(store, take, tmp_root, ref_files)
    record = store.approve(take['take_id'], good)
    assert record['binding']['evidence_review_ids']
    manifest = store.export_manifest(take['take_id'])
    assert manifest['review_status'] == 'APPROVED'
    history = store.history(take['take_id'])
    assert 'stage_records' in history and 'stage_runs' in history


def test_approve_latest_return_outruns_earlier_pass(tmp_root):
    """A latest RETURN is a current rejection: no earlier PASS can approve."""
    store = core.MaterialStore(root=tmp_root)
    ref_files = _write_ref_files(tmp_root)
    path = _make_real_video(tmp_root)
    shot2 = dict(NEW_SHOT)
    shot2['shot_id'] = 'shot-LR'
    take2 = store.register_take(shot2, str(path))
    reviewer_pass = {**VALID_REVIEWER_OUTPUT, 'shot_id': 'shot-LR'}
    reviewer_return = {**reviewer_pass, 'review_status': 'RETURN'}
    store.append_review(take2['take_id'],
                        {'source': 'claude', 'status': 'verified', 'phase': 'B',
                         'structured_output': reviewer_pass},
                        mode='dry_run')
    store.append_review(take2['take_id'],
                        {'source': 'claude', 'status': 'verified', 'phase': 'B',
                         'structured_output': reviewer_return},
                        mode='dry_run')
    with pytest.raises(core.AIError) as exc:
        store.approve(take2['take_id'],
                      _approval_request(store, take2, tmp_root, ref_files))
    assert exc.value.code == 'CURRENT_REVIEW_REJECTED'
    assert store.get_take(take2['take_id'])['approval'] is None


def test_approve_direct_full_reviewer_positive(tmp_root):
    """A direct full §9 reviewer record (fields at top level, own take_id)
    approves and exports without false REVIEW_RECORD_DRIFT."""
    store = core.MaterialStore(root=tmp_root)
    ref_files = _write_ref_files(tmp_root)
    take, _path = _register_decoded_take(store, tmp_root)
    conn = store._connect()
    with conn:
        conn.execute('DELETE FROM reviews WHERE take_id = ?', (take['take_id'],))
    conn.close()
    store.append_review(take['take_id'], {
        'source': 'claude', 'status': 'verified', 'role': 'reviewer',
        'take_id': take['take_id'], 'shot_id': 'shot-101',
        'review_status': 'PASS', 'confirmed_issues': [], 'unverified_issues': [],
        'evidence': ['规则核对：叙述与可见信息一致'],
        'repair_action': None, 'prompt_revision': None, 'reference_changes': [],
        'regenerate_required': False, 'model_id': core.MODEL_ID}, mode='dry_run')
    request = _approval_request(store, take, tmp_root, ref_files, resolution=False)
    record = store.approve(take['take_id'], request)
    assert record['binding']['evidence_review_ids']
    manifest = store.export_manifest(take['take_id'])
    assert manifest['review_status'] == 'APPROVED'
    assert manifest['review_record_id'].startswith('rev-')


# --------------------------------------------------------------------------
# schemas (strict, authoritative field contracts)
# --------------------------------------------------------------------------

def test_schemas_are_strict_valid_draft7_and_full_field_contracts():
    def assert_strict(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node.get('additionalProperties') is False, list(node.get('properties', {}))
            for value in node.values():
                assert_strict(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict(value)

    for role, schema in core.SCHEMAS.items():
        jsonschema.Draft7Validator.check_schema(schema)
        assert_strict(schema)
    director_shot = core.SCHEMAS['director']['properties']['shots']['items']
    assert set(NEW_SHOT_FIELDS) == set(director_shot['properties'])  # user §5 exact
    writer_props = set(core.SCHEMAS['writer']['properties'])
    assert NEW_SHOT_FIELDS <= writer_props  # writer converts director intent to spec
    assert {'reference_requirements', 'notes', 'role'} <= writer_props
    reviewer_props = set(core.SCHEMAS['reviewer']['properties'])
    assert REVIEWER_FIELDS <= reviewer_props  # user §9 exact
    assert 'verdict' not in reviewer_props  # renamed to review_status in new contract
    manifest_props = set(core.SCHEMAS['manifest']['properties'])
    assert USER_MANIFEST_FIELDS <= manifest_props  # user §11 exact
    assert core.SCHEMAS['manifest']['properties']['h3_invoked'] == {'const': False}
    assert core.SCHEMAS['manifest']['properties']['review_status'] == {'const': 'APPROVED'}
    # repair-1 additive optional seams stay optional (old records remain valid)
    human_props = core.SCHEMAS['manifest']['properties']['approval']['properties'][
        'human']['properties']
    assert 'review_resolution' in human_props
    review_item_props = core.SCHEMAS['manifest']['properties']['reviews']['items'][
        'properties']
    for optional in ('review_status', 'binding_stamp', 'confirmed_issues',
                     'input_scope', 'lifecycle_status', 'cuda_execution_status',
                     'joint_head_status', 'refs_status', 'questions_digest'):
        assert optional in review_item_props, optional
    ref_item = director_shot['properties']['source_refs']['items']['properties']
    assert 'sha256' in ref_item  # ref identity may travel, paths never do
    assert 'path' not in ref_item


# --------------------------------------------------------------------------
# A4 — run_material_case / run_dry_run: full chain, zero inference, Mock clear
# --------------------------------------------------------------------------

def _material_case_root(tmp_root):
    (tmp_root / 'cases' / 'koc-dragonfruit-dryrun').mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / 'cases' / 'koc-dragonfruit-dryrun' / 'case.json',
                tmp_root / 'cases' / 'koc-dragonfruit-dryrun' / 'case.json')


def test_run_dry_run_full_chain_zero_inference(tmp_root):
    _material_case_root(tmp_root)
    result = core.run_dry_run(root=tmp_root)
    assert result['h3_invoked'] is False and result['paid_h3_calls'] == 0
    assert result['h3']['status'] == 'mock'
    assert result['vbench']['status'] == 'not_run'
    assert result['status'] == 'partial'  # real same-cases stay partial
    assert result['claude']['status'] == 'blocked'  # known-blocked, not re-issued
    assert result['claude']['known_blocked'] is True
    assert result['clef']['status'] == 'blocked'  # no inference in dry-run
    assert result['case']['case_id'] == 'koc-dragonfruit-dryrun'
    assert result['repair_gate'] == {'max_repairs_per_shot': 2,
                                     'regenerate_whole_batch': False, 'paid_budget': 0}
    shot_ids = [s['shot_id'] for s in result['shots']]
    assert len(shot_ids) == len(set(shot_ids)) and len(shot_ids) >= 12
    assert {'shot-%03d' % i for i in range(1, 13)} <= set(shot_ids)
    for shot in result['shots']:
        assert shot['mock'] is True and shot['h3_invoked'] is False
        assert shot['media']['video_path'] is None  # no fabricated video
    assert len(result['counterexamples']) == 10  # failures isolated, run not aborted
    dumped = json.dumps(result, ensure_ascii=False).lower()
    assert 'laox' not in dumped and 'h3_pipeline' not in dumped  # no protected paths
    assert all(ref['external_access'] is False for ref in result['refs'])
    # strict-JSON safe for CLI serialization
    json.dumps(result, allow_nan=False, ensure_ascii=False)


def test_run_dry_run_persists_per_stage_records_and_mock_takes(tmp_root):
    _material_case_root(tmp_root)
    result = core.run_dry_run(root=tmp_root)
    store = core.MaterialStore(root=tmp_root)
    stages = store.stage_history(result['run_id'])
    by_stage = {}
    for record in stages:
        by_stage.setdefault(record['stage'], []).append(record)
    n_shots = len(result['shots'])
    assert len(by_stage['director']) == 1
    assert len(by_stage['writer']) == n_shots  # skipped known-blocked records
    assert len(by_stage['writer_fallback']) == n_shots
    for stage in ('review_a', 'h3', 'review_b', 'reviewer', 'reviewer_fallback'):
        assert len(by_stage[stage]) == n_shots, stage
    assert all(r['mode'] == 'dry_run' for r in stages)  # non-shadow mode explicit
    assert result['stage_counts']['skipped'] == n_shots
    # mock fallbacks carry the FULL new shot contract, clearly mock
    fallback = by_stage['writer_fallback'][0]['record']
    assert fallback['mock'] is True and fallback['provenance'] == 'mock_fallback'
    assert set(NEW_SHOT_FIELDS) <= set(fallback['structured_output'])
    assert fallback['structured_output']['role'] == 'writer'
    jsonschema.Draft7Validator(core.SCHEMAS['writer']).validate(
        fallback['structured_output'])
    # reviewer fallback and A/B records are appended to the registered takes
    takes = store.list_takes()
    assert takes and all(t['mock'] for t in takes)
    assert all(t['provenance'] == 'mock_h3_dry_run' for t in takes)
    first = store.history(takes[0]['take_id'])
    phases = [r.get('phase') for r in first['reviews']]
    assert 'A' in phases and 'B' in phases
    b_records = [r for r in first['reviews'] if r.get('phase') == 'B']
    assert any(r.get('review_status') == 'NEEDS_REVIEW' for r in b_records)
    # no approvals anywhere in the dry-run
    assert all(t['approval'] is None for t in takes)
    with pytest.raises(core.AIError) as exc:
        store.approve(takes[0]['take_id'],
                      {'human': {}, 'evidence': {}, 'material': {}})
    assert exc.value.code == 'MOCK_NOT_APPROVABLE'


def test_run_dry_run_idempotent(tmp_root):
    _material_case_root(tmp_root)
    first = core.run_dry_run(root=tmp_root)
    store = core.MaterialStore(root=tmp_root)
    takes = store.list_takes()
    again = core.run_dry_run(root=tmp_root)  # rerun must not duplicate versions
    assert len(again['shots']) == len(first['shots'])
    assert len(store.list_takes()) == len(takes)


def _director_output(shots):
    return {'role': 'director', 'case_id': CASE['case_id'],
            'shots': [json.loads(json.dumps(shot)) for shot in shots],
            'concerns': []}


def test_run_material_case_injected_clients_full_model_chain(tmp_root):
    """UNIT clients drive the complete happy chain on REAL model outputs: the
    verified Director shots drive the chain, the Writer spec is what A/H3/B/
    registration see, and the Reviewer receives the raw A/B decisions, VBench
    state, references and take history. No mock fallback anywhere."""
    _material_case_root(tmp_root)
    director_shot = json.loads(json.dumps(NEW_SHOT))
    director_shot['action_process'] = ['导演镜头特有的独立动作链']
    writer_ok = json.loads(json.dumps(VALID_WRITER_OUTPUT))
    writer_ok['shot_id'] = director_shot['shot_id']
    writer_ok['material_id'] = director_shot['material_id']
    writer_ok['action_process'] = ['写手规格特有的 distinctive 动作']
    reviewer_ok = json.loads(json.dumps(VALID_REVIEWER_OUTPUT))
    reviewer_ok['shot_id'] = director_shot['shot_id']
    seen_reviewer_evidence = []
    clients = {
        'director': lambda payload: {'status': 'verified', 'source': 'claude',
                                     'llm_invoked': True,
                                     'structured_output': _director_output([director_shot])},
        'writer': lambda shot: {'status': 'verified', 'source': 'claude',
                                'llm_invoked': True,
                                'structured_output': {**writer_ok,
                                                      'shot_id': shot['shot_id'],
                                                      'material_id': shot['material_id']}},
        'review_a': lambda shot: {'status': 'partial', 'source': 'clef',
                                  'phase': 'A', 'review_status': 'PASS',
                                  'llm_invoked': True},
        'review_b': lambda shot, evidence: {'status': 'partial', 'source': 'clef',
                                            'phase': 'B', 'review_status': 'NEEDS_REVIEW',
                                            'llm_invoked': True},
        'reviewer': lambda shot, evidence: seen_reviewer_evidence.append(evidence) or {
            'status': 'verified', 'source': 'claude', 'llm_invoked': True,
            'structured_output': {**reviewer_ok, 'shot_id': shot['shot_id']}},
    }
    result = core.run_material_case(root=tmp_root, clients=clients)
    counts = result['stage_counts']
    n = len(result['shots'])
    assert n == 1  # the verified Director shots drove the chain, not the plan
    assert result['director_source'] == 'model_director_shots'
    assert counts['writer_model'] == n and counts['reviewer_model'] == n
    assert counts['writer_mock_fallback'] == 0 and counts['skipped'] == 0
    store = core.MaterialStore(root=tmp_root)
    stages = store.stage_history(result['run_id'])
    assert not [s for s in stages if s['stage'].endswith('_fallback')]
    assert result['registration_failures'] == []
    # the EXECUTED spec is the Writer output: it is registered and flows to A/H3
    take = store.get_take(result['shots'][0]['take_id'])
    assert take['shot']['action_process'] == ['写手规格特有的 distinctive 动作']
    h3 = [s for s in stages if s['stage'] == 'h3'][0]
    assert h3['record']['input_spec_sha256'] == core._canonical_hash(
        json.loads(core._canonical_json(take['shot'])))
    # the Reviewer received the raw A/B decisions, VBench, refs and history
    evidence = seen_reviewer_evidence[0]
    assert evidence['a_decision'] and evidence['b_decision']
    assert evidence['a_decision']['review_status'] == 'PASS'
    assert evidence['vbench']['status'] == 'not_run'
    assert evidence['refs'] and evidence['history']
    # the shared Director stage record is linked into the take history
    history = store.history(take['take_id'])
    assert 'director' in {s['stage'] for s in history['stage_records']}


def test_run_material_case_blocked_director_propagates_skipped_stages(tmp_root):
    """Blocked Director -> explicit skipped Writer stage records (no fabricated
    model output), Mock fallback chain continues for every shot."""
    _material_case_root(tmp_root)
    def director_should_not_be_called(payload):  # pragma: no cover - must not run
        raise AssertionError('writer must not be called when director is blocked')
    result = core.run_material_case(root=tmp_root, clients={
        'director': lambda p: {'status': 'blocked', 'error_code': 'AUTH_EXPIRED'},
        'writer': director_should_not_be_called})
    stages = core.MaterialStore(root=tmp_root).stage_history(result['run_id'])
    skipped = [s for s in stages if s['stage'] == 'writer'
               and s['record'].get('status') == 'skipped']
    assert len(skipped) == len(result['shots'])
    assert all('no fabricated model output' in s['record']['reason'] for s in skipped)
    assert result['stage_counts']['writer_mock_fallback'] == len(result['shots'])


def test_run_material_case_per_shot_failure_isolated(tmp_root):
    """A writer client failing for ONE shot records that shot's failure without
    regenerating or aborting the batch; the repair gate stays max-2 / budget 0."""
    _material_case_root(tmp_root)
    writer_ok = json.loads(json.dumps(VALID_WRITER_OUTPUT))
    seen = []

    failing_shot = json.loads(json.dumps(NEW_SHOT))

    def flaky_writer_for_director(shot):
        seen.append(shot['shot_id'])
        if shot['shot_id'] == failing_shot['shot_id']:
            raise RuntimeError('unit failure with secret-token')
        return {'status': 'verified', 'source': 'claude', 'llm_invoked': True,
                'structured_output': {**writer_ok, 'shot_id': shot['shot_id'],
                                      'material_id': shot['material_id']}}

    result = core.run_material_case(root=tmp_root, clients={
        'director': lambda p: {'status': 'verified', 'source': 'claude',
                               'llm_invoked': True,
                               'structured_output': _director_output([failing_shot])},
        'writer': flaky_writer_for_director})
    n = len(result['shots'])
    assert len(seen) == n  # every shot attempted: no whole-batch regeneration
    assert result['stage_counts']['writer_model'] == n - 1
    stages = core.MaterialStore(root=tmp_root).stage_history(result['run_id'])
    writer_blocked = [s for s in stages if s['stage'] == 'writer'
                      and s['record'].get('error_code') == 'CLIENT_ERROR']
    assert len(writer_blocked) == 1
    assert 'secret-token' not in json.dumps(stages)  # exception value never recorded
    assert result['repair_gate']['max_repairs_per_shot'] == 2
    assert result['repair_gate']['paid_budget'] == 0


# --------------------------------------------------------------------------
# A5 — real CLI consumer contract
# --------------------------------------------------------------------------

def test_real_cli_schema_export_matches_core():
    result = subprocess.run(
        [sys.executable, str(ROOT / 'scripts' / 'commodity-materials.py'), 'schema'],
        capture_output=True, timeout=120, cwd=str(ROOT))
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')
    data = json.loads(result.stdout.decode('utf-8'))
    assert set(data) == {'director', 'writer', 'reviewer', 'manifest'}
    assert data == json.loads(json.dumps(core.SCHEMAS))


def test_cli_module_consumed_methods_exist():
    spec = importlib.util.spec_from_file_location(
        'commodity_materials_cli', ROOT / 'scripts' / 'commodity-materials.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    core_mod = cli.load_core()
    assert core_mod.MODEL_ID == 'claude-opus-5-5'
    adapter = core_mod.ClaudeCLIAdapter(root=ROOT, executable='C:/fake/claude.exe',
                                        runner=make_runner(stdout=claude_stdout(
                                            structured=VALID_REVIEWER_OUTPUT)))
    assert adapter.invoke('reviewer', {'shot_id': 'shot-001'})['status'] == 'verified'
    store = core_mod.MaterialStore(root=ROOT / 'runtime' / 'tmp' / 'commodity-zcode' / 'cli-probe')
    assert isinstance(store.list_takes(), list)




# --------------------------------------------------------------------------
# A6 — round-2 reproduced defects (coordinator/Sol/Luna audits 2026-10-11)
# --------------------------------------------------------------------------

def test_clef_raw_missing_or_mismatched_probabilities_blocked(monkeypatch):
    """Native raw must EXIST and MATCH the normalized values for all three
    heads: missing raw, or raw .01 vs normalized .87, used to pass as valid."""
    cases = [
        ('answers.clear.raw', ..., 'RAW_NOUL_PROBABILITY_MISSING'),
        ('answers.clear.raw.noul', 0.01, 'RAW_NOUL_PROBABILITY_MISMATCH'),
        ('answers.route.raw', {'probabilities': {'pass': 0.01, 'return': 0.99}},
         'RAW_CHOICE_PROBABILITY_MISMATCH'),
        ('answers.route.raw', {'choice': 'pass'}, 'RAW_CHOICE_PROBABILITY_MISSING'),
        ('answers.quality.raw', {'probabilities': {'0': 1.0, '1': 0.0, '2': 0.0}},
         'RAW_SCORE_PROBABILITY_MISMATCH'),
        ('answers.quality.raw', {'score': 2.0}, 'RAW_SCORE_PROBABILITY_MISSING'),
    ]
    for dotted, value, code in cases:
        decision = json.loads(json.dumps(VALID_DECISION))
        deep_update(decision, dotted, value)
        monkeypatch.setattr(core, '_http_json', clef_fixture(decision=decision))
        review = core.ClefAdapter().review('A', NEW_SHOT, lifecycle=OwnedLifecycle())
        assert review['status'] == 'blocked', (dotted, review)
        assert review['error_code'] == code, (dotted, review['error_code'], code)
        assert review['review_status'] == 'TECHNICAL_ERROR'


def test_clef_state_carries_full_new_contract_fields(monkeypatch):
    http = clef_fixture()
    monkeypatch.setattr(core, '_http_json', http)
    core.ClefAdapter().review('A', NEW_SHOT, lifecycle=OwnedLifecycle())
    sent = [c['payload'] for c in post_calls(http)][0]
    state = json.loads(sent['state'])
    for key in ('source_refs', 'character_refs', 'product_refs',
                'required_visuals', 'forbidden_visuals', 'generation_prompt'):
        assert key in state, key
    assert state['source_refs'][0]['ref_id'] == 'a1'
    assert 'path' not in json.dumps(state)  # still text identifiers only


def test_claude_model_usage_block_is_authoritative(tmp_root):
    """modelUsage reporting a different model is a mismatch even when the JSON
    self-reported label claims the exact model."""
    payload = json.dumps({
        'model': 'claude-opus-5-5', 'is_error': False,
        'modelUsage': {'model': 'claude-sonnet-4-5', 'input_tokens': 10},
        'structured_output': VALID_REVIEWER_OUTPUT}).encode('utf-8')
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe',
                                    runner=make_runner(stdout=payload))
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked' and record['error_code'] == 'MODEL_MISMATCH'


def test_claude_invoke_guards_link_escape_before_lock_creation(tmp_root):
    """A junction under runtime/ must be refused BEFORE the single-flight lock
    or the isolated cwd is created — otherwise lock files would be written
    through the junction outside the project."""
    outside = tmp_root.parent / ('outside-' + tmp_root.name)
    outside.mkdir(parents=True, exist_ok=True)
    runtime_dir = tmp_root / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    junction = tmp_root / 'runtime' / 'commodity-material-ai-v1'
    made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip('junction creation unavailable in this environment')
    runner = make_runner(stdout=claude_stdout(structured=VALID_REVIEWER_OUTPUT))
    adapter = core.ClaudeCLIAdapter(root=tmp_root, executable='C:/fake/claude.exe',
                                    runner=runner)
    record = adapter.invoke('reviewer', {'shot_id': 'shot-001'})
    assert record['status'] == 'blocked'
    assert record['error_code'] == 'ISOLATED_CWD_UNAVAILABLE'
    assert runner.calls == []
    assert not list(outside.glob('*.lock'))  # nothing written through the junction


def _make_junction_if_possible(_root):
    return sys.platform == 'win32'


def test_run_material_case_validates_root_before_any_read(monkeypatch):
    """The canonical root guard runs BEFORE the case file is read: a forbidden
    external root is refused without a single external filesystem probe."""
    def no_read(self, *args, **kwargs):
        raise AssertionError('case file read attempted before root validation')
    monkeypatch.setattr(Path, 'read_text', no_read)
    with pytest.raises(core.AIError) as exc:
        core.run_material_case(root='F:/outside-the-lab/case-root')
    assert exc.value.code == 'ROOT_OUTSIDE_PROJECT'


@pytest.mark.skipif(not _ffmpeg_available(), reason='next-version flow needs the chain')
def test_run_material_case_preserves_legacy_rows_and_registers_next_version(tmp_root):
    """The real root store keeps 13 legacy Mock v1.dryrun rows: a changed NEW
    contract must register the NEXT version, never overwrite the legacy row,
    and repeated identical runs reuse the same version."""
    _material_case_root(tmp_root)
    store = core.MaterialStore(root=tmp_root)
    legacy_shot = dict(PLAN_SHOT)  # what the initial validation dry-run registered
    legacy = store.register_take(legacy_shot, None, version=1, batch_id='dryrun',
                                 provenance='mock_h3_dry_run')
    first = core.run_dry_run(root=tmp_root)
    take_id = first['shots'][0]['take_id']
    assert take_id != legacy['take_id']  # next version, no collision
    assert take_id.endswith('.v2.dryrun')
    assert store.get_take(legacy['take_id'])['shot'] == legacy_shot  # untouched
    second = core.run_dry_run(root=tmp_root)
    assert second['shots'][0]['take_id'] == take_id  # idempotent reuse
    assert len(store.list_takes()) == len({t['take_id'] for t in store.list_takes()})


def test_run_material_case_writer_schema_invalid_record_preserved(tmp_root):
    """A Writer record that fails the writer schema keeps its REAL SCHEMA_INVALID
    failure record (never overwritten by a generic known-blocked stub) and the
    chain continues on the clearly-Mock fallback."""
    _material_case_root(tmp_root)
    result = core.run_material_case(root=tmp_root, clients={
        'director': lambda p: {'status': 'verified', 'source': 'claude',
                               'llm_invoked': True,
                               'structured_output': _director_output([NEW_SHOT])},
        'writer': lambda shot: {'status': 'verified', 'source': 'claude',
                                'llm_invoked': True,
                                'structured_output': {'role': 'writer',
                                                      'invented': 'shape'}}})
    stages = core.MaterialStore(root=tmp_root).stage_history(result['run_id'])
    invalid = [s for s in stages if s['stage'] == 'writer'
               and s['record'].get('error_code') == 'SCHEMA_INVALID']
    assert len(invalid) == 1  # real failure preserved
    assert result['stage_counts']['writer_mock_fallback'] == 1
    assert result['stage_counts']['skipped'] == 0
    assert result['shots'][0]['spec_provenance'] == 'mock_fallback'


def test_run_material_case_bridge_default_not_run(tmp_root):
    _material_case_root(tmp_root)
    result = core.run_dry_run(root=tmp_root)
    stages = core.MaterialStore(root=tmp_root).stage_history(result['run_id'])
    bridge = [s for s in stages if s['stage'] == 'bridge']
    assert len(bridge) == 1 and bridge[0]['record']['status'] == 'not_run'
    called = []
    result2 = core.run_material_case(root=tmp_root, clients={
        'bridge': lambda payload: called.append(payload) or {
            'status': 'verified', 'linked_shot_ids': ['shot-001']}})
    assert called and 'shots' in called[0]
    stages2 = core.MaterialStore(root=tmp_root).stage_history(result2['run_id'])
    bridge2 = [s for s in stages2 if s['stage'] == 'bridge']
    assert bridge2[0]['record']['status'] == 'verified'
