"""material_manifest (director_lab) — export reviewed PASS shots to material-manifest/v1
and a dry-run generation task list. Immutable-version semantics: exporting the same
PASS shot content twice yields the identical entry; changed content must create a new
version with a fresh review binding (same PASS is never overwritten).
"""
import copy
import json
from pathlib import Path

from agents import koc_rules
from agents import product_storyboards as ps

GRID_TICKS = 1920
TIMESCALE = 48000


def _entry_core(entry):
    core = copy.deepcopy(entry)
    core.pop('review', None)
    return core


def entry_content_hash(entry):
    return koc_rules.canonical_hash(_entry_core(entry))


def _window_for(shot, ref_duration_sec, offset_index):
    """Deterministic grid-aligned window inside the referenced source duration."""
    dur = ps._grid_sec(shot['duration_sec'])
    ticks = int(round(dur * TIMESCALE))
    total = int(round(ref_duration_sec * TIMESCALE))
    usable = max(0, total - ticks)
    start = min(usable, (offset_index * ticks) % (usable + 1)) if usable else 0
    start = start // GRID_TICKS * GRID_TICKS
    return start / TIMESCALE, (start + ticks) / TIMESCALE


def export_manifest(result, case, manifest_id='koc-dragonfruit-test'):
    """Build a TEST material manifest from a run_pipeline result. Only PASS shots are
    exported; blocked shots never enter. All entries inherit test_flag from the case
    and auto_edit_eligible=false (this exporter is TEST-only).

    Every case-specific value comes from the CASE INPUT (EK-15): keywords from
    keyword_tags, person from person_appearance, location from location_note,
    provenance from the case provenance block, technical fields from
    fixture_technical. The exporter refuses rather than invent: a case declaring
    test_flag=false (production export is unsupported this round) or missing a
    provenance declaration raises; undeclared person/location are exported as
    honest 未声明 placeholders, never as demo-case assumptions."""
    if case.get('test_flag') is False:
        raise ValueError('PRODUCTION_EXPORT_UNSUPPORTED: this exporter emits TEST manifests '
                         'only; production export needs the real quality/visual-review '
                         'evidence chain (not implemented this round)')
    provenance_decl = case.get('provenance') or {}
    if not provenance_decl.get('origin'):
        raise ValueError('CASE_PROVENANCE_MISSING: export requires an explicit case '
                         'provenance declaration {origin, product_identity_verified, note}')
    plan = result['plan']
    review_by_id = {s['shot_id']: s for s in result['review']['shots']}
    passed = {sid for sid in result['final']['pass']}
    tech = case.get('fixture_technical', {})
    keyword_tags = [t for t in (case.get('keyword_tags') or []) if isinstance(t, str) and t]
    person_decl = case.get('person_appearance')
    person = person_decl if isinstance(person_decl, str) and person_decl.strip() \
        else '未声明（输入未提供出镜信息）'
    location_decl = case.get('location_note')
    location = location_decl if isinstance(location_decl, str) and location_decl.strip() \
        else '未声明（输入未提供场景信息）'
    entries = []
    offset = 0
    for shot in plan['shots']:
        if shot['shot_id'] not in passed:
            continue
        ref = next((r for r in shot['refs'] if r['ref_id'] in tech), None) or plan['refs'][0]
        ref_tech = tech.get(ref['ref_id'], {}) or {}
        duration_sec = ref_tech.get('duration_sec')
        probe_sha = ref_tech.get('probe_sha256', '0' * 64)
        path = next(r['path'] for r in plan['refs'] if r['ref_id'] == ref['ref_id'])
        sha = next(r['sha256'] for r in plan['refs'] if r['ref_id'] == ref['ref_id'])
        start_sec, end_sec = _window_for(shot, duration_sec, offset)
        offset += 1
        # physical technical metadata derives from the case's per-ref technical
        # evidence; unknown fields are omitted, never filled with fixture values
        technical = {'duration_sec': duration_sec,
                     'duration_source': 'fixture_inventory' if duration_sec else 'unknown'}
        for key in ('container', 'width', 'height'):
            if ref_tech.get(key) is not None:
                technical[key] = ref_tech[key]
        entry = {
            'material_id': 'mat-%s-%s' % (manifest_id, shot['shot_id']),
            'shot_id': shot['shot_id'],
            'product_id': shot['product_id'],
            'version': shot['version'],
            'path': path,
            'source_sha256': sha,
            'review_status': 'PASS',
            'review': {'verdict': 'PASS', 'content_sha256': None,
                       'reviewer': 'deterministic-rules-v1',
                       'checked_utc': plan['created_utc'],
                       'problems': []},
            'semantics': {
                'summary': '%s：%s' % (shot['purpose'], shot['action']['start']),
                'person': person,
                'action': {'start': shot['action']['start'], 'process': list(shot['action']['process']),
                           'end': shot['action']['end']},
                'location': location,
                'observation_texts': [shot['narration'], shot['framing']],
            },
            'features': {'framing': shot['framing'], 'camera': shot['camera'],
                         'movement': shot['movement'],
                         'visual_keywords': [shot['purpose']] + shot['must'][:2]},
            'narration': {'text': shot['narration'], 'language': 'zh'},
            'keywords': [shot['purpose']] + keyword_tags,
            'usable_window': {'start_sec': start_sec, 'end_sec': end_sec, 'grid_ticks': GRID_TICKS},
            'audio': dict(shot['audio']),
            'references': [{'ref_id': r['ref_id'], 'role': r['role']} for r in shot['refs']],
            'restrictions': [],
            'technical': technical,
            'visual': {'quality_flags': [], 'occlusion': 'unknown'},
            'vbench': {'applicable': True, 'status': 'not_run', 'metrics': {},
                       'reason': 'weights not downloaded; metric execution stays not_run (policy forbids inventing PASS)'},
            'applicability': {'auto_edit': True, 'uses': [shot['purpose']]},
            'evidence': {'probe_sha256': probe_sha,
                         'review_evidence_path': 'cases/%s/review.json' % case['case_id'],
                         'provenance': {'origin': provenance_decl['origin'],
                                        'product_identity_verified': bool(provenance_decl.get('product_identity_verified', False)),
                                        'note': provenance_decl.get('note')}},
            'test_flag': True,
            'generated': dict(shot['generated']),
            'auto_edit_eligible': False,
        }
        entry['review']['content_sha256'] = entry_content_hash(entry)
        entries.append(entry)
    manifest = {
        'schema_version': 'material-manifest/v1',
        'manifest_id': manifest_id,
        'created_utc': plan['created_utc'],
        'policy_version': plan['policy_version'],
        'test_flag': bool(case.get('test_flag', True)),
        'source_case': case['case_id'],
        'entries': entries,
    }
    return manifest


