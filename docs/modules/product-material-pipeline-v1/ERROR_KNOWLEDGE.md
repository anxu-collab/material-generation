# ERROR_KNOWLEDGE — product-material-pipeline-v1

真实故障留档（症状 / 触发 / 根因 / 修复位置 / 证据）。只记录实际发生的故障，不把阶段未执行写成 bug。

## EK-1 native headless 权限故障（历史，已随终止关闭）

- **症状**：`No permission client configured for Bash`，git/npm/mkdir/nvidia-smi/`python -c` 全部被拒，仅 `ls`/`python --version` 白名单通过。
- **触发**：2026-10-11 旧 native headless 会话执行部署命令。
- **根因**：headless 运行环境没有配置权限 client（环境级问题，非仓库问题）。
- **修复位置**：无代码修复；用户改走 Orca 工程闭环派发真实 ZCode worker（本任务），命令全部可用。
- **证据**：runtime/progress.json 旧记录；ORCA_INSTALL_TASK_SPEC.yaml v2 diagnosis 节。

## EK-2 PS5.1 把无 BOM UTF-8 脚本当 ANSI 读（中文注释乱码破坏执行语义）

- **症状**：`start-toonflow.ps1` 运行输出与脚本逻辑明显不符：token 生成分支静默失效（输出 `token length 0`）、`ConvertTo-Json` 结果变量为空，最终 settings.json 写成 0 字节文件；server 端 `conf` 包读取时报 `JSON Parse error: Unexpected EOF`。
- **触发**：脚本含中文注释、以 UTF-8 无 BOM 保存（Write 工具默认），PowerShell 5.1 对无 BOM 文件按系统 ANSI（GBK）解码。
- **根因**：编码不匹配导致脚本语义被破坏（多字节序列被错误合并，变量赋值行被吞）。孤立 `-Command` 验证同一段逻辑完全正常，证明是文件编码问题而非逻辑问题。
- **修复位置**：所有 .ps1 以 UTF-8 **带 BOM** 保存（`[IO.File]::WriteAllText($p, $c, [Text.UTF8Encoding]::new($true))`）。本仓库 4 个 PS 脚本已全部加 BOM。
- **证据**：runtime/logs/toonflow-server.err.log（Unexpected EOF 记录）；修复后启动成功（HTTP 200，见 runtime/toonflow-data/server-info.json 与 evidence/deployment/）。

## EK-3 settings.json 带 BOM 导致 conf 包解析失败

- **症状**：server 启动即崩：`SyntaxError: JSON Parse error: Unrecognized token ''`（BOM 字符）。
- **触发**：PS5.1 `Set-Content -Encoding UTF8` 写出的 JSON 带 BOM；Toonflow `conf` 包 `JSON.parse` 不容忍 BOM。
- **根因**：PS5.1 的 UTF8 编码器默认带 BOM。
- **修复位置**：`start-toonflow.ps1` 改用 `[System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))` 写无 BOM UTF-8。
- **证据**：runtime/logs/toonflow-server.err.log 首次崩溃记录。
- **关联**：与 EK-2 同属 PS5.1 编码坑：**脚本要带 BOM，数据 JSON 要无 BOM**。

## EK-4 PS5.1 缺 `RandomNumberGenerator::Fill`（数组构造器可用——2026-10-11 repair1 更正旧错误结论）

- **症状（原始记录）**：token 生成一度为全零/为空；当时把原因记为"PS5.1 数组类型不支持 `::new(size)`"。
- **触发**：PS5.1（.NET Framework 4.8，本机 5.1.19041.6456）。
- **根因（repair1 更正，本机独立探测复核）**：`[byte[]]::new(24)` 在 PS5.1 **完全可用**（实测 Length=24，输出独立随机 24 字节）——旧记录"数组构造器不支持/静默失败"是**错误结论**，已删除。真正确定缺失的 API 只有一个：`[Security.Cryptography.RandomNumberGenerator]::Fill`（.NET Framework 无此静态方法，实测调用即 RuntimeException，与数组构造器问题互相独立）。原始"空 token"症状与 EK-2 的编码破坏（中文注释 ANSI 误读吞掉 token 分支）一致，不能归因于数组构造器。
- **修复位置**：`start-toonflow.ps1` 保持 `[guid]::NewGuid().ToString('N') ×2`（64 位 hex；.NET GUID v4 使用加密 RNG）方案不变，注释已更正为"Fill 不存在"这一真实依据。
- **证据**：2026-10-11 repair1 独立探测（本机 PS5.1）：`[byte[]]::new(24).Length=24`、GUID 链 length=64、`Fill` 调用抛 RuntimeException；协调方复核 probe 结论一致（coordinator-review-1.json R4）。

## EK-5 MCP 画布工具无页面连接时报"工具已禁用，或所需 Toonflow 页面未连接"

- **症状**：MCP `getCanvas` 等画布工具在无 UI 页面连接时报错（`工具已禁用，或所需 Toonflow 页面未连接，请重新读取工具列表`）。
- **触发**：headless server 模式没有打开的网页/桌面窗口。官方 README 明确：画布、节点、文档等界面操作需要已连接页面。
- **根因**：设计行为（页面执行画布操作，MCP 经 SSE 控制通道转发），不是部署缺陷。
- **解决路径**：用无头 Chrome（CDP）真实加载 http://127.0.0.1:3000/ 建立页面连接（connections=1），随后 openProject → addNode/getCanvasNodes/deleteNodes 全链路真实成功。注意 ZCode 浏览器后端发现机制找不到本机 Chrome 时，可直接 `chrome.exe --headless=new --remote-debugging-port=<p>` + Node 原生 WebSocket CDP 驱动（runtime/tmp/cdp-open.mjs 参考）。
- **证据**：runtime/evidence/deployment/mcp-verify-*.json（boundary 记录）与 mcp-node-test-*.json（连接后全链路成功）。

## EK-6 openProject 调用形态（避免误判为故障）

