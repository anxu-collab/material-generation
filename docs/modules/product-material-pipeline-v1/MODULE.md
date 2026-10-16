# MODULE — product-material-pipeline-v1

> 用户已于2026-10-16明确暂停。新生成/新云端请求关闭，自动接续关闭；AutoDL队列0运行/0等待。累计37/40，剩3；在途云端会话保留，03审核通过也不自动生成。详见 `paused-20261016.json`。

## 当前状态（2026-10-16，逐镜生产与生成缺陷诊断）

逐镜准入已生效：合格镜头可独立生成；失败只阻本镜及实际关联范围，批次链路验收仍保留。已用 AutoDL FireRed 实际生成 01/03/04/05/06 五张，累计37/40、本批5/8，剩3次；02文字ready但尚未生成，不称失败。未交付合格整批，旧有效素材集未替换，无视频授权。

| 镜头 | 文字准入 | 实际生成 | 云端像素 |
|---|---|---|---|
| 01 | 原稿通过；配对返修候选另审通过未采用 | v1完成 | fail：平板形态、配对机位不一致 |
| 02 | creator修近远端；reviewer自查后fixed、ready | 未生成 | 未运行 |
| 03 | ok | v1完成 | fail：露脸躯干、托板缺失、闭合电脑、接触起点错误 |
| 04 | fixed | v1完成 | fail：平板、托板结构/接触错误、刻字 |
| 05 | 原稿通过；配对返修候选另审通过未采用 | v1完成 | fail：配对机位/设备不连续；错误的末帧要求已由云端撤销 |
| 06 | fixed | v1完成 | fail：机位/前景、空隙亮带、托板结构问题；暂停前回执已完成 |

剩余3次暂不盲试。保留六镜目标；五张现有图均有云端实际失败记录，加02未生成，至少还需6次成功出图，当前剩3不足完成全目标。共同诊断中的具体像素观察有原图与独立review支持；“种子无关”“成功率低于50%”“追加预算后高成功率”无验证依据，均不采纳。纯产品替代动作会改变既定链路，亦不采纳。03仅生成提示词定向修复由creator处理，原分镜事实/机位/人物范围/模型保持；reviewer通过后仅用1次验证，若同类失败则停止该故障重试并报告能力待解决。

共用Skill v12/home v7，creator/reviewer22、motion/pace/enricher14；模型仍doubao-seed-2-1-pro-260628/high。云端原6图诊断发送异常未留HTTP细节，结果未知；最后GET idle/events=[]，未重发，不把payload大小称根因。另03/04+两参考四图独立诊断真实完成。AutoDL现queue0/0，06后已请求/free；torch分配很低但设备总空闲约7.7GiB，不能凭此宣称整个共享设备显存已全释放，未干预共享进程。

长期入口 `scripts/material-flow.py::_require_shot_review`；持久回归 `tests/test_ark_agent.py::ShotAdmission` 11项通过，计数fixture已隔离生产ledger。任务级脚本 `runtime/evidence/home-image-closure-20261015/{autodl_round,image_audit,pixel_round,scoped_review}.py` 尚未产品化。证据和真实会话在本目录，图片在 `output/laox-stand-home-20261015/`。旧阶段记录仅为历史，当前状态以本节及回执为准。




## 当前查阅入口（2026-10-13 整理）

日常接续先读 [项目总览](../../../README.md) 与 [当前工作计划](IMPLEMENTATION_PLAN.md)，素材标准读 [定稿规范](material-verification-spec.md)。本文件保留按阶段追加的合同和历史实现说明；下文的“本轮”、旧路径、旧默认通道、旧测试结论只适用于其原始阶段，不能作为今天的调用授权。

本地配置已为 `ark_agent`，五角色定义在 `agents/ark_profiles.py`；AutoDL z0902 仍为网络/生成关闭、dry-run 开启。当前下游素材入口是 `material-library/`；下文 SQLite 和 storyboardTeam 等条目属于各自子模块或原阶段设计，不能替代当前 material-flow 入口。旧 Bot/行业设计与原计划从 [归档索引](../../../runtime/backups/project-organization-20261013/archive-index.json) 查阅；本次未重新验证服务或生成能力。

## 身份与责任

- **Module**: `product-material-pipeline-v1`
- **目标目录**: `F:/codex/laox_director_lab`（2026-10-11 前为空目录，本次为唯一基线）
- **职责**: 独立部署并最小整合 Toonflow v2.0.5（含其 storyboardTeam director/writer/reviewer、3D 导演台、MCP）+ VBench/VBench2 基础环境 + MiniMax H3 适配契约，产出**可复用商品分镜素材库**（结构化分镜 + 入库 JSON），不做完整成片平台。
- **非职责**: 不修改现有剪辑系统；不重写 Agent 框架；不引入 Toonflow 之外的 Agent 平台；不下载全量模型权重；不做付费生成。

## 边界（继承执行合同）

- 写入仅限 `F:/codex/laox_director_lab` 内；不写全局配置/PATH/认证。
- `F:/codex/laox_account` 与任何 h3_pipeline 目录：**禁止扫描、遍历、读取、修改**。H3 凭据仅从环境变量读取（见 `config/env.example`），不回显、不落日志。
- 服务绑定 loopback；后台进程用 `Start-Process -WindowStyle Hidden`；启动前查端口冲突；只停止本项目启动的进程。
- 本轮预算：付费生成 0；额外模型权重下载 0（缺失时记 pending 与最小下载计划，待用户授权）。

## 组件与数据合同

