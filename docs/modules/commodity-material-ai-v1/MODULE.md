# commodity-material-ai-v1 — 本轮合同、拆解与实施计划

> 2026-10-13 查阅说明：本文件保留 commodity 子模块合同及阶段记录。日常素材流程从 [项目总览](../../../README.md) 与 [当前计划](../product-material-pipeline-v1/IMPLEMENTATION_PLAN.md) 接续。原计划的分类与 KOC 接口对照已保存在 [整理前原文](../../../runtime/backups/project-organization-20261013/original/docs/modules/product-material-pipeline-v1/IMPLEMENTATION_PLAN.md)。旧 REPAIR/worker 状态未在本轮向 Orca 复核，不代表当前活跃任务。

## 目标及授权

用户提交的完整任务为本轮设计依据：官方本地 Claude CLI 固定 `claude-opus-5-5` 创作和解释，Clef 原生决策审核，Toonflow 分镜和预演，既有 H3 生成，VBench 专项质检。产出独立商品素材和审核历史，不生产整条成片。执行方式已指定为不同性价比 GPT + ZCode。用户后续明确维持 45 个活动自有文件，合并整理过时文档。

所有写入仅在本项目；禁止读取或修改老X及 h3_pipeline。过去两项例外不延续。本轮付费 H3=0、权重下载=0，不改全局配置、认证、权限或已有服务。固定制作角标禁止；内容字幕按镜头需求处理。

最新用户 AGENTS 的 MODEL-LIFECYCLE-POLICY 同时生效：本地模型仅在实际推理时按需加载，检查/健康检查不加载；推理成功、失败、超时或取消后真实卸载或退出自有进程并释放显存，empty_cache 不算卸载。CPU 控制器不能预热或重启闲置模型，不打断其他活跃共享推理。本轮不启动/停止外部 Clef 服务，不执行 VBench 推理，不复制这套服务或模型权重；后续仍须先满足当前路径/服务边界。

## 实测基线与根因

- Claude Code 2.1.283 支持 restricted / dontAsk / permission-prompts none / json-schema。auth status 为 loggedIn=true、oauth_token、firstParty；当前进程未设置 ANTHROPIC_API_KEY / AUTH_TOKEN / BASE_URL 及云供应商覆盖。一次精确模型最小真实调用在 1.84 秒内退出 1，返回 OAuth session expired and could not be refreshed。认证状态声明不等于实际可用；不重复登录、不降级、不转 API。
- Toonflow 现有 v2.0.5，现场端口 3000/10588 为 loopback。不改 Provider 或内部数据库；复用支持的 MCP 工具，实际节点显示/修改验收独立记录。
- Clef Q4_K_M GGUF 6,486,448,288 字节已在已知目录。既有服务文档声明原生 /v1/systemone + state/questions，8088 当前未监听。模型文件存在不等于 Joint Head/CUDA/概率已验证；不启动或改写全局服务。
- VBench 已有环境和能力矩阵，历史授权的轻量权重已在项目缓存；四项评分 not_run，两个人体维度分别缺 mmcv/retinaface。两个 7B 模型未下载。本轮无下载、无 CPU 回退、无伪分数。
- 旧 product-storyboards/v1 固定 deterministic-local-v1；旧 Manifest 固定 TEST/PASS。本轮新增明确独立合同，保持旧接口。旧 H3 适配器会读老X运行时，本轮生产调用因路径边界及零预算保持 blocked，不执行 importlib 加载。

## 分工及文件所有权

| 执行者 | 责任 | 文件 |
|---|---|---|
| GPT-6 Luna medium | 库存、官方 MCP 文件桥接、边界测试 | adapters/storyboard_team.py、tests/test_koc_materials.py |
| GPT-6.1 Sol high | 已完成契约审查；实现最小本地页面/CLI | scripts/commodity-materials.py |
| ZCode，现有配置不改 | 核心适配器、持久化、合同、测试 | adapters/commodity_ai.py、tests/test_commodity_ai.py |
| 主 Codex | 文档、静态基线、真实预检、独立审查 | 本目录两文档、既有 runtime 三状态文件 |