- **症状**：MCP `openProject` 带 `target:{connectionId, directory}` 报 `目标页面的工作区已切换，请重新获取 getAppState`。
- **触发**：页面在 home（directory=null）时 target.directory 与页面状态不一致，源码 resolveTarget 主动拒绝。
- **根因**：调用形态问题。正确形态：`{args:{directory}}`（不带 target，单连接自动匹配）；打开后重新 getAppState 取新 directory/canvasId。
- **证据**：runtime/evidence/deployment/mcp-node-test-*.json。

## EK-7 官方 server 主端口绑定全网卡（0.0.0.0/[::]），违反 loopback 合同

- **症状**：按官方 `start:server`（`bun build/server/index.js`）启动后，`Get-NetTCPConnection`/netstat 显示 3000 端口监听 `0.0.0.0:3000` 与 `[::]:3000`（pid 24524 实测）；协调方复核确认不能作为 loopback 通过。MCP 端口 10588 官方代码本就严格 127.0.0.1。
- **触发**：v2.0.5 `apps/server/src/index.ts` 的 `app.listen(realPort)` 无 hostname 参数（Node 默认绑定所有接口含 IPv6）。
- **根因**：官方代码无 hostname 配置点（独立源码复核确认），不是配置失误。
- **解决位置（v3 合同）**：`scripts/toonflow-loopback.ts` 薄包装——导入原生 `createApp`（`apps/server/src/app.ts` 导出），自持 `app.listen(3000, "127.0.0.1", ...)`，复用原生 `initializeMcpRuntime` 与 `agentsRoot`；不改 vendor、不自造服务。`start-toonflow.ps1` 启动后追加回环断言（发现非 127.0.0.1 监听即失败退出），`check-deployment.ps1` 持久断言 non-loopback count=0。
- **证据**：wrapper 启动后 netstat 仅 `127.0.0.1:3000`（pid 25260）；check-deployment `server-port-loopback-only PASS`。

## EK-8 stop 脚本 PID 复用风险与身份门禁（协调方发现；repair1 修复"字段缺失可绕过"漏洞并补齐真实负面测试）

- **症状**：初版三重校验对**缺失字段 fail-open**：登记记录 `entry=""`、`exe=null`、`creation_time=null` 时，对应检查被 `if ($proc.Path -and $info.exe)` 这类条件短路跳过，`Test-OwnProcess` 返回通过（协调方独立探针实测 `incorrectly_accepted=true`）。仅凭 PID+ProcessName 判断的更早版本另有 PID 复用误停风险。
- **触发**：协调方安装脚本预审（2026-10-11 dispatch 邮件）+ coordinator-review-1 R1（2026-10-11 独立 Bun 夹具探针复现）。
- **根因**：身份检查写成"字段存在才比对"，未把完整性设为前置门禁；且登记 exe/entry 未限定为本项目规范路径，任意 JSON 都能声称身份。
- **修复位置（repair1，2026-10-11）**：`stop-toonflow.ps1` 拆成两级门禁——`Test-RecordIdentity`（登记记录 pid/exe/entry/creation_time 必填、非空、可解析，且必须等于本项目规范 Bun 与 wrapper 路径）+ `Test-OwnProcess`（进程侧 exe/命令行/创建时间元数据必须可得，缺一即拒）；`start-toonflow.ps1` 复用路径 `Confirm-OwnProcess` 同规则 fail-closed。函数块用 `# === IDENTITY FUNCTIONS BEGIN/END ===` 标记，测试按标记原样提取真实函数调用。
- **负面测试（真实执行，tests/test_deployment_guards.py::GuardIdentityRealNegativeTests，9/9）**：真实自有 Bun 夹具（`bun -e` 长睡眠，fixtureB 命令行内嵌规范 entry 路径串）+ 伪造 server-info 逐项验证 e2e 拒绝：different entry / empty entry / missing exe / missing time / forged path（exe 指向 cmd.exe）/ stale time（-60s）/ unparseable time 全部 exit≠0 且夹具存活；正例（完整一致记录经真实函数直调返回通过，只读不停止）；真实记录按字节恢复，真实服务进程不受影响。旧版单点负面测试（同 bun 不同 entry）保留在 git 历史。
- **证据**：unittest 9/9 OK 输出；runtime/tmp/guard-fn-harness.ps1；coordinator-review-1.json R1。

## EK-2 补充（临时脚本同样中招）

`runtime/tmp/negative-stop-test.ps1`（Write 工具默认 UTF-8 无 BOM + 中文注释）首次运行报 `Unexpected token '}'` 解析错误——与 EK-2 同根因（PS5.1 按 ANSI 读）。加 BOM 后即正常。结论：**任何含非 ASCII 的 .ps1 都必须带 BOM**，无例外。

## EK-9 manifest 门禁初稿 fail-open：只验版本不验结构（koc-v1，Coordinator 对抗发现）

- **症状**：消费端 `scheduler/material_manifest.py` 初稿对 review.content_sha256 全零、删除 visual 字段、声明 duration=1000s/窗口999.2s 的三个对抗清单全部 accepted=1（`runtime/evidence/koc-v1/coordinator-adversarial/initial-draft-findings.json`）。
- **触发**：初稿只检查 schema_version 与少数业务门禁，没有逐字段 fail-closed 结构校验、没有 review 内容哈希绑定复核、没有对真实媒体文件的哈希/ffprobe 时长核对。
- **根因**：把"上游已审阅"当成可信输入，信任了清单自述字段（duration/review binding）而没有绑定到可验证事实（文件字节、探针时长、可复算哈希）。
- **修复位置**：重写为显式 material 候选分支——`_structural_problems` 逐必备字段 fail-closed；`entry_content_sha256` 复核 review 绑定（stale 拒绝）；真实媒体 SHA256 + `renderer.ffmpeg.probe` 实际时长约束声明时长与窗口；对抗复跑 4 例全部按预期（`runtime/evidence/koc-v1/adversarial-rerun.json`）。
- **预防规则**：任何跨仓清单/元数据导入门禁，宁可逐字段 fail-closed 也不白名单式放行；"来源自称 PASS"必须绑定到可复算内容哈希与实际媒体事实。TEST 模式只豁免入库/未跑模型限制，**不豁免哈希绑定、结构、实际时长**。