| 组件 | 来源（锁定） | 角色 | 数据合同 |
|---|---|---|---|
| Toonflow server | github.com/HBAI-Ltd/Toonflow-app **tag v2.0.5**（commit 2c7fb25…，2026-10-09 发布） | 画布/分镜/Agent/3D 导演台宿主 | workspace 数据在 `<target>/vendor/toonflow/data/workspaces/<project>` |
| Toonflow MCP | v2.0.5 内置 MCP 实现（文件路径以源码调查为准，见 IMPLEMENTATION_PLAN §2） | 协议入口：initialize→tools/list→workspace/节点操作 | JSON-RPC（MCP 规范），脱敏请求/响应存 `runtime/evidence/` |
| storyboardTeam | v2.0.5 内置 director/writer/reviewer（若该 tag 实际不含，记录事实并核对上游，不虚构） | 分镜生成/审核/RETURN 修复 | storyboard schema（`schemas/storyboard.schema.json`） |
| 3D 导演台 | v2.0.5 内置（directorStudio） | 人物动作/站位/构图/运镜粗预演 | 导出序列或完整视频，供 H3 参考输入 |
| VBench / VBench2 | github.com/Vchitect/VBench（VBench2 = 仓库 VBench-2.0 子目录） | 质量指标：优先 subject_consistency、motion_smoothness（VBench 原版）；Human_Anatomy、Human_Identity（VBench2） | scores JSON；CUDA-only，无权重不评，不 CPU 替代 |
| MiniMax H3 适配 | 老X 唯一 Runtime `F:/codex/laox_account/app/runtime/h3/laox_h3.py`（**私有 importlib 实例复用，不复制实现**；源码 SHA256 锁定于 `runtime/backups/h3-autodl-v1/backup-manifest.json`，漂移即拒载） | 视频生成下游（提交/轮询/下载唯一实现方；本模块只做编排/映射/授权门禁） | lab 侧映射合同见 `adapters/h3_autodl.py`（template_pack + X 静默兼容路由 + 5s TEST 意图） |
| 素材库 SQLite | 本模块自有 | 分镜/任务/审查/版本/入库状态 | `library/materials.db`（schema 见 §数据模型） |

### H3 凭据契约（唯一允许通道，2026-10-11 h3-autodl-v1 更正）

- 真实通道 = 老X Runtime 的 settings resolver：默认读老X `app/api/autodl.env`（本模块不读内容），`AUTODL_ART_TOKEN`/`AUTODL_ART_BASE_URL` 环境变量覆盖优先（见 `config/env.example`）；密钥不回显、不拷贝、不落日志。
- 旧 `H3_API_KEY`/`H3_BASE_URL`/`H3_MODEL` 为未验证占位，已废弃。
- 对 `F:/codex/laox_account` 的读取仅限 AGENTS.md 窄范围例外二列出的精确只读文件（h3-autodl-v1 任务期）；老X 一切写入禁止。

### 官方 MiniMax 中国站直连（h3-minimax-official-v1，2026-10-11 用户授权）

- **独立第二供应商通道，与 AutoDL/老X Runtime 完全分离**：`adapters/h3_minimax_official.py` 不导入、不改动 AutoDL 适配器与老X Runtime；AutoDL 链（h3-autodl-v1）保持原样继续可用。
- 触发：AutoDL z0903 工作流无视频参考字段（EK-18），用户明确授权"全能视频参考接入官方的 minimax"（中国站 minimax.cn 确认），Ref2VA 真实视频参考必须走官方 V2 直连。
- 契约事实源 = `config/minimax_official.json`（provider/model/limits + 中国站价目快照 2026-10-11，含 policy hash）；官方文档 URL 与抓取记录在案（global v2 API reference fetched、China 创建页超时如实记录，以 global schema + China guide 双源核验）。
- 凭据：`MINIMAX_API_KEY` 仅环境变量；`MINIMAX_API_BASE_URL` 仅接受精确 `https://api.minimax.cn`（域钉死，先于任何 key/网络检查拒绝未验证 base）；禁止 AutoDL key 回退、dotenv 读取、key 落日志/回显/进异常文本。
- 请求合同：`POST /v2/video_generation`，model 固定 `MiniMax-H3`，content 显式角色块 `text`/`reference_image`/`reference_video`/`reference_audio`（first_frame/last_frame/mixed 与任意额外字段拒绝）；duration 精确 int 4..15；resolution 仅 `768P`/`2K`；ratio 八值默认 `9:16`；prompt 非空 ≤7000 字符；Ref2VA 至少 1 个真实参考。本地媒体先验真（扩展/大小/ffprobe 元数据全有限）再 Base64 DataURI 组体（JSON UTF-8 ≤64MB）；URL 参考本轮显式 not_implemented。
- 查询：`GET /v2/query/video_generation/{task_id}` 只读、只查本地登记过的 task_id、单次 30s 有界、校验返回 task/model/status；成功时解析 `task.content.url` 但不打印完整签名 URL；无轮询循环、无自动取消。
- doctor：默认离线（presence/schema，凭据只报布尔）；`--live-auth` = 单次安全 GET list（page_num=1&page_size=1，30s 有界、零重试、零 POST），只输出 HTTP 状态/auth 结论/错误码/request_id 脱敏摘要；无 key ⇒ `not_run`。
- 付费门禁：guarded submit 在任何 POST 前要求 `runtime/h3-minimax-official-v1/authorization/` 下的人工授权记录（human_authorized=true + 同意 provenance + provider/model/base/精确指纹/参考哈希/价目哈希绑定 + max_calls=1/calls_used=0 + 有限正 CNY 预算≤200 + 新鲜有效数值估价≤预算）；跨进程指纹排他锁先于传输建立，不确定标记在 POST 前落盘并在超时/网络错误/含糊响应时保留以阻断重提；一次尝试零重试（含 429）；成功持久 task_id。CLI flag 或 key 存在本身永不构成授权。
- 报价合同：中国站价目快照（输出 768P 0.50 CNY/s、2K 0.80 CNY/s；输入视频同档按输出分辨率计费——输入秒数必须计入；前 5 张输入图免费、超出 0.20 CNY/张；音频免费）。总价 = 输出秒×输出档费率 + Σ输入视频秒×输出档费率 + max(图数−5,0)×0.20；逐行条目 + 请求指纹 + 价目哈希；未知/过期(未重新确认)/畸形价目在付费路径拒绝；报价为公布价目计算，非账单担保。
- 本轮范围：本地 prepare（TEST 5s 768P 9:16，reference_video=真实 a1 全片只读）+ doctor 离线 + 全套 UNIT 测试（transport 为 stub，无真实 HTTP 生成）；认证 GET 与付费提交待用户配置 key + 人工授权后另行执行，本轮零付费零上传。

### AutoDL z0902 image-only 当前路由（h3-autodl-z0902-integration，2026-10-11 用户请求；STATIC_ONLY）

