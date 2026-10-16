# material-library：商品视频素材统一根目录

给自动剪辑软件按产品读取。目录结构固定，清单为准。

## 标准名称、标签与任务接续（2026-10-13 新增）

媒体三件套及 `lab-material-library/v3` 保持原样；新增可选 `catalog`，合同为 `lab-material-catalog/v1`。旧素材没有 catalog 仍可使用，不自动改审核状态。

- `clip_id` 和 mp4 文件名稳定；可读名称为 `商品简称_镜头角色_场景_主体动作_素材编号`，写在 `catalog.display_name`。
- 示例仅说明格式：`理线器_动作_办公桌_手整理线缆_lxc-101-v1`。这是素材名称，不是发布标题，也不烧录进画面。
- `catalog.labels` 按 role、scene、subject、action、shot_size、camera_motion、person_exposure、product_presence、story_function、visual_quality 分类；每个标签携带 code、中文 label、来源、证据编号，总计最多24项。
- `catalog.observations/evidence` 记录实际视频观察与时间戳帧；`content/edit/creative` 仍保留原计划信息。旧 jpg 可能是生成参考图，不能冒充实际视频首帧。
- `catalog.state` 为 draft/reviewed/stale/blocked，独立于媒体 approved/rejected 等状态。审核编目不会批准媒体。默认搜索只返回媒体可用、编目已复核、绑定和证据仍有效的条目。
- 图片序列分析不证明整段动作或音轨内容；`usable_segments` 首先是候选区间，不能由模型自行标记“已人工确认”。

主入口新增命令（在项目根目录使用现有 Python 环境）：

```text
scripts/material-flow.py catalog-plan --product <product_id> --clip <clip_id>
scripts/material-flow.py catalog-enqueue --product <product_id> --clip <clip_id>
scripts/material-flow.py catalog-run-once [--authorization runtime/<authorization>.json]
scripts/material-flow.py catalog-tasks [--product <product_id>]
scripts/material-flow.py catalog-review --task <task_id> --decision accept|reject --note <依据> [--by <复核者>]
scripts/material-flow.py catalog-search --product <product_id> [--role action] [--scene office_desk]
scripts/material-flow.py catalog-cancel --task <task_id>
```

`run-once` 每次推进一个可运行阶段；await_catalog_review 会转等待复核，复核接受后再次 run-once 才投影入库。队列记录位于 `runtime/material-analysis/queue.sqlite3`；同一绑定重复入队返回同一任务，不自动重做；结果不明的模型请求进入 uncertain，不能靠再次入队绕过。

**当前真实模型入口尚未接通。** `config/doubao_text.json.video_analysis` 默认 disabled、provider/model 为 null。现有实现通过注入式 transport 验证接口；实际 CLI 到分析阶段会停在 waiting_authorization / capability_unverified，不回退到文本模型。只改 enabled 也不会触发真实调用。已验证实际 mp4 读取与抽帧，不等于已验证语义分析。供应商能力、费用授权与完整视频语义复核仍需后续有界验收。

费用记录包含授权范围、次数、有效期、供应商/模型与单次费用上界；先保留额度再提交。v1 当前拒绝未知单次费用，不沿用旧生视频免报价记录。配置中的 `auto_enqueue` 默认 false；只登记任务，不自动运行模型。

本次定向测试和限制见 [实施记录](../runtime/evidence/material-catalog-scheduler-v1/report.md)。机器合同见 [catalog Schema](../schemas/material-catalog-v1.schema.json)。

```
material-library/
  index.json                      所有产品、各状态数量、可用秒数
  <product_id>/
    product.json                  产品卡：名称、包装事实、成分、定位、使用规则、人物、宣称策略
    manifest.json                 本产品全部素材条目（含待审和不合格，带状态）
    approved/                     审核完成、可以用于剪辑（approved 和 approved_with_caveats）
    pending/                      待审，剪辑软件不要读取
    rejected/                     已判不合格，不要用于剪辑
```

每个状态目录里，一条素材是三个文件：`<clip_id>.mp4`、同名 `.json` 说明、同名 `.jpg` 首帧。审核结论变化时，这三个文件一起搬到对应目录。

## 读取规则

1. 自动剪辑只读 `<product_id>/approved/`，或者读 `manifest.json` 里 `status` 为 `approved`、`approved_with_caveats` 的条目。
2. `pending/` 和 `rejected/` 不参与剪辑。
3. `approved_with_caveats` 的问题写在 `status_note`，剪辑时避开有问题的片段。
4. 路径都是相对产品目录的相对路径。

## 每条素材的说明字段

- `duration_sec` / `resolution`（1088x1920，9:16）/ `fps` / `sha256`：媒体事实，时长不超过 10 秒。
- `audio`：成片带音轨但并未要求声音，内容未验证为静音，请静音或替换。
- `content`：镜头类型 `content_mode`、故事点、口播对应 `narration_target`、有无人物 `person_exposure`、宣称标签 `claim_tags`。
- `edit`：混剪把手——`edit_role`、`hook_strength`(1–5)、`clean_in` / `clean_out`、`loopable`、`pairs_well_with`（镜头号）、`pairs_well_with_clips`（可衔接的素材号）、`text_safe_zone`、`mix_tags`。
- `creative`：联想链 `concept_association`、视觉隐喻 `visual_metaphor`、特效包装 `effects_packaging`、真实商品细节 `real_product_craft`。
- `qc`：亮度、是否黑屏、审核人与备注。
- `generation`：生成通道、提示词、请求指纹、首帧哈希、批次。

## 入库方式

`python scripts/material-flow.py video-poll --batch <批次> --download` 下载成功后自动入库，状态为 `pending_review`。
审核结论用 `video-status` 写入，不合格的素材移入产品目录下的 `rejected/`。同一镜头重做会得到新的 `-v2`，旧版本保留。

## 文件核对记录（2026-10-13）

本次仅核对文件与哈希，不新增审核结论。索引与清单记录天益清 18 条，其中 approved 13 条、approved_with_caveats 2 条、rejected 3 条；可用 81.8 秒。15 条可用视频均存在且 SHA256 与清单一致。

已拒绝条目 `tyq-026-v1` 的 `rejected/tyq-026-v1.mp4` 缺失；保留原拒绝记录，不补造媒体、不改审核状态。其余记录文件的核对见 [文件核对证据](../runtime/evidence/project-organization-20261013/library-file-audit.json)。素材库中的图片和视频可能与生成批次来源重复，按消费路径保留，详见 [项目总览](../README.md)。