## EK-10 把合成旁白当实际画面内容（koc-v1，Coordinator 抽帧发现）

- **症状**：干跑案例初稿的旁白写"包装盒/标签/封口"，但 a1 实际画面是手持火龙果剖开、白色果肉与黑籽（`runtime/evidence/koc-v1/coordinator-a1.jpg`）；合成旁白若被当作内容匹配正例即成伪证据。
- **根因**：案例旁白按 USE 分类模板生成，未先核对参考媒体真实可见内容。
- **修复位置**：case.json 旁白全部改为画面真实可见内容（果子外观/剖开/果肉/籽粒），facts 登记 Coordinator 抽帧证据；A6 正例链路只用真实可见旁白+metadata，合成类目覆盖只留在结构/规则单测。
- **预防规则**：内容匹配正例必须来自对实际素材的观察证据；"合成测试输入"标签不能洗白内容匹配声明。

## EK-11 混源证据哈希误归因（koc-v1，实现中自查）

- **症状**：初版桥接把 manifest 窗口并入同资产 Narrato coverage，候选 evidence 哈希取自 Narrato 引用——manifest 窗口被错误标成 Narrato 证据。
- **根因**：shot-library 候选的 evidence 字段按 asset 粒度生成，两个来源共用一个 ref 时无法表达双来源。
- **修复位置**：改为显式 material 候选分支（mcat- 前缀、frame_count=0、清单哈希作 evidence），analysis_refs 只读不动；不合成 coverage(status=success)/帧观察。
- **预防规则**：跨来源数据进同一原生结构时，来源必须保留在机器消费结果里（id 前缀/独立字段），不能靠事后 report 文字说明。

## EK-12 质检证据只验文件哈希，不解析内容是否对应当前视频（koc-v1 repair-1，Coordinator 对抗发现 R1）

- **症状**：`wrong-video-quality-binding.json` 的报告 `wrong-video-metrics-report.json` 内 `source_sha256=全零`（不是 a1 清单媒体），条目 vbench.status=pass 带匹配的证据文件哈希，production 导入仍 accepted=1。
- **触发**：Coordinator 独立反例复跑（`runtime/evidence/koc-v1/coordinator-adversarial/coordinator-final-review-findings.json`）。
- **根因**：消费端门禁只检查"证据文件存在且 SHA256 == metrics_evidence_sha256"——证明的是**字节没变**，不是**报告内容属于当前视频**。任何人可以对任意 JSON 先算哈希再填进清单，报告与媒体、指标、结论之间的绑定完全缺失。
- **修复位置（repair-1）**：`video_postprod/scheduler/material_manifest.py` 新增 `_load_evidence_json` + 归一化 metrics 证据合同（文档化于 `schemas/material-manifest-v1.schema.json → $defs.metrics_evidence`）：vbench pass 必须解析证据 JSON 并要求 `source_sha256 == 条目媒体哈希`、`status=='pass'`、`metrics == vbench.metrics`（声明与结果逐键相等）；报告若声明 material_id/version/window 则必须与条目一致。任意字节/错视频哈希/错指标/failed/not_run 报告全部拒绝（QUALITY_REVIEW_INVALID_STATUS + detail）。正例单测夹具显式标注 UNIT ONLY，非真实 VBench 分数。
- **预防规则**：证据文件哈希只防篡改，不防张冠李戴；跨文件绑定必须解析内容并核对绑定键（媒体哈希/条目/版本/窗口）。

## EK-13 生产资格把"自述 real_world_recording + not-applicable + 干净标记"当视觉审阅（koc-v1 repair-1，Coordinator 对抗发现 R2）

- **症状**：`missing-production-visual-evidence.json` 没有任何视觉审阅证据文件、`product_identity_verified=false`，仅靠 origin=real_world_recording + applicable=false + occlusion=none + 空 quality_flags 即 accepted=1。
- **根因**：生产资格分支把 not_run+not-applicable+real_world 的**清单自述字段组合**当成"干净的实拍豁免"，视觉审阅与身份核验没有任何独立证据文件——自声明标记替代了实际审阅。
- **修复位置（repair-1）**：`video_postprod/scheduler/material_manifest.py` production 资格分支改为双证据硬门禁：(1) 每条生产资格条目必须携带 `evidence.visual_review_path + visual_review_sha256`，文件存在、哈希一致，且解析后满足归一化 visual-review 证据合同（文档化于 schema `$defs.visual_review_evidence`）：`source_sha256==条目媒体哈希`、`material_id/version/window==条目当前值`、`verdict=='pass'`、reviewer/checked_utc 非空——vbench not-applicable 声明不再等于视觉审阅；(2) `provenance.product_identity_verified` 必须为 true（身份未验证 = 显式保持 blocked）。缺失/过期版本/错窗口/错视频哈希全部拒绝（PRODUCTION_ELIGIBILITY_EVIDENCE_MISSING）。本轮不伪造任何人工审阅证据：现有 H3 演示素材保持 TEST/blocked，不因新增合同被晋升。
- **预防规则**：质量豁免不能来自被审对象自己的字段组合；豁免必须指向**独立证据文件**且绑定到当前媒体/条目/版本/窗口。

## EK-14 消费端只校验条目 Schema，忽略 Manifest 根合同；非对象条目 AttributeError（koc-v1 repair-1，Coordinator 对抗发现 R3）