- **当前活跃 AutoDL 路由 = `minimax_h3_z0902`，项目默认分辨率 1088p竖(1088*1920)**；请求合同为用户自供（`USER_SUPPLIED_NOT_SERVER_VERIFIED`，唯一事实源 `config/autodl_h3_z0902.json`）：prompt 必填字符串 1..20000；duration 可选整数 1..15 默认 5（bool/float 拒绝）；seed 可选纯整数、缺省时整个字段省略（不发明 32 位等范围）；resolution 八档精确枚举、显式序列化、项目默认 1088p竖(1088*1920)（provider 自报默认 768p竖 不采用）；ref_image_0 必填 URL、ref_image_1..5 各自独立选填（允许间隔如 image0+image5，保留调用者原槽位编号、禁止重排/填空图，TaskSpec revision 2）、共 ≤6 个具名槽位（JPG/PNG/WebP，用户 URL 可为签名/无扩展名——不抓取、格式兼容性标 unverified）；**禁 ref_audio_0/任何音频、ref_video/任何视频、官方 MiniMax content/model 字段、ref_image_6 及任何未知字段**。
- **运行时验证 = NOT_RUN_USER_PROHIBITED**：用户明令"你只是对接，不允许测试"。本轮零测试、零 import、零 doctor、零 GET、零提交；z0903 历史真实 GET 证据（h3-autodl-v1）不构成 z0902 的任何服务器事实。
- **传输（未来 guarded 提交的静态设计）**：仅复用钉死 oldX Runtime 的 `http_json` 低层方法（laox_h3.py 72-92 行）经 lab 私有实例调用；不复用其 `submit/settings/prepare_bundle/fetch_schema/validate_schema`（z0903/audio/768p 锁定，见 EK-23）；AutoDL 配置仅从 `AUTODL_ART_BASE_URL`/`AUTODL_ART_TOKEN` 环境变量解析（域限 autodl.art 官方 HTTPS），永不读 oldX autodl.env；workflow 不一致即 fail-closed 无回退。
- **门禁**：`network_enabled=false`/`generation_enabled=false`/`dry_run=true` 默认；submit 在任何状态/网络变更前先查禁用开关即拒绝；配置 token 永不构成授权；新指纹绑定 workflow+规范请求+分辨率（z0903 历史指纹/授权记录结构上不可能命中 z0902 路由——授权 schema 升级为 `lab-h3-submit-authorization/v2-z0902`）；max_calls=1、正有限 CNY 报价 ≤ 有界预算（结构化报价合同同 repair-1 标准）。本轮不生成估价、不生成授权。
- **z0903 历史路由处置**：旧 z0903 代码在 `adapters/h3_autodl.py` 内整段保留为 HISTORICAL（`LEGACY_Z0903_ENABLED=False` 冻结，公共入口先拒绝），其历史证据/回执仍在 `runtime/evidence/h3-autodl-v1/` 与老X runs 记录，不受影响；激活 CLI 只暴露 z0902 路径。官方 MiniMax 通道（上节）不受本轮任何改动。

## 消费者痛点与实际图片验收 — consumer-pain-visual/v1

2026-10-11 用户确认补充。目标为消费者从画面认出具体麻烦、看懂商品介入、看到同一麻烦的解决结果。适用于 `scripts/material-flow.py` 的新规划、返修及后续转视频；历史产物保留，不追认其像素验收。此补充不构成任何生成、模型调用、费用、服务启动或外部目录访问授权。本轮只静态实现和复核，运行时验收 not_run。

### 六项创作要求

1. **需求先行**：明确谁、在什么情境下、想完成什么、被什么阻碍、带来什么具体不便、商品哪项已知功能解决。来源区分用户提供、可追溯真实反馈和创作假设；引用来源必须可核对，不能编消费者事实。设计设定仍是设计设定，不升级为真实商品性能。
2. **痛点可见**：问题物件与受阻状态必须同时读得懂，必要时辅以动作。只皱眉、泛泛凌乱或“生活不便”不足以通过。遮掉提示词和字幕后复述图片承担的麻烦/动作/结果。
3. **因果对应**：每条主链至少包含痛点、商品动作、对应结果，具体不便可独立成镜。对照沿用同一对象、环境与机位；线头掉落对应停在手边，碎屑卡住对应原位置清理变化，人物微笑和漂亮桌面不能替代结果。衍生场景也必须由本商品解决，禁止无关联想凑数。
4. **首帧可执行**：痛点图已显出问题，动作图有商品/对象/操作空间，结果图可为完成状态。不要求每张图同时呈现整个故事，不允许让视频从干净摆拍凭空补出麻烦与完整解决过程。
5. **按购买信息分配**：15 张时默认痛点/后果 5、动作 4、结果 4、纯产品细节最多 2。5/4/4 为规划目标而非下限，不凑满；全批纯摆拍跨场景最多 2，按新增信息判重复。原人物与生成预算不变。
6. **分阶段验收**：方案审核只证明计划，生成后另做实际像素审核。关键项不通过或缺少真实观察不能进入视频；生成图不是性能实测证据，不得虚构功效/实验/对比数据。所有固定角标禁令继续生效。

### 字段与入口

- 每镜 `consumer_need` 必填：`chain_id`、`role`（pain/consequence/action/result/product_detail）、`audience`、`situation`、`task`、`obstacle`、`consequence`、`supported_function`、`source_kind`、`source_ref`、`visible_evidence`、`first_frame_state`、`information_gain`。`supported_function` 逐字引用案例 `functions` 条目；`source_kind` 为 user_provided/source_feedback/hypothesis，假设明确标注在工程字段中。
- `_validate_consumer_plan` 静态结构门禁：每链有 pain/action/result、必要字段非空、功能在案例内、镜头 ID 唯一、相同角色和相同新增信息不重复、纯细节最多 2、人物预算不超限。**这是结构检查，语义合理性仍由实际审核者判断**；改写同义句不能使重复图合格。
- Sonnet 的 `batch.consumer_checks` 逐项记录 `met` 与具体 `evidence`：need_specificity、product_fit、visual_readability、causal_closure、first_frame_ready、information_gain、supported_claims。任一不通过即返工。审核绑定 creation/case 哈希及规则版本；Clef 同时接收消费者字段，仍是纯文本判断，不声称读取图片。
- `create` 默认 15，超出 1..15 在模型调用前拒绝；少量草稿可先保存，链路不完整时不能通过后续 review/gate。`repair` 只更新待复审稿与历史，不再直接输出绕过整批审核的生图任务；接续 `review → gate → jobs --shots <返修ID列表>`，只重生指定镜头，替换同 ID 不重复占有效图名额，但每次生成仍消耗总张数预算。单张补图在已有完整批次中使用 recreate/repair，再选择对应 ID，不另造孤立主链。`enrich` 可以补历史方案字段，不能替历史图片写像素 PASS，补完需重审。

### 实际图片审核接续