不同实现者不回退他人修改，不增加其它文件，不相互改所有权文件。模型价格不推定为实际账单；轻量核查用 Luna，复杂契约用 Sol，核心实现用用户已配置 ZCode。

## 核心接口（页面与适配器统一）

`adapters.commodity_ai` 提供：

- `ROOT`、`MODEL_ID`、`SCHEMAS`（director/writer/reviewer/manifest JSON Schema），`AIError`；Schema 可导出到 runtime 自动证据，不新增自有 Schema 文件。新合同不能由旧 KOC Schema 替代。
- `ClaudeCLIAdapter(root=ROOT, timeout_sec=60, max_output_bytes=262144)`：`preflight()`、`invoke(role, payload, refs=None)`。返回调用记录 `{status, error_code, structured_output, model_id, duration_sec, raw_result, call_id, cached}`；真实调用固定模型，仅从 structured_output 严格校验。阻止认证覆盖、schema失效、输出超限、超时和并发；参数数组、无 shell、无权限询问、无 MCP、无自主写入/执行；隔离 cwd、no-session-persistence。三个角色提示词独立，证据不足不得虚构解释。
- `ClefAdapter(endpoint='http://127.0.0.1:8088', timeout_sec=15)`：`probe()`、`review(phase, shot, evidence=None)`。原生 state+questions，仅记录真实概率、原始结果、真实输入范围、缓存来源。probe 缺真正 Joint Head/CUDA/概率验证则 blocked。当前服务仅文本，B 视觉全 UNVERIFIED、连续动作 NEEDS_REVIEW，不把文件名/描述当视觉检查。
- `MaterialStore(root=ROOT)`：`register_take(shot, video_path, version=1, batch_id='local', provenance='existing_local')`、`list_takes()`、`get_take(take_id)`、`append_review(take_id, review, mode='dry_run')`、`append_usage(take_id, target_shot_id, purpose='reuse')`、`approve(take_id, approval)`、`export_manifest(take_id)`、`history(take_id)`。SQLite 仅在 runtime/commodity-material-ai-v1，全部版本保留，拒绝覆盖原文件，哈希绑定。拒绝/损坏/锁定均可查询；锁定不限制复用。shadow_review 只追加观察，不改批准指针/原 Manifest。批准要求真实非 Mock 审核及有哈希绑定、真人确认与范围的视觉/时序证据；没有证据不能 APPROVED。Manifest 含任务要求的全部字段，严格 Schema、媒体及镜头/审核哈希绑定，批准后变动拒绝导出。
- `run_dry_run(root=ROOT)`：只读现有 KOC case，规范化镜头并登记明确 Mock H3 数据链路，无视频则不伪造视频。真实 Claude/Clef 错误分别记录；既有外部引用只作为不读取的文字标识，不访问。返回可视化摘要（包含 h3_invoked=false、vbench not_run）。

精确方法参数、返回字段增补应由 ZCode 写入本文件接口区后通知页面执行者，不猜测字段。暂不实现通用 Agent 框架或全片编排。

新镜头至少有 `material_id`、`shot_id`、`product_id`、`source_refs`、`narration_target`、`visual_purpose`、`scene`、`character_refs`、`product_refs`、`action_start`、`action_process`、`action_end`、`camera_and_framing`、`duration_sec`、`required_visuals`、`forbidden_visuals`、`audio_policy`、`generation_prompt`。Writer 输出此类可执行镜头规格和参考要求，不能只输出口播或字幕。

Reviewer 至少有 `shot_id`、`take_id`、`review_status`、`confirmed_issues`、`unverified_issues`、`evidence`、`repair_action`、`prompt_revision`、`reference_changes`、`regenerate_required`。仅有旧版 `verdict` / `repair_hint` 不足以完成合同。新 Manifest 至少有 `asset_id`、`shot_id`、`product_id`、`version`、`video_path`、`file_hash`、`duration_sec`、`usable_ranges`、`visual_description`、`actions`、`scene`、`narration_targets`、`tags`、`audio_policy`、`review_status`、`review_record_id`；额外哈希/来源/审核范围字段用于可信绑定。

