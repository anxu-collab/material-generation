# AGENTS.md — laox_director_lab 项目守则

本目录是独立模块 `product-material-pipeline-v1` 的工作区。进入本目录的执行者（ZCode/Codex/人）必须遵守：

## 边界（最高优先）
1. **所有写入仅在 `F:/codex/laox_director_lab` 内**。不写用户全局配置、全局安装、PATH、认证、权限或已有服务。
2. **`F:/codex/laox_account` 及任何 h3_pipeline：禁止扫描、遍历、读取代码/配置、修改**。不得为找 H3 凭据去该目录搜索。凭据只从环境变量读（`H3_API_KEY` 等），不回显值、不落日志、不拷贝。
   - **窄范围例外（2026-10-11 用户直接请求 koc-v1 集成，Orca task_6d2c8f52f480）**：`F:/codex/laox_account/video_postprod`（自动剪辑消费者）允许且仅允许——读取 `scheduler/`、`contracts/`、`tests/`、`jobs/deployment-fixtures/` 中与素材清单桥接直接相关的文件；写入仅限 Task Spec 列名的 7 个桥接文件（`scheduler/material_manifest.py`、`scheduler/shot_library.py`、`scheduler/pipeline.py`、`contracts/edit-job-v2.schema.json`、`tests/test_material_manifest.py`、`docs/modules/auto-postprod-v1/MODULE.md`、`module.yaml`）。不改 oldX H3/runtime、voice、renderer、timeline、已验收媒体与全局设置；该例外不扩大到 laox_account 其它目录，也不延续到后续任务。
   - **窄范围例外二（2026-10-11 用户直接请求"先对接H3的真实调用"，Orca task_e39ada996597，h3-autodl-v1）**：`F:/codex/laox_account` 允许且仅允许——只读以下精确文件：`app/runtime/h3/laox_h3.py`（唯一 H3 Runtime，经私有 importlib 实例复用，源码 SHA256 锁定于 `runtime/backups/h3-autodl-v1/backup-manifest.json`）、`config/h3_policy.yaml`、`app/api/workflow_schema_z0903.json`、`app/api/runtime.local.json`、`.agents/skills/h3-prompt-writing/SKILL.md`（含 `references/ref-en.txt`）、`video_postprod/jobs/deployment-fixtures/inventory.json`、`video_postprod/artifacts/imports/eb/ebba2690…mp4`（a1，只读抽帧来源）。凭据只经原 Runtime settings resolver（`app/api/autodl.env` 或其 `AUTODL_ART_*` 环境变量覆盖通道）解析，本模块不读值、不回显、不拷贝、不落日志。老X 一切写入保持禁止；本例外不扩大到 laox_account 其它目录，也不延续到后续任务。
   - **窄范围例外三（2026-10-11 用户直接请求"全能视频参考接入官方的minimax"（中国站 minimax.cn 确认），Orca task_fb19c1d1ef72，h3-minimax-official-v1）**：允许且仅允许——(a) 只读 `F:/codex/laox_account` 之外的精确外部工具：`E:/software/mini/MiniMax Design/current/resources/ffmpeg/ffprobe.exe`（媒体元数据探针，参数数组、30s 有界）；(b) 只读 `F:/codex/laox_account` 精确文件：`.agents/skills/h3-prompt-writing/SKILL.md`（含 `references/ref-en.txt`，六段 Ref2VA 提示词规则）、`video_postprod/artifacts/imports/eb/ebba2690…mp4`（a1，reference_video 只读来源，SHA 钉死）、`video_postprod/envs/scheduler/Scripts/python.exe`（执行解释器，不安装依赖）。用户已授权在 lab 内新增官方 MiniMax 中国站直连供应商通道（`adapters/h3_minimax_official.py` + `scripts/h3-official-materials.py` + `config/minimax_official.json` + 测试），该通道与 AutoDL 适配器/老X Runtime 完全分离、互不导入。凭据 `MINIMAX_API_KEY` 仅从环境变量读取（`MINIMAX_API_BASE_URL` 仅接受精确 `https://api.minimax.cn`），不回显、不落日志、不落配置；不做 AutoDL key 回退。老X 一切写入保持禁止；不对 laox_account 做任何目录扫描；本例外不扩大范围，也不延续到后续任务。
   - **窄范围例外四（2026-10-11 用户直接请求 AutoDL z0902 对接（`minimax_h3_z0902`，默认 1088p竖(1088*1920)，用户自供 image-only 请求字段），Orca task_8c5482b0a195，h3-autodl-z0902-integration）**：`F:/codex/laox_account` 允许且仅允许——**只读精确文件 `app/runtime/h3/laox_h3.py` 的源码与 SHA256**（用途仅为：复用其 `http_json` 低层传输方法于未来 guarded z0902 提交，及核对 z0903 锁定事实；源码 SHA256 `8b173b0e…` 与 `runtime/backups/h3-autodl-v1/backup-manifest.json` 一致）。本轮按用户明令"你只是对接，不允许测试"零 import、零执行、零测试、零网络；不读取 oldX 其它任何文件（含 autodl.env、api、config、media、tests），不执行其 settings resolver——z0902 的 AutoDL 配置只从环境变量 `AUTODL_ART_BASE_URL`/`AUTODL_ART_TOKEN` 解析（域限 autodl.art 官方 HTTPS）。老X 一切写入保持禁止；本例外不扩大范围，也不延续到后续任务。
   - **窄范围例外五（2026-10-11 用户直接请求"走火山引擎的 seedream 生图模型"并指定 `F:\codex\minimaxh3`，允许测试阶段调用生成，seedream-image-v1）**：允许且仅允许——(a) 一次性读取 `F:/codex/minimaxh3/.env` 中的 `ARK_API_KEY` 一行（其余变量一律不读、不回显），并按用户"这个key单独配置到这个项目中"的要求写入项目内 git 忽略的 `config/seedream.local.env`（已于 2026-10-11 完成，此后不再读 minimaxh3）；key 不落日志/台账/证据/报错；(b) 调用 `https://ark.cn-beijing.volces.com/api/v3/images/generations`，模型固定 `doubao-seedream-5-0-flash-260915`，无回退、无自动重试；(c) 新增 `adapters/seedream_image.py`、`tests/test_seedream_image.py`（该通道与 H3 适配器互不导入）。付费调用须有人工批量授权记录（总张数上限+有效期+同意说明），按台账（先写后调）累计，超限即拒。不扫描 `F:/codex/minimaxh3` 其它文件；不读 `F:/codex/laox_account`；本例外不扩大范围，也不延续到后续任务。
   - **窄范围例外六（2026-10-11 用户告知"AUTODL_ART_TOKEN 在 env 里面有"，天益清流程）**：一次性读取 `F:/codex/发布代码/.env` 中 `H3_API_URL`（仅校验域名为 autodl.art）与 `H3_API_KEY` 两行，写入项目内 git 忽略的 `config/autodl.local.env`（`AUTODL_ART_BASE_URL`/`AUTODL_ART_TOKEN`），值不回显、不落日志/证据。未读取老X目录。付费提交仍须 z0902 适配器的人工授权记录。本例外不扩大范围。
 - **窄范围例外七（2026-10-11 用户明确选择"允许只读 laox_h3.py 中任务查询和下载的函数"，并答复"不需要了解价格，就按照秒数去提交"，天益清 10 条视频验证 tyq-v2）**：在例外四的只读文件 `app/runtime/h3/laox_h3.py` 上，仅追加读取 `poll()`/`download()`/`download_url()`/`http_json()` 及 `SUCCESS`/`FAILED` 状态集，用于在 lab 适配器内实现 z0902 的查询与下载（已复现于 `adapters/h3_autodl.py: poll_z0902/download_z0902`，传输仍复用 `http_json`；下载不带 token）。z0902 提交授权允许"人工免报价"记录（`cost_waiver`：price_known=false，原话入档，预算上限仍≤200 CNY/条），不得伪造平台报价。验证期间 guards 临时开启，批次结束已恢复 `network_enabled=false / generation_enabled=false / dry_run=true`。老X 一切写入仍禁止；本例外不扩大范围，也不延续到后续任务。
 - **图片预算规则（2026-10-11 用户）**：一个产品生成图 ≤20（留 5 张容错）、有效图 ≤15、完整人物 ≤3、纯肢体 ≤3，其余为产品与联想链；视频每批 ≤10 条、每条 ≤10 秒。由 `scripts/material-flow.py policy/jobs/video-jobs` 执行。天益清此前已生成 54 张（规则之前），不再出新图。
 - **创作方向（2026-10-11 用户）**：本项目做好物分享爆款素材。视频展示就是描述功能——功能靠画面演出来；用画面故事（小麻烦→这件东西顺手解决→可见结果）让人记住。功能链路的每一环都要有衍生图：麻烦扩散到的另一个场景或其他物件、用完之后看得见的变化，不能整批都是同一张桌子上的产品特写。不做问答式/清单式测评。画面禁止“内部审阅”“信息待确认”等任何审阅/状态角标或水印。老X案例见 `cases/laox/case.json`。
 - **消费者痛点与实际图片验收（2026-10-11 用户确认“好，你补充”；consumer-pain-visual/v1）**：本项目生图主验收为“观众认出自己的具体麻烦→看懂商品怎样介入→看到同一麻烦被解决”。先写消费者的情境、任务、阻碍、具体不便、对应已知功能与来源，再写镜头和美术；推测场景标创作假设，不冒充用户反馈或实物性能。每个主痛点须有可见问题物件/受阻状态、商品动作和对应结果；仅皱眉、泛泛凌乱、人物微笑或漂亮桌面不能替代。衍生场景仍须能被本商品功能解决，禁止为了场景数量引出商品解决不了的问题。
 - **图片分配与首帧**：15 张有效图的默认规划为痛点/后果 5、动作 4、对应结果 4、纯产品细节最多 2；5/4/4 是建议配额，不是凑数下限，少量素材优先完整因果链。纯产品摆拍跨场景、跨机位合计最多 2；换角度却无新增购买信息按重复处理。原完整人物≤3、纯肢体≤3、有效图≤15、生成≤20、视频≤10条且每条≤10秒的上限不变。每张图明确承担痛点、后果、动作、结果或必要产品细节之一；痛点首帧已显出问题，动作首帧具有所需物件和空间，结果首帧可为完成状态，不要求每图都塞入完整故事。
 - **两段审核与准入**：生图前审方案；出图后必须由实际看过像素的人或具备读图能力的 Agent 逐张记录盲读复述、可见依据、因果关系、动作起点、新增信息和事实边界。文字模型、提示词、文件名、模板和概率均不得冒充像素验收。审核绑定图片版本/实际字节哈希、当前分镜、定稿提示词、商品资料及所选视频动作和时长；换图/改稿/改动作后失效，关键项失败或缺记录不能转视频。旧素材保留，不能自动补 PASS；生成画面不能作为真实性能实测证据。禁止固定文字/制作说明/审阅状态角标及挪到其他角落的替代标签，来源和假设留在工程记录。执行入口、字段与接续步骤见 `docs/modules/product-material-pipeline-v1/MODULE.md` 的 consumer-pain-visual/v1 节。
 - **素材验证规范（2026-10-12 定稿，material-verification-spec/v1）**：方案验证（结构契约：shot 23 字段 + consumer_need 13 字段逐字一致；reviewer 六项 consumer_checks；链路/衍生镜头/同桌特写判定）与像素验收两层标准见 `docs/modules/product-material-pipeline-v1/material-verification-spec.md`。所有行业 Skill 与角色 agent 按此执行；去合规化原则（素材不承担投放合规、效果导向，仅保留素材生产硬规范）同步生效。
