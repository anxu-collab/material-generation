# 当前工作计划 — product-material-pipeline-v1

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

长期入口 `scripts/material-flow.py::_require_shot_review`。证据和真实会话在 `runtime/evidence/home-image-closure-20261015/`，图片在 `output/laox-stand-home-20261015/`。当前接续只看本节。下面的勾选和日期补记是更早的工作记录，不覆盖这次暂停。

## 当前接续顺序

- [x] 2026-10-13 制定“实际视频分析、标准素材编目与调度”计划；沿用本地素材格式、统一命名与标签。
- [x] 实施 4 个计划内新源文件、7 个 catalog 命令与素材库写入保护；不修改旧桥接合同。
- [x] 29 项定向测试＋3 项相关分发回归通过；真实 MP4 读取/6 帧采样通过；取消与发布问题修复后独立复核 PASS。详见[验证报告](../../../runtime/evidence/material-catalog-scheduler-v1/report.md)。
- [x] 2026-10-14 整理已有更改、本会话改动、证据和未完成项；6 个代码/配置文件与最终测试哈希一致。本次整理未重新运行测试。
- [ ] 用户已请求一轮真实模型分析：先核实并接入多模态传输，再对 1 条已有素材实际调用、核对真实画面与名称标签。尚未执行，配置仍关闭；不重复询问是否执行，只处理缺失的技术前提。
- [ ] 用户已明确“拍拍”是微信“拍一拍”触发机器人。机器人项目位置待提供；真实分析通过后启动和设置。未启动、未发消息、未配置。
- [ ] 生产 catalog 回填与后续小批尚未执行，不自动扩大到全库。

独立维护待办：motion/pace 角色验证；完整因果链方案与像素审核（lxc-home1 needs_rework，tyq-v4 仅方向验证）；commodity/KOC 历史修复闭环；VBench/3D；文件预算及模块拆分。历史 worker_done=false 不证明任务当前仍在运行。

## 主入口与代码职责

| 入口或模块 | 当前职责 | 后续处理方向 |
|---|---|---|
| `scripts/material-flow.py` | 日常编排：文本角色、云 Agent 同步、创作/审核、图片门禁、视频批次、素材发布；2218 行、72 个顶层函数 | 优先明确内部边界，再逐块抽取；保留 CLI 与数据格式 |
| `adapters/commodity_ai.py` | Claude/Clef 适配、严格合同、审计与素材存储；3304 行 | 与 material-flow 区分适用范围，避免两个默认入口 |
| `scripts/commodity-materials.py` | commodity-material-ai 专用 CLI 与桥接 | 保留既有调用者；是否收敛到主入口另立任务 |
| `adapters/h3_autodl.py` | AutoDL z0902；保留冻结的 z0903 历史实现 | 不与官方供应商混用，不自动回退 |
| `adapters/h3_minimax_official.py` | 官方 MiniMax 独立通道 | 独立授权、独立合同；不因旧说明自动调用 |
| `adapters/seedream_image.py` | 生图与批量授权台账 | 保留限额、先登记后调用及失败记录 |
| `agents/ark_profiles.py`、`agents/skills/` | 五个文本角色与行业规则 | 现行编辑入口；云端同步不是本地文档修改的自动动作 |
| `agents/koc_rules.py`、`agents/product_storyboards.py` | KOC 参考与确定性本地规则规划 | 规则输出与真实模型输出分开标识 |
| `adapters/material_manifest.py`、`adapters/storyboard_team.py` | 旧清单合同与 Toonflow 桥接 | 保持兼容，不把 TEST 清单视为可生产素材 |
| `scripts/start-toonflow.ps1` 等 | 服务启停与部署检查 | 保留历史部署证据；本次不启停或验证服务 |

行数与函数数来自 2026-10-13 的历史静态读取，后续代码已有变化；此处数字不作为当前盘点。原记录，只用于定位维护范围，详见[代码清点](../../../runtime/evidence/project-organization-20261013/code-map.json)。

## 结构问题与可观察的完成条件

| 优先级 | 现状 | 后续改造与验收条件 |
|---|---|---|
| P1 | material-flow 顶部说明仍固定写 Opus/Doubao/Sonnet，实际默认通道已是 ark_agent | 统一 CLI 文案与实际配置；五角色路由逐项留证，不改变模型或认证 |
| P1 | progress 的多个历史段落会与顶层当前状态混淆 | 后续只更新当前摘要，历史段带日期保留；旧授权不被摘要重新激活 |
| P1 | material-flow 同时负责网络、创作、门禁与文件发布 | 按合同/校验、文本客户端、素材库、CLI 四个职责渐进拆分；已有参数、路径、授权与审核拒绝行为一致 |
| P1 | 文件预算历史有 23/45/49/50/52/53 多种口径 | 本次按自有代码/规则/配置/文档、根文件、三份状态记录、case.json、素材库说明统一盘点；整理前 63，归档两份并新增总览后 62，仍高于最后记录的 53。后续先决定合并范围或调整预算，不为凑数删除工作代码 |
| P2 | 旧 MODULE 同时含安装设想与后续实现，部分原计划路径已归档 | 逐段区分历史合同与当前实现，保留证据链接；目前通过首页导航明确查阅顺序 |
| P2 | 两份 `store.sqlite3` 空副本曾位于 `elsewhere/` 与 `runtime/elsewhere/` | 已只读核对：两份字节哈希相同、五张表均为 0 行；源码仅将活动库指向 `runtime/commodity-material-ai-v1/`。两份副本原字节移入本轮归档，记录见 [目录整理报告](../../../runtime/evidence/project-directory-organization-20261013/report.md) |
| P2 | 媒体存在跨参考、批次、素材库的重复副本 | 若以后要节省磁盘，先完成引用迁移设计；清单路径、媒体哈希、回滚能力全部验证后才移除冗余副本 |
| P2 | Git 尚无首个提交 | 排查应纳入版本控制的源码和说明，排除凭据、模型与大媒体；另行建立可回滚基线，本次不提交 |

