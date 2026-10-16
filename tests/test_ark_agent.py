"""material-flow LLM provider tests: ark_agent_json (Managed Agents session+SSE) / llm_json
dispatch, agent/environment/skill sync (industry Skills binding) and enrich batching.

Standard-library unittest only (no pytest in this environment). Zero network: the Ark
transport is exercised only through an injected mock client (is_mock=True).
"""
import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location('material_flow', ROOT / 'scripts' / 'material-flow.py')
mf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mf)

_ENRICH_KEYS = ['consumer_need', 'person_exposure', 'real_product_craft', 'concept_association',
                'effects_packaging', 'visual_metaphor', 'edit_handles']


class ImageTransportTests(unittest.TestCase):
    def test_encoded_image_hash_is_checked_before_network(self):
        path = ROOT / 'cases/laox/refs/host_front.png'
        client = mf._ArkClient('unit-only-not-a-key', 1)
        with mock.patch.object(client, '_http_json') as send:
            with self.assertRaisesRegex(SystemExit, 'encoded image bytes'):
                client.send_image_message('unit', 'unit', [path], ['0' * 64])
            send.assert_not_called()
            hashes = client.send_image_message('unit', 'unit', [path], [mf._sha_file(path)])
            self.assertEqual(hashes, [mf._sha_file(path)])
            sent = send.call_args.args[2]['events'][0]['content'][1]
            self.assertEqual(mf.hashlib.sha256(mf.base64.b64decode(sent['source']['data'])).hexdigest(), hashes[0])

    def test_clef_batch_failure_exits_managed_session(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'runtime/evidence') as td:
            base = Path(td)
            control = base / 'unit_only_controller.py'
            control.write_text(
                'import contextlib,urllib.request\nfrom pathlib import Path\n'
                'NoRedirect=urllib.request.HTTPRedirectHandler\n'
                '@contextlib.contextmanager\ndef session():\n'
                ' try: yield\n'
                ' finally: Path(__file__).with_suffix(".exited").write_text("released")\n')
            req = base / 'req.json'; out = base / 'out.json'
            req.write_text(json.dumps({'requests_by_question': {'a': {'state': 'UNIT ONLY', 'questions': {}}}}))
            opener = mock.Mock()
            opener.open.side_effect = TimeoutError('UNIT ONLY')
            with mock.patch.object(mf, 'CLEF_ON_DEMAND', control), \
                    mock.patch.object(mf.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(TimeoutError):
                    mf._clef_call_batch(req, out)
            self.assertTrue(control.with_suffix('.exited').exists())
            self.assertEqual(json.loads(out.read_text())['status'], 'failed')
            self.assertEqual(json.loads(out.read_text())['answers'], {})

    def test_clef_question_inputs_keep_all_shots_and_original_questions(self):
        run = ROOT / 'runtime/material-flow/laox-stand-cloud-closed-20261015'
        creation = mf._read(run / 'creation.json')
        case = mf._read(ROOT / creation['_meta']['case'])
        requests = mf._clef_requests(case, creation, mf._read(run / 'review.json'))
        self.assertEqual(set(requests), set(mf._clef_questions(case)))
        for qid, req in requests.items():
            self.assertEqual(req['questions'], {qid: mf._clef_questions(case)[qid]})
            for shot in creation['shots']:
                self.assertIn(shot['shot_id'], req['state'])
            self.assertLess(len(req['state']), 3500)

    def test_clef_preserves_evidence_without_truncation(self):
        run = ROOT / 'runtime/material-flow/laox-stand-cloud-closed-20261015'
        creation = mf._read(run / 'creation.json')
        case = mf._read(ROOT / creation['_meta']['case'])
        state = mf._clef_state(case, creation, mf._read(run / 'review.json'))
        for shot in creation['shots']:
            for value in (shot['scene'], shot['story_beat'], shot['camera'],
                          shot['consumer_need']['visible_evidence'],
                          shot['consumer_need']['information_gain']):
                self.assertIn(value, state)
        self.assertLess(len(state), 16000)

    def test_image_block_carries_exact_bytes(self):
        import base64
        path = ROOT / 'cases/laox-stand/refs/product_main.jpg'
        block = mf._ark_image_block(path)
        self.assertEqual(block['type'], 'image')
        self.assertEqual(block['source']['media_type'], 'image/jpeg')
        self.assertEqual(base64.b64decode(block['source']['data']), path.read_bytes())

    def test_image_block_rejects_nonimage_before_transport(self):
        with self.assertRaises(SystemExit):
            mf._ark_image_block(ROOT / 'config/doubao_text.json')

    def test_image_block_rejects_outside_project(self):
        with self.assertRaises(SystemExit):
            mf._ark_image_block(ROOT.parent / 'outside.jpg')


class _FakeArkClient:
    """Injected Ark client: sessions/messages, agent create/get/update, skill upload."""

    is_mock = True

    def __init__(self, session_text='{"shots": [{"shot_id": "a1"}]}', agents=None):
        self.session_text = session_text
        self.agents = dict(agents or {})  # agent_id -> {'version': int, 'skills': [...]}
        self.created_sessions = []
        self.messages = []
        self.http_calls = []
        self.puts = []
        self.uploads = []

    def _http_json(self, method, url, payload=None):
        self.http_calls.append((method, url, payload))
        if method == 'GET' and '/agents/' in url:
            aid = url.rsplit('/', 1)[-1]
            state = self.agents.get(aid)
            if state is not None:
                return dict(state, id=aid)
            return {'id': aid, 'version': 1, 'skills': []}
        if method == 'POST' and url.endswith('/agents'):
            aid = 'agent-%d' % (len(self.http_calls))
            self.agents[aid] = {'version': 1, 'system': payload.get('system'), 'skills': list(payload.get('skills') or [])}
            return {'id': aid}
        if method == 'POST' and '/agents/' in url:
            # update agent: POST /api/v3/agents/{id}
            aid = url.rsplit('/', 1)[-1]
            self.puts.append((url, payload))
            new_ver = int(payload.get('version') or 1) + 1
            self.agents[aid] = dict(self.agents[aid], version=new_ver, **{k: v for k, v in payload.items() if k != 'version'})
            return {'id': aid}
        if method == 'POST' and url.endswith('/environments'):
            n = len([1 for m, u, _ in self.http_calls if u.endswith('/environments')])
            return {'id': 'env-%d' % n}
        return {'id': 'id-%d' % len(self.http_calls)}

    def _multipart_upload(self, url, fields, file_path, filename, content_type):
        self.uploads.append((url, fields, filename))
        if url.endswith('/versions'):
            return {'skill_id': url.split('/')[-2], 'version': '2'}
        return {'id': 'skill-%d' % len(self.uploads), 'latest_version': '1', 'name': 'industry-x'}

    def create_session(self, agent_id, environment_id, title='material-flow'):
        self.created_sessions.append((agent_id, environment_id))
        return {'id': 'sess-1'}

    def send_message(self, session_id, prompt):
        self.messages.append((session_id, prompt))

    def run(self, session_id, prompt):
        self.messages.append((session_id, prompt))
        return self.session_text

    def list_session_files(self, session_id):
        return getattr(self, 'session_files', [])

    def download_output(self, url):
        return getattr(self, 'session_payload', b'')


def _fake_shot(sid, with_fields=False):
    shot = {'shot_id': sid, 'story_beat': 'beat-' + sid, 'scene': 'scene-' + sid, 'duration_sec': 5}
    if with_fields:
        for k in _ENRICH_KEYS:
            shot[k] = 'none' if k == 'person_exposure' else {'filled': k}
        shot['person_exposure'] = 'none'
        shot['consumer_need'] = {'chain_id': 'c', 'role': 'action'}
    return shot



class GenericAgentContractTest(unittest.TestCase):
    def test_active_skill_context_does_not_mix_industries(self):
        home = mf._skill_context({'industry': 'home'})
        healthcare = mf._skill_context({'industry': 'healthcare'})
        generic = mf._skill_context({'industry': 'generic'})
        self.assertIn('name: industry-home', home)
        self.assertNotIn('name: industry-healthcare', home)
        self.assertIn('name: industry-healthcare', healthcare)
        self.assertNotIn('name: industry-home', healthcare)
        self.assertNotIn('name: industry-', generic)

    def test_industry_negative_blocks_gate_even_when_generic_passes(self):
        case = {'case_id': 'c', 'industry': 'home'}
        review = {'batch': {'consumer_checks': {}}}
        creation = {'_meta': {'case': 'case.json'}, 'shots': []}
        with tempfile.TemporaryDirectory() as td:
            run = Path(td)
            (run/'creation.json').write_text(json.dumps(creation))
            def subprocess_stub(*args, **kwargs):
                answers = {k: {'choice': 'yes', 'scores': {'yes': 0.99}}
                           for k in mf.CLEF_QUESTIONS}
                # Deliberately omit required industry answers: cannot pass.
                (run/'clef-gate.response.json').write_text(json.dumps({'answers': answers}))
                return type('Result', (), {'returncode': 0})()
            real_read = mf._read
            def read(path):
                return case if Path(path).name == 'case.json' else real_read(path)
            with mock.patch.object(mf, '_run_dir', return_value=run), \
                    mock.patch.object(mf, '_read', side_effect=read), \
                    mock.patch.object(mf, '_require_plan_review', return_value=review), \
                    mock.patch.object(mf, '_validate_consumer_plan'), \
                    mock.patch.object(mf, '_require_consumer_checks'), \
                    mock.patch.object(mf, '_clef_state', return_value='UNIT ONLY'), \
                    mock.patch.object(mf, '_sha_file', return_value='0'*64), \
                    mock.patch.object(mf.subprocess, 'run', side_effect=subprocess_stub):
                self.assertEqual(mf.cmd_gate(argparse.Namespace(run='r', timeout=1)), 2)
            self.assertFalse(json.loads((run/'clef-gate.json').read_text())['passed'])

    def test_contract_checks_current_shot_id_pattern(self):
        mf._validate_json_contract('a-1', mf.SHOT_SCHEMA['properties']['shot_id'])
        with self.assertRaises(ValueError):
            mf._validate_json_contract('../bad', mf.SHOT_SCHEMA['properties']['shot_id'])

    def test_contract_rejects_wrong_or_duplicate_ids(self):
        for ids in [['a', 'a'], ['a'], ['a', 'c']]:
            with self.subTest(ids=ids), self.assertRaises(SystemExit):
                mf._require_output_ids([{'shot_id': x} for x in ids], ['a', 'b'])
        mf._require_output_ids([{'shot_id': 'b'}, {'shot_id': 'a'}], ['a', 'b'])

    def test_system_drift_and_skill_version_are_synchronized(self):
        from agents.ark_profiles import SYSTEMS
        wanted = [{'type': 'custom', 'skill_id': 'skill-1', 'version': '2'}]
        for system, version in [('old prompt', '2'), (SYSTEMS['reviewer'], '1')]:
            with self.subTest(system=system[:8], version=version), tempfile.TemporaryDirectory() as td:
                cfg = Path(td) / 'config.json'
                cfg.write_text(json.dumps({'agents': {'reviewer': {'agent_id': 'a'}}}))
                client = _FakeArkClient(agents={'a': {'version': 9, 'system': system,
                    'skills': [dict(wanted[0], version=version)], 'model': {'id': 'keep-me'}}})
                with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                    mf.ensure_agent('reviewer', client=client, skills=wanted)
                self.assertEqual(len(client.puts), 1)
                self.assertEqual(client.puts[0][1]['system'], SYSTEMS['reviewer'])
                self.assertNotIn('model', client.puts[0][1])
                self.assertEqual(client.agents['a']['model'], {'id': 'keep-me'})

    def test_changed_skill_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'cfg.json'; cfg.write_text('{}')
            sd = Path(td) / 'skills'; d = sd / 'industry-demo'; d.mkdir(parents=True)
            f = d / 'SKILL.md'; f.write_text('version one')
            client = _FakeArkClient()
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg), mock.patch.object(mf, 'SKILLS_DIR', sd):
                one = mf.ensure_skill('demo', client=client)
                f.write_text('version two')
                two = mf.ensure_skill('demo', client=client)
            self.assertEqual(one, two)
            self.assertEqual(len(client.uploads), 2)
            self.assertTrue(client.uploads[-1][0].endswith('/skill-1/versions'))
            self.assertEqual(json.loads(cfg.read_text())['skills']['demo']['version'], '2')

    def test_schema_is_sent_and_invalid_output_is_rejected(self):
        schema = {'type': 'object', 'required': ['shots'], 'additionalProperties': False,
                  'properties': {'shots': {'type': 'array', 'minItems': 1}}}
        for result in ['{"error":"missing facts"}', '{"shots":[]}', '{"shots":[{}],"extra":1}']:
            with self.subTest(result=result), tempfile.TemporaryDirectory() as td:
                cfg = Path(td) / 'cfg.json'; cfg.write_text('{"environment_id":"e"}')
                client = _FakeArkClient(result)
                with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg), mock.patch.object(mf.time, 'sleep'):
                    with self.assertRaises(SystemExit):
                        mf.ark_agent_json('a', 'task', schema, 1, Path(td)/'call.json', client=client)
                self.assertIn('additionalProperties', client.messages[0][1])
                self.assertTrue(json.loads((Path(td)/'call.json').read_text())['is_error'])

    def test_missing_industry_does_not_use_default_healthcare(self):
        for case in [{}, {'industry': 'perfume'}, {'industry': '../outside'}]:
            with self.subTest(case=case), self.assertRaises(SystemExit):
                mf._skill_context(case)