3. 不做完整成片平台；不改现有剪辑系统；不重写 Toonflow Agent 框架；不引入其它 Agent 平台替代 Toonflow。
4. 本轮预算：付费生成 0；额外模型权重下载 0。默认 dry-run、次数限制、费用保护。

## 工程规则
- **本轮用户禁令优先（2026-10-11，h3-autodl-z0902-integration，Orca task_8c5482b0a195）**：用户明令禁止一切测试与任何 API 调用（原话："你只是对接，不允许测试"）。该禁令优先于本文件及模块文档中任何旧部署/验证/健康检查/测试条款——旧规则不得激活本轮执行；本轮仅静态文件实现与静态复核有效，运行时验收一律 NOT_RUN_USER_PROHIBITED。
- 先静态/依赖/接口检查，后耗时操作。安装命令退出码 0 ≠ 部署成功，必须有真实运行证据。
- 单故障最多 2 次修复尝试后重新诊断并报告。
- 依赖版本锁定（git tag/commit、lock 文件），下载有界。
- 自有文件 ≤45（第三方源码/锁/venv/自动证据除外）。h3-minimax-official-v1 用户授权附加后上限 49（仅 +4 个官方供应商新文件：adapter/CLI/测试/配置，见 module.yaml constraints）。h3-autodl-z0902-integration（2026-10-11，task_8c5482b0a195）本轮唯一新增 1 个文件（`config/autodl_h3_z0902.json`）后上限 50。seedream-image-v1（2026-10-11，用户直接请求"走火山引擎 seedream 生图"并"是的"确认接入）新增 2 个文件（`adapters/seedream_image.py`、`tests/test_seedream_image.py`）后上限 52。material-flow-v1（2026-10-11，用户请求"规划好问题，把流程走通"，天益清测试创作）新增 1 个编排脚本 `scripts/material-flow.py` 后上限 53（案例输入 `cases/tianyiqing/` 属案例数据）。
- 证据放 `runtime/evidence/<phase>/`（命令+cwd+exitcode+时间+精简输出，脱敏）；进度 `runtime/progress.json`；变更登记 `runtime/changes.json`；验收 `runtime/acceptance.json`。
- 状态词汇：verified / partial / blocked / not_run。禁止把 dry-run、health 检查、schema 校验称为"真实提交成功"；禁止用模拟服务/假 API/测试夹具冒充上游或合格生产资产。
- 后台进程：`Start-Process -WindowStyle Hidden`，绑定 loopback，先查端口冲突，只停本项目进程。
- 回退：按 `runtime/changes.json` 只回退自有新增；禁止删全目录/全 vendor/模型缓存。

