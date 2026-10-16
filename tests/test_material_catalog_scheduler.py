"""UNIT ONLY: focused catalog/queue tests; no network, models or production writes."""
import copy
import hashlib
import importlib
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / 'runtime/evidence/material-catalog-scheduler-v1/tmp'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')


def sha(data):
    return hashlib.sha256(data).hexdigest()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('adapters.material_catalog'),
                             'catalog implementation missing')
        self.c = importlib.import_module('adapters.material_catalog')
        self.s = importlib.import_module('adapters.material_scheduler')
        TMP.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'material-library/demo'
        self.clip = dict(clip_id='d-1-v1', shot_id='d-1', product_id='demo', take=1,
                         status='approved', duration_sec=3.0, sha256=sha(b'UNIT ONLY'),
                         file='approved/d-1-v1.mp4', sidecar='approved/d-1-v1.json',
                         poster='approved/d-1-v1.jpg', status_note='existing human review',
                         content={'scene': 'planned, not observed'}, edit={'mix_tags': ['old']})
        write(self.base / 'manifest.json', {'schema': 'lab-material-library/v3',
              'product_id': 'demo', 'clips': [self.clip], 'counts': {'approved': 1},
              'usable_seconds': 3.0})
        write(self.base / self.clip['sidecar'], self.clip)
        (self.base / self.clip['file']).write_bytes(b'UNIT ONLY')
        write(self.root / 'cases/demo/case.json', {'case_id': 'demo', 'product_name': '理线器'})
        write(self.root / 'material-library/index.json', {'schema': 'lab-material-library/v3', 'products': []})
        self.profile = {'enabled': False, 'provider': None, 'model': None, 'mode': 'unverified'}
        write(self.root / 'config/doubao_text.json', {'video_analysis': self.profile})
        self.binding = self.c.build_binding(self.root, self.clip, self.profile)
        self.tax = self.c.load_taxonomy()

    def raw(self):
        ep = self.root / 'runtime/frame.jpg'
        ep.parent.mkdir(parents=True, exist_ok=True)
        ep.write_bytes(b'UNIT ONLY FRAME')
        tag = lambda code: {'code': code, 'source': 'observed', 'evidence_ids': ['e1']}
        return {'name_parts': {'product': '理线器', 'scene': '办公桌', 'action': '手整理线缆'},
                'summary': '手把桌面的线缆放进夹子。',
                'labels': {'role': [tag('action')], 'scene': [tag('office_desk')],
                           'subject': [tag('hands')], 'action': [tag('organize')]},
                'observations': [{'id': 'o1', 'start_sec': 0, 'end_sec': 2,
                                  'text': '手整理线缆', 'evidence_ids': ['e1']}],
                'evidence': [{'id': 'e1', 'kind': 'frame', 'path': 'runtime/frame.jpg',
                              'sha256': sha(b'UNIT ONLY FRAME'), 'timestamp_sec': 1}],
                'usable_segments': [{'start_sec': 0.5, 'end_sec': 2.5,
                                     'basis': ['o1'], 'reviewed': False}],
                'analysis': {'provider': 'human', 'model': None, 'model_revision': None,
                             'method': 'human_video', 'input_coverage': 'full_video',
                             'audio_status': 'not_analyzed', 'created_utc': '2026-10-13T00:00:00Z',
                             'request_id': None, 'authorization_id': None, 'limitations': []}}

    def catalog(self):
        return self.c.normalize_catalog(self.raw(), self.binding, self.tax)

    def test_name_taxonomy_dedup_and_legacy_preserved(self):
        raw = self.raw()
        raw['labels']['scene'] *= 2
        cat = self.c.normalize_catalog(raw, self.binding, self.tax)
        self.assertEqual(cat['display_name'], '理线器_动作_办公桌_手整理线缆_d-1-v1')
        self.assertEqual(len(cat['labels']['scene']), 1)
        self.assertEqual(cat['labels']['scene'][0]['label'], '办公桌')
        self.assertEqual(cat['labels']['shot_size'][0]['code'], 'unknown')
        self.assertEqual(self.c.validate_catalog(cat, 3, self.tax), [])
        self.assertEqual(self.clip['content']['scene'], 'planned, not observed')

    def test_illegal_name_chars_cleaned_without_losing_id(self):
        raw = self.raw()
        raw['name_parts']['product'] = '  理/线_器\n'
        raw['name_parts']['action'] = '放' * 100
        cat = self.c.normalize_catalog(raw, self.binding, self.tax)
        self.assertNotIn('/', cat['display_name'])
        self.assertTrue(cat['display_name'].endswith('_d-1-v1'))
        self.assertLessEqual(len(cat['display_name']), 128)

    def test_invalid_evidence_time_enum_and_review_refused(self):
        for mutate in [lambda c: c['labels']['scene'][0].update(code='invented'),
                       lambda c: c['labels']['action'][0].update(evidence_ids=[]),
                       lambda c: c['usable_segments'][0].update(end_sec=9),
                       lambda c: c.update(state='reviewed', review=None),
                       lambda c: c['observations'][0].update(evidence_ids=['missing'])]:
            cat = self.catalog()
            mutate(cat)
            self.assertTrue(self.c.validate_catalog(cat, 3, self.tax))

    def test_binding_rejects_changed_bytes_and_escape(self):
        (self.base / self.clip['file']).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.c.build_binding(self.root, self.clip, self.profile)
        for path in ['../../outside.mp4', 'C:/outside.mp4']:
            self.clip['file'] = path
            with self.assertRaises(ValueError):
                self.c.build_binding(self.root, self.clip, self.profile)

    def test_blind_analysis_does_not_expose_plan_and_sampling_is_honest(self):
        payload = {'evidence': self.raw()['evidence'], 'duration_sec': 3,
                   'input_coverage': 'sampled_frames', 'generation': {'prompt': 'SECRET PLAN'},
                   'content': self.clip['content'], 'binding': self.binding}
        seen = []
        def invoke(value):
            seen.append(value)
            return self.raw()
        raw = self.c.analyze_video(payload, invoke)
        self.assertNotIn('SECRET PLAN', json.dumps(seen))
        self.assertNotIn('planned, not observed', json.dumps(seen))
        self.assertEqual(raw['analysis']['input_coverage'], 'sampled_frames')
        self.assertEqual(raw['analysis']['audio_status'], 'not_analyzed')
        self.assertTrue(raw['analysis']['limitations'])

    def test_publish_preserves_latest_review_and_requires_reviewed(self):
        cat = self.catalog()
        with self.assertRaises(ValueError):
            self.c.publish_catalog(self.root, 'demo', 'd-1-v1', cat, self.clip['sha256'])
        cat = self.c.review_catalog(cat, 'tester', 'UNIT ONLY', self.root)
        m = read(self.base / 'manifest.json')
        m['clips'][0]['status_note'] = 'new human decision'
        write(self.base / 'manifest.json', m)
        result = self.c.publish_catalog(self.root, 'demo', 'd-1-v1', cat, self.clip['sha256'])
        self.assertEqual(result['status'], 'published')
        m = read(self.base / 'manifest.json')
        side = read(self.base / self.clip['sidecar'])
        self.assertEqual(m['clips'][0]['status_note'], 'new human decision')
        self.assertEqual(m['clips'][0]['catalog'], side['catalog'])
        self.assertEqual(m['usable_seconds'], 3)
        self.assertEqual((self.base / self.clip['file']).read_bytes(), b'UNIT ONLY')
        self.c.publish_catalog(self.root, 'demo', 'd-1-v1', cat, self.clip['sha256'])
        self.assertEqual(read(self.base / self.clip['sidecar'])['catalog']['revision'], 1)

    def test_publish_recovers_interrupted_projection(self):
        cat = self.c.review_catalog(self.catalog(), 'tester', 'UNIT ONLY', self.root)
        real = self.s.os.replace
        calls = []
        def interrupted(src, dst):
            if Path(dst) == self.base / 'manifest.json':
                calls.append(dst)
                if len(calls) == 1:
                    raise OSError('UNIT ONLY interrupted')
            return real(src, dst)
        with mock.patch.object(self.s.os, 'replace', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.c.publish_catalog(self.root, 'demo', 'd-1-v1', cat, self.clip['sha256'])
        with self.s.library_write_lock(self.root):
            pass
        self.assertEqual(read(self.base / 'manifest.json')['clips'][0]['catalog'],
                         read(self.base / self.clip['sidecar'])['catalog'])

    def test_stale_evidence_and_case_block_search_and_publish(self):
        cat = self.c.review_catalog(self.catalog(), 'tester', 'UNIT ONLY', self.root)
        self.c.publish_catalog(self.root, 'demo', 'd-1-v1', cat, self.clip['sha256'])
        self.assertEqual(len(self.c.search(self.root, 'demo', role='action')), 1)
        (self.root / 'runtime/frame.jpg').write_bytes(b'changed')
        self.assertEqual(self.c.search(self.root, 'demo'), [])
        with self.assertRaises(ValueError):
            self.c.publish_catalog(self.root, 'demo', 'd-1-v1', cat, self.clip['sha256'])

    def test_readonly_plan_does_not_create_queue_or_load_models(self):
        result = self.c.dispatch(self.root, Namespace(cmd='catalog-plan', product='demo', clip='d-1-v1'))
        self.assertFalse(result['provider_ready'])
        self.assertFalse((self.root / 'runtime/material-analysis').exists())

    def test_decode_actual_video_not_poster(self):
        import cv2
        import numpy as np
        path = self.base / self.clip['file']
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 24, (64, 64))
        self.assertTrue(writer.isOpened())
        for n in range(72):
            writer.write(np.full((64, 64, 3), (0, 0, 220), dtype=np.uint8))
        writer.release()
        (self.base / self.clip['poster']).write_bytes(b'NOT A DECODED VIDEO FRAME')
        self.clip['sha256'] = self.c.file_sha(path)
        binding = self.c.build_binding(self.root, self.clip, self.profile)
        result = self.c.inspect_media(self.root, self.clip, binding)
        self.assertEqual(result['fps'], 24)
        self.assertAlmostEqual(result['duration_sec'], 3)
        self.assertLessEqual(len(result['evidence']), 12)
        self.assertEqual(result['input_coverage'], 'sampled_frames')
        frame = cv2.imdecode(np.frombuffer((self.root / result['evidence'][0]['path']).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.assertGreater(float(frame[:, :, 2].mean()), 200)
        self.assertEqual((self.base / self.clip['poster']).read_bytes(), b'NOT A DECODED VIDEO FRAME')

    def test_full_injected_pipeline_keeps_media_approval_separate(self):
        self.profile.update(enabled=True, provider='unit_only', model='unit_only')
        write(self.root / 'config/doubao_text.json', {'video_analysis': self.profile})
        self.binding = self.c.build_binding(self.root, self.clip, self.profile)
        task = self.c.dispatch(self.root, Namespace(cmd='catalog-enqueue', product='demo', clip='d-1-v1'))['task_id']
        auth = {'authorization_id': 'unit', 'approved_by': 'test', 'consent': 'UNIT ONLY',
                'expires_utc': '2099-01-01T00:00:00Z', 'provider': 'unit_only', 'model': 'unit_only',
                'product_id': 'demo', 'clip_ids': ['d-1-v1'], 'max_calls': 1, 'budget_cny': 1,
                'price_known': True, 'max_cost_per_call_cny': 1}
        write(self.root / 'runtime/auth.json', auth)
        calls = []
        def invoke(payload):
            calls.append(payload)
            return self.raw()
        def inspection(*unused):
            self.c._evidence_dir(self.root, self.binding).mkdir(parents=True)
            return {'duration_sec': 3, 'evidence': self.raw()['evidence'], 'input_coverage': 'sampled_frames'}
        args = Namespace(cmd='catalog-run-once', authorization='runtime/auth.json')
        with mock.patch.object(self.c, 'inspect_media', side_effect=inspection):
            for _ in range(4):
                result = self.c.dispatch(self.root, args, invoke)
        self.assertEqual(result['state'], 'waiting_review')
        self.assertEqual(len(calls), 1)
        self.c.dispatch(self.root, Namespace(cmd='catalog-review', task=task, decision='accept', by='tester', note='UNIT ONLY'))
        result = self.c.dispatch(self.root, args, invoke)
        self.assertEqual(result['state'], 'completed')
        self.assertEqual(self.c.dispatch(self.root, args, invoke)['state'], 'idle')
        self.assertEqual(len(calls), 1)
        clip = read(self.base / self.clip['sidecar'])
        self.assertEqual(clip['status'], 'approved')
        self.assertEqual(clip['catalog']['state'], 'reviewed')

    def test_existing_library_status_change_preserves_catalog(self):
        spec = importlib.util.spec_from_file_location('catalog_flow_test', ROOT / 'scripts/material-flow.py')
        flow = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(flow)
        flow.ROOT = self.root
        flow.LIB_ROOT = self.root / 'material-library'
        cat = self.c.review_catalog(self.catalog(), 'tester', 'UNIT ONLY', self.root)
        self.c.publish_catalog(self.root, 'demo', 'd-1-v1', cat, self.clip['sha256'])
        m = read(self.base / 'manifest.json')
        m['clips'][0]['qc'] = {'reviewed_by': 'tester', 'notes': []}
        m['clips'][0]['edit']['pairs_well_with'] = []
        write(self.base / 'manifest.json', m)
        flow._lib_set_status('demo', 'd-1-v1', 'rejected', 'new decision', 'tester')
        result = read(self.base / 'manifest.json')['clips'][0]
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['catalog']['state'], 'reviewed')
        self.assertTrue((self.base / 'rejected/d-1-v1.mp4').exists())
        self.assertEqual(self.c.search(self.root, 'demo'), [])
        # All legacy writes now use the transaction boundary too.
        self.assertTrue((self.root / 'runtime/material-analysis/library-transaction.json').exists())

    def test_catalog_cli_registered(self):
        spec = importlib.util.spec_from_file_location('catalog_cli_test', ROOT / 'scripts/material-flow.py')
        flow = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(flow)
        flow.ROOT = self.root
        with mock.patch('builtins.print') as printed:
            self.assertEqual(flow.main(['catalog-plan', '--product', 'demo', '--clip', 'd-1-v1']), 0)
        self.assertIn('provider_ready', printed.call_args.args[0])

    def test_sampled_analysis_cannot_claim_reviewed_windows(self):
        raw = self.raw()
        raw['usable_segments'][0]['reviewed'] = True
        result = self.c.analyze_video({'duration_sec': 3, 'evidence': raw['evidence'],
                                     'input_coverage': 'sampled_frames'}, lambda payload: raw)
        self.assertFalse(result['usable_segments'][0]['reviewed'])

    def test_name_parts_cannot_claim_unobserved_scene(self):
        raw = self.raw()
        raw['labels']['scene'] = []
        raw['name_parts']['scene'] = '未看过的厨房'
        cat = self.c.normalize_catalog(raw, self.binding, self.tax)
        self.assertIn('_场景未识别_', cat['display_name'])

    def test_custom_validator_rejects_wrong_label_and_extra_fields(self):
        cat = self.catalog()
        cat['labels']['scene'][0]['label'] = '厨房'
        self.assertTrue(self.c.validate_catalog(cat, 3, self.tax))

    def test_plan_comparison_does_not_replace_observed_content(self):
        cat = self.catalog()
        compare = self.c.compare_plan(self.clip, cat)
        self.assertEqual(compare['state'], 'partial')
        self.assertEqual(compare['items'][0]['plan_field'], 'content.scene')
        self.assertIn('planned, not observed', compare['items'][0]['difference'])
        self.assertEqual(compare['items'][0]['observation_ids'], ['o1'])
        self.assertEqual(cat['summary'], '手把桌面的线缆放进夹子。')
        cat = self.catalog()
        cat['assume_pass'] = True
        self.assertTrue(self.c.validate_catalog(cat, 3, self.tax))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('adapters.material_scheduler'),
                             'scheduler implementation missing')
        self.s = importlib.import_module('adapters.material_scheduler')
        TMP.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=TMP)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'queue.sqlite3'
        self.binding = {'product_id': 'demo', 'clip_id': 'd-1-v1', 'media_sha256': 'a' * 64}
        self.now = '2026-10-13T00:00:00Z'

    def enqueue(self):
        return self.s.enqueue(self.db, self.binding, ['inspect_media', 'analyze_video',
                  'validate_catalog', 'await_catalog_review', 'publish_catalog'])

    def test_duplicate_and_exclusive_claim(self):
        task = self.enqueue()
        self.assertEqual(task, self.enqueue())
        self.assertNotEqual(task, self.s.enqueue(self.db, dict(self.binding, clip_id='d-2-v1'), ['inspect_media']))
        one = self.s.claim_next(self.db, 'owner1', self.now)
        two = self.s.claim_next(self.db, 'owner2', self.now)
        self.assertIsNone(two)
        self.s.advance(self.db, one['task_id'], one['claim_token'],
                       {'type': 'wait_authorization', 'reason': 'test'}, self.now)
        two = self.s.claim_next(self.db, 'owner2', self.now)
        self.assertNotEqual(one['task_id'], two['task_id'])

    def test_expired_claim_cannot_commit(self):
        task = self.enqueue()
        row = self.s.claim_next(self.db, 'a', self.now)
        self.s.recover(self.db, '2026-10-13T00:03:00Z')
        newer = self.s.claim_next(self.db, 'b', '2026-10-13T00:03:00Z')
        self.assertEqual(newer['task_id'], task)
        with self.assertRaises(ValueError):
            self.s.advance(self.db, task, row['claim_token'], {'type': 'stage_done', 'result': {}},
                           '2026-10-13T00:03:01Z')

    def test_inflight_recovers_uncertain_never_requeued(self):
        task = self.enqueue()
        row = self.s.claim_next(self.db, 'a', self.now)
        self.s.mark_inflight(self.db, task, row['claim_token'], self.now)
        self.s.recover(self.db, '2026-10-13T00:03:00Z')
        self.assertEqual(self.s.get_task(self.db, task)['state'], 'uncertain')
        self.assertIsNone(self.s.claim_next(self.db, 'b', '2026-10-13T00:03:00Z'))

    def test_only_unfinished_stage_runs_and_review_waits(self):
        task = self.enqueue()
        seen = []
        def step(row):
            seen.append(row['stage'])
            return {'type': 'stage_done', 'result': {'ok': True}}
        handlers = {k: step for k in ['inspect_media', 'analyze_video', 'validate_catalog', 'publish_catalog']}
        for _ in range(4):
            self.s.run_once(self.db, handlers, 'worker')
        self.assertEqual(seen, ['inspect_media', 'analyze_video', 'validate_catalog'])
        self.assertEqual(self.s.get_task(self.db, task)['state'], 'waiting_review')
        self.assertEqual(self.s.run_once(self.db, handlers, 'worker')['state'], 'idle')

    def test_retry_is_bounded_and_cancel_is_terminal(self):
        task = self.enqueue()
        times = ['2026-10-13T00:00:00Z', '2026-10-13T00:00:05Z', '2026-10-13T00:00:25Z']
        for now in times:
            row = self.s.claim_next(self.db, 'a', now)
            self.s.advance(self.db, task, row['claim_token'], {'type': 'retryable_error', 'reason': 'locked'}, now)
        self.assertEqual(self.s.get_task(self.db, task)['state'], 'failed')
        other = self.s.enqueue(self.db, dict(self.binding, clip_id='other'), ['inspect_media'])
        self.s.cancel(self.db, other)
        self.assertEqual(self.s.get_task(self.db, other)['state'], 'cancelled')

    def test_call_reservation_checks_scope_expiry_and_consumes_once(self):
        profile = {'provider': 'unit', 'model': 'unit'}
        auth = {'authorization_id': 'u', 'approved_by': 'tester', 'consent': 'UNIT ONLY',
                'expires_utc': '2026-10-14T00:00:00Z', 'provider': 'unit', 'model': 'unit',
                'product_id': 'demo', 'clip_ids': ['d-1-v1'], 'max_calls': 1, 'budget_cny': 1,
                'price_known': True, 'max_cost_per_call_cny': 1}
        task = self.s.enqueue(self.db, self.binding, ['analyze_video'])
        row = self.s.claim_next(self.db, 'a', self.now)
        for bad in [dict(auth, clip_ids=['other']), dict(auth, expires_utc=self.now),
                    dict(auth, budget_cny=0.5), dict(auth, price_known=False)]:
            with self.assertRaises(ValueError):
                self.s.reserve_call(self.db, task, row['claim_token'], bad, profile, self.now)
        self.assertFalse(self.s.get_task(self.db, task)['in_flight'])
        self.s.reserve_call(self.db, task, row['claim_token'], auth, profile, self.now)
        self.assertTrue(self.s.get_task(self.db, task)['in_flight'])
        with self.assertRaises(ValueError):
            self.s.reserve_call(self.db, task, row['claim_token'], auth, profile, self.now)
        self.s.cancel(self.db, task)
        self.assertEqual(self.s.get_task(self.db, task)['state'], 'running')
        self.assertTrue(self.s.get_task(self.db, task)['cancel_requested'])
        self.s.advance(self.db, task, row['claim_token'], {'type': 'fatal_error'}, self.now)
        self.assertEqual(self.s.get_task(self.db, task)['state'], 'uncertain')
        self.s.resume_authorized(self.db)
        self.assertEqual(self.s.get_task(self.db, task)['state'], 'uncertain')

    def test_paid_timeout_never_retries(self):
        task = self.s.enqueue(self.db, self.binding, ['analyze_video'])
        def handler(row):
            self.s.mark_inflight(self.db, row['task_id'], row['claim_token'], self.s.utc())
            raise TimeoutError('secret must not leak')
        result = self.s.run_once(self.db, {'analyze_video': handler}, 'a')
        self.assertEqual(result['state'], 'uncertain')
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(self.s.run_once(self.db, {'analyze_video': handler}, 'b')['state'], 'idle')

    def test_local_transient_failure_goes_to_bounded_retry(self):
        self.enqueue()
        def handler(row):
            raise OSError('temporarily locked')
        result = self.s.run_once(self.db, {'inspect_media': handler}, 'a')
        self.assertEqual(result['state'], 'retry_wait')

    def test_cancel_keeps_active_occupancy_until_handler_exits(self):
        task = self.enqueue()
        self.s.enqueue(self.db, dict(self.binding, clip_id='other'), ['inspect_media'])
        row = self.s.claim_next(self.db, 'a', self.now)
        self.s.mark_inflight(self.db, task, row['claim_token'], self.now)
        self.s.cancel(self.db, task)
        self.assertIsNone(self.s.claim_next(self.db, 'b', self.now))
        result = self.s.advance(self.db, task, row['claim_token'],
                                {'type': 'stage_done', 'result': {'response': 'saved'}}, self.now)
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(result['results']['inspect_media'], {'response': 'saved'})
        self.assertIsNotNone(self.s.claim_next(self.db, 'b', self.now))

    def test_cancel_before_publication_never_runs_effect(self):
        task = self.s.enqueue(self.db, self.binding, ['publish_catalog'])
        effects = []
        def handler(row):
            self.s.cancel(self.db, task)
            return self.s.finish_publication(self.db, row, Path(self.tmp.name),
                                              lambda: effects.append('published'))
        result = self.s.run_once(self.db, {'publish_catalog': handler}, 'worker')
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(effects, [])

    def test_publication_commits_task_before_cancel_can_succeed(self):
        task = self.s.enqueue(self.db, self.binding, ['publish_catalog'])
        effects = []
        def handler(row):
            return self.s.finish_publication(self.db, row, Path(self.tmp.name),
                                              lambda: effects.append('published') or {'status': 'published'})
        result = self.s.run_once(self.db, {'publish_catalog': handler}, 'worker')
        self.assertEqual(result['state'], 'completed')
        with self.assertRaises(ValueError):
            self.s.cancel(self.db, task)
        self.assertEqual(effects, ['published'])

    def test_unknown_remote_execution_blocks_next_inference(self):
        task = self.enqueue()
        self.s.enqueue(self.db, dict(self.binding, clip_id='other'), ['analyze_video'])
        row = self.s.claim_next(self.db, 'a', self.now)
        self.s.mark_inflight(self.db, task, row['claim_token'], self.now)
        self.s.recover(self.db, '2026-10-13T00:03:00Z')
        self.assertIsNone(self.s.claim_next(self.db, 'b', '2026-10-13T00:03:00Z'))


if __name__ == '__main__':
    unittest.main()
