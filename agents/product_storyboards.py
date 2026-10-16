"""product_storyboards — bounded deterministic dry-run planning/review/repair for
product material shots, adapting the imported koc-video rules (see
config/koc_material_policy.json; every rule is source-bound).

This is NOT an LLM and NOT a new agent framework: the native storyboardTeam stays the
only team; planning.engine is 'deterministic-local-v1' with llm_invoked=false. Real
storyboardTeam LLM invocation remains not_run (paid, budget 0).
"""
import copy

from agents import koc_rules

# Keyword table is the DELIVERED v1 consumer vocabulary snapshot (scheduler.script_plan
# USE_KEYWORDS as delivered with the koc-v1 bridge; the consumer-side bridge keeps an
# identical copy in its DELIVERED_USE_KEYWORDS and tests lock the two together).
# The live consumer script_plan.py has since been rewritten to r3 action semantics
# (open_packaging/split_fruit) by the concurrent external task — see EK-16; this
# snapshot stays on the delivered contract until the coordinator reconciles the drift.
USE_KEYWORDS = {
    'open_box': ['打开', '开盒', '拆开', '撕开', '开封', '揭开', '开箱', '掰开', '切开', '剖开', '对半切'],
    'packaging': ['包装', '盒子', '袋子', '封口', '标签', '外观', '正面'],
    'detail': ['特写', '细节', '颗粒', '切面', '里面', '内部', '果肉', '纹理', '断面', '仔细看'],
    'takeout': ['取出', '拿出来', '舀', '倒出', '夹起'],
    'wrapup': ['盖上', '收起', '合上', '放回', '收尾'],
}
USE_PRIORITY = ['open_box', 'takeout', 'wrapup', 'detail', 'packaging', 'other']
QUALITY_JUDGMENT_MARKERS = ['很新鲜', '新鲜', '好吃', '很好吃', '优质', '实惠', '划算', '香甜', '清甜', '软糯', '多汁', 'Q弹']
OBSERVATION_STEP_MARKERS = ['看清', '展示', '观察', '特写', '看到']
GRID_SEC = 1920 / 48000.0

# Bounded object-kind vocabulary for the ACTION_OBJECT_MISMATCH guard (EK-15). This is a
# deterministic keyword guard for the demonstrated adversarial class (fruit-specific
# actions leaking into non-fruit products), NOT video/semantic understanding.
FRUIT_OBJECT_TERMS = ('果子', '果实', '果肉', '果皮', '籽粒', '果籽', '水果')

# Ref-duty fit: a purpose may only use refs whose declared role can carry it. When no
# declared ref fits, the shot is marked ref_fit='none_fitting' and review RETURNs it
# (NO_FITTING_REF_FOR_PURPOSE) — never a blind fallback to an unrelated role ref.
PURPOSE_REF_ROLES = {
    'packaging': {'product_appearance_demo', 'demo_footage_reference'},
    'open_box': {'product_appearance_demo', 'demo_footage_reference'},
    'takeout': {'product_appearance_demo', 'demo_footage_reference'},
    'detail': {'product_appearance_demo', 'demo_footage_reference', 'structure_reference'},
    'wrapup': {'product_appearance_demo', 'demo_footage_reference'},
    'other': {'scale_reference', 'demo_footage_reference', 'product_appearance_demo'},
}

