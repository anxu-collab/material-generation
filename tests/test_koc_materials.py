"""KOC material integration tests (director_lab side; run with the consumer Python).

Covers: immutable source import, policy self-checks, deterministic planning, exact
PASS/RETURN counterexamples, bounded repair with locked PASS versions, manifest
export semantics (TEST flag, immutability), dry-run generation tasks, and native
storyboardTeam readiness (structural + live registry; LLM stays not_run).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import koc_rules, product_storyboards  # noqa: E402
from adapters import material_manifest as mm  # noqa: E402
from adapters import storyboard_team  # noqa: E402

CASE = json.loads((ROOT / 'cases' / 'koc-dragonfruit-dryrun' / 'case.json').read_text(encoding='utf-8'))


def test_immutable_source_import_verified():
    result = koc_rules.verify_source_import()
    assert result['ok'] and result['zip_ok']
    assert all(result['files'].values()) and len(result['files']) == 7


def test_policy_self_checks_pass():
    assert koc_rules.validate_policy_file() == []
    policy = koc_rules.load_policy()
    assert len(policy['overrides']) >= 4  # manual rounds / full-film / native-speech / compositor disabled
    assert {o['id'] for o in policy['overrides']} >= {
        'disable-self-execution-and-confirmation-cards', 'disable-whole-film-default',
        'disable-native-speech-default', 'disable-final-compositor'}
    assert policy['manifest']['vbench_missing_weights_status'] == ['not_run', 'blocked']


def test_four_primary_references_available_hook_optional():
    refs = koc_rules.primary_references_available()
    required = [n for n in refs if n != 'koc-video/references/hook-decision.md']
    assert all(refs[n] for n in required), refs
    assert 'koc-video/references/hook-decision.md' in refs  # optional, opt-in only


def test_unverified_claim_markers_deterministic():
    assert koc_rules.find_unverified_claims('这种火龙果富含维生素C。') == ['富含']
    assert koc_rules.find_unverified_claims('撕开封口。') == []


def _unit_generic_case(with_action_hints=False, refs_override=None, provenance=None,
                       test_flag=True):
    """UNIT-ONLY synthetic coffee case (EK-15 acceptance): explicitly labelled synthetic
    test input — NOT real coffee assets, NOT production acceptance. Mirrors the
    coordinator's generic-product adversarial shape."""
    segs = [
        {"segment_id": "seg-001", "text": "打开咖啡包装，展示咖啡粉。", "source_kind": "synthetic_test_input", "duration_sec": 2.4},
        {"segment_id": "seg-002", "text": "咖啡粉的颗粒特写。", "source_kind": "synthetic_test_input", "duration_sec": 2.0},
    ]
    if with_action_hints:
        segs[0]['action'] = {"start": "手扶住果子", "process": ["对半切开果子"], "end": "果子切成两半的稳定状态"}
        segs[1]['action'] = {"start": "镜头对准咖啡粉", "process": ["展示咖啡粉颗粒"], "end": "颗粒细节清晰可见"}
    return {
        'case_id': 'unit-coffee-generic',
        'created_utc': '2026-10-11T00:00:00',
        'test_flag': test_flag,
        'product': {
            'product_id': 'TEST-COFFEE', 'name': '测试咖啡', 'subject_term': '咖啡',
            'identity_verified': False,
            'facts': [{'text': 'UNIT ONLY 合成测试输入：咖啡产品为通用适配反例，非真实素材',
                       'source_kind': 'synthetic_test_input',
                       'source_ref': 'tests/test_koc_materials.py::_unit_generic_case'}],
        },
        'generated_defaults': {'status': 'to_be_generated', 'provider': None, 'verified_real_product_test': False},
        'audio_defaults': {'has_original': False, 'must_preserve': False, 'policy': 'discard_allowed'},
        'narration_segments': segs,
        'refs': refs_override or [
            {'ref_id': 'r1', 'path': 'F:/unit-only/not-real.mp4', 'sha256': 'a' * 64,
             'role': 'demo_footage_reference', 'test_only': True},
        ],
        'fixture_technical': {'r1': {'duration_sec': 5.0, 'probe_sha256': 'b' * 64}},
        **({'provenance': provenance} if provenance is not None else {}),
    }