## 本轮静态整理验收

- [x] 当前说明、计划、历史归档各有清晰入口。
- [x] 两份过时方案原字节归档；被修改文件有整理前副本。
- [x] 重复媒体按 SHA256 登记，媒体路径、内容、审核状态不变。
- [x] 默认 provider 与 AutoDL 关闭状态按配置核对。
- [x] 素材库可用文件存在性与哈希核对；已拒绝条目 `tyq-026-v1` 的视频缺失如实记录，未补造媒体。
- [ ] 服务在线、单元测试、模型调用、生成和实际像素验收：not_run，本次整理范围外。

## 历史查阅

- 安装、依赖、KOC 接口对照、旧预算与原勾选项：[原计划全文](../../../runtime/backups/project-organization-20261013/original/docs/modules/product-material-pipeline-v1/IMPLEMENTATION_PLAN.md)。
- 更早的 14 份历史文档、15 个版本：[既有归档索引](../../../runtime/backups/commodity-material-ai-v1/historical-docs/archive-index.json)。
- Bot 模板与行业 Skill 初版设计：[本次归档索引](../../../runtime/backups/project-organization-20261013/archive-index.json)。
- 原模块合同：[MODULE.md](MODULE.md)；commodity 模块合同：[MODULE.md](../commodity-material-ai-v1/MODULE.md)。
- 现行素材结构和像素标准：[material-verification-spec.md](material-verification-spec.md)。
- 本次证据、局限与回退：[整理报告](../../../runtime/evidence/project-organization-20261013/report.md)。


## 2026-10-13 目录整理补记

- [x] 将前轮归档后留下的两个空目录 `ark-bots/`、`ark-skills/` 移出活动文档树并保留可回退归档路径。
- [x] 核对 `elsewhere/` 与 `runtime/elsewhere/` 下两份 SQLite 文件的表名、行数、哈希及源码引用；两份均为空结构库，归档原字节。
- [x] 未触碰活动代码目录、媒体、.pytest_cache、vendor、runtime/cache、runtime/venvs 或未知外部项目。
- [x] 目录和引用静态核对；不运行测试、服务或 API。

---

## 视频分析、标准素材编目与调度实施计划 — material-catalog-scheduler/v1

日期：2026-10-13（Asia/Shanghai）。状态：**partial / 本地实现与定向验证已推进，真实模型接入未验证**。下文保留原计划，实际交付以文末执行记录为准。

> 执行者接续：先读本节设计合同，再按 P0–P6 的 checkbox 执行；适用时使用 `superpowers:executing-plans`。若实际执行处于 Orca 或用户显式启动工程闭环 v1，则先读其权威规则与 Task Spec 模板，由真实 ZCode worker 实现。本文不启动 worker、不激活工程闭环、不授权模型调用。

**Goal：** 在现有素材库中，为真实视频生成可追溯的标准名称与分类标签，并用可恢复任务队列接续分析、复核与入库。

**Architecture：** 保留现有 `lab-material-library/v3`、稳定 clip_id、三件套和审核状态；增加独立的 `catalog` 编目扩展。视频分析与调度分别实现，复用现有 CLI，只在明确启动任务时执行。

**Tech Stack：** Python 标准库（sqlite3、json、hashlib、unittest）与项目已有媒体读取能力；第一版不新增调度框架、模型权重或全局依赖。多模态供应商须完成能力与授权前置检查，不假定现有文本 Agent 能读取视频。

**Spec：** 本节 A–F 是设计合同，P0–P6 是实施计划；同时遵守本目录 `material-verification-spec.md`。这里的“标准”指本项目可验证的素材数据合同，不声称采用了某个外部行业通用标准。

### A. 需求、范围和已核实的基线

用户已确认：学习 video-mover 的任务调度与实际视频分析；素材名称、标签使用标准素材格式；沿用本地格式，统一命名与标签。

验收目标：同一素材可被稳定定位；名称能说明是什么镜头；标签能用于筛选；标签能追溯到视频画面；重启不会重复调用模型；更新编目不改变已有人工作出的媒体审核状态。

当前代码基线（本次只读核对）：

- `scripts/material-flow.py` 的 `_publish()` 从 creation.json 复制 content/edit/creative，不能据此证明实际视频呈现了计划内容。
- `_lib_place()` 接收的 poster 来源是 Seedream 图片，当前 `.jpg` 可能是生成参考图，不是实际视频解码首帧。第一版保持它不变；分析必须从 mp4 读取，并另存观察帧。
- `_lib_save()` 写 sidecar、manifest、index；`_lib_set_status()` 会移动三件套。新任务必须与这些写入协调，不能造成旧快照覆盖新审核。
- 活动素材库合同为 `lab-material-library/v3`。`schemas/material-manifest-v1.schema.json` 属于另一套严格的旧桥接合同，禁止向其根节点塞入本次字段或自动宣称已兼容旧剪辑消费者。
- 已有天益清清单 18 条，15 条可用、3 条 rejected；一条 rejected 视频缺失。此为既有记录，不是本次重新验收结果。

方案比较与选择：