- **症状**：`invalid-manifest-header.json` 删除根字段 created_utc/source_case 并携带未知根字段 `unexpected_field`，导入仍 accepted=1；手工把 entries 换成含非对象元素时在 `entry.get(...)` 处 AttributeError 崩溃而非显式拒绝。
- **根因**：`load_manifest` 只手查 4 个根字段类型；条目级有完整 Schema 校验，根级从未过 Schema（`_load_schema` 只取 entries.items 子模式）。导入循环先 `bound_shas.add(entry.get(...))` 后做 isinstance 检查，非 dict 条目在检查前就崩溃。
- **修复位置（repair-1）**：`load_manifest` 增加**根合同校验**（根 Schema 校验器，entries 仅约束为非空数组——条目内容仍留给逐条校验以保住失败隔离）；缺失必填根字段/未知根字段 → `MANIFEST_STRUCTURE_INVALID` 整单拒绝。导入循环把非对象条目检查前置：非 dict 条目显式记 `MANIFEST_ENTRY_INVALID`（detail=entry_not_object）并继续处理其余合法条目，不再崩溃。
- **预防规则**：跨仓清单校验必须覆盖**根+条目两级**；对不可信数组元素先验类型再取字段，失败隔离不能靠异常吞噬。

## EK-15 通用商品输入残留火龙果用例硬编码（koc-v1 repair-1，Coordinator 对抗发现 R4）

- **症状**：咖啡旁白"打开咖啡包装，展示咖啡粉。"得到的动作是"手扶住果子/对半切开果子"，Review 判 PASS；导出条目还带硬编码 `火龙果` 关键词、H3 来源 provenance、"无人出镜（演示画面）"人物声明与 768x1376 技术字段。
- **根因**：`product_storyboards.py` 的 ACTION_CHAINS/FRAMING_TEMPLATES 把火龙果演示案例的具象动作/取景写成了所有用途的**无条件模板**；`adapters/material_manifest.py` 导出把案例专属值（关键词/H3 provenance/人物/尺寸）写死为默认。用例特例渗入通用路径。
- **修复位置（repair-1）**：(1) 动作/取景改为**输入驱动**——case narration_segments 逐段显式 `action`（可选 framing/camera/movement）提示，火龙果具象选择移入其 TEST case.json；无提示的段落不再发明动作（占位 + review 码 `ACTION_HINT_MISSING`，minimal_fix=补输入）；(2) review 新增 `ACTION_OBJECT_MISMATCH`（有界词表：动作含果子类对象词而旁白/产品名/subject_term 均无 → RETURN——咖啡旁白+切果动作必被抓住）与 `NO_FITTING_REF_FOR_PURPOSE`（无职责匹配 ref 时 RETURN，不再盲回退到无关 ref）；(3) 导出全部改为 case 声明值（keyword_tags/person_appearance/location_note/provenance/技术字段来自 fixture_technical），未声明即拒绝导出（CASE_PROVENANCE_MISSING）/如实"未声明"，TEST 导出器显式拒绝 test_flag=false（PRODUCTION_EXPORT_UNSUPPORTED）；(4) 修复兜底旁白按 subject_term 参数化。审查新码已进 `config/koc_material_policy.json → review_rules`（源绑定 script-and-shots.md §2 / production.md §2）。
- **预防规则**：演示用例的具象内容只能留在用例输入里；通用路径对派生不出的内容要 RETURN 并给出最小补输入建议，而不是套模板 PASS。有界词表检查只覆盖已演示的对抗类别，不是语义理解。

## EK-16 并发外部任务把消费端推进 r3 语义（本轮所有权外，如实登记不修改）

- **症状（2026-10-11 repair-1 复跑基线）**：consumer 焦点套件 16 failed / 35 passed、director_lab KOC 套件 4 failed；`scheduler/service.py:32` 缩进 SyntaxError（**根因更正（2026-10-11 repair-2，Coordinator msg_7f0da64781ad：实读源码与 pytest 一致为 `elif` 缩进错误；旧记录"UTF-8 BOM 被 Python 当非法字符"为误判，BOM 不产生 SyntaxError）**）；`shot_library.py` 产出 `gaps`/`split_fruit` 等超出已交付 shot-library/v1 Schema 的字段/枚举；`shot_planner.py` 新增 `LIBRARY_RULE_VERSION_STALE` 门禁（要求 semantic-actions-v1）；`script_plan.py` USE_KEYWORDS/USE_PRIORITY 重写（open_box 拆为 open_packaging/split_fruit 动作语义）。
- **归属**：另一活跃桌面任务「完善双项目剪辑衔接」（Coordinator 2026-10-11 12:22 接续确认；此前 §KOC_INTEGRATION_REPORT 4-11 已登记其第一轮）。其代码/合同/测试不在本轮 ownership，**保留、不修复、不弱化**。
- **本轮交互面处理**：KOC 桥接此前**委托** `script_plan.classify_visual` 分类用途，漂移后产出合同外 use（split_fruit）导致 mcat 候选进不了已交付合同——repair-1 起桥接改用**已交付 v1 词表本地分类**（快照，双仓一致性由测试锁定），不再消费漂移模块的实时输出；director_lab 侧 USE_KEYWORDS 同样定位为已交付 v1 快照（与消费端桥接表一致）。TEST 闭环链路测试的旁白改为 r3 分类器可解的展示句（动作句在 r3 需 attested runs/确认窗，属外部任务的新证据要求，不是本轮素材桥接的门禁弱化）。完整闭环回归在外部任务收敛其自身 Schema/测试前**保持失败并如实上报**。
- **证据**：`runtime/evidence/koc-v1/repair-1/`（resume-consumer-precheck.txt、baseline-director-lab.txt、repair1-baseline.json external_drift_hashes、drift inspection 脚本输出）。

## EK-17 非哈希 source_sha256 在任何验证之前触发 set.add 崩溃（koc-v1 repair-2，Coordinator 对抗发现）

