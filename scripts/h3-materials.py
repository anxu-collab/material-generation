"""h3-materials — bounded CLI for the real AutoDL H3 integration.

ACTIVE ROUTE (h3-autodl-z0902-integration, 2026-10-11): AutoDL workflow
minimax_h3_z0902, image-only request contract (USER_SUPPLIED_NOT_SERVER_VERIFIED,
single source of truth config/autodl_h3_z0902.json), project default resolution
1088p竖(1088*1920). Delivered STATIC_ONLY: the user explicitly forbade tests and
any API call (你只是对接，不允许测试) — this CLI was never executed during the
integration round and its runtime acceptance is NOT_RUN_USER_PROHIBITED.

Subcommands (all offline/local; there is NO implicit GET anywhere):
  doctor          offline contract/config report — credential ENV presence as
                  booleans only (values never read/echoed); --live is REFUSED
  prepare         build ONE z0902 request locally from --prompt (or
                  --prompt-file) + --image/--image-slot; strict integer checks;
                  explicit default resolution; zero paid calls, zero uploads,
                  zero network
  submit-guarded  future single paid z0902 submission — refuses while guards
                  keep network_enabled/generation_enabled false or dry_run true;
                  CLI flags are never consent

The historical z0903 route (live doctor GET, TEST frame extraction, blank.wav
placeholder) is frozen inside adapters/h3_autodl.py (LEGACY_Z0903_ENABLED=False)
and is intentionally NOT exposed by this CLI. Any Python >= 3.10 with the lab
root importable suffices for the active route; prepare/doctor never load the
oldX runtime.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from adapters import h3_autodl as h3  # noqa: E402

DEFAULT_DURATION = 5        # user contract default; strict integer 1..15


def _write_evidence(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print('wrote', path)


def _build_images(args):
    """Merge the two CLI image forms into the exact slot mapping. --image URLs
    pack positionally into ref_image_0,1,… (a list form is by definition gapless);
    --image-slot SLOT=URL names slots explicitly for gapped selections (revision
    2: slots 1..5 independently optional, caller numbering preserved)."""
    images = {}
    for index, url in enumerate(args.image):
        slot = 'ref_image_%d' % index
        if slot in images:
            raise h3.H3AdapterError('CLI_IMAGE_SLOT_CONFLICT: ' + slot
                                    + ' provided twice')
        images[slot] = url
    for pair in args.image_slot:
        if '=' not in pair:
            raise h3.H3AdapterError('CLI_IMAGE_SLOT_FORMAT: expected SLOT=URL, got "'
                                    + pair + '"')
        slot, url = pair.split('=', 1)
        slot = slot.strip()
        if slot in images:
            raise h3.H3AdapterError('CLI_IMAGE_SLOT_CONFLICT: ' + slot
                                    + ' provided twice')
        images[slot] = url.strip()
    return images


def cmd_doctor(args):
    if args.live:
        print('REFUSED: Z0902_LIVE_UNSUPPORTED: the z0902 contract is '
              'USER_SUPPLIED_NOT_SERVER_VERIFIED and runtime verification is '
              'NOT_RUN_USER_PROHIBITED (user: 你只是对接，不允许测试); doctor is '
              'offline-only on this route', file=sys.stderr)
        return 1
    report = h3.doctor_z0902()
    out = h3.Z0902_EVIDENCE_DIR / ('doctor-%s.json'
                                   % report['utc'].replace(':', '').replace('+', '_'))
    _write_evidence(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_prepare(args):
    if args.prompt is None and args.prompt_file is None:
        raise h3.H3AdapterError('CLI_PROMPT_MISSING: pass --prompt or --prompt-file')
    if args.prompt is not None and args.prompt_file is not None:
        raise h3.H3AdapterError('CLI_PROMPT_CONFLICT: --prompt and --prompt-file are '
                                'mutually exclusive')
    prompt = args.prompt if args.prompt is not None \
        else Path(args.prompt_file).read_text(encoding='utf-8')
    prepared = h3.prepare_z0902(prompt=prompt, images=_build_images(args),
                                duration=args.duration, seed=args.seed,
                                resolution=args.resolution)
    summary = {
        'status': 'PREPARED_LOCALLY_NOT_SUBMITTED',
        'workflow': h3.Z0902_WORKFLOW,
        'request_id': prepared['request_id'],
        'request_path': prepared['request_path'],
        'fingerprint': prepared['fingerprint'],
        'image_slots': sorted(k for k in prepared['body'] if k.startswith('ref_image_')),
        'duration_sec': prepared['body']['duration'],
        'resolution': prepared['body']['resolution'],
        'seed_omitted': 'seed' not in prepared['body'],
        'contract_source': 'USER_SUPPLIED_NOT_SERVER_VERIFIED',
        'runtime_verification': 'NOT_RUN_USER_PROHIBITED',
        'url_format_compatibility': 'unverified — URLs never fetched',
        'paid_calls': 0, 'uploads': 0, 'network_calls': 0,
        'evidence_file': prepared.get('evidence_file'),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_submit_guarded(args):
    result = h3.submit_guarded_z0902(request_path=args.request,
                                     authorization_path=args.authorization)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='h3-materials',
        description='AutoDL H3 z0902 image-only integration CLI (offline/local only; '
                    'paid submit only via explicit human authorization; runtime '
                    'acceptance NOT_RUN_USER_PROHIBITED)')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('doctor', help='offline contract/config report (no GET, no '
                                      'schema fetch, no tool resolution)')
    p.add_argument('--offline', action='store_true',
                   help='accepted for compatibility: doctor is always offline on the '
                        'z0902 route')
    p.add_argument('--live', action='store_true',
                   help='REFUSED: live GET is not supported on the z0902 route')
    p = sub.add_parser('prepare', help='build ONE z0902 request locally (zero paid '
                                       'calls, zero network)')
    p.add_argument('--prompt', help='request prompt text (1..20000 characters)')
    p.add_argument('--prompt-file',
                   help='read the prompt from a local UTF-8 file instead of --prompt')
    p.add_argument('--image', action='append', default=[], metavar='URL',
                   help='image URL, repeatable — packs positionally into '
                        'ref_image_0,1,… in the given order (the first is the '
                        'required ref_image_0)')
    p.add_argument('--image-slot', action='append', default=[], metavar='SLOT=URL',
                   help='explicit image slot, e.g. ref_image_5=URL — use for gapped '
                        'selections (revision 2: slots 1..5 independently optional, '
                        'caller numbering preserved)')
    p.add_argument('--duration', type=int, default=DEFAULT_DURATION,
                   help='plain integer 1..15 (default 5; bool/float refused by the '
                        'adapter, never rounded silently)')
    p.add_argument('--seed', type=int, default=None,
                   help='optional plain integer seed; omitted entirely from the '
                        'request when not given (no invented range)')
    p.add_argument('--resolution', default=h3.Z0902_DEFAULT_RESOLUTION,
                   choices=list(h3.Z0902_RESOLUTION_OPTIONS),
                   help='one of the 8 exact user contract options; project default '
                        '1088p竖(1088*1920) is always serialized explicitly')
    p = sub.add_parser('submit-guarded', help='future single paid z0902 submission '
                                              '(authorization-gated; refuses while '
                                              'guards disabled)')
    p.add_argument('--request', required=True,
                   help='prepared request.json path from prepare')
    p.add_argument('--authorization', required=True,
                   help='human authorization record JSON (schema '
                        + h3.Z0902_AUTH_SCHEMA + '; must match this exact z0902 '
                        'request fingerprint + resolution, max_calls=1, positive '
                        'bounded CNY budget with structured quote)')
    args = parser.parse_args(argv)
    handlers = {'doctor': cmd_doctor, 'prepare': cmd_prepare,
                'submit-guarded': cmd_submit_guarded}
    try:
        return handlers[args.command](args)
    except h3.H3AdapterError as exc:
        print('REFUSED: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