| 方案 | 代价与收益 | 决定 |
|---|---|---|
| 直接在主脚本继续堆叠分析与定时循环 | 改动集中，但扩大已有大文件职责 | 不采用 |
| 独立分析模块＋轻量持久队列＋现有 CLI | 可独立验证、可恢复、无新增平台 | 采用 |
| 完整搬入 video-mover / 新工作流平台 | 引入上传、下载、额外依赖与重复状态系统 | 不采用 |

本版包含：短素材编目、可读名称、分类标签、候选标题/话题的数据预留、单任务/小批队列、审核等待、恢复、旧素材按选择回填。

本版不包含：完整成片剪辑、平台抓取/自动发布、现有素材重新生成、修改外部剪辑系统、自动将文字分析当作像素验收、全量无人值守生产。成片标题/话题生成放在后续独立范围；短镜头只生成素材名称和素材标签。

### B. 标准素材格式

#### B1. 目录、身份和版本

沿用以下结构，不另建第二份生产素材库：

```text
material-library/
  index.json
  <product_id>/
    product.json
    manifest.json
    approved|pending|rejected/
      <clip_id>.mp4
      <clip_id>.json
      <clip_id>.jpg
```

- `clip_id = <shot_id>-v<take>`；继续复用已有值，例如 `tyq-203-v1`，媒体文件名不因名称或标签变化而改变。
- 新生成 take 才改变素材版本；重新分析只增加 `catalog.revision`，不得伪装成新媒体版本。
- 文件路径相对 product 目录；拒绝绝对路径、`..`、越界符号链接与 junction；resolved path 必须落在 lab 的对应产品目录。
- 三件套继续同 stem；现有缺失 poster 不补造事实，记录缺失并交现有流程处理。
- 保留分辨率、fps、实际时长、音轨标记与 sha256；不重编码，不把 24fps 写成 25fps，不把实际 6.58 秒写成请求的 6 秒。
- `status` 的四个值继续是 `pending_review / approved / approved_with_caveats / rejected`；编目不能更改这些值。

#### B2. 可读名称

模板：`{商品简称}_{镜头角色}_{场景}_{主体动作}_{clip_id}`。

示例仅用于格式说明，**不是实际像素验收**：`理线器_动作_办公桌_手整理线缆_lxc-101-v1`。

- 名称保存在 `catalog.display_name`，不批量重命名 mp4。商品简称取产品卡、缺省使用 product_id；其余描述来自审核后的实际观察。
- 角色中文固定映射：pain=痛点、consequence=后果、action=动作、result=结果、product_detail=产品细节、unknown=未判定。
- 各片段先 Unicode NFC 规范化、去首尾空格、连续空白合成一格；禁用控制字符和 `<>:"/\\|?*`，片段内下划线替换为短横线。
- 商品简称/场景各最多 12 个 Unicode 码点，主体动作最多 20；缺失分别用 product_id / 场景未识别 / 动作未识别。整名上限 128，截断描述片段但完整保留 clip_id。
- 不写“爆款”“必买”等营销词；可读名说明素材内容，宣传标题不承担素材身份。
- 实际观察信息不足时允许“未识别”，不得按提示词补成确定描述。

#### B3. 编目扩展合同

现有 clip 顶层新增可选 `catalog`；旧字段不改名、不删除、不覆盖。无 catalog 的旧条目仍按原规则读取。扩展独立版本为 `lab-material-catalog/v1`，有独立 JSON Schema；现行 v3 顶层版本不改。旧严格导出继续走旧桥接器，不输出 catalog。

| 字段 | 类型与要求 | 作用 |
|---|---|---|
| schema / revision | 固定版本字符串 / 从 1 开始的整数 | 合同和编目版本 |
| state | draft / reviewed / stale / blocked | 编目状态，与媒体 status 分离 |
| display_name / summary | 字符串，分别 ≤128 / ≤300 码点 | 素材名称、实际画面摘要 |
| labels | 下节的分类对象 | 供检索的标准标签 |
| observations | ≤30 项 `{id,start_sec,end_sec,text,evidence_ids}` | 带时间范围的画面依据 |
| evidence | ≤32 项 `{id,kind,path,sha256,timestamp_sec}` | 实际视频帧或输入视频证据；时间戳对视频本体可为 null |
| usable_segments | ≤10 项 `{start_sec,end_sec,basis,reviewed}` | 候选剪辑区间；0≤start<end≤实际时长 |
| plan_comparison | `{state,items}`；state=matched/partial/mismatch/unavailable | 比较原计划与实际观察；items 包含计划字段、差异和 observation_ids |
| binding | 下述全部必填字段 | 防止旧分析套给新视频 |
| analysis | 下述处理信息 | 记录谁看了什么 |
| review | `{by,at_utc,note,analysis_sha256}` 或 null | 人/具备读视频能力的 Agent 对编目结果的复核 |
| publishing | 固定 null（v1） | 成片标题、简介、#话题的后续独立扩展位置 |

`binding`：product_id、clip_id、media_sha256、case_sha256、plan_sha256（无计划时 null）、prompt_sha256、taxonomy_sha256、analysis_profile_sha256。分析前后都核对视频字节；命名或标签词典/商品资料/计划/分析参数变化后，旧版本保留并标 stale，不自动重复付费。

`analysis`：provider、model、model_revision（供应商无返回时 null）、method（video_multimodal/frame_sequence/human_video）、input_coverage（full_video/sampled_frames）、audio_status（not_analyzed/analyzed）、created_utc、request_id（无则 null）、authorization_id（人工分析时 null）、limitations[]。不得捏造模型版本或记录凭据。

`reviewed` 要求 review 非 null、绑定仍有效；`draft/blocked` 不得冒充人工认可。技术性失败结果只留任务证据，不生成一个假的完整 catalog。存在新版本时不覆写旧的不可变原始分析。