# Generic framing fallbacks are PRODUCT-NEUTRAL camera language only. Concrete framing
# (a fruit cross-section, seeds, a specific object's texture) comes from per-segment
# framing hints in the case input; nothing product-specific may live here (EK-15).
FRAMING_TEMPLATES = {
    'packaging': [('产品正面中景，产品居中', '固定机位', '无'), ('正面/标签特写', '缓推', '轻微推近')],
    'open_box': [('双手近景', '手持 POV', '跟随动作'), ('桌面俯拍近景', '固定机位', '无'), ('侧面中景', '固定机位', '轻微拉远')],
    'takeout': [('手部特写', '手持 POV', '跟随取出动作'), ('桌面中景', '固定机位', '无')],
    'detail': [('局部大特写', '缓推', '轻微推近'), ('表面细节特写', '固定机位', '无'), ('纹理特写', '横移', '缓慢横移')],
    'wrapup': [('桌面中景', '固定机位', '无')],
    'other': [('产品与手部同框中景', '固定机位', '无')],
}
# Actions are INPUT-DERIVED: each narration segment declares its own action chain
# (start/process/end). The planner does NOT invent product-specific actions for
# unspecified products — a segment without an action hint gets a placeholder and
# review RETURNs it with ACTION_HINT_MISSING (EK-15). Demo-case-specific chains (e.g.
# the dragonfruit TEST case) live in that case's case.json, never here.
PLACEHOLDER_ACTION = {'start': '（未从输入派生）', 'process': [], 'end': '（未从输入派生）'}


def _pick_ref(refs, purpose, idx):
    """Deterministic role-fit ref selection: only refs whose declared duty can carry
    this purpose. Returns (ref, fitting): fitting=False when no declared ref matches —
    the caller records ref_fit='none_fitting' and review RETURNs the shot; the rotation
    never falls back to an unrelated role ref."""
    fitting = [r for r in refs if r['role'] in PURPOSE_REF_ROLES.get(purpose, set())]
    if not fitting:
        return refs[idx % len(refs)], False
    return fitting[idx % len(fitting)], True


def _segment_action(seg):
    """Action chain from segment input: a dict with non-empty start/end strings and a
    list of process strings. Anything else counts as no hint (placeholder + RETURN)."""
    hint = seg.get('action')
    if isinstance(hint, dict) and isinstance(hint.get('start'), str) and hint['start'].strip() \
            and isinstance(hint.get('end'), str) and hint['end'].strip() \
            and isinstance(hint.get('process'), list) \
            and all(isinstance(s, str) and s.strip() for s in hint['process']):
        return {'start': hint['start'], 'process': list(hint['process']), 'end': hint['end']}
    return None


def _object_terms(text):
    return {t for t in FRUIT_OBJECT_TERMS if t in (text or '')}


def _action_object_scope(plan, shot):
    """Text that legitimately establishes the product's object kind: the narration
    itself and the case product name, plus the declared subject term ONLY when it is
    corroborated — the case's own narration (or the product name) actually uses that
    term. A stale/contradictory subject_term (e.g. 果子 left on a coffee case) cannot
    unlock object kinds the narration and name never establish (coordinator
    stale-subject negative, EK-15)."""
    product = plan.get('product') or {}
    name = product.get('name') or ''
    subject = product.get('subject_term') or ''
    parts = [shot.get('narration') or '', name]
    if subject:
        segments_text = ' '.join(s.get('text', '') for s in plan.get('narration_segments', []))
        if subject in segments_text or subject in name:
            parts.append(subject)
    return ' '.join(p for p in parts if p)


def classify_purpose(text):
    matched = [u for u in USE_PRIORITY if u in USE_KEYWORDS and any(k in text for k in USE_KEYWORDS[u])]
    return matched[0] if matched else 'other'


def _grid_sec(sec):
    """Round seconds down-outward onto the 1920-tick grid (25fps)."""
    ticks = round(sec * 48000 / 1920) * 1920
    return ticks / 48000.0


def _content_core(shot):
    core = copy.deepcopy(shot)
    core.pop('review', None)
    return core


def shot_content_hash(shot):
    return koc_rules.canonical_hash(_content_core(shot))