1. 完成方案审核与正常授权生成后，确定拟转视频的图片和动作；若需要 `pace` 改写动作，应先完成，再生成审核模板。
2. `python scripts/material-flow.py visual-review-template --approval <项目内approval.json>` 仅读取本地选择和图片，输出该批 `runtime/material-flow/video-batches/<batch>/visual-review.json`。模板所有结论为 pending、met=null、pixels_viewed=false，不能作为通过证据；已有文件拒绝覆盖，返修时先保留旧记录再重建。
3. 审核者用真实图片查看工具逐张看图。先不看提示词写 `blind_readback`，再对照 consumer_need、前后镜头及拟用视频动作写七项 checks 的可见依据。填真实 `reviewer`、`reviewed_at_utc`、`method=human_pixels|agent_pixels` 和 `pixels_viewed=true`。只有所有关键项有依据通过才能 verdict=pass；未看图、看不清或存在问题保持 pending/fail，不能为了继续流程填 true。模板中 image_path 和文字只是辅助，binding 必须对应实际打开的版本。
4. `video-jobs` 在导入供应商之前检查整批：方案审核/Clef 仍有效，逐张像素记录通过，当前实际图像字节、版本、分镜、定稿、商品资料、视频动作和时长均与审核绑定一致。记录像素审核文件与准备请求的哈希。
5. `video-submit` 在读取凭据或调用供应商前再次检查整个选择、实际图片、审核记录和准备请求。旧批次没有此合同会拒绝新提交；既有已提交视频的查询/下载和素材保留不受补充修改。

审核文件是可信审核者的记录，不是自动视觉模型。字段校验只能拒绝缺失、失败和过期记录，不能证明有人确实看过图；不得制造审核者身份或可见事实。签名远程图片 URL 的内容/有效期仍属于原链路未验证边界，本轮不请求远程资源。直接调用底层供应商适配器不属于该编排门禁，项目执行者必须经此工作流，已有底层费用授权检查不变。

### 本轮验证与回退

仅静态文件/语法结构复核，未 import 或执行项目代码、未运行测试、未调用 Claude/Clef/Seedream/H3、未读凭据或访问老X。证据与逐文件基线在 `runtime/evidence/consumer-pain-visual-v1/`。运行时拦截、真实出图与审核链路均 not_run，不将静态检查称为生产验证。回退仅比较并恢复本轮八个文件的对应改动，不覆盖他人后续修改，不删除任何素材。

## 四阶段接口

- **Phase1 部署验证**: `POST` Toonflow server HTTP 200（loopback:3000 或实际端口）；MCP 握手实测；VBench import/CLI/GPU 探测（neverCPU、noScoresOnBlocked 负面测试）。产出 `runtime/acceptance.json` phase1 节。
- **Phase2 分镜生成**: 输入 `cases/<case>/case.yaml`（商品资料+参考图+用途覆盖表）→ storyboardTeam 真实调用 → ≥10 条 schema 合格、用途不重复分镜 + RETURN→修复→再审闭环 → 导出 `cases/<case>/storyboards.json` 任务清单。
- **Phase3 3D 预演 + H3 dry-run**: 1 条人物动作 3D 粗预演全程检查 → 导出媒体验证（格式/时长/尺寸）→ H3 请求构造 dry-run（**零付费、零提交**）。
- **Phase4 质量入库**: 允许来源测试视频 → 技术解码/元数据 + 真实视觉审查 + 适用 VBench 指标 → 硬门禁入库 → `library/export/auto_edit_*.json` 导出。

## 数据模型（SQLite 摘要）

- `products` (id, case_ref, source_url, sha256, created_at)
- `storyboards` (id, product_id, version, status[draft/planned/return/pass], reviewer_verdict_json, content_json, action_tags, use_purpose, duration_sec, needs_3d, pass_version_locked_at)
- `generation_tasks` (id, storyboard_id, provider, dry_run, request_digest, state[pending/dry_run_ok/submitted/failed], retry_count, budget_spent)
- `assets` (id, storyboard_id, path, sha256, start_sec, end_sec, tech_check_json, visual_check_json, vbench_json, auto_edit_eligible, created_at) — **入库硬门禁：全部检查 PASS 且非测试夹具才可 eligible**
- 不变量：PASS 版本不可变（hash 锁定）；RETURN 只影响单镜；retry ≤ 2；dry-run 永不上传。

## 状态 / 费用 / GPU / 回退

- 状态机与 retry 上限见上；费用：`generation_tasks.budget_spent` 必须为 0（本轮预算 0）；GPU：VBench 推理强制 CUDA，探测失败快速拒绝（exit code ≠ 0，不产生分数）。
- 回退：`runtime/changes.json` 登记所有新增；回退 = 停本项目进程 + 删除对应新增文件；保留证据与已 PASS 资产。

## 验收（对照 A1–A6）

见 `runtime/acceptance.json`（verified/partial/blocked/not_run + evidence 路径）与 `DEPLOYMENT_REPORT.md` 八项。

## 当前未完成（随进度更新）

- [2026-10-11] **h3-autodl-z0902-integration 已实施（STATIC_ONLY；Orca task_8c5482b0a195，用户请求 AutoDL `minimax_h3_z0902` 对接并明令禁止测试）**：`config/autodl_h3_z0902.json`（新，用户合同+来源+门禁，无凭据）+ `adapters/h3_autodl.py`（活跃 z0902 image-only 路由：本地 prepare 仅映射调用方 prompt+图片 URL，不抽帧、不造 blank.wav、不伪 PASS；未来 guarded 提交走复用 Runtime `http_json` 的静态设计，默认禁用即拒绝）+ `scripts/h3-materials.py`（CLI 只保留 z0902 doctor 离线/prepare/submit-guarded，`--live` 显式拒绝）+ AGENTS.md 窄例外四 + 本文档/module.yaml/EK-23。**验收=纯静态文件级映射（`runtime/evidence/h3-autodl-z0902-integration/`）；运行时/接口/服务器验收 NOT_RUN_USER_PROHIBITED，不得宣称对接运行成功**；未来任何 z0902 真实调用需用户新授权。