#### B4. 分类标签词典

所有标签结构统一为 `{code,label,source,evidence_ids}`；code 是稳定英文小写标识，label 是固定中文显示词，source ∈ observed/planned/derived。自由长句放 observations，不塞进标签 code。

| labels 内字段 | 数量 | v1 词表/要求 |
|---|---:|---|
| role | 1 | pain/consequence/action/result/product_detail/unknown |
| scene | 0–3 | office_desk/living_room/kitchen/bedroom/bathroom/outdoor/studio/other |
| subject | 0–5 | product/person/hands/cable/bottle/tablet/packaging/desk/food/other |
| action | 0–3 | hold/place/pick_up/open/close/organize/wipe/pour/take_out/turn/sit/walk/static_display/other |
| shot_size | 1 | extreme_close_up/close_up/medium_shot/full_shot/wide_shot/mixed/unknown |
| camera_motion | 0–3 | static/push_in/pull_out/pan/tilt/tracking/orbit/mixed/unknown |
| person_exposure | 1 | none/full_person/limbs_only/partial_person/unknown；这是观察值，不覆写原生产人物预算字段 |
| product_presence | 1 | hero/supporting/in_use/absent/unknown |
| story_function | 0–3 | hook/problem/consequence/demonstration/result/transition/detail/ending |
| visual_quality | 0–8 | low_light/blur/occluded/cropped/deformed/text_unreadable/overlay_present/black_frame |

- 词表及 code→中文名称、同义词映射保存在新 Schema 的 `x-taxonomy` 中，一处维护；如特写/近景分别按受控定义映射，不让模型自由扩充正式词表。
- v1 遇到未覆盖场景/动作使用 other＋observation 原文，新增词先进入建议记录，经词典版本更新后才成为正式 code。
- 除 unknown 外，observed 标签至少引用一个真实 evidence；story_function 可为 derived，但要引用 observation。planned 标签必须明确其来源为原计划，不进入默认“已观察”检索条件。
- role=结果不意味着商品效果已被证明；只描述镜头叙事角色。原 `claim_tags`、`mix_tags`、`narration_target` 原样保留，不能因为画面里出现商品就添加功效事实。
- 同分类同 code 去重、按词表顺序输出；单素材正式标签总数≤24。超限按分类优先级 role→scene→subject→action→shot_size→camera_motion→person_exposure→product_presence→story_function→visual_quality 裁剪非必填项，并记录被省略项；质量问题仍完整保留 observations。
- 不把分辨率/fps当视觉标签；这些读取已有技术字段。素材标签不带 #。默认检索只使用当前绑定有效且 state=reviewed 的 observed/derived 标签；draft 必须显式选择才能检索。

### C. 实际视频分析流程

1. 从选定的 manifest 条目定位 mp4，验证路径、文件存在、sha256 与技术元数据。拒绝损坏/缺失/哈希不符的视频，保留其媒体审核状态。
2. 从实际 mp4 获取证据。优先选择经过能力验证的完整视频输入；若仅支持图像序列，明确采用 frame_sequence，不声称看过整段连续动作或听过声音。
3. 帧序列按首帧、末个可解码帧及每秒一帧取样，按实际时间去重；3–10 秒素材最多 12 帧。解码时间戳必须记录。采样不能证明采样间隙无畸变、无水印或动作完整；可用区间只能作为候选。
4. 第一遍只给实际媒体、技术信息和输出合同，做盲读；第二步在本地或同一授权范围内加入商品资料/分镜做对照。计划字段不能反向填充看不清的画面观察。
5. 模型输出用严格结构解析，验证枚举、长度、证据引用、时间区间、绑定和字段。媒体中的可见文字按数据处理，不执行其指令。
6. 先写不可变分析证据，再产生 draft 编目。失败保留原因；禁止输出空标题空标签后声称成功。
7. 复核者确认名称、标签、摘要、证据和局限，才将 catalog 置为 reviewed。新入库素材仍走独立媒体审核；已有 approved 素材不会因补标签被降级或自动重新批准。
8. 正式写入 sidecar/manifest 时重新解析当前路径和审核状态，并核对预期摘要，防止人工移动素材后写回旧目录。

证据目录：`runtime/material-analysis/<product_id>/<clip_id>/<analysis_key>/`；保存输入摘要、probe、时间戳帧、脱敏模型结果、校验和复核记录。以相对 lab 路径引用；帧和视频的 sha256 对应实际字节。现有三件套中的 poster 不作为分析输入。

供应商前置决定：P0 核实现有已配置通道的真实多模态入口、模型、输入限制、时长/大小限制、输出结构和计费方式。能用原通道才复用；能力未证明则 P2 只完成注入式分析接口与离线校验，真实分析标 blocked_capability。不得自动切账号、模型、Jev、Qwen 或上传到另一供应商。

不启动本地模型做检查；若后续明确选用本地推理，推理结束/失败/取消后真正卸载，仅释放缓存不算完成。CPU 队列可以持久保存任务但不预热模型。

### D. 调度设计

#### D1. 任务范围与状态

v1 仅编排 `inspect_media → analyze_video → validate_catalog → await_catalog_review → publish_catalog`。原有 create/image/video-submit 保持原入口和门禁；v1 调度器不接管付费生图、生视频。已有视频完成入库后，可由显式开启的 enqueue 钩子登记任务，但不自动发起推理。

SQLite 队列路径固定：`runtime/material-analysis/queue.sqlite3`。使用独立文件，不复用商品数据库。task_id 稳定，幂等键为 product_id＋clip_id＋media_sha256＋case/plan/prompt/taxonomy/profile 哈希；相同字节不同产品/镜头仍是不同任务。