- **症状**：`import_material_manifest(…, mode='test')` 对两条 Coordinator 对抗 TEST 清单分别抛 `TypeError: unhashable type: 'list'`——`coordinator-nonhashable-entry.json`（首条目 `source_sha256=[]`）崩于导入循环的 `bound_shas.add`；`coordinator-nonhashable-nested.json`（首条目 `audio.policy=[]`、其余字段有效且声明 SHA 合法）崩于 `_structural_problems` 的 set 成员运算。两例均非显式拒绝，且拖垮同单 20 条合法兄弟条目。
- **触发**：`runtime/evidence/koc-v1/repair-1/coordinator-nonhashable-entry.json`（sha256 642d468c…）与 `coordinator-nonhashable-nested.json`（sha256 cd8e1424…，msg_c4748bb729f7 追加的嵌套精确反例；其余条目均为合法 TEST 条目）。
- **根因**：两处对不可信条目字段值的**不安全类型操作先于完整 Schema 校验**：(a) 导入循环在逐条目校验前执行 `bound_shas.add(entry.get('source_sha256'))`（EK-14 只前置了非对象条目检查，对象条目的字段值未先验形状就进集合运算；null/数字虽可哈希但会被错误登记进 bound_shas）；(b) 手写结构遍历对嵌套容器值（audio.policy 等 enum 集合成员判断）做 set 运算，嵌套 list/dict 未过 Schema 即触发。
- **修复位置（repair-2）**：`video_postprod/scheduler/material_manifest.py` 导入循环，系统性两步（Coordinator msg_c4748bb729f7 指示：完整 Schema 拒绝先于一切不安全类型操作，不逐字段补丁堆叠）：(1) `bound_shas.add` 前先做声明 SHA 形状校验（`isinstance(str)` + `$defs/hash` 的 `^[a-f0-9]{64}$` 全匹配）才登记——**合法形状的声明 SHA 即使本条目随后被拒也保持绑定**（bound-source 保护不弱化：被拒素材不得经 Narrato 分析回流），非哈希值（list/dict/null/数字）不崩溃、不进 bound_shas；(2) 逐条目**完整 JSON Schema 校验前置于手写结构遍历**——jsonschema 对任意 JSON 值全域安全，而结构遍历含对不可信嵌套值（audio.policy/vbench.status/technical.duration_source/generated.status/provenance…）的 set 成员运算，未过 Schema 的嵌套非哈希值会在其中 TypeError。Schema 违规条目按既有 `MANIFEST_ENTRY_INVALID` 拒绝（detail=schema:<字段路径>:<首错>，可诊断），结构遍历只接待 Schema 合格的容器；合法兄弟条目继续处理。
- **预防规则**：不可信清单的字段值在进入任何哈希/集合/字典键运算前先经完整 Schema 校验（类型+形状一次判定）；失败隔离与绑定保护是两条独立不变量，修复一个不得牺牲另一个。
- **证据**：`runtime/evidence/koc-v1/repair-2/`（pre-fix-repro.txt 双文件崩溃复现——entry 文件崩于 bound_shas.add、nested 文件崩于 _structural_problems set membership；evidence-import-result.json 修复后双文件导入=各 20 候选/拒绝 1/bound a1+a2；三个测试套件输出）；消费端单测新增 list/dict/null/数字 SHA 负例 + 嵌套非哈希负例（audio.policy 等七处容器值）+ 双证据文件逐字复现 + 恶形条目带合法 SHA 保持绑定/Narrato 回流阻断测试（tests/test_material_manifest.py）。

## EK-18 把参考视频喂给 z0903 / 把"必填 ref_audio_0"当声音授权（h3-autodl-v1，合同事实）

- **症状（类别一，视频引用不匹配）**：直觉上"拿 a1 演示片段当 H3 参考视频"没有对应入口——live schema `input_rules` 全部字段= prompt/ref_image_0..5/ref_audio_0..2/duration/resolution/seed，**无任何 video 类型或 ref_video 字段**；若在提示词里使用 H3 技能（h3-prompt-writing）通用 `<Video N>` 标签，属于能力误判：技能的 Ref2VA 通用格式支持 Video 标签≠当前 z0903 工作流支持视频输入。
- **症状（类别二，必填音频语义混淆）**：静默无口播 B-roll 仍必须提供 `ref_audio_0`（required=true）；把官方 default blank.wav 占位当成"声音参考/口播授权"或反过来以为"静默=可省略音频"都是错的。
- **触发**：h3-autodl-v1 接入时对参考素材（a1/a2 为 mp4 演示片段）的自然处置倾向；live 工作流定义 2026-09-15 版（GET 2026-10-11 复核）。
- **根因**：z0903 为"六图三音频"工作流，接口面不含视频输入；必填音频是接口合同，与内容是否需要声音无关。
- **修复位置**：`adapters/h3_autodl.py`——`assert_no_video_reference()` 对任何 data:video/视频扩展名 API 引用显式拒绝（`VIDEO_REFERENCE_UNSUPPORTED`，不做隐式抽帧/转码）；视频素材唯一合法用途=受登记的**只读单帧抽取**作 TEST 视觉参考（`extract_frame`，源 SHA+时刻+用途登记）；`doctor()` 报告 `video_reference_supported=false` 并列出 `video_inputs_found=[]`；静默请求以 live schema 声明的官方 blank.wav URL 填 ref_audio_0，提示词内显式声明"占位非授权、生成音频下游丢弃"（`spoken_locked` 保持）。
- **预防规则**：接口能力以 live schema 实测字段为准，通用技能文档的标签能力不构成当前工作流支持证明；必填占位与内容授权分开声明。
- **证据**：`runtime/evidence/h3-autodl-v1/doctor-2026-10-11T055349*.json`（video_inputs_found=[]、required_fields 含 ref_audio_0、blank_audio_default）；`tests/test_h3_autodl.py::test_video_references_rejected_clearly`。

## EK-19 h3-autodl-v1 实现期三处真实缺陷（ROOT 深度/语义反转/宽松授权类型；Coordinator inflight 对抗发现+自查）