## 页面与操作

`python scripts/commodity-materials.py doctor|dry-run|serve|schema|export|bridge`。serve 默认 127.0.0.1:3011，先查冲突，不替换服务。API GET /api/takes、/api/takes/<id>、/api/takes/<id>/history、/api/status；/media?path=… 只服务项目内已登记原视频/缩略图/关键帧，支持 Range；页面全部版本、视频、原始前后审查、未验证项、版本比较、使用历史，Mock 标识显著。HTML/JSON 用户输入转义，不提供无认证批准/付费生成 HTTP 路由。

`bridge <项目内导演JSON文件> --workspace <runtime/toonflow-data/workspaces下的新直接子目录>` 严格校验 Director 合同并通过官方 MCP workspaceFiles 写入/回读，拒绝复用已存在目录或覆盖文件。入口不调用模型，不把用户文件或 TEST 文件标成真实 Claude 输出。真实 CLI 导入已通过（cli-bridge-live.json），节点显示/编辑另有 toonflow-node-bridge.json；真实 Claude 输出的全链验收仍 blocked。

## 实施及可观察验收

- [x] 完整提示词与旧工程复读；明确角色和边界。
- [x] 用户确认维持45；分类摘要合并到既有计划，14份不同历史文档（15个原文版本）完整归档并核对 SHA256。活动清点包括 case.json 输入；旧 H3 Task Spec 两个原文版本均保留，来源不作推定。
- [x] 先写本 MODULE 和 module.yaml。
- [ ] ZCode 先写会失败的安全测试，再写核心实现；真实 ZCode 结果保留，不把进程存在当完成。
- [x] Sol 实现单文件页面/CLI，复用核心接口；最终核心返修后的整合验收仍待完成。
- [ ] 独立验证严格 JSON、认证错误、输出/时间/并发边界、Clef 原生概率及文本视觉未验证、缓存失效、全版本保留、重复使用、shadow不改批准、Manifest拒绝过期、零H3、失败只影响单镜头。
- [ ] 实际页面/CLI检查；现场 Toonflow桥接按支持工具回读，若真实Claude失败则分镜真实传递验收blocked，不能用Mock替代。
- [ ] 用项目内已有视频真实审核；没有本轮允许读取的原视频则明确blocked，不去老X取。
- [ ] 回写 runtime/{progress,changes,acceptance}.json 与本 MODULE；仅凭证据结论。

安全验证命令：`python -m pytest tests/test_commodity_ai.py tests/test_koc_materials.py tests/test_deployment_guards.py --basetemp runtime/tmp/commodity-pytest -q`。旧 H3/消费者测试在收集时读禁止目录，本轮不能自动收集。新测试中合成媒体/Mock服务只证明单元行为，不计真实模型或生产素材验收。

## 提示词拆解与验收映射

原提示词分成四类约束：组件职责（谁负责创作、决策、预演、生成、检测）、业务合同（独立镜头、全版本、复用、批准 Manifest）、执行边界（认证、权限、费用、路径、超时、缓存）、真实验收（工具返回和生产资产不能被 Mock 替代）。前两类实现代码，后两类同时写入门禁与证据；现有部署故障不扩大为重新安装任务。