任务状态：queued/running/waiting_authorization/waiting_review/retry_wait/uncertain/completed/failed/cancelled/stale。状态日志和 catalog.state、媒体 status 分别保存，不共用 PASS 一类含糊字段。

- queued 经原子认领进入 running；阶段结果落盘后才推进 next_stage。
- 缺能力/授权：进入相应等待状态，reason 指明 capability 或 authorization；不消耗推理次数。
- 网络调用前先记录授权消费、request_key 和 in_flight，再提交；结果落盘后记录 request_id 与完成阶段。
- 付费推理超时、进程崩溃或响应丢失：uncertain。先查已知 request_id 或要求人工核对，不自动再次发起请求。
- 纯本地文件临时占用/可确定未提交的免费查询：最多总计 3 次尝试（首次＋2 次重试），间隔 5、20 秒。结构校验失败直接 failed，不循环让模型重写。
- 媒体/资料/词表变更：stale；重新建任务需显式 enqueue，不从后台自动开启新付费任务。
- 取消等待中任务即时生效；远端调用已发出时尽可能记录其结果与计费，不把本地取消当成远端撤销成功。

#### D2. 启动、并发和恢复

- v1 提供一次性 `run-once`；本轮不建 Windows 定时任务、不添加自动启动服务。后续持续运行也必须通过同一队列状态，不能每轮重新扫描后无差别执行。
- 同时最多一个分析任务；SQLite 原子 claim＋owner_id＋120 秒租约，每 20 秒续租。外部请求放在事务外，单次调用超时上限 90 秒；长会话用 request_id 分段查询和续租，不阻塞数据库事务。
- 失去租约的旧 worker 不得提交结果；完成前比较 owner_id 和 claim_token。过期任务如果停在纯本地阶段可恢复，如果已 in_flight 则 uncertain。
- 检查相同媒体的已有成功阶段及结果哈希，重启只执行尚未完成的阶段；已完成模型调用的结果即使尚未投影也要复用。
- CLI 出错返回非零并给出 task_id/reason；状态查询不加载模型、不上传素材。
- 所有进程与路径限定在 lab；仅停止本任务拥有的进程。

#### D3. 多文件一致性

- 编目发布与现有 `_publish / _lib_save / _lib_set_status` 共用项目库写锁；锁覆盖当前清单读取、CAS 校验、落盘与恢复。全库 index 更新也在同一锁内，第一版不追求多产品并发写。
- 网络和模型推理期间不持有素材库锁。发布前读取最新 status/path，仅替换目标 clip 的 catalog，其他字段保持不变。
- 在 runtime 写事务日志，暂存 sidecar/manifest/index，逐文件原子替换，记录提交完成。重启恢复后才允许下一次写入；每次发布前创建有哈希的旧文件备份。
- 这不等于外部直接读取三个文件能获得跨文件原子快照。lab 新读取入口遵守锁/恢复状态；未修改的外部消费者优先读取单个 manifest。并发外部文件夹读取列为兼容限制，不能宣称已验证。

### E. 接口与文件边界

拟新增 **4 个自有源文件**：

| 文件 | 责任 |
|---|---|
| `schemas/material-catalog-v1.schema.json` | catalog 合同、受控词典、同义词、必填字段与上限 |
| `adapters/material_catalog.py` | 绑定、媒体观察输入、注入式模型接口、输出校验、名称/标签归一化、编目发布 |
| `adapters/material_scheduler.py` | SQLite 任务、授权消费记录、租约、状态推进、恢复和库写锁/事务日志 |
| `tests/test_material_catalog_scheduler.py` | 零网络合同、状态机、幂等、故障恢复和兼容测试；测试夹具明确 UNIT ONLY |

拟修改：`scripts/material-flow.py`（薄 CLI、入库 enqueue 钩子、现有写操作锁定）、`tests/test_ark_agent.py`（现有入口回归）、`config/doubao_text.json`（只新增禁用的 video_analysis 配置，不改当前角色/provider）、`material-library/README.md`、本计划、`MODULE.md`、`module.yaml`、`runtime/progress.json`、`runtime/changes.json`、`runtime/acceptance.json`。

配置 `video_analysis`：enabled=false、mode=unverified、provider/model=null、max_concurrency=1、timeout_sec=90、max_batch=3、max_frames=12；null 表示不得调用，绝不隐式使用文本默认模型。能力核实后由明确授权填写，凭据仅复用允许的解析通道，不进入配置快照。

文件预算前置：当前计划记录自有文件 62，历史上限 53；本设计预计新增 4 个，按该口径为 66。**66 是拟议预算，不是已批准上限。** P0 必须重新盘点并取得新增 4 文件的范围批准，或另行批准合并方案；不得删除工作代码凑数。本轮仅扩写现有计划，不增加源文件。

明确接口（实现时采用这些名称；dict 的结构受上文 Schema 约束）：

```python
# adapters/material_catalog.py
build_binding(root: Path, clip: dict, profile: dict) -> dict
inspect_media(root: Path, clip: dict, binding: dict) -> dict
analyze_video(observation_input: dict, invoke: Callable[[dict], dict]) -> dict
normalize_catalog(raw: dict, binding: dict, taxonomy: dict) -> dict
validate_catalog(catalog: dict, duration_sec: float, taxonomy: dict) -> list[str]
publish_catalog(root: Path, product_id: str, clip_id: str,
                catalog: dict, expected_media_sha256: str) -> dict

# adapters/material_scheduler.py
enqueue(db: Path, binding: dict, stages: list[str]) -> str
claim_next(db: Path, owner_id: str, now_utc: str) -> dict | None
advance(db: Path, task_id: str, claim_token: str,
        event: dict, now_utc: str) -> dict
run_once(db: Path, handlers: dict[str, Callable], owner_id: str) -> dict
recover(db: Path, now_utc: str) -> dict
library_write_lock(root: Path) -> ContextManager
```

