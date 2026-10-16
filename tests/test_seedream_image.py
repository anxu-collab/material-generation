"""Offline guard tests for adapters/seedream_image.py.

The injected transport is a unit-test seam for the guards (cap, ledger, key handling, no retry).
It says nothing about the upstream service; the real call evidence lives in
runtime/evidence/seedream-probe/.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adapters'))
import seedream_image as sd  # noqa: E402

FAKE_KEY = 'unit-test-key-0000-not-real'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def _future():
    return (datetime.now(timezone.utc) + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')


class Transport:
    def __init__(self, fail_first=False, boom=False):
        self.calls = []
        self.fail_first = fail_first
        self.boom = boom

    def __call__(self, url, headers, data, timeout):
        self.calls.append((url, dict(headers)))
        if data is None:
            return 200, PNG
        if self.boom:
            raise TimeoutError('simulated timeout')
        if self.fail_first and len(self.calls) == 1:
            return 404, json.dumps({'error': {'code': 'ModelNotOpen', 'message': 'x ' + FAKE_KEY}}).encode()
        body = json.loads(data)
        return 200, json.dumps({
            'model': body['model'], 'usage': {'generated_images': 1},
            'data': [{'url': 'https://ark-acg-cn-beijing.tos-cn-beijing.volces.com/a.png',
                      'size': '1584x2816'}]}).encode()


class SeedreamGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = self.root / 'store'
        self.env = {'ARK_API_KEY': FAKE_KEY}

    def tearDown(self):
        self.tmp.cleanup()

    def _jobs(self, prompts, name='jobs.json'):
        items = [{'item_id': 'img%d' % i, 'prompt': p, 'category': 'sensation'}
                 for i, p in enumerate(prompts)]
        path = self.root / name
        path.write_text(json.dumps({'batch_id': 'b1', 'items': items}, ensure_ascii=False),
                        encoding='utf-8')
        return path

    def _auth(self, cap=5, **over):
        rec = {'schema': sd.AUTH_SCHEMA, 'human_authorized': True, 'authorization_id': 'auth1',
               'model': sd.MODEL, 'max_images': cap, 'expires_utc': _future(),
               'consent': 'user approved a bounded test batch'}
        rec.update(over)
        path = self.root / 'auth.json'
        path.write_text(json.dumps(rec), encoding='utf-8')
        return path

    def _run(self, jobs, auth, transport, **kw):
        return sd.run(jobs, auth, transport=transport, store=self.store,
                      environ=self.env, markers=['抗氧化'], **kw)

    def test_plan_is_offline_and_tags_claims(self):
        out = sd.plan(self._jobs(['冰橙汁清爽感', '抗氧化的蓝莓']), markers=['抗氧化'])
        self.assertEqual((out['network_calls'], out['paid_calls']), (0, 0))
        self.assertEqual(out['items'][1]['claim_risk_markers'], ['抗氧化'])
        self.assertFalse(self.store.exists())

    def test_model_is_pinned(self):
        self.assertEqual(sd.MODEL, 'doubao-seedream-5-0-flash-260915')
        _, planned = sd.load_jobs(self._jobs(['a']), markers=[])
        self.assertEqual(planned[0]['body']['model'], sd.MODEL)

    def test_authorization_refusals(self):
        jobs = self._jobs(['a'])
        for over in ({'human_authorized': False}, {'model': 'other'}, {'max_images': 0},
                     {'max_images': True}, {'expires_utc': '2020-01-01T00:00:00Z'},
                     {'consent': ''}, {'schema': 'x'}):
            t = Transport()
            with self.assertRaises(sd.SeedreamError):
                self._run(jobs, self._auth(**over), t)
            self.assertEqual(t.calls, [])

    def test_cap_enforced_across_runs_and_claim_tag_does_not_block(self):
        auth = self._auth(cap=2)
        t = Transport()
        r1 = self._run(self._jobs(['抗氧化的蓝莓']), auth, t)
        self.assertEqual(len(r1['results']), 1)
        self.assertEqual(r1['attempts_used'], 1)
        t2 = Transport()
        with self.assertRaises(sd.SeedreamError):
            self._run(self._jobs(['x', 'y'], 'jobs2.json'), auth, t2)
        self.assertEqual(t2.calls, [])

    def test_identical_request_is_not_billed_twice(self):
        auth = self._auth()
        jobs = self._jobs(['同一画面'])
        self._run(jobs, auth, Transport())
        t = Transport()
        out = self._run(jobs, auth, t)
        self.assertEqual(out['skipped_existing'], 1)
        self.assertEqual(t.calls, [])

    def test_failure_counts_as_spent_and_never_retries(self):
        t = Transport(fail_first=True)
        out = self._run(self._jobs(['a']), self._auth(), t)
        self.assertEqual(len(t.calls), 1)
        self.assertEqual(out['attempts_used'], 1)
        self.assertEqual(len(out['failures']), 1)
        self.assertNotIn(FAKE_KEY, json.dumps(out))

    def test_unknown_outcome_counts_as_spent(self):
        out = self._run(self._jobs(['a']), self._auth(), Transport(boom=True))
        self.assertEqual(out['attempts_used'], 1)
        self.assertIn('OUTCOME_UNKNOWN', out['failures'][0]['detail'])

    def test_key_never_lands_in_store_and_versions_are_kept(self):
        auth = self._auth()
        self._run(self._jobs(['a']), auth, Transport())
        self._run(self._jobs(['b']), auth, Transport())          # same item_id, new prompt
        files = [p for p in self.store.rglob('*') if p.is_file()]
        names = sorted(p.name for p in files)
        self.assertIn('img0.v1.png', names)
        self.assertIn('img0.v2.png', names)
        for path in files:
            if path.suffix in ('.json', '.jsonl'):
                self.assertNotIn(FAKE_KEY, path.read_text(encoding='utf-8'))

    def test_key_resolution(self):
        with self.assertRaises(sd.SeedreamError):
            sd.resolve_key(environ={}, env_file=self.root / 'none.env')
        env_file = self.root / 'x.env'
        env_file.write_text('OTHER_SECRET=nope\nARK_API_KEY="%s"\n' % FAKE_KEY, encoding='utf-8')
        self.assertEqual(sd.resolve_key(environ={}, env_file=env_file), FAKE_KEY)
        self.assertEqual(sd.resolve_key(environ={'ARK_API_KEY': 'env-wins-0000'},
                                        env_file=env_file), 'env-wins-0000')
        with self.assertRaises(sd.SeedreamError):                  # missing file, no env
            sd.resolve_key(environ={}, env_file=self.root / 'absent.env')
        self.assertEqual(sd.KEY_ENV_FILE, sd.LAB_ROOT / 'config' / 'seedream.local.env')
        self.assertEqual(sd.DEFAULT_SIZE, '1088x1920')

    def test_default_size_is_1080p_portrait(self):
        _, planned = sd.load_jobs(self._jobs(['a']), markers=[])
        self.assertEqual(planned[0]['body']['size'], '1088x1920')

    def test_key_file_is_git_ignored(self):
        ignore = (sd.LAB_ROOT / '.gitignore').read_text(encoding='utf-8')
        self.assertIn('config/seedream.local.env', ignore)

    def test_invalid_items_refused(self):
        for bad in ({'item_id': '../x', 'prompt': 'a'}, {'item_id': 'ok', 'prompt': ''},
                    {'item_id': 'ok', 'prompt': 'a', 'size': 'huge'}):
            with self.assertRaises(sd.SeedreamError):
                sd.validate_item(bad)

    def test_reference_images_must_live_under_cases(self):
        with self.assertRaises(sd.SeedreamError):
            sd.validate_item({'item_id': 'r1', 'prompt': 'a', 'ref_images': [str(self.root / 'x.jpg')]})
        with self.assertRaises(sd.SeedreamError):
            sd.validate_item({'item_id': 'r1', 'prompt': 'a', 'ref_images': ['a'] * 5})

    def test_reference_image_is_sent_but_fingerprinted_by_hash(self):
        ref = sd.REF_ROOT / '_unit_test_ref.png'
        ref.write_bytes(PNG)
        try:
            out = sd.validate_item({'item_id': 'r1', 'prompt': 'a',
                                    'ref_images': ['cases/_unit_test_ref.png']})
        finally:
            ref.unlink()
        self.assertTrue(out['body']['image'].startswith('data:image/png;base64,'))
        self.assertEqual(out['ref_image_sha256'], [sd._sha(PNG)])

    def test_download_host_must_be_vendor(self):
        with self.assertRaises(sd.SeedreamError):
            sd._check_https('https://evil.example.com/a.png', '.volces.com')
        with self.assertRaises(sd.SeedreamError):
            sd._check_https('http://x.volces.com/a.png', '.volces.com')


if __name__ == '__main__':
    unittest.main()