def build_plan(case):
    """Deterministically turn a case into structured shots: one shot per narration
    segment, plus the case's declared counterexample shots. No LLM, no randomness.

    Generated status, audio policy, ref duties AND actions/framing all come from the
    CASE INPUT, never from demo-case assumptions (EK-15): each segment may declare an
    explicit action chain (and optional framing/camera/movement); a segment without an
    action hint gets a placeholder the reviewer RETURNs (ACTION_HINT_MISSING) instead of
    an invented template action. A purpose with no duty-fitting ref marks the shot
    ref_fit='none_fitting' (review RETURN NO_FITTING_REF_FOR_PURPOSE) instead of
    silently rotating onto an unrelated ref. A case that did not verify generation
    provenance gets to_be_generated/not_generated with an empty provider; B-roll audio
    defaults to no original."""
    policy = koc_rules.load_policy()
    prov_ok = koc_rules.verify_source_import()
    if not prov_ok['ok']:
        raise ValueError('KOC_SOURCE_IMPORT_VERIFICATION_FAILED')
    ref_durations = {r['ref_id']: (case.get('fixture_technical', {}).get(r['ref_id'], {}) or {}).get('duration_sec')
                     for r in case['refs']}
    limit = koc_rules.conservative_clip_limit_sec(ref_durations.values())
    capability_provenance = 'fixture_probe_evidence' if all(
        (case.get('fixture_technical', {}).get(r['ref_id'], {}) or {}).get('duration_sec') for r in case['refs']) \
        else 'unverified_assumed_conservative'
    generated_defaults = dict(case.get('generated_defaults') or {
        'status': 'to_be_generated', 'provider': None, 'verified_real_product_test': False})
    audio_defaults = dict(case.get('audio_defaults') or {
        'has_original': False, 'must_preserve': False, 'policy': 'discard_allowed'})
    per_purpose = {}
    shots = []
    for seg in case['narration_segments']:
        purpose = classify_purpose(seg['text'])
        idx = per_purpose.get(purpose, 0)
        per_purpose[purpose] = idx + 1
        templates = FRAMING_TEMPLATES[purpose]
        framing, camera, movement = templates[idx % len(templates)]
        if isinstance(seg.get('framing'), str) and seg['framing'].strip():
            framing = seg['framing']
        if isinstance(seg.get('camera'), str) and seg['camera'].strip():
            camera = seg['camera']
        if isinstance(seg.get('movement'), str) and seg['movement'].strip():
            movement = seg['movement']
        action = _segment_action(seg)
        if action is None:
            action = {'start': PLACEHOLDER_ACTION['start'], 'process': list(PLACEHOLDER_ACTION['process']),
                      'end': PLACEHOLDER_ACTION['end']}
            action_source = 'missing_hint'
        else:
            action_source = 'case_input'
        ref, fitting = _pick_ref(case['refs'], purpose, idx)
        duration = _grid_sec(min(seg.get('duration_sec', 2.4), limit or seg.get('duration_sec', 2.4)))
        shots.append({
            'shot_id': seg['segment_id'].replace('seg', 'shot'),
            'product_id': case['product']['product_id'],
            'version': 1,
            'purpose': purpose,
            'narration': seg['text'],
            'refs': [{'ref_id': ref['ref_id'], 'role': ref['role']}],
            'action': action,
            'action_source': action_source,
            'ref_fit': 'fitting' if fitting else 'none_fitting',
            'framing': framing, 'camera': camera, 'movement': movement,
            'duration_sec': duration,
            'model_capability': {'provenance': capability_provenance, 'verified': False,
                                 'max_clip_sec': limit},
            'must': ['产品在画面中完整可见', '画面连续不截断动作'],
            'must_not': ['固定角标与水印', '虚构产品功效文字'],
            'risk_flags': [],
            'needs_3d': False, 'needs_3d_reason': None, 'complex_grip': False,
            'overlay_request': None,
            'reuse': 'reusable_segment',
            'audio': dict(audio_defaults),
            'generated': {'status': generated_defaults['status'],
                          'verified_real_product_test': generated_defaults['verified_real_product_test'],
                          'provider': generated_defaults.get('provider')},
            'counterexample_of': None,
        })
    for ce in case.get('counterexamples', []):
        base = copy.deepcopy(next(s for s in shots if s['shot_id'] == ce['clone_from']))
        base.update(ce['overrides'])
        base['shot_id'] = ce['shot_id']
        base['counterexample_of'] = ce['exercising']
        shots.append(base)
    return {
        'schema_version': 'product-storyboards/v1',
        'case_id': case['case_id'],
        'created_utc': case['created_utc'],
        'policy_version': policy['policy_version'],
        'planning': {'engine': 'deterministic-local-v1', 'llm_invoked': False,
                     'distinct_use_purposes': sorted({s['purpose'] for s in shots}),
                     'action_provenance': 'case narration_segments[].action 输入驱动；无提示段落 RETURN ACTION_HINT_MISSING，不套用例模板（EK-15）',
                     'native_storyboard_team_context':
                         'runtime/toonflow-data/agents/storyboardTeam (members + knowledge/product-materials.md); real LLM invocation not_run'},
        'product': copy.deepcopy(case['product']),
        'narration_segments': copy.deepcopy(case['narration_segments']),
        'refs': copy.deepcopy(case['refs']),
        'shots': shots,
        'source_bindings': [
            {'rule': 'action start-process-end + cut reasons', 'file': 'koc-video/references/script-and-shots.md', 'section': '2. 从表达和动作设计镜头'},
            {'rule': 'actions derive from case/segment input, never invented for unspecified products', 'file': 'koc-video/references/script-and-shots.md', 'section': '2. 从表达和动作设计镜头'},
            {'rule': 'refs duties and existence', 'file': 'koc-video/references/production.md', 'section': '2. 生成前 refs 检查'},
            {'rule': 'fact boundary (no efficacy/price inference)', 'file': 'koc-video/references/source-routing.md', 'section': '1. 建立事实边界'},
            {'rule': 'conservative clip duration limits', 'file': 'koc-video/references/production.md', 'section': '1. 编译边界'},
            {'rule': 'speech/audio ownership declared per shot', 'file': 'koc-video/references/script-and-shots.md', 'section': '0. 确定…speech plan'},
        ],
    }