def test_generic_case_without_hints_returns_and_leaks_nothing_case_specific():
    """UNIT ONLY. A generic product case without action hints must not get fruit/H3/
    fixture values from anywhere: every shot RETURNs ACTION_HINT_MISSING and the whole
    plan carries no demo-case wording (EK-15 / coordinator finding 4)."""
    case = _unit_generic_case()
    result = product_storyboards.run_pipeline(case)
    base = [s for s in result['plan']['shots'] if not s['shot_id'].startswith('ce-')]
    assert base and all(s['action_source'] == 'missing_hint' for s in base)
    codes = {p['code'] for s in result['review']['shots'] for p in s['problems']}
    assert 'ACTION_HINT_MISSING' in codes
    assert result['final']['pass'] == []
    plan_json = json.dumps(result['plan'], ensure_ascii=False)
    for leak in ('火龙果', '果子', '果肉', '籽粒', 'H3', 'existing_h3_generated_demo',
                 '768', '1376', '演示'):
        assert leak not in plan_json, leak
    # the RETURN carries a precise minimal missing-input recommendation
    problem = next(p for s in result['review']['shots'] for p in s['problems']
                   if p['code'] == 'ACTION_HINT_MISSING')
    assert problem['evidence'] and 'action' in problem['minimal_fix']


def test_coffee_narration_with_fruit_action_hint_is_returned():
    """UNIT ONLY. Reviewer must catch coffee narration + fruit-cut action even when a
    (wrongly copied) fruit action hint is supplied (coordinator finding 4)."""
    case = _unit_generic_case(with_action_hints=True)
    review = product_storyboards.review_plan(product_storyboards.build_plan(case))
    by_id = {s['shot_id']: s for s in review['shots']}
    coffee_cut = by_id['shot-001']
    assert coffee_cut['verdict'] == 'RETURN'
    assert 'ACTION_OBJECT_MISMATCH' in [p['code'] for p in coffee_cut['problems']]
    # a correctly case-driven action (coffee-powder closeup) passes this guard
    assert 'ACTION_OBJECT_MISMATCH' not in [p['code'] for p in by_id['shot-002']['problems']]


def test_stale_subject_term_cannot_mask_coffee_fruit_contradiction():
    """UNIT ONLY (coordinator stale-subject negative, msg_6b74fc580ece, evidence
    coordinator-stale-subject-negative.json): product.name=测试咖啡 with a residual
    subject_term=果子 and an explicit fruit action must still RETURN — subject_term is
    corroborated only when the case's own narration/name uses it, never an exemption
    that masks a narration/product contradiction."""
    case = _unit_generic_case(with_action_hints=True)
    case['product']['subject_term'] = '果子'   # stale residue from another case
    review = product_storyboards.review_plan(product_storyboards.build_plan(case))
    verdict = next(s for s in review['shots'] if s['shot_id'] == 'shot-001')
    assert verdict['verdict'] == 'RETURN'
    assert 'ACTION_OBJECT_MISMATCH' in [p['code'] for p in verdict['problems']]
    # the corroborated dragonfruit case keeps using its subject term (name/narration
    # actually establish it) — no false positive introduced by the corroboration rule
    plan = product_storyboards.build_plan(CASE)
    dragon = product_storyboards.review_plan(plan)
    base = [s for s in dragon['shots'] if not s['shot_id'].startswith('ce-')]
    assert all('ACTION_OBJECT_MISMATCH' not in [p['code'] for p in s['problems']] for s in base)


def test_no_fitting_ref_returns_instead_of_blind_fallback():
    """UNIT ONLY. A purpose with no duty-fitting ref RETURNs NO_FITTING_REF_FOR_PURPOSE;
    the plan never silently rotates onto an unrelated role ref (EK-15)."""
    case = _unit_generic_case(refs_override=[
        {'ref_id': 'r1', 'path': 'F:/unit-only/not-real.mp4', 'sha256': 'a' * 64,
         'role': 'scale_reference', 'test_only': True},
    ])
    plan = product_storyboards.build_plan(case)
    shot = next(s for s in plan['shots'] if s['shot_id'] == 'shot-001')
    assert shot['ref_fit'] == 'none_fitting'
    review = product_storyboards.review_plan(plan)
    verdict = next(s for s in review['shots'] if s['shot_id'] == 'shot-001')
    assert 'NO_FITTING_REF_FOR_PURPOSE' in [p['code'] for p in verdict['problems']]
    assert verdict['verdict'] == 'RETURN'