模块单向依赖：catalog 可调用 scheduler 的通用库写锁；scheduler 仅调用注入 handlers，不 import catalog 或 material-flow。CLI 装配两者，避免循环导入。`advance` 的 event.type 限定 stage_done/wait_authorization/wait_review/retryable_error/uncertain/fatal_error/cancel/stale，payload 按阶段合同检查，不能任意跳转 completed。

拟新增 CLI（以下命令当前不存在，不得当作本轮已执行）：

```text
material-flow.py catalog-plan --product <id> --clip <id>          # 只读需求与能力检查
material-flow.py catalog-enqueue --product <id> --clip <id>       # 显式入队，不调用模型
material-flow.py catalog-run-once --authorization <path>         # 每次最多认领一任务
material-flow.py catalog-tasks [--product <id>]                  # 只读状态
material-flow.py catalog-review --task <id> --decision accept|reject --note <text>
material-flow.py catalog-search --product <id> --role action --scene office_desk
material-flow.py catalog-cancel --task <id>
```

`catalog-review accept` 只确认 catalog，沿用现有执行者身份记录方式并绑定 analysis_sha256；不调用 `video-status`。`catalog-search` 是本项目只读筛选，不修改外部剪辑器。发布标题与 #话题 CLI 不在 v1。

### F. 全局约束、失败重点与回退

- 写入仅 lab；不扫描、读取或写入 `laox_account`、h3_pipeline、minimaxh3 或历史例外中的外部工具。需要媒体工具时先查项目已配置路径；缺依赖则记录阻塞，不自行重新安装。
- 本轮为纯计划：零生成、零模型、零服务启停、零测试执行；未来测试命令仅供获得执行授权后的实施使用。历史 API/测试禁令不得由计划文字自动解除。
- 保留原图片≤20/有效≤15/人物≤3/肢体≤3、视频每批≤10/每条≤10 秒规则；分析队列每批默认最多 3 条，分析不是生成，不消耗图片张数但有独立模型调用预算。
- 不烧录素材名称、标签、来源、状态、水印或制作说明；无视频重编码。
- 分析授权必须含产品/clip 范围、最大调用数、有效期、批准说明、模型/provider、预算上限；上传视频/帧范围随授权绑定。价格未知须明确记录，不能借用历史生视频免报价授权。第一次真实验收仅授权 1 条时绝不跑 3 条。
- 不把 0 网络 mock 测试、静态校验、历史通过记录写成真实视频分析通过。

五类重点失败及归属：

1. 同一路径换了视频字节、生成封面与视频不同：P2 验证 hash 与实际视频证据，拒绝旧分析。
2. 人工改审核状态同时后台回写：P4 验证最新状态保留、路径不回退、无条目丢失。
3. API 已扣费但进程断开：P3 验证 uncertain，恢复不重复提交。
4. 中文空白/非法字符、同义词、未知词、过长输出：P1 验证确定性名称、受控 code、保留身份后缀。
5. 观察帧无法证明连续动作、缺音频分析、计划与实际冲突：P2/P5 验证 limitations、不标全片看过、不用计划替观察。

回退：关闭新增分析配置与 enqueue 钩子，停止本功能自有任务；依据事务日志恢复被修改 JSON 的原始字节。保留 mp4/jpg、旧审核结论、调用账本、分析证据和队列审计；不整目录删除 runtime。新增模块可移出活动入口，但只有检查引用和变更登记后才能移除自有文件。外部平台、全局环境与既有模型不参与回退。

### P0. 基线、能力和范围锁定

- [ ] 读取最新 AGENTS、progress、本计划、变更与验收记录；核对是否实际处于 Orca，确定执行者，不凭历史 worker 标记判断仍在运行。
- [ ] 静态盘点自有文件及 dirty 基线，处理 E 节拟增 4 文件预算；制作精确 Allowed files 的 Task Spec，限制主脚本新增 glue≤120 行、旧写入加锁≤80 行，不做全脚本重构。超出重新拆任务。
- [ ] 确认项目内解释器和媒体解码能力；核实 SDK/API 官方能力及当前配置，确定 video 或 frame_sequence 输入。未证明多模态能力时，先完成离线实现，不假装已有供应商可用。
- [ ] 保存脱敏基线到 `runtime/evidence/material-catalog-scheduler-v1/precheck/`；记录所有既有媒体 SHA256 和审核字段，以供回归比较。

可观察交付：范围、文件预算、接口模式和仍需授权项目明确；此阶段不能宣称真实分析成功。

### P1. 标准素材合同与命名标签

文件：新增 Schema、material_catalog.py、test_material_catalog_scheduler.py；不改现有媒体。

- [ ] 先写 `CatalogContractTests`：测试同一输入输出稳定、中文清洗、身份后缀保留、unknown/other、标签去重与24项上限、非法枚举拒绝、越界时间拒绝、证据缺失拒绝。
- [ ] 在获准运行测试后先确认失败原因是未实现，再实现 B 节 normalize_catalog/validate_catalog；仅使用项目已有 Schema 校验依赖，无则实现此合同必要校验，不另装库。
- [ ] 测试旧 v3 条目无 catalog 仍可读取；新扩展不能改写旧 content/edit/creative 或媒体 status；严格旧桥接导出不包含 catalog。
- [ ] 通过本阶段离线测试并静态复核；更新词典和字段说明。

