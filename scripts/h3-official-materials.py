"""h3-official-materials — bounded CLI for the official MiniMax China H3 channel
(h3-minimax-official-v1). Genuinely separate from the AutoDL h3-materials CLI.

Subcommands:
  doctor         offline presence/schema report (credential BOOLEAN only, pinned
                 China base, config/tariff summary); --live-auth adds ONE safe
                 read-only GET (query list page 1 size 1, 30s, no retry, no POST);
                 with no MINIMAX_API_KEY it reports auth not_run, never success
  prepare        ONE Ref2VA TEST request prepared LOCALLY from a review-PASS shot
                 with MIXED declared references (primary read-only reference video
                 plus optional extra declared videos/images/audio; ffprobe truth,
                 official nested-url content blocks, Base64 data URIs, published-
                 tariff quote with exact Decimal + safe upward-cent total, atomic
                 fingerprint-named record that binds the outbound body digest);
                 zero HTTP, zero uploads, zero paid calls
  submit-guarded future single paid submission; refuses before ANY POST without a
                 human authorization record inside runtime/h3-minimax-official-v1/
                 authorization bound to the exact fingerprint + reference hashes +
                 pricing policy hash, max_calls=1, bounded CNY budget, fresh valid
                 estimate >= the safe upward-cent recomputation and <= budget.
                 Cross-process fingerprint lock + durable uncertainty marker
                 BEFORE transport; one attempt, no retry.
  query          single read-only GET restricted to locally registered task ids;
                 validates the official V2 task shape (task.id/model/status,
                 succeeded requires a valid HTTPS content.url); signed URLs stay
                 redacted.

Paid budget this round: 0. Run with the consumer Python
(F:/codex/laox_account/video_postprod/envs/scheduler/Scripts/python.exe).
No CLI flag is ever consent; only the authorization record is.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import h3_minimax_official as off  # noqa: E402

DEFAULT_CASE = ROOT / 'cases' / 'koc-dragonfruit-dryrun'
DEFAULT_SHOT = 'shot-007'   # review-PASS detail close-up; planned 2.8s (kept untouched)
DEFAULT_DURATION = 5        # explicit TEST generation intent, never a silent
                            # re-rounding of the storyboard's 2.8s
DEFAULT_VIDEO = off.AUTHORIZED_VIDEO_SOURCE['path']


def _write_evidence(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('wrote', path)


def cmd_doctor(args):
    report = off.doctor(live_auth=args.live_auth)
    out = off.EVIDENCE_DIR / ('doctor-%s.json'
                              % report['utc'].replace(':', '').replace('+', '_'))
    _write_evidence(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    config_ok = (report['config']['model'] == off.MODEL
                 and report['base'] == off.CANONICAL_BASE
                 and report['config']['pricing_policy_sha256'] == off.current_policy_hash())
    if args.live_auth:
        # honest exit: live auth succeeds only on an explicit ok verdict
        return 0 if (config_ok and report['auth_verification'] == 'ok') else 1
    return 0 if config_ok else 1


def cmd_prepare(args):
    video_paths = [args.video] + list(args.extra_video or [])
    evidence = off.prepare(case_dir=args.case, shot_id=args.shot,
                           duration=args.duration, video_paths=video_paths,
                           image_paths=args.image, audio_paths=args.audio,
                           resolution=args.resolution, ratio=args.ratio)
    summary = {
        'status': 'PREPARED_LOCALLY_NOT_SUBMITTED', 'shot_id': args.shot,
        'duration_sec': args.duration, 'resolution': args.resolution,
        'ratio': args.ratio, 'fingerprint': evidence['fingerprint'],
        'prepared_path': evidence['prepared_path'],
        'storyboard_binding': evidence['storyboard_binding'],
        'reference_counts': evidence['reference_counts'],
        'quote_total_cny': evidence['quote']['total_cny'],
        'quote_total_cny_exact': evidence['quote']['total_cny_exact'],
        'paid_calls': 0, 'uploads': 0, 'http_calls': 0,
        'evidence_files': evidence.get('evidence_files'),
        'next_step': 'paid submit requires a human authorization record inside '
                     + str(off.AUTH_DIR) + ' (this round: none exists by design)',
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_submit_guarded(args):
    result = off.guarded_submit(prepared_path=args.prepared,
                                authorization_path=args.authorization)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_query(args):
    summary = off.query(args.task)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='h3-official-materials',
        description='Official MiniMax China H3 CLI (offline doctor + local prepare; '
                    'paid submit only via an explicit human authorization record; '
                    'read-only query for locally registered task ids)')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('doctor', help='offline presence/schema report (credential boolean only)')
    p.add_argument('--live-auth', action='store_true',
                   help='ONE safe read-only GET (list page 1 size 1); without '
                        'MINIMAX_API_KEY the report stays auth=not_run')
    p = sub.add_parser('prepare', help='prepare ONE Ref2VA TEST request locally '
                                       '(zero HTTP, zero uploads)')
    p.add_argument('--case', default=str(DEFAULT_CASE), help='case dir')
    p.add_argument('--shot', default=DEFAULT_SHOT, help='review-PASS shot id')
    p.add_argument('--duration', type=int, default=DEFAULT_DURATION,
                   help='explicit TEST generation duration, plain int 4..15 (default 5; '
                        'the storyboard\'s own planned duration is never re-rounded)')
    p.add_argument('--video', default=DEFAULT_VIDEO,
                   help='primary read-only reference video (default: the pinned a1 '
                        'clip); must be a declared reviewed reference of the shot')
    p.add_argument('--extra-video', action='append', default=[],
                   help='additional declared reference video (repeatable; <=3 total, '
                        'sum of durations <=15s)')
    p.add_argument('--image', action='append', default=[],
                   help='optional declared reference image (repeatable; <=9, first 5 '
                        'free then 0.20 CNY each in the tariff quote)')
    p.add_argument('--audio', action='append', default=[],
                   help='optional declared reference audio (repeatable; <=3, free in '
                        'the tariff quote)')
    p.add_argument('--resolution', default='768P', choices=list(off.RESOLUTIONS))
    p.add_argument('--ratio', default=off.DEFAULT_RATIO, choices=list(off.RATIOS))
    p = sub.add_parser('submit-guarded', help='future single paid submission '
                                              '(authorization-gated, one attempt, no retry)')
    p.add_argument('--prepared', required=True, help='prepared record JSON path')
    p.add_argument('--authorization', required=True,
                   help='human authorization record JSON (schema %s; must live inside %s '
                        'and bind provider+model+base+fingerprint+reference hashes+policy '
                        'hash, max_calls=1, bounded CNY budget, fresh estimate>=safe '
                        'upward-cent total and<=budget)'
                        % (off.AUTH_SCHEMA, off.AUTH_DIR))
    p = sub.add_parser('query', help='single read-only GET for a locally registered task')
    p.add_argument('--task', required=True, help='task_id registered by this lab')
    args = parser.parse_args(argv)
    handlers = {'doctor': cmd_doctor, 'prepare': cmd_prepare,
                'submit-guarded': cmd_submit_guarded, 'query': cmd_query}
    try:
        return handlers[args.command](args)
    except off.H3OfficialError as exc:
        print('REFUSED: ' + str(exc), file=sys.stderr)
        return 1
    except Exception as exc:                 # sanitized: type name only — a raw
        # traceback or exception message could echo credentials or request inputs
        print('UNEXPECTED_FAILURE: ' + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