| 原要求 | 实现责任与验收依据 | 当前真实验收限制 |
|---|---|---|
| Director / Writer / Reviewer | ZCode：独立提示词和严格 Schema，共用固定模型 CLI | OAuth 已过期，三角色真实调用 blocked |
| 非交互与固定认证计费来源 | ZCode：参数数组、禁工具/MCP、时间/输出/并发限制、拒绝认证覆盖 | 以最小真实调用为准，不凭 loggedIn 宣称可用 |
| Toonflow 分镜读取、显示、修改 | Luna：官方 workspaceFiles 写入/回读；保留现有 Agent | 文件及节点显示/编辑 verified_TEST_only；实际 Claude 输出仍因 OAuth blocked |
| 镜头标准与参考一致性 | ZCode：完整字段、动作起止、可信参考绑定；复用现有 KOC 规则 | 不读取项目外资产，不推断未看见的画面 |
| Clef 原生能力 | ZCode：state/questions，noul/choice/score 原始概率校验 | 8088 拒绝连接；静态 GGUF 证明不代替运行验证 |
| 生成前 A / 生成后 B | ZCode：独立审核记录、输入范围和缓存键 | 文本输入不能给视觉 PASS；时序另行检查 |
| 局部返修 | ZCode：Reviewer 证据与单镜头修订合同，最多2次 | 未调用 H3，不自动重新生成整批 |
| 版本、拒绝、损坏、锁定与复用 | ZCode：SQLite 追加历史，Sol：所有版本可查 | Mock / 无视频明确标识，不算生产资产 |
| APPROVED 与 Manifest | ZCode：当前视频/镜头/参考/审核哈希、真实证据和范围门禁 | 无合格素材时拒绝导出；契约测试不冒充批准 |
| 页面、版本比较、使用历史 | Sol：单文件只读 HTTP 页面 + CLI | 真实核心+HTTP UNIT整合及页面浏览通过；返修后需重验 |
| VBench 专项检测 | 复用既有脚本和能力矩阵 | CUDA/import 验证通过，专项评分 not_run |
| H3 / production | 复用已有流程的边界描述，零预算门禁 | 当前禁止访问旧 Runtime，production blocked |
| dry_run / shadow_review | ZCode 核心、Sol 操作入口 | dry_run=Mock H3；shadow 只追加，不改变批准 |
| 成本、状态、失败和回退 | 主会话：独立测试、状态三文件、原文归档及哈希 | paid H3=0、权重下载=0；未完成项保持真实状态 |

GPT 选择按任务复杂度安排，未根据未知的账户账单估算金额。Luna 负责窄范围适配与负例，Sol 负责跨合同整合与独立复核；ZCode 保持用户现有模型设置执行核心，主会话验收和控制边界。

## 当前执行证据（2026-10-11，接续记录）

GPT-6.1 Sol 已实现单文件 CLI/页面、三模式及 bridge 入口；UNIT ONLY 页面及路径门禁验证通过，返修后仍须复验核心整合。GPT-6 Luna 的官方 MCP workspaceFiles 桥接已真实写入并回读合成 TEST 分镜文件（SHA256 一致）。最初 `connections=[]` 已由自有浏览器连接解决；最终完整 TEST 节点显示、原文编辑和直接 JSON 回读已验证，证据见下方补充。两轮独立审查修复非有限 JSON、路径、重定向、输出上限、canonical junction 与环境代理问题，桥接文件37项测试已由主会话重跑通过。曾由负例误写一份 NaN TEST 文件，已通过官方 remove 精确清理；后续负例均为 NoNetwork/NoCall，成功桥接记录保留。

ZCode 原生 headless 首次调用600,150ms超时，无核心文件，三个自有进程均已退出；诊断中 provider_not_configured 标为 unreliable，stderr 同时提到 GLM-5.3，不据此断言模型配置丢失。未修改其全局配置或凭据。改用现有 Orca 1.4.215 的真实 ZCode worker：Run `run_5849cdb0a524`，Task `task_748d1c83d802`，Dispatch `ctx_a09013e757e5`，工作区精确绑定 lab，未覆盖模型/effort；已收到真实 worker_done（msg_58b6fcc4bc01）；独立核心+桥接95项通过，但具体负例审查结论 REPAIR，不能据单元绿灯宣布核心验收。自有协调终端 `term_e2719d44-7340-4347-ac73-23362aa4b44f`，不借用其它会话邮箱。详细 Task Spec 和收据均在本轮 runtime/evidence。

VBench 新鲜验证见 `runtime/evidence/deployment/vbench-check-1791699199.json`：CUDA/import/CLI 均通过，人体维度缺 mmcv/retinaface、专项评分全部未跑。Clef GGUF 静态检查确认 architecture=clef、name=Clef-Flash、file_type=15、decision.*张量保留；原生端口请求失败，运行 Joint Head、实际 CUDA 推理、视觉能力保持未验证。仅发现旧 UNIT 视频夹具，不用于真实素材验收。