交付：可输入人工构造且明确标 UNIT ONLY 的观察记录，得到确定性的标准名称与标签；不声称模型已看视频。

### P2. 视频观察与分析接口

文件：material_catalog.py、测试、现有 doubao_text.json（禁用配置）。

- [ ] 写 `VideoAnalysisTests`：真实视频路径与 poster 不同、哈希变化、文件缺失、越界路径/junction、帧数量上限、无法读音频、供应商能力缺失、超时、无效 JSON、伪造证据引用。
- [ ] 实现 build_binding/inspect_media/analyze_video；媒体读取使用项目已有解码能力，记录实际帧时间戳与字节哈希；观察帧不覆盖现有 poster。
- [ ] 单元测试注入 transport，不创建云 Agent、不做网络。盲读输入中断言不含 generation.prompt、原 story_beat 或功效标签；对照阶段才读取这些数据。
- [ ] 输出候选窗口和 limitations，区间用秒并符合实际 duration；v1 不向旧桥接导出，因此不擅自取整到其 25fps 时间网格。

交付：可测试的分析边界和失败记录；真实模型能力与真实质量在 P5 单列。

### P3. 持久队列与中断恢复

文件：新增 material_scheduler.py，扩展测试。

- [ ] 写 `SchedulerTests`：重复入队同 task、不同 clip 同哈希不合并、两个 worker 只认领一次、租约过期、旧 owner 无法提交、重启复用结果、取消、重试次数与5/20秒退避。
- [ ] 写授权测试：过期/范围外/次数耗尽均不调用；ledger 先落盘；调用前崩溃 conservatively uncertain；响应后落盘前崩溃也不重复提交。
- [ ] 实现 enqueue/claim_next/advance/run_once/recover；注入可控时钟，测试不真实 sleep，不启动后台服务。
- [ ] 验证无待办时 run-once 立即返回 idle、不加载模型；等待审核任务不占用租约和 GPU。

交付：零网络条件下，队列可从各故障点恢复，且不会重复付费提交。

### P4. 现有素材库与 CLI 接入

文件：material-flow.py、两个新 adapter、两份测试、素材库说明。

- [ ] 写 `LibraryIntegrationTests`：sidecar/manifest 对应 catalog 相等；重复发布幂等；人工改 rejected 后发布仍为 rejected；并发修改另一个 clip 不丢数据；index 数量/usable_seconds 不变。
- [ ] 写三文件写入中断测试，逐个替换点注入失败；重启恢复完成后 sidecar/manifest/index 一致。覆盖 _lib_set_status 与 catalog-publish 共用锁。
- [ ] 实现 D3 写锁、日志、CAS 与恢复；接入 E 节七个 CLI。新增 import 不触发服务、网络或创建目录。
- [ ] 新素材入库时仅在 enabled 且用户明确开启自动登记时 enqueue；不开启则保持原行为。无论如何不自动执行分析。
- [ ] 运行现有角色/供应商入口相关离线回归；保留 v3、旧状态与三件套路径。

交付：新功能有稳定入口，默认关闭；能筛选 reviewed 编目，旧消费者仍使用原字段。

### P5. 有界真实验收与回填

- [ ] 先做授权前只读候选清单：从现有可用素材选择 3 条，分别覆盖产品细节、动作、场景；若实际库无某类，不编造覆盖，改选并记录缺项。rejected/缺失视频只用于负向检查，不进入可用库。
- [ ] 先申请/落实 1 条真实分析的明确调用额度与费用上限，再执行首条。检查真实视频输入被供应商接受、实际输出、耗时、请求标识、名称标签与像素是否一致。
- [ ] 由人或具备视频理解能力的 Agent 实际查看视频并复核；frame_sequence 局限必须保留。连续动作与候选剪辑点需查看完整对应区间才能确认。
- [ ] 首条通过且后续额度已授权后，扩至最多3条；用相同任务重跑证明无新增模型请求。若失败，单故障最多2次修复后重新诊断；新模型调用另计额度，不因修复自动增加额度。
- [ ] 明确选择历史回填清单才批量执行，不自动处理全部18条。逐条保留原审核、原命名路径和原字节；新 catalog 先 draft。

真实验收标准：所选视频哈希一致；标准名称能准确指认镜头；正式标签全部在词表中且有有效依据；无提示词冒充画面；同任务恢复零重复模型请求；媒体与已有审核无变化。任一失败保留 partial，不给整链 PASS。

### P6. 交付与知识回写

- [ ] 将 Schema/词典/命名/搜索方式写入 material-library/README.md；MODULE 记录新能力、边界及局限。
- [ ] 更新 progress/changes/acceptance：离线测试、真实分析、复核、兼容验证分别记录 verified/partial/blocked/not_run；不混成单一“完成”。
- [ ] 对照 P0 媒体哈希和审核基线做差异检查；验证关闭功能后的原有入库路径仍可用。
- [ ] 交付3条或实际获准数量的标准编目样例、真实分析证据、恢复测试结果与回退清单；拟议功能不得混入已交付清单。

### 实施时验证命令与完成定义

以下解释器是本次只读确认存在的项目内路径；执行前仍要核对环境。**本轮未运行这些命令**。

```powershell
& 'F:/codex/laox_director_lab/runtime/venvs/vbench/Scripts/python.exe' -m unittest discover -s tests -p 'test_material_catalog_scheduler.py' -v
& 'F:/codex/laox_director_lab/runtime/venvs/vbench/Scripts/python.exe' -m unittest discover -s tests -p 'test_ark_agent.py' -v
```

