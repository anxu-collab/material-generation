# 素材验证规范（Material Verification Spec）

- 版本：v1（2026-10-12 定稿，经 lxc-home1 create/review 真实验证确认）
- 适用范围：product-material-pipeline-v1 全部行业 Skill（industry-healthcare / industry-home / 后续行业）与全部角色 agent（creator / reviewer / motion / pace / enricher）
- 总原则：本管线只生产**素材镜头**（供自动剪辑选用的 3–10s 视频素材及配套分镜/提示词），不产成片。素材验证 = 保证"每个镜头观众能看懂、能单独被自动剪辑拿去用、链条完整不重复"。素材不承担投放级合规，效果导向；合规红线仅保留素材生产硬规范。

---

## 1. 验证分层

| 层 | 时机 | 执行者 | 验证对象 |
|---|---|---|---|
| 方案验证 | create 后 / review 时（文字计划） | reviewer agent + 本地结构校验 | 分镜方案的因果链、可见性、结构契约 |
| 像素验收 | 出图后（真实像素） | 具有读图能力的 Agent/人 | 实际生成的图片/视频帧 |

两层独立：文字方案审核不得声称已看见实际图片；像素验收不得用文件名、提示词、模板代替。

---

## 2. 方案验证（文字计划）

### 2.1 结构契约（根结构与逐镜字段分开处理）

- 根结构无法解析、shot_id 重复等身份歧义阻断该根输入；不可确定商品资料身份或资料绑定失效时，不准生产。可定位的单镜字段/契约错误只拒绝该镜及有证据的依赖镜头，其余合格镜头可继续。
- 顶层对象：`{"shots": [...]}`，全批提交/验收时 shots 长度严格等于本批要求数量 N；scoped 审核只返回显式目标 ID，关联镜头作为上下文，不伪造其新判定。
- 每个 shot 必须逐字包含以下 **23 个字段**：
  `shot_id, story_beat, visual_mode, narration_target, scene, action_start, action_process, action_end, camera, duration_sec, product_ref, image_prompt, video_prompt_draft, claim_tags, content_mode, has_host, person_exposure, consumer_need, real_product_craft, concept_association, effects_packaging, visual_metaphor, edit_handles`
- `consumer_need` 必须逐字包含 **13 个字段**（chain_id 与 role 在 consumer_need 内部，不在 shot 顶层）：
  `chain_id, role, audience, situation, task, obstacle, consequence, supported_function, source_kind, source_ref, visible_evidence, first_frame_state, information_gain`
- 五组能力字段名逐字一致：
  - `real_product_craft`：`product_presence`(hero/supporting/in_use/absent)、`hero_details`[]、`material_and_light`、`fidelity_risks`[]
  - `concept_association`：`selling_point`、`association_chain`[](≥2 步)、`viewer_takeaway`
  - `effects_packaging`：`layers`[{effect, timing, intensity, purpose}]、`realism_level`(photoreal_practical_light / photoreal_with_overlay / realistic_3d_visualization)
  - `visual_metaphor`：`used`(bool)、`source_image`、`target_meaning`、`why_it_reads`
  - `edit_handles`：`edit_role`、`hook_strength`(1–5)、`clean_in`、`clean_out`、`loopable`、`pairs_well_with`[]、`text_safe_zone`、`mix_tags`[]

### 2.2 内容校验（机器 + reviewer 双轨）

机器校验（`_validate_consumer_plan`）：
- shot_id 唯一、`[A-Za-z0-9_-]{1,40}`。
- `role` ∈ {pain, consequence, action, result, product_detail}；`source_kind` ∈ {user_provided, source_feedback, hypothesis}。
- `supported_function` 必须逐字命中商品资料 `functions` 中一项（不能编功能）。
- `(role, information_gain)` 不重复；同一 `chain_id` 内必须含 pain、action、result 闭环。
- 预算硬限：纯产品细节 ≤2；full_person ≤3；limbs_only ≤3；总有效图 ≤15；生成 ≤20；视频 ≤10 条且每条 ≤10s。
- 相同信息换场景/换角度视为重复。

reviewer 六项（`consumer_checks`，每项 met + evidence）：
1. `need_specificity`：任务/阻碍具体，来源不冒充事实（source_ref 可查）。
2. `product_fit`：每个痛点确能被所引用功能解决。
3. `visual_readability`：计划中的画面能独立读懂（遮住说明文字仍看懂本镜承担的麻烦/动作/结果）。
4. `causal_closure`：同一痛点有动作和对应结果。
5. `first_frame_ready`：动作有适合的起点状态（痛点首帧显出问题；动作首帧含商品+空间；结果首帧可为完成状态）。
6. `information_gain`：无换景重复；纯摆拍/识别细节全批 ≤2。

链路与数量（batch 层）：
- `chain_links`：把功能链路拆成环节（麻烦起因、麻烦扩散、动作、结果、结果延伸），逐环列 shot_ids 与衍生镜头 `derivative_shot_ids`（离开产品本体/原场景的画面，可以不出现产品）。
- `same_desk_product_closeups`：同一张桌面、产品为主体的特写/细节镜头 ≤4。
- `requirement_checks`：逐条核对商品资料创作方向与 brief（功能靠画面演、链路衍生图、不做清单测评、人物预算、禁角标、产品专属规则）。