本轮安全旧基线测试39通过、1失败（旧 H3 脚本保留过期例外路径），不得删除测试掩盖或称全绿。旧 H3/消费者测试禁止收集，因为会实际读取本轮禁止目录。

## 错误知识与回退

E-01 auth status.loggedIn 可以与 OAuth过期并存：以真实精确模型调用为准，立即AUTH_EXPIRED，不修改登录。
E-02 GGUF/健康/CUDA卡存在不证明Joint Schema Head；缺原生决策证据返回TECHNICAL_ERROR，不切聊天或CPU。
E-03 文本Clef、少量抽帧、缺VBench不得推断完整视觉/时序PASS。
E-04 外部H3读取授权已经结束；历史配置不能扩大本轮边界。
E-05 旧pytest在收集阶段即访问禁目录，显式安全测试列表。

单故障最多2次修复后重新诊断。原件、历史、证据保留；回退只恢复本次登记的自有修改、按归档索引恢复需要的历史版本、停止本轮登记的本项目页面进程，不删除vendor/模型/数据库/原素材整树。活动自有文件最终不超过45；历史不可变归档及自动证据不计活动实现文件。

## 独立审查与返修1

初次交付后，Luna 复现了非零进程退出仍标 verified、缓存绕过认证覆盖拒绝、非有限 JSON 与进程异常外抛，以及不同实例并发。Sol 以项目内 UNIT 视频及音频复现 Mock/退回/错镜头/缺参考可批准、音频容器误作已解码视频、参考与审核 JSON 变化不使导出失效，以及新 Reviewer 字段与 Manifest 审核对象不兼容。真实模型调用为0；这些结论不依赖模拟上游成功。

结论 REPAIR。返修 Task task_6639d7fc5b36 / Dispatch ctx_0b0de15110e7，沿用 Run run_5849cdb0a524，仍仅ZCode写核心2文件，修复预算总计最多2次，不因启动失败重置。首个复用终端启动在 agent_readiness timeout、资源未创建，按实际失败收据改为同 Task 新 ZCode 终端；初始已交付终端按 worker-release 释放。正式修复合同见 runtime/evidence/commodity-material-ai-v1/repair1-task-spec.json。

本轮最小页面真实运行于 http://127.0.0.1:3011，项目自有只读 Python进程登记在 ui-server-process.json。已在浏览器查看13条Mock/no-video版本、分镜/审核原文和使用历史；不会把这些样例当真实视频。待返修后重新验证新阶段记录及页面显示。

文件分类入口已合并在既有 IMPLEMENTATION_PLAN 的“分类与查阅”表。现场新增4个H3源码/配置/测试保留，不纳入本轮改动；旧 KOC_INTERFACE_MAP 的分类摘要合并在该计划的“KOC接口对照”节，完整原文留存归档。当前更严格清点包括 case.json 输入、活动自有45；其它 cases 输出为自动生成证据。归档14份不同文档、15个原文版本，原字节全部保留并核对 SHA。

## Toonflow 文件与节点实测补充

Root 新建自己的 IAB 页面，唯一官方 MCP connection 经新 TEST 项目打开后，在实际页面以完整目录核实归属。Luna 在该项目复用官方 workspaceFiles、getCanvas、addNode、getNodeTools、nodeTools，将完整新导演合同 JSON 写入 remote-textNode 并回读。Root实际页面显示字段，点击编辑、修改 visual_purpose，再官方 MCP 回读并逐字核验；原始及修改文件分别保留。只改自有 TEST 工作区，不动 Provider/内部数据库。

发现富文本编辑器会把下划线和数组括号转义成 Markdown。机器 JSON 必须在“原文”模式编辑；Root已纠正并验证最终 outputs.text 直接 json.loads + director Schema通过，无反转义猜解析。证据 toonflow-node-bridge.json 的 raw_mode_direct_json_parse=true；截图 toonflow-node-visible.jpg。该事实是 TEST 输入的实际读取/显示/修改，不是 Opus 生成分镜的真实验收，后者仍因 OAuth过期 blocked；3D预演仍 not_run。