- **症状**：①执行实例装载报 `H3ADAPTER_ROOT_BINDING_FAILED`（ROOT 落到 `runtime\private` 而非 `runtime`）；②doctor 报告 `video_reference_supported: true` 而 live schema 实际无视频字段（语义反转，与 EK-18 报告字段直接相关）；③`guarded_submit` 对畸形授权单元放行（Coordinator 探针 `coordinator-inflight-findings.json` 记录 `malformed_auth_unit: accepted`）。
- **触发**：2026-10-11 实现中三处独立失误：①虚拟 `__file__` 少算一层目录——原 Runtime `ROOT=Path(__file__).resolve().parents[3]` 要求文件恰在 ROOT 下 4 层（app/runtime/h3/laox_h3.py），镜像路径多了一层 `h3/`；②`'video_reference_supported': not video_inputs` 命名与取值方向写反；③授权校验用 `!=1`/`isinstance(int)`——`1.0`（float）、`True`（bool 是 int 子类）、`-1`（负数）全部绕过。
- **根因**：①对 parents 索引与目录深度未先断言验证；②布尔命名在"否定语义"字段上极易写反；③Python 数值类型等值陷阱（`1.0==1`、`isinstance(True,int)`）出现在授权门禁这类必须精确类型的边界。
- **修复位置**：`adapters/h3_autodl.py`——①`VIRTUAL_FILE=EXEC_ROOT/'private/app/runtime/laox_h3.py'`（4 层）+ 装载后 ROOT 等值断言（fail-closed）；②改 `bool(video_inputs)`（空=不支持）并同步 CLI doctor 判定（必须 False）；③`_validate_authorization` 全字段 `type() is int/float` 精确类型（bool/float/字符串/NaN 全拒，calls_used 负数拒绝）。
- **预防规则**：跨 ROOT 重绑定必须装载后断言目标路径而不是信任构造；语义为"能力不存在"的布尔字段命名要能直读（或直接输出证据列表）；授权/计费等安全边界的数值字段一律精确类型检查，禁止 `==`/`isinstance` 宽松比较。
- **证据**：`runtime/evidence/h3-autodl-v1/coordinator-inflight-findings.json`（修复前探针）；`tests/test_h3_autodl.py::test_private_instances_root_bound_and_pinned / test_submit_refuses_malformed_numeric_units`（修复后 22/22 绿）；`post-change-hashes.json`（老X Runtime 源码 pin 未变）。

## EK-20 适配层三处范围性设计缺陷：抽帧不锁源/授权写入无目录门禁/凭据与工具断链（h3-autodl-v1，Coordinator follow-up 批次发现）

- **症状**：①`prepare` 对任意 case/shot 的首个 ref 自动 `extract_frame`，`extract_frame` 也接受任意 source/out_png 路径——TEST 抽帧越出"唯一允许的 a1 显式授权"范围，可能对任意用户视频隐式转换后宣称兼容；②`guarded_submit` 末尾 `auth_path.write_text` 无目录校验，可写 lab 外任意用户路径；③执行实例 ROOT 切到 lab 后，其内部 `settings()`/`tool()` 会查 `lab/app/api/autodl.env` 与空 runtime.local.json——凭据与工具解析断链，初版以"要求用户重新导出 AUTODL_ART_TOKEN"打补丁，违背"复用既有 resolver"的 TaskSpec 要求。
- **触发**：Coordinator 2026-10-11 follow-up 批次（msg_d5c3c57d8a68 / msg_697247b038aa / msg_8fb3c423b251）对初版源码的静态审阅。
- **根因**：把"抽帧 helper"写成了通用工具而不是 pinned 单源证据操作；授权记录写回未沿用与 Runtime within() 同级的路径门禁思想；双实例设计只重绑了 ROOT，没有把 settings/tool 的依赖也一并注入，导致用环境变量要求补凭据的错误补丁。
- **修复位置**：`adapters/h3_autodl.py`——①`AUTHORIZED_FRAME_SOURCE` 精确 pin a1 路径+SHA：URL/data URI、非 a1 本地视频、SHA 不符、越界/非 PNG/等于源文件的输出路径、非法帧时刻（非有限/越界）全部在 ffmpeg 运行前拒绝（`TEST_FRAME_SOURCE_NOT_AUTHORIZED`/`TEST_FRAME_OUTPUT_ESCAPE_REFUSED`/`FRAME_TIMESTAMP_INVALID`），ffmpeg 30s 有界超时且超时清理半成品；②授权记录 resolve 后必须 `is_relative_to(AUTH_DIR)`，先于任何读/写/调用拒绝（`AUTH_RECORD_PATH_UNAUTHORIZED`）；③`runtime_exec()` 对**仅私有执行实例**显式注入 `settings=resolver.settings`、`tool=resolver.tool`（两实例均不注册 sys.modules，不改共享模块、不复制凭据、不持久新 .env，原环境覆盖优先级原样生效），删除环境 token 强制要求——submit 时由原 Runtime `settings(require_token=True)` 自行取值校验。
- **预防规则**：证据类只读操作（抽帧/探针）必须 pin 到精确授权对象+SHA，输出目录强制 resolve 校验；对不可信路径的写回与读入同级设目录门禁；跨 ROOT 复用模块时，除 ROOT 外还必须审计其内部绝对路径依赖（凭据/工具/锁）并显式注入，不得用环境变量让用户补管线自己的断链。
- **证据**：`tests/test_h3_autodl.py::test_frame_extraction_pinned_to_authorized_a1 / test_submit_refuses_authorization_outside_auth_dir / test_exec_instance_delegates_settings_and_tool_to_resolver`（26/26 绿）；`runtime/evidence/h3-autodl-v1/re-verification.json`（三项 inflight 发现对最终源码的复验+最终 SHA）。

## EK-21 预算门禁只校验文本依据长度——未知报价与超额报价都被接受（h3-autodl-v1 repair-1，Coordinator 复核对抗发现）