- [2026-10-11] **h3-minimax-official-v1 已交付（Orca task_fb19c1d1ef72，用户请求"全能视频参考接入官方的minimax"，中国站 minimax.cn 确认）**：`adapters/h3_minimax_official.py` + `scripts/h3-official-materials.py`（doctor/prepare/submit-guarded/query）+ `tests/test_h3_minimax_official.py` + `config/minimax_official.json`。要点：(1) 与 AutoDL 适配器/老X Runtime 完全分离的官方中国站直连通道——不导入 AutoDL 代码，AutoDL 链原样；(2) 官方 V2 契约严格校验：model 固定 MiniMax-H3、content 显式角色块（text/reference_image/reference_video/reference_audio，first_frame/last_frame/mixed 与额外字段拒绝）、duration 精确 int 4..15、resolution 仅 768P/2K、ratio 八值默认 9:16、prompt ≤7000、Ref2VA ≥1 真实参考；本地媒体先验真（ffprobe 30s 有界、元数据全有限）再 Base64 DataURI（body ≤64MB），URL 参考显式 not_implemented，不做静默转码/抽帧/裁剪；(3) TEST prepare：真实 a1 全片（6.583333s，只读）作 reference_video，绑定 shot-007 v1 PASS 内容哈希+review，原 2.8s 不动；独立 5s TEST 意图经本地确定性 reviewer 复审 PASS（非 storyboardTeam LLM）；六段 Ref2VA 提示词（英文、无口播 `<d>`、无角标/水印，spoken_locked 保持，生成音频下游丢弃）；产物按指纹原子落盘 `runtime/h3-minimax-official-v1/prepared/`，不可覆盖异内容；(4) 报价：中国站价目快照 2026-10-11（policy hash 绑定），输入视频秒数计入（5s 出+5s 入 768P=5.00 CNY、2K=8.00 CNY，>5 图加价），未知/过期/畸形价目付费路径拒绝；(5) 门禁：MINIMAX_API_KEY 仅 env、base 域钉死 api.minimax.cn、无 AutoDL 回退；doctor 默认离线（--live-auth 单次安全 GET，无 key=not_run）；guarded submit 授权记录（human_authorized/provenance/全绑定/max_calls=1/预算≤200/新鲜估价≤预算）先于任何 POST，跨进程指纹排他锁+不确定标记先于传输、超时/含糊响应保留阻断重提、零重试（含 429），成功落 task_id；query 只读只查本地登记 task_id、校验返回形状、不打印完整签名 URL。本轮零付费零上传零真实认证（key 未配置，auth=not_run 如实报告）；TEST 产物演示身份未验证、auto_edit_eligible=false。

- [2026-10-11] **h3-autodl-v1 formal repair 1 已实施（第一次正式返修 1/2；Coordinator 复核发现预算门禁缺口，详见 EK-21）**：Coordinator 纯校验器探针（`coordinator-budget-findings.json`，对初版终源 fb4ccbb）证明两条反例均被放行——`unknown`（budget 0.01 CNY、estimate_cny=null，估价未知）与 `underbudget`（budget 0.01 CNY、estimate 80 CNY，估价超预算 8000 倍）；根因是初版 `_validate_authorization` 对 `cost_estimate_basis` 只查文本长度，无数值估价、无请求绑定、无估价-预算比较。修复（仅 `adapters/h3_autodl.py` + `tests/test_h3_autodl.py` + 三份文档）：(1) 新增 `_validate_cost_estimate()` 结构化报价合同——授权记录必须携带 `cost_estimate`：status=`human_confirmed_platform_quote`（仅信可信协调方登记的人工确认平台报价）、currency=CNY、unit=`CNY_per_single_call`、estimate_cny 精确类型有限正数（bool/str/NaN/inf/零/负/null 全拒）、workflow_id+request_fingerprint 双绑定本请求、provenance(recorded_by/source/recorded_utc)；(2) `_validate_authorization()` 强制 `estimate_cny <= budget.max_total`（`AUTH_BUDGET_INSUFFICIENT`），全部在网络/状态变更前拒绝；旧"仅文本依据"记录不再能授权（fail-closed）；(3) 诚实信任边界：记录校验只验证形状与绑定，不证明平台实际计价或人类确实同意；billing_config 价目（如 70）货币/单位/粒度未验证，永不换算成 CNY；本地估价门禁+单次调用绑定是本地能力，**非平台侧硬计费上限承诺**。验证：root 双反例修复后拒绝且零委托零状态变更；19 项畸形/错绑负例+旧文本记录拒绝；足额合成报价经 UNIT ONLY fake runtime 恰好委托一次；焦点套件全绿（计数以 `runtime/evidence/h3-autodl-v1/repair-1/repair-1-delivery.json` 的实测 argv/输出为准——终跑 76 passed = h3 29 + koc 47；koc 套件计数为本轮窗口内外部并发任务漂移值（初版31→45→47），非本模块固定事实，两文件哈希已钉入该证据）；live doctor GET200 复验 + 5s TEST prepare 复验（指纹 7f59bd6a… 不变，paid0/upload0）；5 修复范围文件字节备份于 `runtime/backups/h3-autodl-v1/repair-1/`。付费 POST/上传/生成/下载仍=0（paid_generation=not_run）。

- [2026-10-11] **h3-autodl-v1 已交付（Orca task_e39ada996597，用户请求"先对接H3的真实调用"）**：`adapters/h3_autodl.py` + `scripts/h3-materials.py`（doctor/prepare/submit-guarded）+ `tests/test_h3_autodl.py`。要点：(1) 老X Runtime 以**私有 importlib 双实例**复用——"resolver 实例"保持原 ROOT（settings/tool/ffprobe 解析与真实 GET），"执行实例"以 lab 内虚拟 `__file__` 把 ROOT 重定到 `F:/codex/laox_director_lab/runtime`，一切 lock/run/preview 落 lab，不复制/不 monkeypatch 源码，源码 SHA256 锁定漂移即拒载；(2) 真实 doctor：官方 workflow 定义 GET 200（20s 看门狗、不重试、不跟随重定向、脱敏报告），确认必填 prompt/ref_image_0/ref_audio_0、duration 1..15 整数、无任何 video 输入、`auth_verification=not_run`（GET200+凭据已配置≠认证成功）；(3) 输入绑定：仅 review PASS 且 content_sha256/version 双绑定的镜头可映射；RETURN/哈希过期/未知时长/needs_3d/视频引用 fail-closed；原 PASS 分镜 2.0/2.8s **不静默改 5s**——5s TEST 生成意图为**独立新 shot ID + 独立本地 PASS 复审 + 新哈希**（`runtime/evidence/h3-autodl-v1/`），沿用 case 真实 ref/provenance，经既有 deterministic-rules reviewer 复审后才允许 prepare，普通 prepare 不改写原 PASS；(4) 映射合同：template_pack=true + `TEMPLATE_PACK_` 状态、creation_mode=x 仅作 Runtime 既有的"六段自撰+silent"兼容路由（非 X 工作流激活，metadata 说明）、768p竖(768*1376)、时长 4..12（lab 操作上限，h3_policy max_segment_seconds=12；provider 真实 1..15）、无口播（无 `<d>`、dialogue 空）；必填 ref_audio_0 用 live schema 声明的官方 blank.wav URL 占位（非批准声音，生成音频下游丢弃）；TEST 视觉参考 = **仅限精确 pin 的 a1 路径+SHA 只读抽单帧**（Coordinator 2026-10-11 follow-up：URL/非 a1 本地视频/越界输出路径在 ffmpeg 运行前拒绝；PNG 强制落 `runtime/evidence/h3-autodl-v1/`，ffmpeg 30s 有界超时，源文件永不修改）；(5) 凭据不断链：lab 执行实例经**私有实例显式注入**委托 resolver 的 settings/tool（老X autodl.env + 原环境覆盖优先级原样生效，不复制凭据、不要求重复导出 token；submit 时由原 Runtime settings(require_token=True) 自行校验）；(6) submit-guarded：授权记录必须位于 `runtime/h3-autodl-v1/authorization/`（目录门禁先于任何读/写/调用），绑定单请求指纹、max_calls=1、正有界 CNY 预算且**须含结构化报价合同**（repair-1 起，见上条；schema price=0 不是免费证明），数值字段精确类型检查（bool/float/NaN/inf/负数全拒）；无授权在任何网络/状态变更前拒绝，CLI flag 本身不构成同意；未来提交仅经 Runtime.submit 委托。本轮付费 POST/上传/生成/下载=0，paid_generation=not_run；A1/A2 证据见 `runtime/evidence/h3-autodl-v1/`。