def test_case_driven_action_hints_flow_into_plan():
    """Positive: declared per-segment action/framing hints ARE the plan content."""
    plan = product_storyboards.build_plan(CASE)
    seg_actions = {s['segment_id']: s['action'] for s in CASE['narration_segments']}
    for shot in plan['shots']:
        if shot['shot_id'].startswith('ce-') or '-b' in shot['shot_id']:
            continue
        seg_id = shot['shot_id'].replace('shot', 'seg')
        assert shot['action_source'] == 'case_input'
        assert shot['action'] == seg_actions[seg_id], shot['shot_id']
        assert shot['ref_fit'] == 'fitting'


def test_exporter_refuses_undeclared_provenance_and_production_flag():
    """UNIT ONLY. TEST exporter honesty: no provenance declaration -> refuse; an
    explicitly production case -> PRODUCTION_EXPORT_UNSUPPORTED (EK-15)."""
    case_no_prov = _unit_generic_case(with_action_hints=True)
    result = product_storyboards.run_pipeline(case_no_prov)
    with pytest.raises(ValueError, match='CASE_PROVENANCE_MISSING'):
        mm.export_manifest(result, case_no_prov)
    prod_case = _unit_generic_case(with_action_hints=True, test_flag=False,
                                   provenance={'origin': 'generated_for_case',
                                               'product_identity_verified': False, 'note': 'UNIT'})
    with pytest.raises(ValueError, match='PRODUCTION_EXPORT_UNSUPPORTED'):
        mm.export_manifest(product_storyboards.run_pipeline(prod_case), prod_case)


def test_export_carries_only_case_declared_values():
    """Positive: exported keywords/person/location/technical/provenance come from the
    case declaration, and a provenance-declared generic case exports without any
    fixture hardcoding (EK-15)."""
    case = _unit_generic_case(with_action_hints=True,
                              provenance={'origin': 'generated_for_case',
                                          'product_identity_verified': False,
                                          'note': 'UNIT ONLY 合成测试输入'})
    result = product_storyboards.run_pipeline(case)
    manifest = mm.export_manifest(result, case)
    assert mm.validate_manifest(manifest) == []
    for entry in manifest['entries']:
        assert '火龙果' not in entry['keywords'] and '演示' not in entry['keywords']
        assert entry['keywords'][0] == entry['applicability']['uses'][0]
        assert entry['semantics']['person'] == '未声明（输入未提供出镜信息）'
        assert entry['semantics']['location'] == '未声明（输入未提供场景信息）'
        assert 'width' not in entry['technical'] and 'height' not in entry['technical']
        assert entry['evidence']['provenance']['origin'] == 'generated_for_case'
        assert entry['auto_edit_eligible'] is False and entry['test_flag'] is True
    dragon = mm.export_manifest(product_storyboards.run_pipeline(CASE), CASE)
    first = dragon['entries'][0]
    assert '火龙果' in first['keywords']                      # from CASE keyword_tags
    assert first['semantics']['person'].startswith('仅手部出镜')  # from CASE person_appearance
    assert first['technical'].get('width') == 768 and first['technical'].get('height') == 1376
    assert first['evidence']['provenance']['origin'] == 'existing_h3_generated_demo'


def test_conservative_clip_limit_from_evidence_only():
    assert koc_rules.conservative_clip_limit_sec([6.583333, 5.166667]) == 5.167
    assert koc_rules.conservative_clip_limit_sec([]) is None  # no evidence -> no invented limit


