"""koc-materials — bounded local CLI for the KOC material integration (koc-v1).

Subcommands: verify-import | plan | review | export | dry-run | team-check.
Local deterministic dry-run only: no model calls, no paid submissions, no uploads,
no weight downloads (config/koc_material_policy.json → budget all zero). Run with the
consumer Python (F:/codex/laox_account/video_postprod/envs/scheduler/Scripts/python.exe)
so jsonschema is available; results land under cases/<case>/.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import koc_rules, product_storyboards  # noqa: E402
from adapters import material_manifest, storyboard_team  # noqa: E402

CASE_DIR = ROOT / 'cases' / 'koc-dragonfruit-dryrun'


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print('wrote', path)


def _load_case(case_arg):
    case_path = Path(case_arg) if case_arg else CASE_DIR / 'case.json'
    return json.loads(case_path.read_text(encoding='utf-8'))


def _validate_storyboards(plan):
    import jsonschema
    schema = json.loads((ROOT / 'schemas' / 'product-storyboards-v1.schema.json').read_text(encoding='utf-8'))
    jsonschema.Draft202012Validator(schema).validate(plan)


def cmd_verify_import(_args):
    result = koc_rules.verify_source_import()
    refs = koc_rules.primary_references_available()
    print(json.dumps({'source_import': result, 'primary_references': refs}, ensure_ascii=False, indent=2))
    return 0 if result['ok'] and all(refs.values()) else 1


def cmd_plan(args):
    case = _load_case(args.case)
    plan = product_storyboards.build_plan(case)
    _validate_storyboards(plan)
    _write(CASE_DIR / 'storyboards.json', plan)
    purposes = plan['planning']['distinct_use_purposes']
    print('planned %d shots, %d distinct purposes: %s' % (len(plan['shots']), len(purposes), ','.join(purposes)))
    return 0


def cmd_review(args):
    case = _load_case(args.case)
    result = product_storyboards.run_pipeline(case)
    _validate_storyboards(result['plan'])
    _write(CASE_DIR / 'storyboards.json', result['plan'])
    _write(CASE_DIR / 'review.json', {'review': result['review'], 'repair_rounds': result['repair_rounds'],
                                      'repair_log': result['repair_log'], 'final': result['final']})
    print('PASS=%d RETURN/blocked=%d repair_rounds=%d' %
          (len(result['final']['pass']), len(result['final']['blocked']), result['repair_rounds']))
    return 0


def cmd_export(args):
    case = _load_case(args.case)
    result = product_storyboards.run_pipeline(case)
    manifest = material_manifest.export_manifest(result, case)
    problems = material_manifest.validate_manifest(manifest)
    if problems:
        print('MANIFEST_VALIDATION_PROBLEMS:', problems)
        return 1
    tasks = material_manifest.export_generation_tasks(result, case)
    _write(CASE_DIR / 'manifest.json', manifest)
    _write(CASE_DIR / 'generation-tasks-dryrun.json', tasks)
    print('exported %d TEST entries, %d blocked dry-run tasks, paid_calls=0' %
          (len(manifest['entries']), len(tasks['tasks'])))
    return 0


def cmd_dry_run(args):
    case = _load_case(args.case)
    source = koc_rules.verify_source_import()
    result = product_storyboards.run_pipeline(case)
    _validate_storyboards(result['plan'])
    manifest = material_manifest.export_manifest(result, case)
    problems = material_manifest.validate_manifest(manifest)
    tasks = material_manifest.export_generation_tasks(result, case)
    readiness = storyboard_team.readiness_report()
    _write(CASE_DIR / 'storyboards.json', result['plan'])
    _write(CASE_DIR / 'review.json', {'review': result['review'], 'repair_rounds': result['repair_rounds'],
                                      'repair_log': result['repair_log'], 'final': result['final']})
    _write(CASE_DIR / 'manifest.json', manifest)
    _write(CASE_DIR / 'generation-tasks-dryrun.json', tasks)
    summary = {
        'case_id': case['case_id'],
        'source_import_ok': source['ok'],
        'shots_planned': len(result['plan']['shots']),
        'distinct_purposes': result['plan']['planning']['distinct_use_purposes'],
        'pass_count': len(result['final']['pass']),
        'blocked': result['final']['blocked'],
        'repair_rounds': result['repair_rounds'],
        'manifest_entries': len(manifest['entries']),
        'manifest_validation_problems': problems,
        'manifest_test_flag': manifest['test_flag'],
        'generation_tasks_dry_run': True,
        'team_readiness': {'ready': readiness['ready'],
                           'live_registry': readiness['live_registry'].get('status'),
                           'llm_invocation': 'not_run'},
        'budget': koc_rules.budget(),
    }
    _write(CASE_DIR / 'dryrun-summary.json', summary)
    ok = (source['ok'] and not problems and len(result['final']['pass']) >= 10
          and len(result['plan']['planning']['distinct_use_purposes']) >= 4)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def cmd_team_check(_args):
    report = storyboard_team.readiness_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['ready'] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog='koc-materials',
                                     description='KOC material integration CLI (dry-run only, zero paid calls)')
    parser.add_argument('--case', default=None, help='case.json path (default: cases/koc-dragonfruit-dryrun/case.json)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('verify-import', help='verify immutable koc-video ZIP import + primary references')
    sub.add_parser('plan', help='deterministic shot planning from the case')
    sub.add_parser('review', help='PASS/RETURN review + bounded repair (<=2 rounds)')
    sub.add_parser('export', help='export reviewed TEST manifest + dry-run generation tasks')
    sub.add_parser('dry-run', help='full local pipeline: plan+review+repair+export+team readiness')
    sub.add_parser('team-check', help='native storyboardTeam readiness (files + live registry; no LLM call)')
    args = parser.parse_args(argv)
    handlers = {'verify-import': cmd_verify_import, 'plan': cmd_plan, 'review': cmd_review,
                'export': cmd_export, 'dry-run': cmd_dry_run, 'team-check': cmd_team_check}
    return handlers[args.command](args)


if __name__ == '__main__':
    raise SystemExit(main())