- **症状**：Coordinator 纯校验器探针（`coordinator-budget-findings.json`，对初版终源 fb4ccbb）两条反例全部放行：case `unknown`（budget 0.01 CNY、estimate_cny=null——**估价未知**）与 case `underbudget`（budget 0.01 CNY、estimate 80 CNY——**估价超预算 8000 倍**）均 outcome=accepted。即付费授权门禁实际既不知道要花多少钱，也不检查预算是否够花。
- **触发**：初版 `_validate_authorization` 对 `cost_estimate_basis` 只检查"是非空字符串且长度≥8"——一段自述文本即满足"可核验估价依据"；无任何数值估价字段、无请求/工作流绑定、无估价与预算比较。live schema `price=0` 与 billing_config 价目（如 70）的货币/计价单位/粒度均未验证，本就不能换算成 CNY（见 module.yaml h3_adapter 注记）。
- **根因**：把"授权依据存在"错当"费用已知且被预算覆盖"——文本字段满足了"人写过依据"的形式检查，却没有任何机器可比对的数量约束；同时旧的正面测试只断言"缺文本拒绝"，从未行使真实费用边界（数字估价与预算的关系）。
- **修复位置（repair-1）**：`adapters/h3_autodl.py`——新增 `_validate_cost_estimate()` 结构化报价合同并接入 `_validate_authorization()`：授权记录必须携带 `cost_estimate` 对象：`status=human_confirmed_platform_quote`（仅信可信协调方登记的人工确认平台报价，未知/本地估算 status 拒绝）、`currency=CNY`、`unit=CNY_per_single_call`（计价粒度未验证，禁止 per-second/per-video 换算猜测）、`estimate_cny` 为精确类型有限正数（bool/str/NaN/inf/零/负全拒，null=未知=拒绝）、`workflow_id` 与 `request_fingerprint` 双绑定本请求（错绑 `AUTH_ESTIMATE_MISBOUND`）、`provenance.recorded_by/source/recorded_utc` 报价出处；并强制 `estimate_cny <= budget.max_total`（超出 `AUTH_BUDGET_INSUFFICIENT`），全部在网络/状态变更前拒绝。旧"仅文本依据"记录从此不再能授权（fail-closed）。
- **信任边界（诚实声明）**：记录校验只验证**形状与绑定**，不能证明平台实际计价、不能证明所指人类确实同意——真实报价须由可信协调方带 provenance 带外登记；billing_config 价目货币/单位/粒度未验证，永不换算。本地估价门禁+单次调用绑定是**本地能力**，不是平台侧硬计费上限承诺。
- **预防规则**：付费门禁的"金额"必须是机器可比对的结构化数值（精确类型+有限+正数+绑定+与预算的关系），文本依据只能作补充叙述不能作数量证明；"schema price=0"或任何未验证货币/粒度的价目都不是免费或定价证据。
- **证据**：`runtime/evidence/h3-autodl-v1/coordinator-budget-findings.json`（修复前两条反例 accepted）；`tests/test_h3_autodl.py::test_submit_refuses_unknown_or_overbudget_quote`（root 双反例修复后拒绝且零委托）/ `test_submit_refuses_malformed_or_misbound_estimate`（19 项畸形/错绑负例+旧文本记录拒绝）/ `test_submit_delegates_once_with_sufficient_synthetic_quote`（足额合成报价经 fake runtime 恰好委托一次，UNIT ONLY）；`runtime/evidence/h3-autodl-v1/repair-1/`（终跑实测记录与套件计数/文件哈希——计数为当轮实测快照，koc 侧为外部并发任务漂移值，非固定事实）。

## EK-22 AutoDL 无视频参考 vs 官方 Ref2VA：供应商能力缺口与双通道边界（h3-minimax-official-v1，合同事实+实现期教训）

- **症状（供应商能力缺口）**：用户需要"全能视频参考"（Ref2VA——视频+可选图/音真实参考），但 AutoDL z0903 工作流 live schema 无任何视频输入字段（EK-18 已证）；通用 H3 技能文档的 `<Video N>` 标签能力不构成当前 AutoDL 工作流支持证明。真实视频参考必须走官方 MiniMax 直连 V2 API。
- **触发**：2026-10-11 用户明确授权"全能视频参考接入官方的 minimax"（中国站 minimax.cn 确认），Orca task_fb19c1d1ef72。
- **根因**：AutoDL 是工作流编排层（字段由工作流定义决定），官方直连是供应商原生 API（content 显式 reference_image/reference_video/reference_audio 角色块）——两者是不同供应商合同，不能靠提示词或字段映射互通；凭据、域名（api.minimax.cn vs autodl 域）、计价（官方公布 CNY 价目 vs AutoDL billing_config 未验证价目）也完全不同。
- **修复位置**：`adapters/h3_minimax_official.py`——独立第二供应商通道，与 `adapters/h3_autodl.py`/老X Runtime 零导入零改动（测试锁定 AutoDL 源码哈希不变）；契约/价目快照唯一事实源 `config/minimax_official.json`（policy hash 绑定报价与付费门禁）；请求合同严格校验（model 固定 MiniMax-H3、content 角色块、first_frame/last_frame/mixed 与额外字段拒绝、duration 精确 int 4..15、resolution 768P/2K、ratio 八值、prompt ≤7000、Ref2VA ≥1 真实参考、AutoDL 字段名泄漏拒绝）；本地媒体先验真（ffprobe 30s 有界、元数据全有限）再 Base64 DataURI（≤64MB 预检）；URL 参考显式 not_implemented（不做任意远端抓取/外部上传）；付费门禁=授权记录（human_authorized/provenance/provider+model+base+指纹+参考哈希+价目哈希/max_calls=1/预算≤200/新鲜数值估价≤预算且与本地复算一致）+跨进程指纹排他锁+传输前不确定标记（超时/网络错误/含糊响应保留阻断重提、零重试含 429）。
- **实现期真实教训（同轮发现并修复，未留过夜）**：① ffprobe JSON 的 `format.duration` 是**字符串**（如 '6.583333'），数值有限性校验若不先做 str→float 转换会把合法媒体拒成 MEDIA_METADATA_INVALID；② 单测里 query 路径误用真实 transport，向 api.minimax.cn 发出过一次带合成假 key 的只读 GET（401）——UNIT 测试必须对每个网络边界显式 set_transport(stub)，不能只 patch 被 CLI 传参的那条路径；③ 授权 `calls_used: True` 因 `True == 1` 会被选成 AUTH_CALLS_EXHAUSTED 而非 AUTH_RECORD_INVALID——错误码选择也要精确类型判断（EK-19 教训在新通道复现一次）。
- **预防规则**：不同供应商=不同合同（端点/字段/凭据/计价），绝不复用或映射字段名，绝不混用凭据通道；外部 JSON 探针输出的"数字"字段先看实际类型再校验；测试中所有网络边界（哪怕只读 GET）都必须走注入的 stub；诚实边界保持——认证/付费/产出在 key 与人工授权就位前一律 not_run，报价是公布价目计算非账单担保。
- **证据**：`runtime/evidence/h3-minimax-official-v1/`（doctor 离线报告 credential=false/auth not_run、prepare 真实 a1 本地构造指纹 3a1845be…/报价 5.79 CNY 含输入视频秒数/零 HTTP 零上传）；`tests/test_h3_minimax_official.py`（31 passed + 1 skipped：Windows 符号链创建权限不可用主机的诚实跳过）；76 项既有回归全绿。