## 文档
- 模块文档：`docs/modules/product-material-pipeline-v1/`（MODULE.md、module.yaml、USER_REQUIREMENTS.md、TASK_SPEC.md、IMPLEMENTATION_PLAN.md、DEPLOYMENT_REPORT.md）。
- 接续工作先读 `runtime/progress.json` 与 IMPLEMENTATION_PLAN 的 checkbox。

## 已知环境事实
- Windows（win32 10.0.19045），PowerShell 可用。
- `python`（PATH）= 3.14.3；系统 Python 3.10.9 用于本项目 venv；`node` = v24.21.0（主会话探测）/ v24.19.0（2026-10-11 Orca worker 实测）；`git` = 2.53.0.windows.3；GPU = RTX 3080 10240MiB driver 610.88。
- 2026-10-11 native headless 的 Bash 权限故障已随其终止关闭；Orca 工程闭环的真实 worker 可正常执行命令。
- Toonflow = Bun monorepo（v2.0.5, commit 2c7fb25）；Bun 1.3.14 已项目局部安装在 `vendor/tools/bun-windows-x64/`（不写全局）。

## Human score table (hard gate)

- **Hard gate (code-enforced):** `jobs` and `video-jobs` (via `_require_visual_review`) require a submitted `lab-human-score-table/v1` bound to current `review.json` / `creation.json` / case. Commands: `score-template`, `score-submit`, `score-show`.
- **Clef `gate` is advisory only** — `passed=false` or `clef_unavailable` does **not** block jobs. Prefills reference columns on the human table.
- `jobs --shots` must be a subset of `selected_ids` from the submitted table.