def review_shot(shot, plan):
    """Deterministic rule evaluation per policy.review_rules. Returns problems with
    concrete evidence and a minimal fix hint. Empty list == PASS."""
    problems = []
    policy = koc_rules.load_policy()
    ref_index = {r['ref_id']: r for r in plan['refs']}
    max_steps = policy['planning']['max_action_process_steps_per_shot']

    for ref in shot['refs']:
        declared = ref_index.get(ref['ref_id'])
        if declared is None:
            problems.append({'code': 'MISSING_REFS',
                             'evidence': "shot 引用 ref_id=%s，case 未声明" % ref['ref_id'],
                             'minimal_fix': '改绑 case 中已声明的 ref，或补充该 ref 的证据来源'})
        elif ref['role'] != declared['role']:
            problems.append({'code': 'CONTINUITY_REF_MISMATCH',
                             'evidence': "shot 用 ref=%s 扮演 role=%s，case 声明该 ref 的 role=%s" % (ref['ref_id'], ref['role'], declared['role']),
                             'minimal_fix': '按 case 声明的职责使用该 ref，或换用具备该职责的 ref'})
    if shot.get('ref_fit') == 'none_fitting':
        fitting_roles = sorted(PURPOSE_REF_ROLES.get(shot['purpose'], set()))
        problems.append({'code': 'NO_FITTING_REF_FOR_PURPOSE',
                         'evidence': "purpose=%s 无职责匹配的 ref（case 声明的 refs 均不能承载该用途）；不回退到无关职责 ref" % shot['purpose'],
                         'minimal_fix': '为 case 补充 role ∈ %s 的 ref，或调整该段用途' % '、'.join(fitting_roles)})
    if shot.get('action_source') == 'missing_hint':
        problems.append({'code': 'ACTION_HINT_MISSING',
                         'evidence': "该段旁白未提供动作提示，规划器不为未指明的产品发明动作（narration='%s'）" % shot['narration'],
                         'minimal_fix': '在 case narration_segments 该段补 action: {start, process, end}（可选 framing/camera/movement）输入'})
    action_text = ' '.join([shot['action']['start']] + list(shot['action']['process']) + [shot['action']['end']])
    action_fruit = _object_terms(action_text)
    if action_fruit and not _object_terms(_action_object_scope(plan, shot)):
        problems.append({'code': 'ACTION_OBJECT_MISMATCH',
                         'evidence': "动作含对象词 %s，但旁白与产品声明（name/subject_term）均未出现该类对象——用例具象动作套进了别的产品" % '、'.join(sorted(action_fruit)),
                         'minimal_fix': '按本 case 输入重写该段 action 提示，使动作对象与旁白/产品一致'})
    if shot['duration_sec'] is None:
        problems.append({'code': 'DURATION_UNKNOWN', 'evidence': 'duration_sec 为空，无已核实时长',
                         'minimal_fix': '补 probe/清单证据中的时长，或标 blocked'})
    else:
        cap = shot['model_capability']['max_clip_sec']
        # note: this cap bounds planned windows against available source-footage
        # evidence; it is NOT a verified model generation-duration capability
        if cap is None:
            problems.append({'code': 'DURATION_OUTSIDE_VERIFIED_LIMITS',
                             'evidence': '无任何已核实的源素材时长证据（模型时长能力另属未验证，不据此放行）',
                             'minimal_fix': '在拿到已核实源素材时长前把时长标 blocked'})
        elif shot['duration_sec'] > cap + 1e-9:
            problems.append({'code': 'DURATION_OUTSIDE_VERIFIED_LIMITS',
                             'evidence': 'duration=%.2fs 超过已核实源素材时长上限 %.2fs（模型生成时长能力未验证，不计为兼容）' % (shot['duration_sec'], cap),
                             'minimal_fix': '缩短到源素材可承载范围内并网格对齐'})
    claims = koc_rules.find_unverified_claims(shot['narration'])
    if claims:
        problems.append({'code': 'UNRELIABLE_FACTS',
                         'evidence': 'narration 含无来源断言标记: %s' % '、'.join(claims),
                         'minimal_fix': '删除断言句或补 case 事实来源（fixture 证据/明确合成测试输入不含功效）'})
    judgments = [m for m in QUALITY_JUDGMENT_MARKERS if m in shot['narration']]
    if judgments:
        has_observation = any(any(m in step for m in OBSERVATION_STEP_MARKERS)
                              for step in shot['action']['process'])
        if not has_observation:
            problems.append({'code': 'NARRATION_WITHOUT_VISIBLE_INFO',
                             'evidence': 'narration 质量判断词 %s 无对应可见信息动作步骤' % '、'.join(judgments[:3]),
                             'minimal_fix': '增加展示/看清步骤，或删去判断词只保留可观察描述'})
    if shot.get('risk_flags'):
        problems.append({'code': 'OBSCURED_OR_CROPPED_PRODUCT',
                         'evidence': 'risk_flags=%s，产品可识别性不成立' % shot['risk_flags'],
                         'minimal_fix': '重新取景使产品完整可见并清除风险标记'})
    if len(shot['action']['process']) > max_steps:
        problems.append({'code': 'MULTI_ACTION_OVERLOAD',
                         'evidence': '单镜动作步骤 %d 个，超过上限 %d' % (len(shot['action']['process']), max_steps),
                         'minimal_fix': '拆成可独立生成的 coverage 镜头'})
    if shot.get('complex_grip') and shot.get('needs_3d'):
        problems.append({'code': 'UNSAFE_COMPLEX_GRIP_NEEDS_3D',
                         'evidence': '复杂抓握动作依赖 3D；简化 3D 人体模型不能认证抓握/手指，且全动作 3D 检查本轮不可用',
                         'minimal_fix': '无本轮可执行修复：保持 blocked，待全动作 3D 检查可用后重审'})
    if shot.get('overlay_request') in ('fixed_corner_label', 'watermark'):
        problems.append({'code': 'PROHIBITED_OVERLAYS',
                         'evidence': 'overlay_request=%s 为禁项（固定角标/水印）' % shot['overlay_request'],
                         'minimal_fix': '移除角标/水印请求'})
    return problems