### 2.3 判定规则

以下 pass/needs_rework 表示批次验收状态。批次场景分散、完整因果与重复信息仍须验收，但不是无关联合格单镜的生成否决。单镜需真实 ok/fixed 审核、六项 consumer_checks 与 common/行业适用 quality_checks 通过；按镜头内容、审核提示词、商品资料、参考字节及明确 dependencies 哈希绑定。受影响范围用 affected_shot_ids 和证据明示，最新同内容失败及其依赖不可回退旧 PASS。无记录、过期或关联失败拒绝；无关修改不使全组失效。像素审核保持盲读→对照与六项标准；逐镜通过仅进入候选，不代表完整批次通过。

当前生产状态与任务级入口见[本轮报告](../../../runtime/evidence/home-image-closure-20261015/report.md)。

- **pass**：六项全 met、每条链路有衍生镜头、同桌特写 ≤4、requirement_checks 全满足。
- **needs_rework**：任一项不 met，或任一环缺衍生镜头，或同桌特写超限，或任一 requirement 未满足；batch_issues 必须写清缺什么。
- **fixed**：仅参考编号绑定、笔误或不改变语义状态的同义表达可由 reviewer 给出 `image_prompt_final`；首帧矛盾、承重/接触冲突、动作过载或需增删物件/人物等结构问题必须退回 creator，不得先改稿再放行。
- **escalate_to_creator**：镜头内容重复、应替换为其他环节的衍生画面（如重复产品摆拍 → 改为某条链路的衍生镜头），交 creator 返工。
- 旧素材不自动通过：缺 consumer_need 契约或未重新审核的 legacy shots 一律拒收（"legacy shots are not auto-approved"）。

---

## 3. 画面硬规范（素材生产约束，所有行业通用）

1. 画面不得出现任何审阅/状态角标、水印、"内部审阅""信息待确认"类文字；无固定文字、常驻标签、制作说明。
2. 无口播、无字幕、无配乐（素材输入层不携带；音频由剪辑端负责）。
3. 每条素材 ≤10s，`duration_sec` 为 3–10 整数；动作在时长内完整收住；首尾各留约半秒稳定画面。
4. 图片禁止插画感、CG 塑料感、额外文字、logo 变形；真人入镜时主体人物唯一。
5. 特效只允许真实光效（photoreal_practical_light / photoreal_with_overlay），不用抽象能量流、卡通光轨、强逆光剪影。
6. 动作幅度适中，避免复杂手指操作与包装文字形变（图生视频首帧友好）。

---

## 4. 像素验收（出图后，必须真实读图）

1. 逐张记录：盲读复述（看到什么）、可见依据（像素级证据）、因果关系、动作起点、新增信息、事实边界。
2. 审核绑定：图片版本/实际字节哈希、当前分镜、定稿提示词、商品资料及所选视频动作和时长。
3. 换图/改稿/改动作后验收失效，须重新验收；旧素材保留，不能自动补 PASS。
4. 生成画面是创作，不是产品功效实测证据；推测场景标创作假设（source_kind=hypothesis + source_ref），不冒充用户反馈或实物性能。
5. 来源与假设记工程记录，不进画面。

---

## 5. 去合规化原则（2026-10-12 用户裁定）

- 本管线只产素材视频，不产成片，因此**删除一切投放级合规限制**（禁医疗断言/临床数据/医生背书/威胁式营销/点名竞品/翻倍秒杀断言/夸大用法/数字对比等）。
- 各行业 Skill 只保留正向方法表述（"怎么拍有效果"）+ 第 3 节素材生产硬规范 + 创作标注。
- 保留素材可用性硬规范（无角标、无口播字幕、≤10s、无插画感、主体唯一）。

---

## 6. 与代码/工程映射

| 规范项 | 代码实现 |
|---|---|
| 结构契约 | `SHOT_SCHEMA` / `CONSUMER_NEED_SCHEMA` / `REVIEW_SCHEMA`（scripts/material-flow.py） |
| 内容校验 | `_validate_consumer_plan()` |
| 六项检查 | `CONSUMER_CHECKS`（need_specificity/product_fit/visual_readability/causal_closure/first_frame_ready/information_gain） |
| 预算常量 | `MAX_EFFECTIVE=15`、`MAX_FULL_PERSON=3`、`MAX_LIMBS_ONLY=3` |
| 行业方法论 | `agents/skills/industry-<行业>/SKILL.md`（四支柱 + 第 3 节硬规范） |
| 像素验收 | AGENTS.md「两段审核与准入」；`runtime/evidence/<phase>/` 留痕 |

## 7. 变更记录

- 2026-10-12 v1 定稿：基于 lxc-home1 create/review/enrich 真实验证确认；去合规化；结构契约逐字化；六项判定细则。