def validate_manifest(manifest, schema_path=None):
    """Structural validation against schemas/material-manifest-v1.schema.json plus the
    semantic invariants the schema cannot express."""
    import jsonschema
    schema_path = schema_path or Path(koc_rules.ROOT) / 'schemas' / 'material-manifest-v1.schema.json'
    schema = json.loads(Path(schema_path).read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator(schema).validate(manifest)
    problems = []
    for entry in manifest['entries']:
        if entry['review']['content_sha256'] != entry_content_hash(entry):
            problems.append('REVIEW_BINDING_STALE:' + entry['material_id'])
        if entry['test_flag'] and entry['auto_edit_eligible']:
            problems.append('TEST_ENTRY_CANNOT_BE_PRODUCTION_ELIGIBLE:' + entry['material_id'])
        start = entry['usable_window']['start_sec'] * TIMESCALE
        end = entry['usable_window']['end_sec'] * TIMESCALE
        if any(abs(v - round(v)) > 1e-6 or int(round(v)) % GRID_TICKS for v in (start, end)):
            problems.append('WINDOW_NOT_GRID_ALIGNED:' + entry['material_id'])
        if not 0 <= start < end:
            problems.append('WINDOW_OUT_OF_RANGE:' + entry['material_id'])
        if entry['technical']['duration_sec'] is None or end > entry['technical']['duration_sec'] * TIMESCALE + 1e-6:
            problems.append('DURATION_UNVERIFIED_OR_WINDOW_BEYOND_DURATION:' + entry['material_id'])
    return problems


def export_generation_tasks(result, case, out_provider=None):
    """Dry-run-only generation task list: no provider, no uploads, no fees. Existing
    demo footage needs no generation; blocked capability stays blocked — never a fake
    accepted request."""
    tasks = []
    for blocked in result['final']['blocked']:
        tasks.append({'shot_id': blocked['shot_id'], 'state': 'blocked',
                      'blocked_reasons': blocked['problems'],
                      'dry_run': True, 'provider': out_provider, 'paid_calls': 0})
    for shot_id in result['final']['pass']:
        shot = next(s for s in result['plan']['shots'] if s['shot_id'] == shot_id)
        if shot['generated']['status'] == 'to_be_generated':
            tasks.append({'shot_id': shot_id, 'state': 'dry_run_only', 'dry_run': True,
                          'provider': None, 'paid_calls': 0,
                          'note': 'H3 duration 字段未核实，不标 compatible；提交需另行授权'})
    return {'schema_version': 'generation-tasks-dryrun/v1', 'case_id': case['case_id'],
            'dry_run': True, 'budget': koc_rules.budget(), 'tasks': tasks}


def reexport_with_change(entry, changes):
    """Immutability semantics: changing exported content creates version+1 and clears
    the review binding (a fresh review is required); the same PASS is never overwritten."""
    updated = copy.deepcopy(entry)
    updated.update(changes)
    updated['version'] = entry['version'] + 1
    updated['review'] = {'verdict': 'RETURN', 'content_sha256': None, 'reviewer': 'pending',
                         'problems': [{'code': 'REVIEW_BINDING_STALE', 'evidence': 'content changed after export', 'minimal_fix': 're-review required'}]}
    return updated