def test_plan_deterministic_and_schema_valid():
    import jsonschema
    a = product_storyboards.build_plan(CASE)
    b = product_storyboards.build_plan(CASE)
    assert koc_rules.canonical_hash(a) == koc_rules.canonical_hash(b)
    schema = json.loads((ROOT / 'schemas' / 'product-storyboards-v1.schema.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator.check_schema(schema)  # schema itself must be valid
    jsonschema.Draft202012Validator(schema).validate(a)
    rules = koc_rules.planning_rules()
    assert len(a['shots']) >= rules['min_planned_shots']
    assert len(a['planning']['distinct_use_purposes']) >= 4
    assert a['planning']['llm_invoked'] is False
    assert a['planning']['engine'] == 'deterministic-local-v1'


def test_every_counterexample_returns_with_its_exercised_code():
    plan = product_storyboards.build_plan(CASE)
    review = product_storyboards.review_plan(plan)
    by_id = {s['shot_id']: s for s in review['shots']}
    for ce in CASE['counterexamples']:
        verdict = by_id[ce['shot_id']]
        assert verdict['verdict'] == 'RETURN', ce['shot_id']
        assert ce['exercising'] in [p['code'] for p in verdict['problems']], ce['shot_id']
        for problem in verdict['problems']:
            assert problem['evidence'] and problem['minimal_fix']
    base = [s for s in review['shots'] if not s['shot_id'].startswith('ce-')]
    assert all(s['verdict'] == 'PASS' for s in base)


def test_pipeline_repairs_only_returned_and_locks_pass_versions():
    plan = product_storyboards.build_plan(CASE)
    first = product_storyboards.review_plan(plan)
    locked = {s['shot_id']: s['content_sha256'] for s in first['shots'] if s['verdict'] == 'PASS'}
    result = product_storyboards.run_pipeline(CASE)
    assert 1 <= result['repair_rounds'] <= koc_rules.planning_rules()['repair_budget']
    for shot_id, content_hash in locked.items():
        shot = next(s for s in result['plan']['shots'] if s['shot_id'] == shot_id)
        assert product_storyboards.shot_content_hash(shot) == content_hash  # PASS never mutated
        assert shot['version'] == 1
    for shot in result['plan']['shots']:
        if shot.get('repair_attempts'):
            assert shot['repair_attempts'] <= 2 and shot['version'] == 1 + shot['repair_attempts']
    blocked = {b['shot_id']: b['problems'] for b in result['final']['blocked']}
    assert blocked == {'ce-007': ['MISSING_REFS'], 'ce-008': ['UNSAFE_COMPLEX_GRIP_NEEDS_3D']}
    assert len(result['final']['pass']) >= 10


def test_manifest_export_test_only_and_semantically_valid():
    import jsonschema
    result = product_storyboards.run_pipeline(CASE)
    manifest = mm.export_manifest(result, CASE)
    schema = json.loads((ROOT / 'schemas' / 'material-manifest-v1.schema.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator.check_schema(schema)  # meta-validation, not just instance checks
    jsonschema.Draft202012Validator(schema).validate(manifest)
    assert mm.validate_manifest(manifest) == []
    assert manifest['test_flag'] is True
    assert len(manifest['entries']) == len(result['final']['pass'])
    for entry in manifest['entries']:
        assert entry['auto_edit_eligible'] is False           # TEST entries never production-eligible
        assert entry['vbench']['status'] == 'not_run'          # weights missing -> never invented PASS
        assert entry['evidence']['provenance']['product_identity_verified'] is False
        assert entry['review']['content_sha256'] == mm.entry_content_hash(entry)
        assert entry['source_sha256'] in {r['sha256'] for r in CASE['refs']}


def test_manifest_pass_version_immutable_changed_content_versions_up():
    result = product_storyboards.run_pipeline(CASE)
    manifest = mm.export_manifest(result, CASE)
    again = mm.export_manifest(result, CASE)
    first = {e['material_id']: e for e in manifest['entries']}
    second = {e['material_id']: e for e in again['entries']}
    assert first == second  # same PASS content re-exports identical, never overwritten
    entry = manifest['entries'][0]
    updated = mm.reexport_with_change(entry, {'narration': {'text': '改动后的旁白。', 'language': 'zh'}})
    assert updated['version'] == entry['version'] + 1
    assert updated['review']['verdict'] == 'RETURN' and updated['review']['content_sha256'] is None


def test_generation_tasks_dry_run_only_zero_paid():
    result = product_storyboards.run_pipeline(CASE)
    tasks = mm.export_generation_tasks(result, CASE)
    assert tasks['dry_run'] is True
    assert tasks['budget'] == {'paid_calls': 0, 'uploads': 0, 'llm_calls': 0, 'weight_downloads': 0, 'dry_run_default': True}
    blocked = [t for t in tasks['tasks'] if t['state'] == 'blocked']
    assert {t['shot_id'] for t in blocked} == {'ce-007', 'ce-008'}
    assert all(t['paid_calls'] == 0 for t in tasks['tasks'])


def test_storyboard_team_files_carry_rules_and_knowledge_registered():
    report = storyboard_team.verify_team_files()
    assert report['problems'] == []
    for member in ('director', 'writer', 'reviewer'):
        assert report['members'][member]['product_rules_appended'] is True
        member_path = ROOT / 'runtime/toonflow-data/agents/storyboardTeam/members' / (member + '.md')
        original_marker = '# 导演' if member == 'director' else '# 分镜'
        assert original_marker in member_path.read_text(encoding='utf-8')
    # coordinator-authorized 28th-file edit: product-materials.md registered natively
    assert report['knowledge_registered'] is True


def test_storyboard_team_live_registry_enabled_or_reported_down():
    live = storyboard_team.verify_live_registry()
    if live['status'] == 'service_not_running':
        pytest.skip('loopback service not running; structural readiness still verified above')
    assert live['status'] == 'registered' and live['enabled'] is True and not live['load_error']
    assert live['llm_invocation'] == 'not_run'


def test_storyboard_team_knowledge_loads_via_native_teamresources():
    native = storyboard_team.verify_native_read()
    if native['status'] == 'service_not_running':
        pytest.skip('loopback service not running; registry evidence still verified above')
    assert native['status'] == 'native_read_ok'
    assert native['knowledge_loadable_natively'] is True
    assert native['llm_invocation'] == 'not_run'


def test_commodity_shots_workspace_bridge_explicit_mock_roundtrip():
    import base64

    class MockMcpClient:
        is_mock = True

        def __init__(self):
            self.calls = []
            self.files = {}

        def get_tools(self, timeout):
            return [
                {'name': 'getAppState', 'inputSchema': {'properties': {}}},
                {'name': 'workspaceFiles', 'inputSchema': {
                'properties': {
                    'target': {'properties': {'directory': {'type': 'string'}}},
                    'args': {'properties': {
                        'action': {'enum': ['mkdir', 'readBinary', 'writeBinary']},
                        'path': {'type': 'string'}, 'base64': {'type': 'string'},
                        'exclusive': {'type': 'boolean'},
                    }},
                },
            }}]

        def call_tool(self, name, arguments, timeout):
            self.calls.append((name, arguments))
            if name == 'getAppState':
                return {'connections': [], 'workspaceRoot': str(ROOT / 'runtime/toonflow-data/workspaces')}
            args = arguments['args']
            directory = arguments['target']['directory']
            key = (directory, args['path'])
            if args['action'] == 'mkdir':
                return {'success': True}
            if args['action'] == 'writeBinary':
                assert args['exclusive'] is True
                assert key not in self.files
                self.files[key] = base64.b64decode(args['base64'])
                return {'success': True}
            if args['action'] == 'readBinary':
                return {'path': args['path'], 'base64': base64.b64encode(self.files[key]).decode('ascii')}
            raise AssertionError('unexpected workspaceFiles action: ' + args['action'])

    client = MockMcpClient()
    shots = [{'shot_id': 'commodity-test-001', 'purpose': 'detail', 'narration': '展示商品标签'}]
    target = {
        'directory': str(ROOT / 'runtime/toonflow-data/workspaces/commodity-test-workspace'),
        'path': 'commodity-shots.json',
    }
    result = storyboard_team.import_commodity_shots(shots, target, timeout=15, client=client)

    assert result['status'] == 'verified'
    assert result['mock'] is True
    assert result['verification_scope'] == 'UNIT_ONLY_mock_transport'
    assert result['bridge'] == 'workspaceFiles-json'
    assert result['input_sha256'] == result['readback_sha256']
    assert result['readback_status'] == 'verified'
    assert result['node_display'] == 'blocked_no_connected_page'
    assert result['node_edit'] == 'blocked_no_connected_page'
    assert result['llm_invocation'] == 'not_run'
    assert result['paid_calls'] == result['h3_calls'] == 0
    assert [name for name, _args in client.calls] == ['getAppState', 'workspaceFiles', 'workspaceFiles', 'workspaceFiles']
    assert [args['args']['action'] for name, args in client.calls if name == 'workspaceFiles'] == ['mkdir', 'writeBinary', 'readBinary']


@pytest.mark.parametrize('shots', [
    [{'shot_id': 'bad-nan', 'score': float('nan')}],
    [{'shot_id': 'bad-object', 'value': object()}],
])
def test_commodity_bridge_rejects_non_json_values_as_blocked(shots):
    class NoCallMock:
        is_mock = True
        calls = []

        def get_tools(self, timeout):
            self.calls.append('get_tools')
            return []

        def call_tool(self, name, arguments, timeout):
            self.calls.append(name)
            return {}

    client = NoCallMock()
    result = storyboard_team.import_commodity_shots(
        shots,
        {'directory': str(ROOT / 'runtime/toonflow-data/workspaces/non-json-test'),
         'path': 'shots.json'},
        client=client,
    )
    assert result['status'] == 'blocked'
    assert result['readback_status'] == 'not_run'
    assert client.calls == []


def test_commodity_bridge_rejects_external_literal_target_before_client_use(monkeypatch):
    original_resolve = Path.resolve

    def guarded_resolve(path, *args, **kwargs):
        if 'laox_account' in str(path).lower():
            raise AssertionError('external target was resolved')
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'resolve', guarded_resolve)

    class NoCallMock:
        is_mock = True
        calls = []

        def get_tools(self, timeout):
            self.calls.append('get_tools')
            return []

        def call_tool(self, name, arguments, timeout):
            self.calls.append(name)
            return {}

    client = NoCallMock()
    result = storyboard_team.import_commodity_shots(
        [{'shot_id': 'external-target'}],
        {'directory': 'F:/codex/laox_account/never-read', 'path': 'shots.json'},
        client=client,
    )
    assert result['status'] == 'blocked'
    assert client.calls == []


def test_commodity_bridge_refuses_existing_target_without_overwrite():
    class NoCallMock:
        is_mock = True
        calls = []

        def get_tools(self, timeout):
            self.calls.append('get_tools')
            return []

        def call_tool(self, name, arguments, timeout):
            self.calls.append(name)
            return {}

    client = NoCallMock()
    target_dir = storyboard_team.WORKSPACES_ROOT / 'existing-target-no-overwrite-test'
    target_dir.mkdir(exist_ok=False)
    try:
        result = storyboard_team.import_commodity_shots(
            [{'shot_id': 'existing-target'}],
            {'directory': str(target_dir), 'path': 'shots.json'},
            client=client,
        )
        assert result['status'] == 'blocked'
        assert 'already exists' in result['blocked_reason']
        assert client.calls == []
    finally:
        target_dir.rmdir()


def test_commodity_bridge_rejects_workspace_junction_outside_project_without_real_resolve(monkeypatch):
    project = Path('F:/codex/laox_director_lab')
    external = Path('X:/outside/forbidden/workspaces')
    target = storyboard_team.WORKSPACES_ROOT / 'junction-target'

    def fake_resolve(path, *args, **kwargs):
        path_text = str(path).replace('\\', '/').lower()
        if path_text == str(koc_rules.ROOT).replace('\\', '/').lower():
            return project
        if path_text == str(storyboard_team.WORKSPACES_ROOT).replace('\\', '/').lower():
            return external
        if path_text == str(target.parent).replace('\\', '/').lower():
            return external
        raise AssertionError('unexpected Path.resolve: %s' % path)

    monkeypatch.setattr(Path, 'resolve', fake_resolve)
    monkeypatch.setattr(Path, 'exists', lambda self: False)
    monkeypatch.setattr(Path, 'is_symlink', lambda self: False)

    with pytest.raises(storyboard_team.CommodityBridgeError, match='project root'):
        storyboard_team._target_for_commodity_import({'directory': str(target), 'path': 'shots.json'})


def test_mcp_opener_disables_environment_proxies_without_network(monkeypatch):
    from urllib.request import ProxyHandler, Request

    captured = {}

    class FakeOpener:
        def open(self, request, timeout):
            captured['request'] = request
            captured['timeout'] = timeout
            return 'mock-response'

    def fake_build_opener(*handlers):
        captured['handlers'] = handlers
        return FakeOpener()

    monkeypatch.setattr(storyboard_team, 'build_opener', fake_build_opener)
    request = Request('http://127.0.0.1:10588/mcp', headers={'Authorization': 'Bearer hidden'})
    assert storyboard_team._open_mcp_request(request, 3) == 'mock-response'
    assert captured['request'] is request
    assert captured['timeout'] == 3
    proxy_handlers = [handler for handler in captured['handlers'] if isinstance(handler, ProxyHandler)]
    assert len(proxy_handlers) == 1
    assert proxy_handlers[0].proxies == {}


@pytest.mark.parametrize('endpoint', [
    'http://127.0.0.1.evil:10588/mcp',
    'http://user:secret@127.0.0.1:10588/mcp',
    'http://127.0.0.1:99999/mcp',
])
def test_mcp_endpoint_rejects_pseudo_loopback_userinfo_and_invalid_port(endpoint):
    with pytest.raises(storyboard_team.CommodityBridgeError):
        storyboard_team._validate_mcp_endpoint(endpoint)


def test_mcp_redirect_handler_refuses_redirects():
    from email.message import Message
    from urllib.request import Request

    handler = storyboard_team._NoRedirectHandler()
    request = Request('http://127.0.0.1:10588/mcp', headers={'Authorization': 'Bearer hidden'})
    assert handler.redirect_request(request, None, 302, 'Found', Message(), 'http://evil.example/mcp') is None


def test_mcp_response_body_limit_is_enforced():
    from io import BytesIO

    class FakeResponse(BytesIO):
        headers = {'Content-Length': '5'}

    with pytest.raises(storyboard_team.CommodityBridgeError, match='response exceeds'):
        storyboard_team._read_bounded_response(FakeResponse(b'12345'), 4)


def test_mcp_response_stream_limit_is_enforced_without_content_length():
    from io import BytesIO

    class FakeResponse(BytesIO):
        headers = {}

    with pytest.raises(storyboard_team.CommodityBridgeError, match='response exceeds'):
        storyboard_team._read_bounded_response(FakeResponse(b'12345'), 4)


def test_workspace_readback_base64_limit_is_enforced(monkeypatch):
    import base64

    monkeypatch.setattr(storyboard_team, 'MAX_FILE_BYTES', 3)
    encoded = base64.b64encode(b'abcd').decode('ascii')
    with pytest.raises(storyboard_team.CommodityBridgeError, match='20 MiB'):
        storyboard_team._decode_workspace_base64(encoded)


def test_commodity_bridge_timeout_must_be_positive_and_bounded():
    with pytest.raises(storyboard_team.CommodityBridgeError):
        storyboard_team._validate_timeout(0)
    with pytest.raises(storyboard_team.CommodityBridgeError):
        storyboard_team._validate_timeout(61)


def test_commodity_bridge_rejects_oversize_json_before_mcp(monkeypatch):
    class NoCallMock:
        is_mock = True
        calls = []

        def get_tools(self, timeout):
            self.calls.append('get_tools')
            return []

        def call_tool(self, name, arguments, timeout):
            self.calls.append(name)
            return {}

    client = NoCallMock()
    monkeypatch.setattr(storyboard_team, 'MAX_FILE_BYTES', 4)
    result = storyboard_team.import_commodity_shots(
        [{'shot_id': 'too-large'}],
        {'directory': str(ROOT / 'runtime/toonflow-data/workspaces/too-large-test'),
         'path': 'shots.json'},
        client=client,
    )
    assert result['status'] == 'blocked'
    assert 'exceeds' in result['blocked_reason']
    assert client.calls == []