- [2026-10-11] **koc-v1 formal repair 2 已实施（第二次也是最后一次正式返修，2/2；Coordinator 对抗新证据 EK-17，含 msg_c4748bb729f7 嵌套反例）**：两条对抗 TEST 清单（`coordinator-nonhashable-entry.json` 首条目 `source_sha256=[]`、`coordinator-nonhashable-nested.json` 首条目 `audio.policy=[]` 且声明 SHA 合法）都在 `import_material_manifest(mode='test')` 的不安全类型操作处 TypeError 崩溃（前者 `bound_shas.add`，后者 `_structural_problems` set 成员运算）。修复（`video_postprod/scheduler/material_manifest.py`，系统性不逐字段堆叠）：(1) 声明 SHA 先验形状（str + `$defs/hash` 全匹配）才进集合运算——**合法形状声明 SHA 即使条目被拒也保持 bound_shas 绑定**（Narrato 回流阻断不弱化），非哈希值不崩溃不进 bound_shas；(2) **逐条目完整 JSON Schema 校验前置于手写结构遍历**（jsonschema 对任意 JSON 全域安全；结构遍历只接待 Schema 合格容器）——恶形条目（含嵌套 list/dict/null/数字）一律显式 `MANIFEST_ENTRY_INVALID`（detail=schema:<字段路径>:<首错>），合法兄弟条目继续导入。验证：consumer 焦点套件 **42 passed**（新增 list/dict/null/数字 SHA 负例、嵌套非哈希负例 7 处、双证据文件逐字复现、恶形条目合法 SHA 保持绑定+Narrato 回流阻断正例）；lab **31 passed**；7 文件回归 **74 passed, 1 failed**（唯一失败 `test_incremental_v2.py::test_auto_narration_synthesizes_once_then_cache_reuses` 卡 `AWAIT_SHOT_GAP`，属外部任务 r3 planner/service 所有权且其作业不设 material_manifest——与本轮改动无因果，如实上报不修复）；双证据文件修复后导入=各 **candidates 20 / 拒绝 1 / bound a1+a2**（`runtime/evidence/koc-v1/repair-2/`，含修复前双崩溃复现、argv/cwd/exit 记录、保护文件与外部漂移哈希对照——pipeline.py/service.py/shot_planner.py repair-1 后又被外部任务改动）。生产素材库仍 not ready，LLM/H3/VBench/3D 仍 not_run。

