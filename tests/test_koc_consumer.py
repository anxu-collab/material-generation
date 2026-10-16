"""Actual consumer integration test (isolated TEST mode; run with the consumer Python
from the director_lab cwd). Real modules, real fixture inventory, real native boundary:
manifest -> scheduler.material_manifest (explicit mcat- candidates branch) ->
build_shot_library(material_candidates) -> build_script_plan -> plan_shots ->
compile_edit_decision -> timeline/v1.

Positive content matching uses narration that reflects what the fixtures actually show
(hand-held dragonfruit cut open, white flesh, black seeds — coordinator frame check
2026-10-11); synthetic category coverage stays in structure/rule tests, never claimed
as content-match evidence. Nothing here claims production acceptance: the manifest is
TEST-flagged and refused by the production gate (asserted), b1 spoken audio untouched,
protected media hashes verified unchanged at the end. Zero paid calls.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CONSUMER = Path('F:/codex/laox_account/video_postprod')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CONSUMER))

from agents import product_storyboards  # noqa: E402
from adapters import material_manifest as dl_mm  # noqa: E402

from scheduler.material_manifest import (import_material_manifest, entry_content_sha256,
                                         DELIVERED_USE_KEYWORDS, DELIVERED_USE_PRIORITY)  # noqa: E402
from scheduler.shot_library import build_shot_library  # noqa: E402
from scheduler.shot_planner import plan_shots, compile_edit_decision  # noqa: E402
from scheduler.semantic import validate_timeline  # noqa: E402

CASE = json.loads((ROOT / 'cases/koc-dragonfruit-dryrun/case.json').read_text(encoding='utf-8'))
INVENTORY = json.loads((CONSUMER / 'jobs/deployment-fixtures/inventory.json').read_text(encoding='utf-8'))


def _build_test_manifest(tmp_path):
    result = product_storyboards.run_pipeline(CASE)
    manifest = dl_mm.export_manifest(result, CASE)
    assert dl_mm.validate_manifest(manifest) == []
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return manifest, path, hashlib.sha256(path.read_bytes()).hexdigest()


def _assets():
    assets = []
    for ref in CASE['refs']:
        assets.append({'asset_id': ref['ref_id'], 'path': ref['path'], 'sha256': ref['sha256'],
                       'role': 'product', 'mode': 'broll', 'analysis': {'mode': 'single_shot'}})
    return assets


def test_keyword_table_matches_delivered_v1_snapshot():
    """The director_lab planner table and the consumer bridge snapshot must stay the
    DELIVERED v1 contract, locked together. The live scheduler.script_plan was
    rewritten to r3 action semantics (open_packaging/split_fruit) by the concurrent
    external task (EK-16): the bridge deliberately does NOT consume the drifted
    classifier — its outputs would violate the delivered shot-library use enum; the
    drift is recorded and reported for coordinator reconciliation, not silently
    re-coupled here."""
    assert product_storyboards.USE_KEYWORDS == DELIVERED_USE_KEYWORDS
    assert product_storyboards.USE_PRIORITY == DELIVERED_USE_PRIORITY


def test_production_mode_refuses_test_manifest(tmp_path):
    _manifest, path, sha = _build_test_manifest(tmp_path)
    with pytest.raises(ValueError, match='TEST_FLAG_REFUSED_IN_PRODUCTION'):
        import_material_manifest(_assets(), {'path': str(path), 'sha256': sha}, mode='production')


def test_manifest_hash_tamper_refused(tmp_path):
    _manifest, path, sha = _build_test_manifest(tmp_path)
    with pytest.raises(ValueError, match='MANIFEST_HASH_MISMATCH'):
        import_material_manifest(_assets(), {'path': str(path), 'sha256': '0' * 64}, mode='test')


def test_test_mode_import_explicit_material_candidates_and_full_consumer_chain(tmp_path):
    manifest, path, sha = _build_test_manifest(tmp_path)
    imported = import_material_manifest(_assets(), {'path': str(path), 'sha256': sha}, mode='test')
    assert imported['report']['test_flag'] is True
    assert imported['report']['evidence_origin'].startswith('reviewed_material_manifest')
    assert imported['report']['candidate_id_prefix'] == 'mcat-'
    assert imported['failures'] == [] and len(imported['candidates']) == len(manifest['entries'])
    for cand in imported['candidates']:
        assert cand['candidate_id'].startswith('mcat-')
        assert cand['evidence']['frame_count'] == 0            # review metadata, no frame detection
        assert cand['evidence']['analysis_evidence_sha256'] == sha  # manifest-hash traceable
    assert imported['bound_shas'] == {r['sha256'] for r in CASE['refs']}

    library = build_shot_library(_assets(), [], {'job_id': 'koc-dryrun', 'revision': 1},
                                 material_candidates=imported['candidates'])
    assert library['schema_version'] == 'shot-library/v1'
    usable = [c for c in library['candidates'] if c['usable']]
    assert len(usable) >= 10
    assert all(c['candidate_id'].startswith('mcat-') for c in usable)

    # real consumer chain. Narration uses DISPLAY sentences (切面特写/籽粒看清/纹理)
    # matching what the fixtures actually show: under the externally drifted r3
    # planner (semantic-actions-v1) ACTION sentences require attested action runs or
    # operator-confirmed windows, which review-window material candidates honestly
    # cannot carry — display matching is the material bridge's supported path (EK-16).
    from scheduler.script_plan import build_script_plan
    script = '切面的特写，籽粒看得很清楚。白色果肉的纹理仔细看。'
    script_plan = build_script_plan({'mode': 'pasted', 'script_text': script,
                                     'script_sha256': hashlib.sha256(script.encode()).hexdigest()},
                                    {'brief': 'KOC dry-run', 'claims': []}, library,
                                    job_id='koc-dryrun', revision=1)
    narration = {'schema_version': 'narration-plan/v1',
                 'source': {'audio_sha256': 'a' * 64, 'actual_duration_ticks': 192000},
                 'sentences': [{'sentence_id': 's001', 'in_tick': 0, 'out_tick': 96000, 'status': 'aligned',
                                'alignment_evidence_sha256': 'b' * 64},
                               {'sentence_id': 's002', 'in_tick': 96000, 'out_tick': 192000, 'status': 'aligned',
                                'alignment_evidence_sha256': 'b' * 64}]}
    decision = plan_shots(script_plan, narration, library,
                          {'grid_ticks': 1920, 'max_tail_pause_ticks': 19200, 'min_clip_ticks': 1920})
    assert decision['missing'] == []
    resources = {'sources': {a['asset_id']: {'asset_id': a['asset_id'], 'path': a['path'], 'sha256': a['sha256'],
                                             'probe': {'duration_ticks': INVENTORY[a['asset_id']]['probe']['duration_ticks'],
                                                       'stream_indices': INVENTORY[a['asset_id']]['probe']['stream_indices']},
                                             'visual_evidence': {'path': 'F:/x/%s-analysis.json' % a['asset_id'],
                                                                 'sha256': hashlib.sha256(a['asset_id'].encode()).hexdigest()}}
                             for a in _assets()},
                 'narration': {'path': 'F:/x/voice.wav', 'sha256': 'a' * 64,
                               'probe': {'duration_ticks': 240000, 'stream_indices': {'audio': 0}}},
                 'alignment': {'path': 'F:/x/align.json', 'sha256': 'b' * 64},
                 'voice_stem': {'path': 'F:/x/stem.wav', 'sha256': 'c' * 64,
                                'probe': {'duration_ticks': 240000, 'stream_indices': {'audio': 0}}},
                 'subtitle_template': {'template_id': 'subtitle-bottom', 'version': '1.0.0'},
                 'cues': []}
    timeline = compile_edit_decision(decision, resources)
    validate_timeline(timeline, None, verify_files=False)
    assert timeline['duration_ticks'] % 1920 == 0
    assert all(c['mode'] == 'broll' for c in timeline['video_clips'])  # product footage only, no fixed route


def test_wrong_product_entry_never_matches_via_generic_chinese_intersection(tmp_path):
    manifest, path, sha = _build_test_manifest(tmp_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    victim = data['entries'][0]
    victim['product_id'] = 'prod-someone-else'
    # craft observation text that WOULD bigram-match the consumer script
    victim['semantics']['observation_texts'] = ['把果子对半切开。切面的特写，籽粒看得很清楚。']
    victim['review']['content_sha256'] = entry_content_sha256(victim)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    sha2 = hashlib.sha256(path.read_bytes()).hexdigest()
    imported = import_material_manifest(_assets(), {'path': str(path), 'sha256': sha2,
                                                    'product_id': CASE['product']['product_id']},
                                       mode='test')
    reasons = {f['reason'] for f in imported['failures']}
    assert 'PRODUCT_ID_MISMATCH' in reasons
    assert len(imported['candidates']) == len(data['entries']) - 1
    # the refused entry's window never became a selectable candidate
    victim_ticks = (int(round(victim['usable_window']['start_sec'] * 48000)),
                    int(round(victim['usable_window']['end_sec'] * 48000)))
    assert victim_ticks not in {(c['in_tick'], c['out_tick']) for c in imported['candidates']}


def test_must_preserve_audio_excluded_from_silent_free_pool(tmp_path):
    manifest, path, sha = _build_test_manifest(tmp_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    data['entries'][0]['audio'] = {'has_original': True, 'must_preserve': True, 'policy': 'must_preserve_original'}
    data['entries'][0]['review']['content_sha256'] = entry_content_sha256(data['entries'][0])
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    sha2 = hashlib.sha256(path.read_bytes()).hexdigest()
    imported = import_material_manifest(_assets(), {'path': str(path), 'sha256': sha2}, mode='test')
    assert 'AUDIO_MUST_PRESERVE_EXCLUDED' in {f['reason'] for f in imported['failures']}
    assert len(imported['candidates']) == len(data['entries']) - 1
    library = build_shot_library(_assets(), [], {'job_id': 'j', 'revision': 1},
                                 material_candidates=imported['candidates'])
    assert all(c['speech_locked'] is False and c['fixed_role'] is None for c in library['candidates'])


def test_refused_entry_cannot_return_via_bound_asset_analysis(tmp_path):
    """The manifest gate governs bound assets: a refused entry's footage must not slip
    back into the pool through a Narrato-style analysis ref of the same file."""
    manifest, path, sha = _build_test_manifest(tmp_path)
    data = json.loads(path.read_text(encoding='utf-8'))
    bad = data['entries'][0]
    bad['visual']['quality_flags'] = ['obscured_product']        # refused for poor quality
    bad['review']['content_sha256'] = entry_content_sha256(bad)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    sha2 = hashlib.sha256(path.read_bytes()).hexdigest()
    imported = import_material_manifest(_assets(), {'path': str(path), 'sha256': sha2}, mode='test')
    assert 'POOR_QUALITY_FLAG' in {f['reason'] for f in imported['failures']}
    assert bad['source_sha256'] in imported['bound_shas']
    # pipeline semantics: analysis refs of bound assets are dropped before the library
    assets = _assets()
    narrato = [{'asset_id': assets[0]['asset_id'], 'source_sha256': bad['source_sha256'], 'usable': True,
                'duration_ticks': 316000,
                'coverage': [{'in_tick': 0, 'out_tick': 96000, 'status': 'success',
                              'observations': [{'text': '果肉特写', 'frame_id': 'f1', 'source_tick': 0}]}],
                'detect': None, 'product_identity_verified': False, 'analysis_evidence_sha256': 'd' * 64}]
    filtered = [r for r in narrato if r.get('source_sha256') not in imported['bound_shas']]
    library = build_shot_library(assets, filtered, {'job_id': 'j', 'revision': 1},
                                 material_candidates=imported['candidates'])
    refused_window = (int(round(bad['usable_window']['start_sec'] * 48000)),
                      int(round(bad['usable_window']['end_sec'] * 48000)))
    windows = {(c['in_tick'], c['out_tick']) for c in library['candidates']}
    assert refused_window not in windows
    assert all(not c['candidate_id'].startswith('cand-') or c['evidence']['frame_count'] > 0
               for c in library['candidates'])  # no synthetic Narrato candidates for bound assets


def test_no_manifest_path_behavior_identical():
    from scheduler.contracts import validate_named
    assets = _assets()
    ref = {'asset_id': 'a1', 'source_sha256': CASE['refs'][0]['sha256'], 'usable': True,
           'duration_ticks': 316000,
           'coverage': [{'in_tick': 0, 'out_tick': 96000, 'status': 'success',
                         'observations': [{'text': '果肉特写', 'frame_id': 'f1', 'source_tick': 0}]}],
           'detect': None, 'product_identity_verified': False, 'analysis_evidence_sha256': 'd' * 64}
    legacy = validate_named(build_shot_library(assets, [dict(ref)]), 'shot-library-v1')
    with_param = validate_named(build_shot_library(assets, [dict(ref)], None, material_candidates=None), 'shot-library-v1')
    assert legacy == with_param  # byte-identical when no manifest is supplied


def test_edit_job_v2_schema_optional_manifest_backward_compatible():
    import jsonschema
    from scheduler.contracts import validate_job
    schema = json.loads((CONSUMER / 'contracts/edit-job-v2.schema.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator.check_schema(schema)
    assert 'material_manifest' in schema['properties']
    assert 'material_manifest' not in schema['required']
    script = '你好。'
    base_job = {
        'schema_version': 'edit-job/v2', 'job_id': 'koc-compat', 'revision': 1, 'parent_revision': None,
        'account': 'laoX', 'profile': 'warming',
        'intent': {'brief': 'compat', 'language': 'zh', 'target_duration_ticks': 192000,
                   'product_badge_requested': False},
        'product_documents': [], 'claims': [],
        'assets': [{'asset_id': 'a1', 'path': CASE['refs'][0]['path'], 'sha256': CASE['refs'][0]['sha256'],
                    'mode': 'broll', 'role': 'product', 'analysis': {'mode': 'single_shot'},
                    'provenance_ref': 'F:/codex/laox_account/video_postprod/jobs/deployment-fixtures/a1-provenance.json',
                    'locked': None}],
        'script_input': {'mode': 'pasted', 'script_text': script,
                         'script_sha256': hashlib.sha256(script.encode()).hexdigest(), 'generation_note': None},
        'narration_input': {'mode': 'auto', 'preset': 'p', 'uploaded': None, 'history': None},
        'providers': {'text': 't', 'vision': 'v', 'tts': 's', 'asr': 'a', 'lipsync': None},
        'templates': {'subtitle': {'template_id': 's', 'version': '1.0.0'}, 'bgm': None,
                      'effects': [], 'sfx': [], 'product_badge': {'enabled': False, 'template': None}},
        'overrides': {'base_timeline_sha256': None, 'subtitle_edits': [], 'bgm_override': None,
                      'shot_edits': [], 'speech_edits': [], 'template_overrides': []},
        'v2_overrides': {'clip_replacements': [], 'sentence_redubs': []},
        'cost': {'paid_enabled': False, 'currency': 'CNY', 'job_limit_micros': 0,
                 'daily_limit_micros': 0, 'max_paid_requests': 0},
        'output': {'width': 768, 'height': 1376, 'fps': 25, 'audio_sample_rate': 48000},
        'review_policy': {'visual': 'manual', 'lipsync': 'manual', 'content': 'evidence_required'},
        'changed_paths': [],
    }
    validate_job(json.loads(json.dumps(base_job)))  # old job without the field: unchanged behavior
    with_manifest = json.loads(json.dumps(base_job))
    with_manifest['material_manifest'] = {'path': 'F:/x/manifest.json', 'sha256': '0' * 64, 'product_id': None}
    validate_job(with_manifest)
    rogue = json.loads(json.dumps(base_job))
    rogue['material_manifestt'] = with_manifest['material_manifest']  # typo field must still be rejected
    with pytest.raises(Exception):
        jsonschema.Draft202012Validator(schema).validate(rogue)


def test_protected_media_and_b1_untouched():
    for key in ('a1', 'a2', 'b1'):
        fixture = INVENTORY[key]
        actual = hashlib.sha256(Path(fixture['path']).read_bytes()).hexdigest()
        assert actual == fixture['sha256'], key