def review_plan(plan):
    shots = []
    for shot in plan['shots']:
        problems = review_shot(shot, plan)
        shots.append({'shot_id': shot['shot_id'], 'version': shot['version'],
                      'content_sha256': shot_content_hash(shot),
                      'verdict': 'PASS' if not problems else 'RETURN', 'problems': problems})
    return {'engine': 'deterministic-rules-v1', 'policy_version': plan['policy_version'],
            'shots': shots,
            'pass_count': sum(1 for s in shots if s['verdict'] == 'PASS'),
            'return_count': sum(1 for s in shots if s['verdict'] == 'RETURN')}


def repair_returned(plan, review, round_no):
    """Bounded deterministic repair: only RETURN shots change, each gets version+1 and
    repair_attempts+1 (budget from policy). PASS shots are locked byte-for-byte.
    Returns (new_plan, repair_log)."""
    policy = koc_rules.load_policy()
    budget = policy['planning']['repair_budget_per_shot']
    attempts = {s['shot_id']: s.get('repair_attempts', 0) for s in plan['shots']}
    by_id = {r['shot_id']: r for r in review['shots']}
    new_plan = copy.deepcopy(plan)
    new_shots = {s['shot_id']: s for s in new_plan['shots']}
    # fallback narration is parameterized by the case's declared subject term (EK-15);
    # no demo-case object wording is hardwired here
    subject = (new_plan.get('product') or {}).get('subject_term') or '产品'
    log = []
    followups = []
    for shot in new_plan['shots']:
        verdict = by_id[shot['shot_id']]
        if verdict['verdict'] != 'RETURN':
            assert shot_content_hash(shot) == verdict['content_sha256'], 'PASS version mutated before repair'
            continue
        if attempts[shot['shot_id']] >= budget:
            log.append({'shot_id': shot['shot_id'], 'action': 'budget_exhausted_keep_blocked',
                        'problems': [p['code'] for p in verdict['problems']]})
            continue
        changed = False
        codes = {p['code'] for p in verdict['problems']}
        if 'DURATION_OUTSIDE_VERIFIED_LIMITS' in codes and shot['duration_sec'] is not None and shot['model_capability']['max_clip_sec']:
            shot['duration_sec'] = _grid_sec(shot['model_capability']['max_clip_sec'])
            changed = True
        if 'DURATION_UNKNOWN' in codes:
            cap = shot['model_capability']['max_clip_sec']
            if cap:
                shot['duration_sec'] = _grid_sec(cap)
                shot['model_capability']['provenance'] = 'fixture_probe_evidence'
                changed = True
        if 'UNRELIABLE_FACTS' in codes:
            kept = [sent for sent in shot['narration'].split('。') if sent and not koc_rules.find_unverified_claims(sent)]
            shot['narration'] = (kept[0] + '。') if kept else '画面演示%s外观与使用过程。' % subject
            changed = True
        if 'NARRATION_WITHOUT_VISIBLE_INFO' in codes:
            if len(shot['action']['process']) < policy['planning']['max_action_process_steps_per_shot']:
                shot['action']['process'].append('展示画面细节供观众看清')
                changed = True
            else:
                shot['narration'] = '画面展示%s外观与使用过程。' % subject
                changed = True
        if 'OBSCURED_OR_CROPPED_PRODUCT' in codes and shot.get('risk_flags'):
            # concrete reframe (new framing/camera/action), not just clearing the flag
            shot['risk_flags'] = []
            shot['framing'] = '重新取景：产品完整居中入画，边界内收一帧余量'
            shot['camera'] = '机位拉远至覆盖产品完整轮廓'
            shot['movement'] = '无'
            shot['action'] = {'start': '产品完整入画居中', 'process': ['稳定展示产品全貌'],
                              'end': '产品完整可见保持到画面结束'}
            shot['must'].append('重新取景后产品完整可见（机位拉远+边界余量）')
            changed = True
        if 'PROHIBITED_OVERLAYS' in codes and shot.get('overlay_request') in ('fixed_corner_label', 'watermark'):
            shot['overlay_request'] = None
            changed = True
        if 'MULTI_ACTION_OVERLOAD' in codes and len(shot['action']['process']) > policy['planning']['max_action_process_steps_per_shot']:
            keep = shot['action']['process'][:policy['planning']['max_action_process_steps_per_shot']]
            moved = shot['action']['process'][policy['planning']['max_action_process_steps_per_shot']:]
            shot['action']['process'] = keep
            if moved:
                follow = copy.deepcopy(shot)
                follow['shot_id'] = shot['shot_id'] + '-b'
                follow['version'] = 1
                follow['action'] = {'start': shot['action']['end'], 'process': moved,
                                    'end': '动作完成后的稳定状态'}
                follow['counterexample_of'] = None
                follow['narration'] = '继续完成剩余动作。'
                follow['repair_attempts'] = 0
                followups.append(follow)
            changed = True
        if 'MISSING_REFS' in codes or 'CONTINUITY_REF_MISMATCH' in codes:
            declared = {r['ref_id']: r for r in new_plan['refs']}
            fixed = []
            for ref in shot['refs']:
                d = declared.get(ref['ref_id'])
                if d is not None:
                    fixed.append({'ref_id': d['ref_id'], 'role': d['role']})
            if fixed:
                shot['refs'] = fixed
                changed = True
            else:
                log.append({'shot_id': shot['shot_id'], 'action': 'unfillable_refs_stay_blocked',
                            'problems': sorted(codes)})
        # UNSAFE_COMPLEX_GRIP_NEEDS_3D intentionally has no in-scope repair.
        if changed:
            shot['version'] += 1
            shot['repair_attempts'] = attempts[shot['shot_id']] + 1
            log.append({'shot_id': shot['shot_id'], 'round': round_no,
                        'action': 'repaired', 'new_version': shot['version'],
                        'fixes': sorted(codes)})
    for follow in followups:
        if follow['shot_id'] not in new_shots:
            new_plan['shots'].append(follow)
    new_plan['planning']['distinct_use_purposes'] = sorted({s['purpose'] for s in new_plan['shots']})
    return new_plan, log