## EK-23 z0903 音频锁定序列化器无法接受 z0902 image-only 合同——活跃路由静态重构而非改常量（h3-autodl-z0902-integration，合同事实+静态诊断）

- **症状（静态不兼容，非运行时故障）**：用户选择 AutoDL `minimax_h3_z0902` 工作流（image-only：ref_image_0 必填 + ref_image_1..5 可选，无任何音频/视频输入，分辨率八档含 1088p竖(1088*1920)，duration 1..15 默认 5）。既有 `adapters/h3_autodl.py`（h3-autodl-v1）与钉死 oldX Runtime 的整条提交链为 z0903 合同：仅改 `WORKFLOW` 常量无法复用，会提交不兼容字段。
- **触发**：2026-10-11 用户直接请求 z0902 对接（Orca task_8c5482b0a195），并明令"你只是对接，不允许测试"——本轮只做静态文件实现，零测试零调用。
- **根因（逐行源码证据，laox_h3.py SHA256 8b173b0e… 只读核对）**：① `settings()` L56 硬拒任何 `AUTODL_WORKFLOW_ID != minimax_h3_z0903`（"此模板仅适配已核实的 z0903 工作流"）——工作流覆盖在 settings 层即 fail；② `RESOLUTIONS` L9 只有 `768p竖(768*1376)`，`load_project` L160 与 `validate_schema` L402 双处硬拒其它分辨率——1088p竖 不可能通过；③ `references()` L302 要求 audio 素材 `1<=count<=3` 必填、`render_x_prompt` L321 强制 `<Audio 1>` 标签——image-only 请求在渲染层即被拒；④ `prepare_bundle` L390 固定序列化 `seed`+`resolution`+渲染 prompt，且指纹 `digest(encode({'workflow':WORKFLOW,'body':body}))` L392 绑定 z0903 常量；⑤ duration 上游 `load_project` L171 锁 4..15，lab 旧范围 4..12，与 z0902 合同 1..15 默认 5 不一致。结论：z0903 的 submit/settings/prepare_bundle/fetch_schema/validate_schema 整链不可路由 z0902；唯一安全复用点是独立低层 `http_json`（L72-92，不读 settings、不绑 workflow）。
- **修复位置（静态实现）**：`adapters/h3_autodl.py`——活跃 z0902 路由：`build_z0902_request()` 只序列化 prompt/duration/resolution(默认 1088p竖 显式)/可选 seed(缺省省略)/独立可选槽位（ref_image_0必填、1..5各自选填且允许间隔，保留编号），精确类型检查（bool/float 拒绝）、未知/音频/视频字段拒绝；`compute_z0902_fingerprint()` 以同构 canonical JSON 绑定 `workflow=z0902+请求体`（与 z0903 指纹空间结构性隔离）；未来 `submit_guarded_z0902()` 先查禁用开关（network/generation/dry_run，先于任何状态/网络），AutoDL 配置仅从 `AUTODL_ART_BASE_URL`/`AUTODL_ART_TOKEN` 环境变量解析（域限 autodl.art 官方 HTTPS，永不读 oldX autodl.env），授权记录新 schema `lab-h3-submit-authorization/v2-z0902`（z0903 历史记录结构上不可命中），传输仅经 lab 私有实例复用 Runtime `http_json`；workflow 不一致 fail-closed 无回退。旧 z0903 函数整段保留为 HISTORICAL，`LEGACY_Z0903_ENABLED=False` 公共入口先拒绝，激活 CLI（`scripts/h3-materials.py`）只暴露 z0902 路径。
- **预防规则**：工作流 ID 不是可调参数而是贯穿 settings/校验/序列化/指纹的合同绑定——换工作流必须逐行核对整链锁定点，禁止"改一个常量就复用"旧提交函数；跨工作流复用只允许无状态低层传输（如 `http_json`），且必须新指纹+新授权 schema 结构性隔离历史授权；分辨率/时长/素材数量范围以当前用户合同为准，不继承旧 lab 操作上限。
- **运行时状态（诚实边界）**：NOT_RUN_USER_PROHIBITED——本轮零测试、零 import、零 GET、零提交；z0902 服务器合同未经任何验证（USER_SUPPLIED_NOT_SERVER_VERIFIED），z0903 历史 GET 证据不构成 z0902 事实；未来真实调用需用户新授权。静态映射证据：`runtime/evidence/h3-autodl-z0902-integration/static-implementation-report.json`。