class ArkAgentJsonTest(unittest.TestCase):
    def test_signed_output_download_does_not_forward_api_credential(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        client = mf._ArkClient('UNIT-ONLY-NOT-A-KEY', 1)
        with mock.patch.object(mf.urllib.request, 'urlopen', return_value=response) as opened:
            self.assertEqual(client.download_output('https://storage.example/signed'), b'{}')
        request = opened.call_args.args[0]
        self.assertFalse(any(k.lower() == 'authorization' for k in request.headers))

    def test_output_download_refuses_plain_http_before_network(self):
        client = mf._ArkClient('UNIT-ONLY-NOT-A-KEY', 1)
        with mock.patch.object(mf.urllib.request, 'urlopen') as opened:
            with self.assertRaises(SystemExit):
                client.download_output('http://storage.example/file')
            opened.assert_not_called()

    def test_actual_file_metadata_uses_filename_then_detail_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'cfg.json'; cfg.write_text('{"environment_id":"e"}')
            client = _FakeArkClient('written to output file')
            client.session_files = [{'id': 'file-real', 'filename': 'shots.json'}]
            client.session_payload = b'{"shots": [{"shot_id": "a1"}]}'
            original_http = client._http_json
            def http(method, url, payload=None):
                if method == 'GET' and url.endswith('/files/file-real'):
                    return {'id': 'file-real', 'filename': 'shots.json', 'download_url': 'https://storage.example/file'}
                return original_http(method, url, payload)
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg), \
                    mock.patch.object(client, '_http_json', side_effect=http), \
                    mock.patch.object(mf.time, 'sleep'):
                result = mf.ark_agent_json('a', 'task', {}, 1, Path(td)/'log.json', client=client)
            self.assertEqual(result['shots'][0]['shot_id'], 'a1')

    def test_parses_object_strips_fences_and_logs(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{}', encoding='utf-8')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                client = _FakeArkClient('```json\n{"shots": [{"shot_id": "a1"}]}\n```')
                log = Path(td) / 'call.log.json'
                out = mf.ark_agent_json('agent-1', 'prompt', {}, 30, log, client=client)
            self.assertEqual(out, {'shots': [{'shot_id': 'a1'}]})
            self.assertEqual(client.created_sessions[0][0], 'agent-1')
            self.assertEqual(client.created_sessions[0][1],
                             json.loads(cfg.read_text(encoding='utf-8'))['environment_id'])
            rec = json.loads(log.read_text(encoding='utf-8'))
            self.assertIs(rec['is_error'], False)
            self.assertEqual(rec['agent_id'], 'agent-1')

    def test_rejects_non_json(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{}', encoding='utf-8')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                client = _FakeArkClient('not json')
                log = Path(td) / 'call.log.json'
                with self.assertRaises(SystemExit) as ctx:
                    mf.ark_agent_json('agent-1', 'p', {}, 30, log, client=client)
                self.assertIn('non-JSON', str(ctx.exception))
                rec = json.loads(log.read_text(encoding='utf-8'))
                self.assertIs(rec['is_error'], True)

    def test_prefers_session_output_file_over_reply_text(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{}', encoding='utf-8')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                client = _FakeArkClient('agent wrote a summary, not the payload')
                client.session_files = [{'file_name': 'shots.json', 'download_url': 'https://x/shots.json'}]
                client.session_payload = b'{"shots": [{"shot_id": "b1", "consumer_need": {"role": "pain"}}]}'
                log = Path(td) / 'call.log.json'
                out = mf.ark_agent_json('agent-1', 'p', {}, 30, log, client=client)
            self.assertEqual(out['shots'][0]['shot_id'], 'b1')
            rec = json.loads(log.read_text(encoding='utf-8'))
            self.assertEqual(rec['source'], 'session_output_file:shots.json')
            self.assertIs(rec['is_error'], False)

    def test_rejects_injected_client_without_mock_flag(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{}', encoding='utf-8')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                with self.assertRaises(SystemExit) as ctx:
                    mf.ark_agent_json('agent-1', 'p', {}, 30, Path(td) / 'c.log', client=object())
                self.assertIn('is_mock', str(ctx.exception))


class LlmJsonDispatchTest(unittest.TestCase):
    def test_defaults_to_claude_when_config_missing(self):
        calls = {}
        with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', Path(mf.ROOT) / 'no-such-config.json'), \
                mock.patch.object(mf, 'claude_json',
                                  side_effect=lambda model, prompt, schema, t, log:
                                  calls.update(model=model) or {'ok': True}):
            out = mf.llm_json('creator', 'claude-opus-5-5', 'p', {}, 30, Path('x'))
        self.assertEqual(out, {'ok': True})
        self.assertEqual(calls['model'], 'claude-opus-5-5')

    def test_ark_agent_refuses_empty_agent_id(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text(json.dumps({'provider': 'ark_agent',
                                       'agents': {'creator': {'agent_id': ''}}}), encoding='utf-8')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                with self.assertRaises(SystemExit) as ctx:
                    mf.llm_json('creator', 'claude-opus-5-5', 'p', {}, 30, Path('x'))
                self.assertIn('agent_id', str(ctx.exception))

    def test_ark_agent_dispatches_role_with_agent_id(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text(json.dumps({'provider': 'ark_agent',
                                       'agents': {'creator': {'agent_id': 'agent-9', 'max_tokens': 4096}}}),
                           encoding='utf-8')
            calls = {}
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg), \
                    mock.patch.object(mf, 'ark_agent_json',
                                      side_effect=lambda agent_id, prompt, schema, t, log, max_tokens=8192:
                                      calls.update(agent_id=agent_id, max_tokens=max_tokens) or {'ok': True}):
                out = mf.llm_json('creator', 'claude-opus-5-5', 'p', {}, 30, Path('x'))
            self.assertEqual(out, {'ok': True})
            self.assertEqual(calls, {'agent_id': 'agent-9', 'max_tokens': 4096})


class SkillSyncTest(unittest.TestCase):
    def _skill_tree(self, td):
        """Create a local industry skill directory under a temp SKILLS_DIR."""
        skills = Path(td) / 'skills'
        sdir = skills / 'industry-testhc'
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / 'SKILL.md').write_text(
            '---\nname: industry-testhc\ndescription: test skill\n---\n# Test\nrules.', encoding='utf-8')
        return skills

    def test_ensure_skill_uploads_zip_once_and_persists(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{}', encoding='utf-8')
            skills = self._skill_tree(td)
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg), \
                    mock.patch.object(mf, 'SKILLS_DIR', skills):
                client = _FakeArkClient()
                first = mf.ensure_skill('testhc', timeout_s=30, client=client)
                second = mf.ensure_skill('testhc', timeout_s=30, client=client)
            self.assertEqual(first, 'skill-1')
            self.assertEqual(second, 'skill-1')
            self.assertEqual(len(client.uploads), 1)
            self.assertEqual(client.uploads[0][2], 'industry-testhc.zip')
            written = json.loads(cfg.read_text(encoding='utf-8'))
            self.assertEqual(written['skills']['testhc']['skill_id'], 'skill-1')
            self.assertEqual(written['skills']['testhc']['version'], '1')
            self.assertEqual(len(written['skills']['testhc']['source_sha256']), 64)
            self.assertFalse((skills / 'industry-testhc.zip').exists())

    def test_ensure_skill_refuses_missing_dir(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{}', encoding='utf-8')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg), \
                    mock.patch.object(mf, 'SKILLS_DIR', Path(td) / 'nope'):
                with self.assertRaises(SystemExit) as ctx:
                    mf.ensure_skill('missing', timeout_s=30, client=_FakeArkClient())
                self.assertIn('REFUSED', str(ctx.exception))


class ClefIndustryTest(unittest.TestCase):
    """Clef gate is industry-aware: case.industry selects extra questions + requirement text."""

    def _case(self, industry):
        return {'case_id': 'c1', 'product_name': 'P', 'industry': industry,
                'functions': ['f1', 'f2']}

    def test_healthcare_questions_and_requirement(self):
        q = mf._clef_questions(self._case('healthcare'))
        self.assertIn('state_pain_shown', q)
        self.assertIn('core_utility_shown', q)
        self.assertEqual(len(q), len(mf.CLEF_QUESTIONS) + 2)
        text = mf._clef_requirements_text(self._case('healthcare'))
        self.assertIn('状态痛点', text)
        self.assertIn('核心效用', text)

    def test_home_questions_and_requirement(self):
        q = mf._clef_questions(self._case('home'))
        self.assertIn('pain_specific', q)
        self.assertIn('before_after_visual', q)
        self.assertEqual(len(q), len(mf.CLEF_QUESTIONS) + 2)
        text = mf._clef_requirements_text(self._case('home'))
        self.assertIn('危害要具体可见', text)

    def test_unknown_industry_falls_back_to_generic(self):
        q = mf._clef_questions(self._case('mystery'))
        self.assertEqual(sorted(q.keys()), sorted(mf.CLEF_QUESTIONS.keys()))
        text = mf._clef_requirements_text(self._case('mystery'))
        self.assertNotIn('状态痛点', text)
        self.assertNotIn('危害要具体可见', text)


class AgentSyncTest(unittest.TestCase):
    def test_ensure_agent_creates_with_skills_and_persists_once(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{"agents": {"creator": {}}}', encoding='utf-8')
            skills = [{'type': 'custom', 'skill_id': 'skill-1', 'version': '1'}]
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                client = _FakeArkClient()
                first = mf.ensure_agent('creator', timeout_s=30, client=client, skills=skills)
                second = mf.ensure_agent('creator', timeout_s=30, client=client, skills=skills)
            self.assertEqual(first, second)
            written = json.loads(cfg.read_text(encoding='utf-8'))
            self.assertEqual(written['agents']['creator']['agent_id'], first)
            posts = [u for m, u, _ in client.http_calls if m == 'POST' and u.endswith('/agents')]
            self.assertEqual(len(posts), 1)
            self.assertEqual(client.agents[first]['skills'], skills)

    def test_ensure_agent_updates_when_skill_missing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text(json.dumps({'agents': {'creator': {'agent_id': 'agent-9'}}}), encoding='utf-8')
            skills = [{'type': 'custom', 'skill_id': 'skill-1', 'version': '1'}]
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                client = _FakeArkClient(agents={'agent-9': {'version': 3, 'skills': []}})
                out = mf.ensure_agent('creator', timeout_s=30, client=client, skills=skills)
            self.assertEqual(out, 'agent-9')
            self.assertEqual(len(client.puts), 1)
            self.assertEqual(client.puts[0][1]['version'], 3)
            self.assertEqual(client.puts[0][1]['skills'], skills)
            self.assertEqual(client.agents['agent-9']['version'], 4)

    def test_ensure_agent_skips_update_when_skills_match(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text(json.dumps({'agents': {'reviewer': {'agent_id': 'agent-8'}}}), encoding='utf-8')
            skills = [{'type': 'custom', 'skill_id': 'skill-1', 'version': '1'}]
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                from agents.ark_profiles import SYSTEMS
                client = _FakeArkClient(agents={'agent-8': {'version': 2, 'system': SYSTEMS['reviewer'], 'skills': skills}})
                out = mf.ensure_agent('reviewer', timeout_s=30, client=client, skills=skills)
            self.assertEqual(out, 'agent-8')
            self.assertEqual(client.puts, [])
            self.assertEqual(client.agents['agent-8']['version'], 2)

    def test_ensure_environment_creates_and_persists_once(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{}', encoding='utf-8')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg):
                client = _FakeArkClient()
                first = mf.ensure_environment(timeout_s=30, client=client)
                second = mf.ensure_environment(timeout_s=30, client=client)
            self.assertEqual(first, second)
            written = json.loads(cfg.read_text(encoding='utf-8'))
            self.assertEqual(written['environment_id'], first)
            posts = [u for m, u, _ in client.http_calls if m == 'POST' and u.endswith('/environments')]
            self.assertEqual(len(posts), 1)

    def test_cmd_sync_agents_creates_skill_five_roles_and_environment(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text('{}', encoding='utf-8')
            skills = Path(td) / 'skills'
            sdir = skills / 'industry-healthcare'
            sdir.mkdir(parents=True, exist_ok=True)
            (sdir / 'SKILL.md').write_text(
                '---\nname: industry-healthcare\ndescription: healthcare skill\n---\n# Hc\nrules.',
                encoding='utf-8')
            common = skills / 'material-production'
            common.mkdir(exist_ok=True)
            (common / 'SKILL.md').write_text('---\nname: material-production\ndescription: production\n---\nRules.')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg), \
                    mock.patch.object(mf, 'SKILLS_DIR', skills), \
                    mock.patch.object(mf, '_new_ark_client', return_value=_FakeArkClient()):
                args = argparse.Namespace(timeout=30, industry='healthcare')
                out = mf.cmd_sync_agents(args)
            written = json.loads(cfg.read_text(encoding='utf-8'))
            self.assertEqual(sorted(written['agents']),
                             ['creator', 'enricher', 'motion', 'pace', 'reviewer'])
            self.assertTrue(all(written['agents'][r]['agent_id'] for r in written['agents']))
            self.assertTrue(written['environment_id'])
            self.assertEqual(written['skills']['healthcare']['skill_id'], 'skill-1')
            self.assertEqual(out['status'], 'ok')
            self.assertEqual(out['industries'], ['healthcare'])
            # All five roles bind common and industry Skills.
            self.assertEqual(out['agents']['creator']['skills'][0]['skill_id'], 'skill-1')
            self.assertEqual(out['agents']['reviewer']['skills'][0]['skill_id'], 'skill-1')
            self.assertEqual(out['agents']['motion']['skills'], out['agents']['creator']['skills'])
            self.assertEqual(out['agents']['pace']['skills'], out['agents']['creator']['skills'])
            self.assertEqual(out['agents']['enricher']['skills'], out['agents']['creator']['skills'])

    def test_cmd_sync_agents_binds_all_configured_industries(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / 'doubao_text.json'
            cfg.write_text(json.dumps({'skills': {'healthcare': {'skill_id': '', 'version': ''},
                                                  'home': {'skill_id': '', 'version': ''}}}),
                           encoding='utf-8')
            skills = Path(td) / 'skills'
            for ind in ('industry-healthcare', 'industry-home'):
                sdir = skills / ind
                sdir.mkdir(parents=True, exist_ok=True)
                (sdir / 'SKILL.md').write_text(
                    '---\nname: %s\ndescription: %s skill\n---\n# %s\nrules.' % (ind, ind, ind),
                    encoding='utf-8')
            common = skills / 'material-production'
            common.mkdir(exist_ok=True)
            (common / 'SKILL.md').write_text('---\nname: material-production\ndescription: production\n---\nRules.')
            with mock.patch.object(mf, 'DOUBAO_TEXT_CONFIG', cfg), \
                    mock.patch.object(mf, 'SKILLS_DIR', skills), \
                    mock.patch.object(mf, '_new_ark_client', return_value=_FakeArkClient()):
                args = argparse.Namespace(timeout=30, industry=None)
                out = mf.cmd_sync_agents(args)
            written = json.loads(cfg.read_text(encoding='utf-8'))
            self.assertEqual(written['skills']['healthcare']['skill_id'], 'skill-1')
            self.assertEqual(written['skills']['home']['skill_id'], 'skill-2')
            bound = out['agents']['creator']['skills']
            self.assertEqual(sorted(s['skill_id'] for s in bound), ['skill-1', 'skill-2', 'skill-3'])
            self.assertEqual(out['agents']['motion']['skills'], out['agents']['creator']['skills'])
            self.assertEqual(out['industries'], ['healthcare', 'home'])


class EnrichBatchingTest(unittest.TestCase):
    def _write_run(self, tmp_run, creation, review):
        tmp_run.mkdir(parents=True, exist_ok=True)
        (tmp_run / 'creation.json').write_text(json.dumps(creation, ensure_ascii=False), encoding='utf-8')
        (tmp_run / 'review.json').write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')

    def test_batches_three_shots_per_call(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(mf, 'RUNS', Path(td)):
            run = Path(td) / 'r1'
            shots = [_fake_shot('cc-a%d' % i) for i in range(1, 6)]
            creation = {'_meta': {'case': 'cases/laox-cable/case.json'}, 'shots': shots}
            review = {'shots': [{**s, 'image_prompt_final': 'img ' + s['shot_id'],
                                 'video_prompt_final': 'vid ' + s['shot_id']} for s in shots]}
            self._write_run(run, creation, review)
            calls, log_paths = [], []

            def fake_llm(role, fallback, prompt, schema, timeout, log):
                calls.append(role)
                log_paths.append(Path(log))
                names = [x for x in ('cc-a1', 'cc-a2', 'cc-a3', 'cc-a4', 'cc-a5') if x in prompt]
                return {'shots': [_fake_shot(n, with_fields=True) for n in names]}

            with mock.patch.object(mf, 'llm_json', side_effect=fake_llm):
                args = argparse.Namespace(run='r1', shots='cc-a1,cc-a2,cc-a3,cc-a4,cc-a5',
                                          batch_size=3, timeout=60)
                mf.cmd_enrich(args)
            self.assertEqual(calls, ['enricher', 'enricher'])
            self.assertEqual([p.name for p in log_paths],
                             ['enrich-batch-01.call.json', 'enrich-batch-02.call.json'])
            merged = json.loads((run / 'creation.json').read_text(encoding='utf-8'))
            self.assertTrue(all(k in merged['shots'][0] for k in _ENRICH_KEYS))
            self.assertEqual(merged['_meta']['enriched']['batches'], 2)
            self.assertEqual(merged['_meta']['enriched']['batch_size'], 3)

    def test_single_batch_when_under_limit(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(mf, 'RUNS', Path(td)):
            run = Path(td) / 'r2'
            shots = [_fake_shot('cc-b%d' % i) for i in range(1, 3)]
            creation = {'_meta': {'case': 'cases/laox-cable/case.json'}, 'shots': shots}
            review = {'shots': [{**s, 'image_prompt_final': 'img', 'video_prompt_final': 'vid'}
                                for s in shots]}
            self._write_run(run, creation, review)
            calls, log_paths = [], []

            def fake_llm(role, fallback, prompt, schema, timeout, log):
                calls.append(role)
                log_paths.append(Path(log))
                names = [x for x in ('cc-b1', 'cc-b2') if x in prompt]
                return {'shots': [_fake_shot(n, with_fields=True) for n in names]}

            with mock.patch.object(mf, 'llm_json', side_effect=fake_llm):
                args = argparse.Namespace(run='r2', shots='cc-b1,cc-b2', batch_size=3, timeout=60)
                mf.cmd_enrich(args)
            self.assertEqual(calls, ['enricher'])
            self.assertEqual([p.name for p in log_paths], ['enrich-batch-01.call.json'])


class ReviewSkillGateTest(unittest.TestCase):
    def sample(self, industry='home'):
        case = {'industry': industry}
        plan = {'shots': [{'shot_id': 'a1', 'has_host': True, 'product_ref': 'product'}]}
        ids = ['state_consistency', 'reference_and_exposure', 'scope_and_closure', 'review_edit_scope']
        if industry == 'home':
            ids += ['home_use_feasibility', 'home_result_conditions']
        review = {'shots': [{'shot_id': 'a1', 'verdict': 'ok', 'issues': [],
                             'image_prompt_final': '图1为唯一人物身份参考，产品参考图2。'}],
                  'batch': {'batch_verdict': 'pass', 'batch_issues': [],
                            'consumer_checks': {k: {'met': True, 'evidence': 'a1 actual plan'}
                                                for k in mf.CONSUMER_CHECKS},
                            'requirement_checks': [{'requirement': k, 'met': True,
                                                    'evidence': 'a1 action_start: original state'} for k in ids]}}
        return case, plan, review

    def test_valid_bound_references_and_skill_records_pass(self):
        mf._validate_review_output(*self.sample())

    def test_home_checks_cannot_be_omitted_but_do_not_leak_to_generic(self):
        case, plan, review = self.sample('generic')
        mf._validate_review_output(case, plan, review)
        with self.assertRaisesRegex(SystemExit, 'home_use_feasibility'):
            mf._validate_review_output({'industry': 'home'}, plan, review)

    def test_false_check_cannot_accompany_pass(self):
        case, plan, review = self.sample()
        review['batch']['requirement_checks'][0]['met'] = False
        with self.assertRaisesRegex(SystemExit, 'contradicts'):
            mf._validate_review_output(case, plan, review)

    def test_missing_edit_scope_audit_is_rejected(self):
        case, plan, review = self.sample()
        review['batch']['requirement_checks'] = [
            c for c in review['batch']['requirement_checks'] if c['requirement'] != 'review_edit_scope']
        with self.assertRaisesRegex(SystemExit, 'review_edit_scope'):
            mf._validate_review_output(case, plan, review)

    def test_cloud_removal_of_reference_numbers_is_rejected(self):
        case, plan, review = self.sample()
        review['shots'][0]['image_prompt_final'] = '人物参考host_front，产品参考product_main。'
        with self.assertRaisesRegex(SystemExit, 'reference'):
            mf._validate_review_output(case, plan, review)
        self.assertEqual(review['batch']['batch_verdict'], 'pass')  # Never forge cloud verdict.

    def test_product_only_requires_first_reference(self):
        case, plan, review = self.sample()
        plan['shots'][0]['has_host'] = False
        review['shots'][0]['image_prompt_final'] = '产品参考图2，银色底座。'
        with self.assertRaisesRegex(SystemExit, 'reference'):
            mf._validate_review_output(case, plan, review)
        review['shots'][0]['image_prompt_final'] = '产品参考图1，银色底座。'
        mf._validate_review_output(case, plan, review)

    def test_rejection_is_preserved_without_requiring_generation_prompt(self):
        case, plan, review = self.sample()
        review['batch'].update(batch_verdict='needs_rework', batch_issues=['a1 missing state'])
        review['shots'][0].update(verdict='escalate_to_creator', image_prompt_final='Unresolved: creator must fix state')
        mf._validate_review_output(case, plan, review)
        self.assertEqual(review['batch']['batch_verdict'], 'needs_rework')

    def test_existing_plan_gate_rechecks_skill_contract(self):
        case, plan, review = self.sample()
        review['shots'][0]['image_prompt_final'] = 'host_front / product_main'
        plan['_meta'] = {'case': 'cases/unit.json'}
        with mock.patch.object(mf, '_read', side_effect=[plan, case, review]), \
                mock.patch.object(mf, '_project_file', return_value=ROOT/'cases/unit.json'), \
                mock.patch.object(mf, '_validate_consumer_plan'):
            with self.assertRaisesRegex(SystemExit, 'reference'):
                mf._require_plan_review(ROOT/'runtime/unit-only')


# Persisted per-shot admission regressions; production artifacts are read-only inputs.
import tempfile, argparse
BASE=ROOT/'runtime/material-flow/laox-stand-home-image-20261015'
class ShotAdmission(unittest.TestCase):
 def setUp(self):
  ledger_patch=mock.patch.object(mf,'_ledger_attempts',return_value=32)
  ledger_patch.start();self.addCleanup(ledger_patch.stop)
  self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'runtime');self.run=Path(self.tmp.name)
  self.plan=mf._read(BASE/'creation.json');self.review=mf._read(BASE/'review.json');self.review.pop('_meta',None)
  self.review['scope_version']='shot-review/v1'
  for x in self.review['shots']:
   x.update(consumer_checks={k:{'met':True,'evidence':'UNIT ONLY'} for k in mf.CONSUMER_CHECKS},quality_checks={k:{'met':True,'evidence':'UNIT ONLY'} for k in mf._shot_quality_keys(mf._read(mf._project_file(self.plan['_meta']['case'])))},dependencies=[],affected_shot_ids=[],impact_evidence='UNIT ONLY no dependencies or impact')
  self.source=self.run/'input.json';self.raw=self.run/'raw.json';self.call=self.run/'call.json'
  mf._write(self.run/'creation.json',self.plan);mf._write(self.source,self.plan)
  # Binding target for human-score-table (jobs hard gate); not used by shot-review evidence path.
  plan_review=dict(self.review)
  plan_review['_meta']={'consumer_policy':mf.CONSUMER_POLICY,'creation_sha256':mf._sha_file(self.run/'creation.json'),'case_sha256':mf._sha_file(mf._project_file(self.plan['_meta']['case']))}
  mf._write(self.run/'review.json',plan_review)
  self.manifest=mf._read(BASE/'shot-review-evidence.json');src=self.manifest['sources'][0];src.update(creation=str(self.source),review=str(self.raw),receipt=str(self.call));src.pop('verified_legacy_dependencies',None)
  self.save()
  self._write_human_score(selected=[s['shot_id'] for s in self.plan['shots']])
 def _write_human_score(self, selected):
  lhs=mf.lab_human_score
  arch=self.run/'hs-archive';arch.mkdir(exist_ok=True)
  with mock.patch.object(lhs,'ARCHIVE_DIR',arch):
   creation=mf._read(self.run/'creation.json');case=mf._read(mf._project_file(creation['_meta']['case']))
   pending=lhs.build_pending_table(self.run,mf.ROOT,self.run.name,creation,case,mf.CONSUMER_POLICY,mf._sha_file)
   for i,row in enumerate(pending['shots']):
    if row['shot_id'] in selected:
     row.update(selected=True,user_rank=i+1,score=4,note='UNIT')
   submitted=lhs.validate_submission(pending,self.run,mf.ROOT,creation,case,mf.CONSUMER_POLICY,mf._sha_file,'unit-op')
   lhs.persist_submission(self.run,mf.ROOT,submitted)
 def tearDown(self):self.tmp.cleanup()
 def save(self):
  mf._write(self.raw,self.review);mf._write(self.call,{'status':'completed','input_sha256':mf._sha_file(self.source),'output_sha256':mf._sha_file(self.raw),'case_sha256':mf._sha_file(mf._project_file(self.plan['_meta']['case']))});mf._write(self.run/'shot-review-evidence.json',self.manifest)
 def jobs(self):
  with mock.patch.object(mf,'_run_dir',return_value=self.run):mf.cmd_jobs(argparse.Namespace(run='unit',shots=None))
  return mf._read(self.run/'seedream-jobs.json')['items']
 def fail02(self):
  self.review['batch']['batch_verdict']='needs_rework';self.review['shots'][1]['verdict']='escalate_to_creator';self.review['shots'][1]['issues']=['UNIT defect'];self.save()
 def test_one_bad_five_good(self):
  self.fail02();self.assertEqual(len(self.jobs()),5)
 def test_affected_only(self):
  self.fail02();self.review['shots'][1]['affected_shot_ids']=['lxs-hp-04'];self.save();self.assertEqual({x['item_id'] for x in self.jobs()},{'lxs-hp-01','lxs-hp-03','lxs-hp-05','lxs-hp-06'})
 def test_unrelated_edit_preserves_and_dependency_edit_blocks(self):
  self.review['shots'][0]['dependencies']=['lxs-hp-05'];self.save();self.plan['shots'][2]['camera']+=' UNIT changed';mf._write(self.run/'creation.json',self.plan);mf._require_shot_review(self.run,'lxs-hp-01')
  self.plan['shots'][4]['camera']+=' UNIT changed';mf._write(self.run/'creation.json',self.plan)
  with self.assertRaises(SystemExit):mf._require_shot_review(self.run,'lxs-hp-01')
 def test_missing_evidence_and_reference_mismatch_rejected(self):
  self.review['shots'][0]['quality_checks']={};self.save()
  with self.assertRaises(SystemExit):mf._require_shot_review(self.run,'lxs-hp-01')
  self.review['shots'][0]['quality_checks']={k:{'met':True,'evidence':'UNIT'} for k in mf._shot_quality_keys(mf._read(mf._project_file(self.plan['_meta']['case'])))};self.manifest['sources'][0]['reference_hashes']={};self.save()
  with self.assertRaises(SystemExit):mf._require_shot_review(self.run,'lxs-hp-01')
 def test_partial_batch_still_checks_prompt_reference(self):
  self.fail02();self.review['shots'][0]['image_prompt_final']='No numbered reference in this invalid fixture prompt';self.save()
  with self.assertRaises(SystemExit):mf._require_shot_review(self.run,'lxs-hp-01')
 def test_budget_not_bypassed(self):
  rep,manifest=mf.policy_report(mf._read(mf._project_file(self.plan['_meta']['case'])));rep['generated_attempts']=rep['max_generated']
  with mock.patch.object(mf,'policy_report',return_value=(rep,manifest)):
   with self.assertRaises(SystemExit):self.jobs()
 def test_cross_source_failed_dependency_blocks_older_pass(self):
  self.review['shots'][0]['dependencies']=['lxs-hp-02'];self.save()
  old_source=dict(self.manifest['sources'][0])
  newer=self.run/'new-raw.json';call=self.run/'new-call.json'
  bad=dict(self.review['shots'][1],verdict='escalate_to_creator',issues=['UNIT failure'])
  mf._write(newer,{'scope_version':'shot-review/v1','shots':[bad]})
  receipt=mf._read(self.call);receipt['output_sha256']=mf._sha_file(newer);mf._write(call,receipt)
  self.manifest['sources'].append(dict(old_source,review=str(newer),receipt=str(call)));mf._write(self.run/'shot-review-evidence.json',self.manifest)
  with self.assertRaises(SystemExit):mf._require_shot_review(self.run,'lxs-hp-01')
 def test_explicit_mixed_selection_keeps_good_and_reports_bad(self):
  self.fail02()
  with mock.patch.object(mf,'_run_dir',return_value=self.run):mf.cmd_jobs(argparse.Namespace(run='unit',shots='lxs-hp-01,lxs-hp-02'))
  out=mf._read(self.run/'seedream-jobs.json');self.assertEqual(len(out['items']),1);self.assertEqual(out['blocked'][0]['shot_id'],'lxs-hp-02')
 def test_hold_only_blocks_bound_shot(self):
  self.manifest['holds']={'lxs-hp-02':{'shot_sha256':mf._shot_digest(self.plan['shots'][1]),'reason':'UNIT unresolved'}};self.save()
  with self.assertRaisesRegex(SystemExit,'unresolved review contradiction'):mf._require_shot_review(self.run,'lxs-hp-02')
  self.assertEqual(mf._require_shot_review(self.run,'lxs-hp-01')['verdict'],'ok')
 def test_scoped_preparation_failure_has_zero_network(self):
  import runpy,socket,sys
  runner=ROOT/'runtime/evidence/home-image-closure-20261015/scoped_review.py'
  with mock.patch.object(sys,'argv',[str(runner),'lxs-hp-01','unit_missing_preparation']), mock.patch.object(socket,'create_connection') as connect:
   with self.assertRaisesRegex(SystemExit,'Preparation missing'):runpy.run_path(str(runner),run_name='__main__')
   connect.assert_not_called()
 def test_real_legacy_pair(self):
  self.assertIn(mf._require_shot_review(BASE,'lxs-hp-01')['verdict'],('ok','fixed'))
  self.assertIn(mf._require_shot_review(BASE,'lxs-hp-05')['verdict'],('ok','fixed'))
  with self.assertRaises(SystemExit):mf._require_shot_review(BASE,'lxs-hp-03')

if __name__ == '__main__':
    unittest.main(verbosity=2)