def run_pipeline(case, repair_budget_default=True):
    """plan → review → bounded repair (≤ policy budget rounds) → final verdicts.
    PASS versions are locked: their content hash can never change across rounds."""
    plan = build_plan(case)
    review = review_plan(plan)
    locked = {s['shot_id']: s['content_sha256'] for s in review['shots'] if s['verdict'] == 'PASS'}
    rounds = 0
    logs = []
    budget = koc_rules.planning_rules()['repair_budget'] if repair_budget_default else 0
    while review['return_count'] > 0 and rounds < budget:
        plan, log = repair_returned(plan, review, rounds + 1)
        logs.extend(log)
        review = review_plan(plan)
        for shot_id, content_hash in locked.items():
            shot = next(s for s in plan['shots'] if s['shot_id'] == shot_id)
            assert shot_content_hash(shot) == content_hash, 'PASS version mutated: ' + shot_id
        rounds += 1
    final = {'pass': [s['shot_id'] for s in review['shots'] if s['verdict'] == 'PASS'],
             'blocked': [{'shot_id': s['shot_id'], 'problems': [p['code'] for p in s['problems']]}
                         for s in review['shots'] if s['verdict'] == 'RETURN']}
    return {'plan': plan, 'review': review, 'repair_rounds': rounds, 'repair_log': logs, 'final': final}