- [2026-10-11] **koc-v1 formal repair 1 已实施（Coordinator 复核 REPAIR 后的第一次正式返修，1/2；task_04c85d659f21）**：修复四项独立发现（详见 ERROR_KNOWLEDGE EK-12..15）：① 生产质检证据从"文件哈希存在"升级为**内容解析绑定**（归一化 metrics 证据合同：报告 source_sha256/status/metrics 必须与条目当前媒体/声明一致，防错视频报告）；② 生产资格新增**视觉审阅证据硬门禁 + 身份核验**（visual_review_path+SHA 独立证据文件，绑定 material/version/window；not-applicable 声明不再等于视觉审阅；product_identity_verified=false 显式保持 blocked）；③ 消费端补**Manifest 根合同校验**（缺失根字段/未知根字段整单拒绝；非对象条目显式拒绝且不拖垮合法条目）；④ 通用商品规划**去除火龙果用例硬编码**（动作/取景/关键词/人物/场景/provenance/技术字段全部 case 输入驱动；无动作提示→RETURN ACTION_HINT_MISSING；咖啡旁白+切果动作→RETURN ACTION_OBJECT_MISMATCH；无职责匹配 ref→RETURN NO_FITTING_REF_FOR_PURPOSE；TEST 导出器拒绝 test_flag=false 且未声明 provenance 拒绝导出）。正例校验夹具全部 UNIT ONLY 标注；TEST 导出保持 not_run/auto_edit_eligible=false；CUDA/LLM/H3/VBench 推理继续零调用。**外部并发任务（完善双项目剪辑衔接）已把消费端推进 r3 语义**（service.py SyntaxError、shot_library gaps/split_fruit 超已交付 Schema、planner 版本门禁）——其文件/测试不在本轮 ownership，保留并如实上报；消费端完整闭环回归在其收敛前保持失败（EK-16），本轮不宣称全绿。
- [2026-10-11] **koc-v1 集成轮 delivered（工程闭环 Orca task_6d2c8f52f480）**：koc-video skill ZIP 不可变导入（`imports/koc-video.zip` SHA256 313a92cb…，7 文件逐文件哈希锁定于 `config/koc_material_policy.json`）；规则适配器 `agents/koc_rules.py`（每条策略源绑定 ZIP 文件+小节；四主参考常备、hook opt-in、自我执行/确认卡/全片 native-speech/终剪合成器显式 override 禁用）；确定性干跑规划/审阅/修复 `agents/product_storyboards.py` + `adapters/material_manifest.py` + `adapters/storyboard_team.py`；CLI `scripts/koc-materials.py`（verify-import/plan/review/export/dry-run/team-check，零付费零上传零 LLM）。火龙果干跑案例 `cases/koc-dragonfruit-dryrun/`：23 分镜（6 用途）→ 21 PASS / 2 blocked（MISSING_REFS 不可补、复杂抓握需全动作 3D 检查）→ 21 条 TEST manifest 条目（test_flag=true、auto_edit_eligible=false、vbench not_run）。storyboardTeam members/*.md 追加商品规则引用（原文保留）+ `knowledge/product-materials.md`（team.json 未改）；活体 `/api/agents/get` enabled/无 loadError，LLM 调用 not_run。消费端桥接（老X video_postprod，用户直接请求授权的窄范围）：`scheduler/material_manifest.py`（显式 mcat- 候选分支、frame_count=0、真实媒体哈希+ffprobe 时长 fail-closed、REVIEW_STALE 内容哈希绑定、条目级 product_id 拒绝、must_preserve 排除）+ `shot_library.build_shot_library(material_candidates=)` + `run_v2` 可选 manifest 分支（bound 资产由 manifest 门禁管辖）+ `edit-job-v2` 可选 `material_manifest` 字段（旧作业兼容）。验证：director_lab 23 tests + consumer 41 tests 全绿（消费端 23 项基线不变）；Coordinator 4 个对抗反例复跑全部按预期拒绝（`runtime/evidence/koc-v1/adversarial-rerun.json`）。接口图见 `KOC_INTERFACE_MAP.md`，交付报告见 `KOC_INTEGRATION_REPORT.md`。（该轮结论后被 Coordinator 独立复核判 REPAIR，见上方 repair-1 条目——"全绿"指当轮测试证据，不覆盖复核发现的四项缺口。）
- [2026-10-11] Phase1 安装轮 **delivered**（合同 v3）+ **repair1 返修完成**（R1 身份门禁 fail-closed+真实负面测试 9 项；R2 verify-mcp 有界超时/错误拒绝/节点生命周期进永久脚本/无页面 exit 3；R3 依赖与矩阵对齐新鲜证据；R4 事实更正与机器时间戳）。Toonflow v2.0.5 经 `scripts/toonflow-loopback.ts`（原生 createApp 薄包装，显式 127.0.0.1）运行，bun pid 40644（repair1 轮重启登记），HTTP 200，netstat 实测 3000/10588 均仅 127.0.0.1；MCP 协议实测全通（46 工具；节点增读删在永久脚本内 verified）；storyboardTeam 官方构建 + agentsRoot 首装 + `/api/agents/get` 活体注册（加载级部署）；3D 导演台构建产物 + 页面注册；VBench/VBench2 venv + torch CUDA 全绿。详见 `DEPLOYMENT_REPORT.md`（repair1 节）与 `runtime/acceptance.json`。
- storyboardTeam 实际调用（需配置 LLM=付费）与 3D 预演（Phase3）：not_run，待后续授权。koc-v1 轮的规划/审阅为**确定性本地规则引擎**（deterministic-local-v1，llm_invoked=false），与未执行的原生团队 LLM 调用明确区分。
- VBench 轻量权重：2026-10-11 用户授权后已下载到 `runtime/cache/`（约 11 GiB，清单与 SHA256 见 `runtime/evidence/deployment/lightweight-weights.json`）。`LLaVA-Video-7B-Qwen2` 与 `Qwen2.5-7B-Instruct` 未下载。指标执行仍是 not_run：主体一致性/运动平滑度的权重已在磁盘上，但还没有打分；Human_Anatomy 仍卡 mmcv，Human_Identity 仍卡 retinaface 包。koc-v1 manifest 的 vbench 字段只输出 not_run/blocked；schema 面向未来允许 pass 但必须带非空 metrics+哈希绑定证据。
- H3 契约核对：**untested**（Phase3 范围，本轮零探测提交）。2026-10-11 repair1 仅做过一次有范围限定的存在性探测：worker 子进程 shell 中 `H3_API_KEY`/`H3_BASE_URL`/`H3_MODEL` 均 unset（只报有无，未回显任何值）；这不等于全局不存在，Phase3 dry-run 前须先做验证探测再定缺口。
- Phase2-4 为后续路线（原 USER_REQUIREMENTS 十节方案），本轮未实施；koc-v1 干跑成果为 TEST 性质，生产素材库尚不可用（a1/a2 为 H3 演示、身份未验证，生产门禁会正确拒绝——这是预期行为而非集成失败）。


## material-catalog-scheduler/v1（2026-10-13）

在现有 v3 素材库上增加可选 catalog 扩展，保持 clip_id、三件套与媒体审核状态。标准名称、分类词典和时间戳证据见 `schemas/material-catalog-v1.schema.json`；使用说明见 `material-library/README.md`。新增编目、队列、合同和定向测试共4个源文件；不重构 Toonflow、不修改外部剪辑系统。

调度按阶段单任务认领，持久保存结果、租约与授权消费；结果不明不自动重复调用。实际视频分析与媒体审核分开，旧生成参考图不作为视频证据。素材库 JSON 写入采用共用锁与恢复日志；未修改的外部消费者仍无跨文件原子快照保证。

本地接口及定向验证已实施；实际 CLI 尚无已验证多模态 transport，默认禁用。当前 `catalog-run-once` 到分析阶段会等待能力/授权，不能当作真实分析已通。后续需核实供应商、预算和1条真实语义分析；本轮零模型/网络调用。精确证据见 `runtime/evidence/material-catalog-scheduler-v1/report.md`。

## 2026-10-15 通用角色与 Skill 分层（当前有效）

五个 Managed Agent 的通用执行协议由 `agents/ark_profiles.py` 维护，仅保留职责、输入/Skill核对、证据边界、自检、JSON输出与失败处理。内容规范在 `agents/skills/material-production/SKILL.md`；行业判断分别在 `industry-healthcare`、`industry-home`。通用 reviewer 不再内置行业画风/痛点禁令。所有角色均有适用 Skill，任务按 `case.industry` 显式选择一个行业全文；缺失或未知行业拒绝，明确只做通用范围时使用 `generic`。

云端实证：creator/reviewer v10，motion/pace/enricher v2；共用 Skill v1，两个行业 Skill v2，模型与思考档位未更改。云端包已重新下载，与本地全文逐字节一致。脚本同步会检查system、绑定版本与Skill内容变化；不再因ID相同而跳过正文。JSON Schema随请求发送并本地检查，写入前检查数量、唯一ID及任务范围。行业Clef问题只在行业Skill中维护，并全部纳入门禁结果。

本轮28项定向离线测试通过、静态场景复核PASS；未调用云端模型生成，不表示真实生成效果或像素验收通过。完整报告与回退：`runtime/evidence/generic-agent-skills-v2-20261015/report.md`。本节替代旧“仅creator/reviewer绑定行业Skill”及system重复生产细则的说明。


### 2026-10-15 居家创作与两层人物属性

当前home Skill v3：creator的观察倾向、克制幽默与人物参与属于行业Skill；具体身份、外观、生活习惯属于case。未向通用Agent增加居家规则。五角色绑定已回读，creator/reviewer v11，其余v3，模型保留。

桌面支架实际完成4个文本请求与6镜首轮稿。真实reviewer虽给pass，主审发现改首帧未同步动作、人物漏计，已退回；定向返修又多带字段，被本地契约拒绝。原稿留存，主审明确编辑后得到可读文字稿，未把它配旧审核投入生产。

文件取回兼容真实API的filename与详情下载地址，签名下载不转发API凭据；31项定向离线测试通过。长连接中断仅完成本次会话的只读恢复，尚不具备生产入口的完整自动恢复。

交付与证据：`runtime/evidence/home-stand-creation-20261015/report.md`、`桌面支架_首轮分镜.md`、`storyboard-reviewed.json`。本轮未生成图片/视频，最终稿云端重审及像素验收均not_run；production_ready=false。


### 2026-10-15 审核漏项回归与自动拒绝

共用Skill v4负责改后版本交叉核对、实际人物分类、范围/后果闭环、证据来源和机器契约；home v6负责使用可行性与结果条件。五角色绑定回读通过，系统提示词和模型未改。

实际reviewer v12/v13进行了4个文本审核请求（当前稿与旧问题稿各两次）。旧问题稿被退回，但仍有首帧漏检；当前稿两次被判pass，却分别漏必需记录、删参考编号。原稿、原始云端判定均保留，未手工修补为通过。

本地 `_validate_review_output` 从Skill读取检查ID与参考装配契约，接入cmd_review和_require_plan_review：缺记录、判定矛盾或通过稿缺编号即拒绝。审核先存review.raw.json，校验通过才写正式review.json；生成前复查。该检查只能证明确定性契约，不证明语义或像素。38项定向离线测试通过，四份真实输出重放中两份pass被拒绝。

最新creator/reviewer v14、motion/pace/enricher v6包含机器契约声明，未再追加第五次推理。Skill部署与门禁verified，语义可靠性partial，当前生产准入false；无媒体生成。详情：`runtime/evidence/reviewer-skill-regression-20261015/复审结果.md`、`report.md`。


### 2026-10-15 云端居家分镜返修闭环完成

- [x] 共用Skill v7、居家Skill v7完整云端包回读一致；通用Agent核心和模型不变。必需检查包括review_edit_scope，由既有门禁读取。
- [x] 云端creator完成六镜修正，最后仅定向修正03首帧承重与04时序；无主会话补稿。
- [x] 负例回归由reviewer v16真实退回03结构冲突，原提示词保持；不把该样本当作全面零漏检证明。
- [x] 最终reviewer v17给pass，39项离线测试及正式方案门禁通过；实际审核修订仅05身份措辞、术语和笔误。
- [ ] Clef及实际图像/视频验收尚未执行；无媒体生成，生产准入仍关闭。

正式run：`runtime/material-flow/laox-stand-cloud-closed-20261015`。完整证据、原始云端判定与可读分镜：`runtime/evidence/home-cloud-closure-20261015/report.md`。本段更新此前“最终稿未过审”的当前状态，不覆盖历史失败证据。


### 2026-10-15 图片生产接续：真实读图通道已验证，Clef仍阻断

共用Skill v8增加分离的pixel_blind/pixel_compare模式；home保持v7。reviewer职责首句最小扩展为文字或实际素材，五角色模型/工具保持原值，云包已完整回读。两张已有真实参考通过匿名像素transport验证，不能等同生产图审核通过。45项Ark定向测试通过。

正式六镜creation/review未改，未生成图片/视频，剩余图片额度8，有效图集未改。原Clef截句输入语义不通过；完整输入超2048token physical batch；已按原七题机械投影且保持全部镜头链关系，第一题在既定20秒超时。门禁保持false，无人工PASS，模型已释放。后续需要解决有界等待窗口，不因服务故障改分镜或放宽质量。

证据与诊断：`runtime/evidence/home-image-closure-20261015/report.md`、`clef-latency-diagnosis.json`。本段为当前状态，历史过审仅指文字稿。


### 2026-10-15 后续状态：原问题已真实评分，云端返修复审中

本批按明确协调授权使用项目局部60秒请求窗口（默认20秒不变，外层480秒）。七题完成，六题通过，scene_spread=0.278419仍失败；没有改阈值/题目或刷分。上段20秒超时为历史问题，不是当前阻断。云端creator repairgate及独立reviewgate完成；reviewer真实退回02机位/视点冲突。creator repairgate2已修复，完整reviewgate2（sesn-20261015132957-n5czm）运行中，之后必须重新全Clef。

用户明确把图片后端改为指定AutoDL ComfyUI，仍由原云端Agent按Skill读图。现有FireRed1.1多参考模板及节点/模型已读核验，静态工作流和受保护执行器已准备；不是已生成。沿用产品32/40累计预算，本批6张+最多2张返修，首张计入6；旧有效集保留，未登记新图。实际提交必须通过云端文字+Clef，实际图片最终必须完整blind+compare和哈希绑定。仅本批图片授权，未授权视频。

入口负例检查5项通过，实际编码字节hash检查7项通过；证据`runtime/evidence/home-image-closure-20261015/entry-guard-tests.txt`、`pixel-transport-final-tests.txt`。详情及后续实时状态以该证据目录和`runtime/progress.json`为准。


### 本执行窗口最终状态：外部云端复审 blocked
reviewgate3在25分钟内平台两次model_request_end均is_error=true，公开错误细节未知，原session仍保留且未取消/重发。观察器与自动生成接续已退出。任务未完成；AutoDL仅静态准备，0新增图片，产品共享32/40不变，队列空。最新repairgate3稿保留待真实复审，旧完整Clef失败保持。恢复先GET原session sesn-20261015141736-qhk6v，再按真实复审合同→全Clef→首张→全批真实像素审核顺序，禁止重复推理提交或自动PASS。完整回执与恢复步骤见runtime/evidence/home-image-closure-20261015/report.md。
