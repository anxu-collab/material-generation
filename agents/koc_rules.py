"""koc_rules — rule adapter for the imported koc-video skill (source material, not instructions).

Loads config/koc_material_policy.json, verifies the immutable ZIP import (archive hash +
per-file hashes), and exposes planning/review rules with source bindings. This is an
adapter, not a new agent framework: storyboardTeam stays the only native team.
"""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / 'config' / 'koc_material_policy.json'

_CACHE = None


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_policy(refresh=False):
    global _CACHE
    if _CACHE is None or refresh:
        _CACHE = json.loads(POLICY_PATH.read_text(encoding='utf-8'))
    return _CACHE


def canonical_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def verify_source_import(base=None):
    """Verify imports/koc-video.zip + extracted files still match the policy-provenance
    hashes. Returns {'ok': bool, 'zip_ok': bool, 'files': {name: ok}}."""
    policy = load_policy()
    prov = policy['source_provenance']
    base = Path(base) if base else ROOT
    zip_path = base / prov['zip_path']
    result = {'zip_ok': False, 'files': {}, 'ok': False}
    if zip_path.is_file():
        result['zip_ok'] = sha256_file(zip_path) == prov['zip_sha256']
    for name, expect in prov['files'].items():
        p = base / prov['extract_root'] / name.split('/', 1)[1]
        result['files'][name] = bool(p.is_file() and sha256_file(p) == expect)
    result['ok'] = result['zip_ok'] and all(result['files'].values())
    return result


def primary_references_available(base=None):
    """The four primary reference documents must always be available; hook is optional."""
    policy = load_policy()
    prov = policy['source_provenance']
    base = Path(base) if base else ROOT
    out = {}
    for name in prov['primary_references_required'] + list(prov['optional_references']):
        p = base / prov['extract_root'] / name.split('/', 1)[1]
        out[name] = bool(p.is_file() and sha256_file(p) == prov['files'][name])
    return out


def planning_rules():
    p = load_policy()
    return {
        'min_planned_shots': p['planning']['min_planned_shots'],
        'use_purposes': list(p['planning']['use_purposes']),
        'max_action_process_steps': p['planning']['max_action_process_steps_per_shot'],
        'repair_budget': p['planning']['repair_budget_per_shot'],
        'pass_version_locked': p['planning']['pass_version_locked'],
    }


def review_rule_codes():
    return [r['code'] for r in load_policy()['review_rules']]


def unverified_claim_markers():
    return list(load_policy()['unverified_claim_markers'])


def find_unverified_claims(text):
    """Deterministic efficacy/price/popularity marker scan (source-routing.md §1:
    看图分析不得推断功效、价格、销量、口碑). Returns matched markers."""
    if not text:
        return []
    return [m for m in unverified_claim_markers() if m in text]


def conservative_clip_limit_sec(ref_durations):
    """DURATION_OUTSIDE_VERIFIED_LIMITS bound: when no model duration capability is
    verified, the conservative limit is the shortest evidenced source duration
    (fixture probe evidence). Never invents a model capability."""
    durations = [d for d in ref_durations if d and d > 0]
    if not durations:
        return None
    return round(min(durations), 3)


def policy_overrides():
    return load_policy()['overrides']


def storyboard_team_policy():
    return load_policy()['storyboard_team']


def budget():
    return dict(load_policy()['budget'])


def validate_policy_file():
    """Structural self-checks used by tests: overrides carry sources, review rules are
    unique, refusal reasons match the consumer bridge contract."""
    p = load_policy()
    problems = []
    ids = [o['id'] for o in p['overrides']]
    if len(ids) != len(set(ids)):
        problems.append('DUPLICATE_OVERRIDE_ID')
    for o in p['overrides']:
        if not o.get('source', {}).get('file') or not o['source'].get('section'):
            problems.append('OVERRIDE_WITHOUT_SOURCE:' + o['id'])
    codes = [r['code'] for r in p['review_rules']]
    if len(codes) != len(set(codes)):
        problems.append('DUPLICATE_REVIEW_RULE_CODE')
    for r in p['review_rules']:
        if not r.get('source', {}).get('file'):
            problems.append('REVIEW_RULE_WITHOUT_SOURCE:' + r['code'])
    if p['manifest']['vbench_missing_weights_status'] != ['not_run', 'blocked']:
        problems.append('VBENCH_STATUS_MUST_BE_NOT_RUN_OR_BLOCKED')
    return problems