预期：离线测试全部 OK、网络 transport 调用仅 mock、模型加载次数为0；不把测试 fixture 放入生产素材库。媒体解码/真实模型检查在 P5 按实际授权单列，不能用上述 OK 代替。

整体完成条件：P0–P6 有可核验证据；新增字段兼容、命名标签一致、实际观察有据、审核不越权、恢复不重复付费、文件预算已明确、模型生命周期满足项目规则。本计划当前仅完成需求与设计，所有实施 checkbox 保持未勾选。

### 参考与本轮计划自查

- 借鉴 [video-mover/main.py](https://github.com/toki-plus/video-mover/blob/main/main.py) 的步骤分离与调度组织；不直接继承其忽略处理返回值、时间窗口重复执行和无持久阶段状态的问题。
- 借鉴 [video-mover/Upload/vx_upload.py](https://github.com/toki-plus/video-mover/blob/main/Upload/vx_upload.py) 的实际视频输入、结构化文案输出；不复制其空结果继续上传、硬编码配置和处理后清空目录行为。
- 本地依据：README、当前代码 `_publish/_lib_save/_lib_set_status`、素材库说明、v3 清单、旧桥接 Schema、素材验证规范。未读取禁止的外部项目。
- [x] 区分项目素材标准与发布标题；沿用现有三件套、身份与审核状态。
- [x] 区分分镜、生成参考图与实际视频观察；记录采样局限。
- [x] 阶段责任、精确文件、接口、状态、验证、费用边界和回退均已给出。
- [x] 预算、供应商能力和真实调用额度作为执行前置，未假装已解决。
- [x] 当前只完成计划；不启动实施、不运行测试、不产生媒体费用。

### 2026-10-13 执行补记（用户追加“只验证更改的部分”之后）

此前的“纯计划/未实施”描述是制定计划当时的范围，不再代表本次接续状态。用户本次允许定向验证；本轮没有恢复旧批次生产授权。

- [x] 新增4个计划内源文件：catalog Schema、素材编目模块、任务队列模块、定向测试。
- [x] 接入7个 catalog-* CLI；库写锁与可恢复 JSON 投影接入现有素材库写操作。
- [x] 标准名称/词典标签、绑定与证据验证、实际视频采样、盲读接口、计划和观察并列记录。
- [x] 串行队列、阶段恢复、有限本地重试、授权预留、结果不明不重提、独立编目复核与投影。
- [x] 使用临时 UNIT ONLY 素材验证结构和状态；只执行新测试文件与现有 LlmJsonDispatchTest，未跑全项目。
- [x] 选用1条现有素材验证真实读取和抽帧：tyq-027-v1，24fps，解码时长约4.458秒，6张时间戳帧；零模型/网络调用。
- [ ] 真实多模态 transport 接入与首条语义分析：未完成，当前 fail-closed；配置未切换供应商/模型。
- [ ] 人工/读视频 Agent 对真实语义分析结果复核与最多3条小批验收：未运行。
- [ ] 历史素材编目回填：未运行；现有媒体、清单与审核记录保持原样。

执行决定：本地仓库无首次提交且要求写入不出 lab，本轮使用项目内精确备份，不创建外部 worktree、不提交全部未跟踪文件。新增文件限原计划的4个，不将此解释为全局文件预算无限放宽。验证按用户指定范围执行，不执行 skill 默认的全量测试。

实现细化：场景名由已观察的受控标签产生；抽帧使用解码器报告的时间戳。计划对照先并列原计划与实际观察，标 partial 待复核，不让字符串比较替代语义判断。当前费用门禁要求已知单次价格上界；未知价格保持拒绝，未来如支持免报价必须另立明确规则。

精确测试结果、独立复核、变更清单与回退见 `runtime/evidence/material-catalog-scheduler-v1/report.md`；真实模型能力未验证，因此不将整个 P0–P6 标为完成。

### 2026-10-15 通用 Agent / Skill 分层执行

- [x] 先下载云端两份行业 Skill 完整包与全文，保留修改前5角色定义。
- [x] 5角色精简为4条共同协议；行业方法归行业Skill，生产规范归共用Skill。
- [x] 清理7处请求模板；加固同步、输出契约及镜头ID；行业门禁读取Skill。
- [x] 28项定向离线测试、独立静态复核PASS。
- [x] 两行业Skill升级v2、共用Skill v1、五角色更新；全文下载/Agent GET回读一致，模型保留。
- [ ] 目标云端模型真实生成遵从性/成片效果验证（未授权本轮付费生成，不作为配置改造完成的证明）。

证据与回退：runtime/evidence/generic-agent-skills-v2-20261015/report.md。只新增1份共用Skill源文件，历史文件预算状态未被重新宣称通过。


### 2026-10-15 居家桌面支架创作试跑

- [x] 用户选择两种人物属性与桌面支架；范围固定为首轮文字方案。
- [x] home Skill v3上传、完整下载比对、五角色绑定回读，模型保持。
- [x] 云端创作、总结、审核、一次定向返修，共4个请求，未因超时重复提交。
- [x] 主审否决漏项pass，保留原结果；处理返修契约拒绝并显式编辑6镜文字稿。
- [x] 结构、数量、时长、参考编号校验；文件取回修复的31项定向离线测试通过。
- [ ] 最终修改稿重新云端审核与媒体生产：本轮未执行。
- [ ] 长连接中断后的生产级自动恢复：尚未实现，本次用只读原会话恢复。

详见 `runtime/evidence/home-stand-creation-20261015/report.md`。此前“真实模型未运行”仅代表当时配置改造；本轮已试跑，暴露的审核漏项未被隐藏。


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
