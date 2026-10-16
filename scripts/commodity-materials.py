"""Project-local CLI and read-only material review page; no paid HTTP operations."""
import argparse
import importlib
import json
import mimetypes
import os
import re
import socket
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / "runtime" / "commodity-material-ai-v1"
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
SECRET_KEYS = re.compile(r"^(?:authorization|cookie|set-cookie|.*api[_-]?key|.*auth[_-]?token|access[_-]?token|refresh[_-]?token|client[_-]?secret|token|secret|password|credential|credentials)$", re.I)


def clean(value):
    """Redact credential fields before any CLI/API/log output."""
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if SECRET_KEYS.match(str(k)) else clean(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                return json.dumps(clean(json.loads(value)), ensure_ascii=False, allow_nan=False)
            except (ValueError, TypeError, RecursionError):
                pass
        value = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value, flags=re.I)
        return re.sub(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{12,}", "[REDACTED]", value)
    return value


def json_bytes(value):
    return json.dumps(clean(value), ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")


def emit(value):
    sys.stdout.buffer.write(json_bytes(value) + b"\n")


def load_core():
    return importlib.import_module("adapters.commodity_ai")


def project_file(raw):
    path = Path(raw)
    if path.drive and not path.is_absolute():
        raise ValueError("MEDIA_DRIVE_RELATIVE_PATH_REFUSED")
    # abspath/normcase/commonpath normalize strings only. Refuse external literals
    # before resolve(), stat(), exists(), or any other filesystem metadata access.
    root_literal = os.path.normcase(os.path.abspath(str(ROOT)))
    candidate = os.path.abspath(str(path if path.is_absolute() else ROOT / path))
    try:
        inside = os.path.commonpath([root_literal, os.path.normcase(candidate)]) == root_literal
    except ValueError:
        inside = False
    if not inside:
        raise ValueError("MEDIA_OUTSIDE_PROJECT")
    path = Path(candidate).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("MEDIA_OUTSIDE_PROJECT")
    return path



def bridge_director(raw_file, raw_workspace, core):
    """Bounded saved Director JSON to the existing exclusive workspace bridge."""
    source = project_file(raw_file)
    if ".." in Path(raw_workspace).parts:
        raise ValueError("BRIDGE_WORKSPACE_PARENT_TRAVERSAL_REFUSED")
    workspace = project_file(raw_workspace)
    raw_path = Path(raw_workspace)
    literal_workspace = Path(os.path.abspath(str(raw_path if raw_path.is_absolute() else ROOT / raw_path)))
    if literal_workspace.is_symlink() or literal_workspace.exists():
        raise ValueError("BRIDGE_WORKSPACE_EXISTS_OR_LINK")
    bridge = importlib.import_module("adapters.storyboard_team")
    target = {"directory": str(workspace), "path": "commodity-director-shots.json"}
    # Reuse the adapter's actual freshness/canonical boundary guard before MCP.
    bridge._target_for_commodity_import(target)
    with source.open("rb") as handle:
        payload = handle.read(262145)
    if len(payload) > 262144:
        raise ValueError("DIRECTOR_JSON_TOO_LARGE")
    def reject_constant(_value):
        raise ValueError("DIRECTOR_JSON_NONFINITE")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("DIRECTOR_JSON_DUPLICATE_KEY")
            result[key] = value
        return result
    try:
        output = json.loads(payload.decode("utf-8"), parse_constant=reject_constant,
                            object_pairs_hook=unique_object)
        # Also refuse finite JSON syntax whose exponent overflows to Infinity.
        json.dumps(output, allow_nan=False)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("DIRECTOR_JSON_INVALID") from None
    import jsonschema
    try:
        jsonschema.Draft7Validator(core.SCHEMAS["director"]).validate(output)
    except jsonschema.ValidationError:
        raise ValueError("DIRECTOR_SCHEMA_INVALID") from None
    report = bridge.import_commodity_shots(output["shots"], target=target)
    return {**report,
            "input_file": str(source),
            "input_provenance": "user_provided_or_TEST_not_verified_as_Claude_output",
            "claude_chain_status": "not_run", "model_calls": 0,
            "note": "Saved user-provided / TEST input; this bridge does not prove real Claude generation."}

def error_record(exc):
    return {"status": "blocked", "error_code": getattr(exc, "code", type(exc).__name__),
            "message": str(exc)[:1000]}


def evidence_snapshot():
    """Read only explicit project-owned coordinator evidence; never model directories."""
    records = {}
    fixed = {
        "claude_exact_model": "runtime/evidence/commodity-material-ai-v1/claude-minimal-precheck.json",
        "clef_native": "runtime/evidence/commodity-material-ai-v1/clef-native-probe.json",
        "clef_static_model": "runtime/evidence/commodity-material-ai-v1/clef-gguf-static.json",
        "service_registry": "runtime/evidence/commodity-material-ai-v1/services-precheck.json",
        "vbench_environment": "runtime/evidence/deployment/vbench-check-1791699199.json",
    }
    for name, relative in fixed.items():
        path = ROOT / relative
        if not path.is_file():
            records[name] = {"status": "not_run", "source": relative}
            continue
        try:
            if path.stat().st_size > 1048576:
                raise ValueError("EVIDENCE_SIZE_LIMIT")
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            records[name] = {"source": relative, "file_modified_utc": datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc).isoformat(), "record": data}
        except (OSError, ValueError) as exc:
            records[name] = {"source": relative, **error_record(exc)}
    return clean(records)


def doctor(core=None):
    report = {"recorded_at": datetime.now(timezone.utc).isoformat(),
              "model_id": core.MODEL_ID if core else "claude-opus-5-5", "h3_invoked": False,
              "h3": {"status": "blocked", "reason": "current path boundary and zero paid budget"}}
    for name, run in (("claude", lambda: core.ClaudeCLIAdapter(root=ROOT).preflight()),
                      ("clef", lambda: core.ClefAdapter().probe())):
        try:
            report[name] = run() if core else {"status": "not_run", "error_code": "CORE_UNAVAILABLE"}
        except Exception as exc:
            report[name] = error_record(exc)
    caps = ROOT / "config" / "vbench_capabilities.json"
    if caps.is_file():
        report["vbench"] = {"status": "not_run", "source": str(caps),
                            "capabilities": json.loads(caps.read_text(encoding="utf-8"))}
    else:
        report["vbench"] = {"status": "not_run", "reason": "capability record unavailable"}
    report["known_preflight_evidence"] = {
        "source": "docs/modules/commodity-material-ai-v1/MODULE.md",
        "recorded_date": "2026-10-11",
        "note": "Historical real-call result; auth status alone does not revalidate model access."}
    report["recorded_evidence"] = evidence_snapshot()
    actual = report["recorded_evidence"].get("claude_exact_model", {}).get("record", {})
    try:
        payload = json.loads(actual.get("stdout", "{}"))
    except (TypeError, ValueError):
        payload = {}
    output = payload.get("structured_output", {}) if isinstance(payload, dict) else {}
    exact_success = actual.get("exit_code") == 0 and isinstance(payload, dict) and not payload.get("is_error", False) and \
        isinstance(output, dict) and output.get("ok") is True and \
        output.get("model_request") == report["model_id"]
    report["model_callability"] = {
        "status": "verified" if exact_success else "blocked" if actual.get("exit_code") else "not_run",
        "reason": "Recorded exact-model structured test; live auth preflight alone cannot replace it.",
        "evidence_time": actual.get("time"), "exit_code": actual.get("exit_code")}
    report["status"] = "verified" if exact_success and isinstance(report.get("claude"), dict) and \
        report["claude"].get("status") in ("verified", "ready") and isinstance(report.get("clef"), dict) and \
        report["clef"].get("status") == "verified" else "blocked"
    return report


PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>商品素材审核</title>
<style>body{margin:0;background:#f3f5f7;color:#202936;font:15px system-ui,sans-serif}header,main{max-width:1250px;margin:auto;padding:24px}header{padding-bottom:0}h1{margin:0 0 8px;font-size:27px}.notice{padding:12px;background:#fff1ce;border:1px solid #e2be62;border-radius:8px}.toolbar{display:flex;gap:12px;flex-wrap:wrap;margin:20px 0}input,select,button{font:inherit;padding:9px;border:1px solid #bcc5d0;border-radius:6px;background:white}#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(265px,1fr));gap:16px}.card,.panel{background:white;border:1px solid #dae0e7;border-radius:10px;padding:16px}.card h3{margin:0 0 10px;word-break:break-all}.meta{line-height:1.8;color:#556171}.flag{display:block;font-weight:700;color:#9e5b00;margin:8px 0}.empty{padding:28px;border:1px dashed #9caaba;border-radius:8px}video{width:100%;max-height:360px;background:#121821;margin-top:10px}pre{white-space:pre-wrap;word-break:break-word;font:13px/1.6 ui-monospace,monospace;background:#f6f8fa;padding:14px;border-radius:6px;max-height:650px;overflow:auto}.panel{margin-top:20px}.compare{display:grid;grid-template-columns:1fr 1fr;gap:14px}.gallery{display:flex;gap:12px;flex-wrap:wrap}.gallery img,.thumb{max-width:240px;max-height:180px;object-fit:contain}.gallery figure{margin:0;max-width:250px}summary{cursor:pointer;padding:8px 0}h2{font-size:20px}a{color:#156cba}.muted{color:#647285}@media(max-width:700px){.compare{grid-template-columns:1fr}header,main{padding:16px}}</style></head>
<body><header><h1>商品素材审核</h1><p class="muted">保留全部版本 · 只读查询 · 批准素材单独导出</p>
<div class="notice">首次接入验证：Mock / 样例记录不代表真实 H3 生成或生产验收。文字审核不能验证画面。此页面只读，不执行批准、付费生成或删除。</div></header>
<main><div class="toolbar"><input id="search" placeholder="按镜头、商品或素材 ID 查询" aria-label="查询素材"><button id="refresh">刷新</button><span id="count"></span></div>
<div id="grid"></div><section id="detail" class="panel" hidden><h2>素材与审核详情</h2><div id="detailVideo"></div>
<h2>缩略图与实际关键帧</h2><div id="gallery" class="gallery"></div>
<div class="compare"><div><h2>生成前审核 A</h2><div id="preReviews"></div></div><div><h2>生成后审核 B</h2><div id="postReviews"></div></div></div>
<h2>创作与返修记录（未绑定审核阶段）</h2><div id="creativeRecords"></div>
<h2>完整阶段历史</h2><p class="muted">按核心登记的运行与镜头显示；阶段名称不能代替实际审核证据。</p><div id="stageRecords"></div>
<h2>尚未验证的项目</h2><pre id="unverified"></pre>
<p class="muted">下面显示完整原始记录，包含实际输入范围、审核状态和未验证项目；没有检测证据时不推断通过。</p>
<details open><summary>素材、分镜、生成前/后审核及未验证项</summary><pre id="detailJson"></pre></details>
<details open><summary>版本与使用历史</summary><pre id="historyJson"></pre></details>
<h2>同镜头版本比较</h2><div class="toolbar"><select id="compareA" aria-label="版本 A"></select><select id="compareB" aria-label="版本 B"></select></div>
<div class="compare"><div><div id="videoA"></div><pre id="jsonA"></pre></div><div><div id="videoB"></div><pre id="jsonB"></pre></div></div></section>
<section class="panel"><details><summary>运行状态与真实阻塞</summary><pre id="status"></pre></details></section></main>
<script>
'use strict';let takes=[];const byId=id=>document.getElementById(id);
function text(el,value){el.textContent=typeof value==='string'?value:JSON.stringify(value,null,2)}
function field(t,k){return t[k]??(t.shot&&t.shot[k])??''}
function approvalLabel(t){return String(t.approval_status??'').toUpperCase()==='APPROVED'?'APPROVED':'未批准'}
async function get(url){const r=await fetch(url,{cache:'no-store'});const data=await r.json();if(!r.ok)throw new Error(data.message||data.error_code||'读取失败');return data}
function video(el,t){el.replaceChildren();if(t.ui_media_url){const v=document.createElement('video');v.controls=true;v.preload='metadata';v.src=t.ui_media_url;el.append(v)}else{const p=document.createElement('p');p.className='muted';text(p,'没有本轮允许读取的已登记视频；不生成替代素材。');el.append(p)}}
function reviewRole(r){return String(r.role??r.structured_output?.role??r.stage??'').toLowerCase()}
function reviewDecision(r){for(const value of [r.review_status,r.structured_output?.review_status,r.verdict])if(typeof value==='string'&&value.trim())return value;return null}
function reviewLabel(r){const decision=reviewDecision(r);const mock=r.mock===true||String(r.status??'').toLowerCase()==='mock'||String(r.source??'').includes('deterministic');const nested=r.structured_output??{};const visualUnverified=r.phase==='B'&&([r.visual_status,nested.visual_status].some(v=>String(v??'').toUpperCase()==='UNVERIFIED')||[r.continuous_motion_status,nested.continuous_motion_status].some(v=>String(v??'').toUpperCase()==='NEEDS_REVIEW'));let label=visualUnverified?'NEEDS_REVIEW / 视觉或连续动作未验证（原始文本决策：'+(decision??'无')+'）':(decision??'未登记审核结论');if(mock)label='MOCK / 非生产审核 · '+label;return {label,flag:mock||visualUnverified}}
function reviews(t,h){const found=[];const seen=new Set();const roles=['director','writer','reviewer'];function walk(x,depth){if(!x||typeof x!=='object'||depth>12)return;if(!Array.isArray(x)&&((x.phase==='A'||x.phase==='B')||x.review_id||x.call_id||roles.includes(reviewRole(x))||(['claude','clef'].includes(x.source)&&typeof x.status==='string'))){const key=JSON.stringify(x);if(!seen.has(key)){seen.add(key);found.push(x)}return}for(const [k,v]of Object.entries(x)){if(k!=='raw_result'&&k!=='raw_response'&&k!=='stage_records')walk(v,depth+1)}}walk(t,0);walk(h,0);return found}
function stageSections(t,h){const el=byId('stageRecords');el.replaceChildren();const found=[];const seen=new Set();for(const source of [t.stage_records,h.stage_records]){if(!Array.isArray(source))continue;for(const item of source){if(!item||typeof item!=='object')continue;const key=item.record_id??JSON.stringify(item);if(!seen.has(key)){seen.add(key);found.push(item)}}}if(!found.length){const p=document.createElement('p');text(p,'核心尚未提供此素材的阶段记录；缺少记录不能视为执行完成。');el.append(p);return}const names={director:'Director 创作',writer:'Writer 镜头规格',writer_fallback:'Writer 回退',review_a:'生成前审核 A',h3:'H3 数据链路',h3_mock:'H3 Mock 数据链路',review_b:'生成后审核 B',reviewer:'Reviewer 返修',reviewer_fallback:'Reviewer 回退'};for(const item of found){const r=item.record&&typeof item.record==='object'?item.record:item;const stage=String(item.stage??r.stage??'未登记阶段');const mock=r.mock===true||item.mock===true||String(r.status??'').toLowerCase()==='mock'||String(r.provenance??'').toLowerCase().includes('mock');const d=document.createElement('details');d.open=true;const title=document.createElement('summary');const decision=reviewDecision(r);const reviewInfo=reviewLabel(r);const conclusion=decision?reviewInfo.label:'未登记审核结论';text(title,`${mock?'MOCK / 非生产记录 · ':''}${names[stage]??stage} · ${conclusion} · 调用状态：${r.status??'未登记'} · 镜头：${item.shot_id??r.shot_id??'本次运行共享记录'} · 运行：${item.run_id??r.run_id??'未登记'}${r.error_code?' · 错误：'+r.error_code:''}`);if(mock||reviewInfo.flag||['blocked','skipped'].includes(r.status))title.className='flag';const raw=document.createElement('pre');text(raw,item);d.append(title,raw);el.append(d)}}
function reviewSections(t,h){const all=reviews(t,h);for(const [phase,target]of [['A','preReviews'],['B','postReviews'],[null,'creativeRecords']]){const el=byId(target);el.replaceChildren();const items=all.filter(r=>phase?r.phase===phase:!(r.phase==='A'||r.phase==='B'));if(!items.length){const p=document.createElement('p');text(p,phase?'尚无此阶段审核记录；缺少记录不能视为完成或通过。':'尚无创作或返修调用记录；缺少记录不能视为完成。');el.append(p)}for(const r of items){const d=document.createElement('details');d.open=true;const s=document.createElement('summary');const info=reviewLabel(r);const role=reviewRole(r)||'未登记角色';const scope=phase?(r.actual_input_modality??r.input_modality??'输入范围见原始记录'):`${role} · 未绑定 A/B 审核阶段`;text(s,`${info.label} · ${scope} · 调用状态：${r.status??'未登记'} · ${r.review_record_id??r.review_id??r.call_id??''}`);if(info.flag||!phase)s.className='flag';const pre=document.createElement('pre');text(pre,r);d.append(s,pre);el.append(d)}}const missing=[];function scan(x,depth){if(!x||typeof x!=='object'||depth>12)return;if(!Array.isArray(x)&&String(x.verification_status??x.status??'').toUpperCase()==='UNVERIFIED')missing.push(x);for(const [k,v]of Object.entries(x)){if(typeof v==='string'&&(v.toUpperCase()==='UNVERIFIED'||(k==='continuous_motion_status'&&v.toUpperCase()==='NEEDS_REVIEW')))missing.push({[k]:v});if(/unverified/i.test(k)&&v&&(!Array.isArray(v)||v.length))missing.push({[k]:v});scan(v,depth+1)}}scan(t,0);scan(h,0);text(byId('unverified'),missing.length?missing:'未登记明确的未验证项目；缺少记录不能视为完成或通过。请查看实际输入范围与审核证据。');const gallery=byId('gallery');gallery.replaceChildren();const frames=[...(t.ui_keyframes||[])];if(t.ui_thumbnail_url)frames.unshift({url:t.ui_thumbnail_url,kind:'缩略图'});for(const frame of frames){const fig=document.createElement('figure');const img=document.createElement('img');img.loading='lazy';img.src=frame.url;img.alt=frame.kind||'实际关键帧';const cap=document.createElement('figcaption');text(cap,`${frame.kind||'关键帧'} · ${frame.timestamp_sec==null?'未记录时间点':frame.timestamp_sec+' 秒'}`);fig.append(img,cap);gallery.append(fig)}if(!frames.length){const p=document.createElement('p');text(p,'没有登记且可读取的缩略图或关键帧；不以占位图替代画面证据。');gallery.append(p)}}
function draw(){const q=byId('search').value.trim().toLowerCase();const visible=takes.filter(t=>JSON.stringify([t.take_id,field(t,'shot_id'),field(t,'product_id')]).toLowerCase().includes(q));text(byId('count'),`显示 ${visible.length} / 全部 ${takes.length} 条版本`);const grid=byId('grid');grid.replaceChildren();if(!visible.length){const p=document.createElement('div');p.className='empty';text(p,takes.length?'没有匹配记录。':'目前没有素材记录。可先运行 dry-run；没有真实视频时只显示明确的 Mock 数据链路。');grid.append(p)}for(const t of visible){const card=document.createElement('article');card.className='card';const h=document.createElement('h3');text(h,t.take_id||'记录缺少素材 ID');card.append(h);const meta=document.createElement('div');meta.className='meta';text(meta,`镜头：${field(t,'shot_id')}\n商品：${field(t,'product_id')}\n版本：${t.version??''}　批次：${t.batch_id??''}\n素材状态：${t.status??'查看原始记录'}\n批准状态：${approvalLabel(t)}`);meta.style.whiteSpace='pre-line';card.append(meta);const f=document.createElement('span');f.className='flag';text(f,t.ui_mock?'MOCK / 样例 / 非生产资产':'真实属性以审核证据为准');card.append(f);if(t.ui_thumbnail_url){const img=document.createElement('img');img.className='thumb';img.src=t.ui_thumbnail_url;img.alt='已登记缩略图';card.append(img)}const preview=document.createElement('div');video(preview,t);card.append(preview);const b=document.createElement('button');text(b,'查看审核与版本');b.addEventListener('click',()=>openTake(t.take_id).catch(showError));b.disabled=!t.take_id;card.append(b);grid.append(card)}}
function showError(e){text(byId('status'),{status:'blocked',message:e.message});byId('status').parentElement.open=true}
async function openTake(id){const [t,h]=await Promise.all([get('/api/takes/'+encodeURIComponent(id)),get('/api/takes/'+encodeURIComponent(id)+'/history')]);byId('detail').hidden=false;text(byId('detailJson'),t);text(byId('historyJson'),h);video(byId('detailVideo'),t);reviewSections(t,h);stageSections(t,h);const same=takes.filter(x=>field(x,'shot_id')===field(t,'shot_id'));for(const name of ['compareA','compareB']){const s=byId(name);s.replaceChildren();for(const x of same){const o=document.createElement('option');o.value=x.take_id;text(o,`${x.take_id} · v${x.version??'?'} · ${x.status??x.review_status??''}`);s.append(o)}}byId('compareA').value=id;byId('compareB').value=(same.find(x=>x.take_id!==id)||t).take_id;await compare();byId('detail').scrollIntoView({behavior:'smooth'})}
async function compare(){for(const suffix of ['A','B']){const id=byId('compare'+suffix).value;if(!id)continue;const t=await get('/api/takes/'+encodeURIComponent(id));text(byId('json'+suffix),t);video(byId('video'+suffix),t)}}
async function refresh(){[takes]=await Promise.all([get('/api/takes'),get('/api/status').then(s=>text(byId('status'),s))]);draw()}
byId('search').addEventListener('input',draw);byId('refresh').addEventListener('click',()=>refresh().catch(showError));for(const suffix of ['A','B'])byId('compare'+suffix).addEventListener('change',()=>compare().catch(showError));refresh().catch(showError);
</script></body></html>'''


class ReviewServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, store, status):
        self.store, self.status = store, clean(status)
        self.store_lock = threading.RLock()
        super().__init__(address, ReviewHandler)


def takes_list(store):
    takes = store.list_takes()
    if not isinstance(takes, list) or any(not isinstance(t, dict) for t in takes):
        raise ValueError("STORE_LIST_CONTRACT_INVALID")
    return takes


def video_path_for(take):
    media = take.get("media")
    if isinstance(media, dict) and isinstance(media.get("video_path"), str):
        return media["video_path"]
    return take.get("video_path")


def media_items(take):
    """Explicit registered media fields only; raw responses and reference inputs are excluded."""
    items = []
    video_path = video_path_for(take)
    if isinstance(video_path, str) and video_path:
        items.append({"path": video_path, "kind": "video"})

    def walk(record, depth=0):
        if not isinstance(record, dict) or depth > 8:
            return
        if isinstance(record.get("thumbnail_path"), str) and record["thumbnail_path"]:
            items.append({"path": record["thumbnail_path"], "kind": "thumbnail"})
        for key in ("keyframes", "frames"):
            frames = record.get(key)
            for frame in frames if isinstance(frames, list) else []:
                if isinstance(frame, dict) and isinstance(frame.get("path"), str):
                    items.append({"path": frame["path"], "kind": "keyframe",
                                  "timestamp_sec": frame.get("timestamp_sec")})
        for key in ("media", "reviews", "review", "evidence", "pre_reviews", "post_reviews", "review_records"):
            value = record.get(key)
            for child in value if isinstance(value, list) else [value]:
                walk(child, depth + 1)

    walk(take)
    return items


def media_paths(take):
    """Allow only registered video/image MIME types, never textual evidence files."""
    result = set()
    for item in media_items(take):
        try:
            path = project_file(item["path"])
            mime = mimetypes.guess_type(str(path))[0] or ""
            valid = path.suffix.lower() in (".mp4", ".mov", ".webm", ".m4v") if item["kind"] == "video" else \
                path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
            if valid and mime.startswith(("video/", "image/")) and path.is_file():
                result.add(path)
        except (ValueError, OSError):
            continue
    return result


def media_record(take, history=None):
    record = dict(take)
    if isinstance(history, dict) and isinstance(history.get("reviews"), list):
        record["review_records"] = history["reviews"]
    return record


def ui_take(take, history=None):
    from urllib.parse import quote
    result = dict(take)
    result["ui_media_url"] = None
    result["ui_thumbnail_url"] = None
    result["ui_keyframes"] = []
    registered = media_record(take, history)
    allowed = media_paths(registered)
    seen = set()
    for item in media_items(registered):
        try:
            path = project_file(item["path"])
            if path not in allowed:
                continue
            url = "/media?path=" + quote(str(path.relative_to(ROOT.resolve())).replace("\\", "/"), safe="")
            if item["kind"] == "video":
                result["ui_media_url"] = url
            elif item["kind"] == "thumbnail":
                result["ui_thumbnail_url"] = url
            elif (url, str(item.get("timestamp_sec"))) not in seen:
                result["ui_keyframes"].append({"url": url, "timestamp_sec": item.get("timestamp_sec"), "kind": "关键帧"})
                seen.add((url, str(item.get("timestamp_sec"))))
        except (ValueError, OSError):
            pass
    result["ui_mock"] = bool(take.get("mock") or take.get("test_flag") or
                             "mock" in str(take.get("provenance", "")).lower() or not video_path_for(take))
    return clean(result)


def byte_range(header, size):
    if not header:
        return 0, size - 1
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("INVALID_RANGE")
    first, last = match.groups()
    if not first:
        length = int(last)
        if length <= 0:
            raise ValueError("INVALID_RANGE")
        return max(0, size - length), size - 1
    start, end = int(first), int(last) if last else size - 1
    if start >= size or start > end:
        raise ValueError("INVALID_RANGE")
    return start, min(end, size - 1)


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass  # Request paths may contain sensitive user strings; no raw URL logging.

    def send_body(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            self.send_body(403, json_bytes({"error_code": "LOCAL_HOST_REQUIRED"}))
            return
        parsed = urlsplit(self.path)
        try:
            with self.server.store_lock:
                if parsed.path == "/":
                    self.send_body(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
                elif parsed.path == "/api/status":
                    self.send_body(200, json_bytes(self.server.status))
                elif parsed.path == "/api/takes":
                    self.send_body(200, json_bytes([ui_take(t) for t in takes_list(self.server.store)]))
                elif parsed.path.startswith("/api/takes/"):
                    parts = parsed.path[len("/api/takes/"):].split("/")
                    take_id = unquote(parts[0])
                    if not ID_RE.fullmatch(take_id) or len(parts) > 2 or (len(parts) == 2 and parts[1] != "history"):
                        self.send_body(404, json_bytes({"error_code": "NOT_FOUND"}))
                        return
                    take = self.server.store.get_take(take_id)
                    if not isinstance(take, dict):
                        self.send_body(404, json_bytes({"error_code": "TAKE_NOT_FOUND"}))
                        return
                    history = self.server.store.history(take_id)
                    data = history if len(parts) == 2 else ui_take(take, history)
                    self.send_body(200, json_bytes(data))
                elif parsed.path == "/media":
                    self.serve_media(parsed.query)
                else:
                    self.send_body(404, json_bytes({"error_code": "NOT_FOUND"}))
        except (KeyError, FileNotFoundError):
            self.send_body(404, json_bytes({"error_code": "NOT_FOUND"}))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self.send_body(400, json_bytes(error_record(exc)))

    def serve_media(self, query):
        paths = parse_qs(query).get("path", [])
        if len(paths) != 1:
            self.send_body(400, json_bytes({"error_code": "ONE_MEDIA_PATH_REQUIRED"}))
            return
        try:
            path = project_file(paths[0])
        except (ValueError, OSError):
            self.send_body(403, json_bytes({"error_code": "MEDIA_NOT_REGISTERED"}))
            return
        allowed = set()
        for take in takes_list(self.server.store):
            history = self.server.store.history(take["take_id"]) if take.get("take_id") else None
            allowed.update(media_paths(media_record(take, history)))
        if path not in allowed:
            self.send_body(403, json_bytes({"error_code": "MEDIA_NOT_REGISTERED"}))
            return
        # Open first, then derive range from this handle; no user-path file metadata reads.
        with path.open("rb") as media:
            import os
            size = os.fstat(media.fileno()).st_size
            try:
                start, end = byte_range(self.headers.get("Range"), size)
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            partial = bool(self.headers.get("Range"))
            length = max(0, end - start + 1)
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", mimetypes.guess_type(str(path))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            media.seek(start)
            while length:
                chunk = media.read(min(length, 65536))
                if not chunk:
                    break
                self.wfile.write(chunk)
                length -= len(chunk)

    def do_POST(self):
        self.send_body(405, json_bytes({"error_code": "READ_ONLY_GET_ONLY"}))

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_HEAD = do_POST


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="商品素材本地只读审核与命令入口")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="检查实际接口与阻塞；不发起创作/H3调用")
    commands.add_parser("dry-run", help="零H3数据链路验证，所有Mock均明确标记")
    run = commands.add_parser("run", help="显式模式入口；shadow只追加审核观察，production当前禁止")
    run.add_argument("--mode", choices=("dry_run", "shadow_review", "production"), default="dry_run")
    run.add_argument("--take-id", help="shadow_review所需已登记素材")
    run.add_argument("--phase", choices=("A", "B"), default="B")
    serve = commands.add_parser("serve", help="启动127.0.0.1只读审核页")
    serve.add_argument("--port", type=int, default=3011)
    schema = commands.add_parser("schema", help="导出核心JSON Schema")
    schema.add_argument("role", choices=("director", "writer", "reviewer", "manifest"), nargs="?")
    export = commands.add_parser("export", help="仅导出核心已批准素材Manifest")
    export.add_argument("take_id")
    export.add_argument("--output", help="项目runtime内输出路径；默认仅标准输出")
    bridge = commands.add_parser("bridge", help="严格校验保存的Director JSON并桥接至新Toonflow工作区；不调用模型")
    bridge.add_argument("director_json_file", help="本项目内UTF-8 JSON，最多262144字节")
    bridge.add_argument("--workspace", required=True, help="本项目Toonflow workspaces内的新直接子目录；禁止覆盖")
    args = parser.parse_args(argv)
    try:
        if args.command == "run" and args.mode == "production":
            emit({"status": "blocked", "error_code": "PRODUCTION_BLOCKED",
                  "mode": "production", "h3_invoked": False, "paid_h3_calls": 0,
                  "reason": "Current zero paid budget and external Runtime path boundary prohibit production submission."})
            return 1
        if args.command == "doctor":
            try:
                core = load_core()
            except ModuleNotFoundError:
                core = None
            report = doctor(core)
            emit(report)
            return 0 if report["status"] == "verified" else 1
        core = load_core()
        if args.command == "bridge":
            report = bridge_director(args.director_json_file, args.workspace, core)
            emit(report)
            return 0 if report.get("status") == "verified" else 1
        elif args.command == "schema":
            emit(core.SCHEMAS[args.role] if args.role else core.SCHEMAS)
        elif args.command == "dry-run" or (args.command == "run" and args.mode == "dry_run"):
            emit(core.run_dry_run(root=ROOT))
        elif args.command == "run" and args.mode == "shadow_review":
            if not args.take_id or not ID_RE.fullmatch(args.take_id):
                raise ValueError("SHADOW_REVIEW_REQUIRES_VALID_TAKE_ID")
            store = core.MaterialStore(root=ROOT)
            take = store.get_take(args.take_id)
            if not isinstance(take, dict) or not isinstance(take.get("shot"), dict):
                raise ValueError("SHADOW_SHOT_RECORD_UNAVAILABLE")
            review = core.ClefAdapter().review(args.phase, take["shot"], evidence=take)
            stored = store.append_review(args.take_id, review, mode="shadow_review")
            emit({"mode": "shadow_review", "take_id": args.take_id, "review": review,
                  "stored_record": stored, "approval_changed": False, "h3_invoked": False})
        elif args.command == "export":
            if not ID_RE.fullmatch(args.take_id):
                raise ValueError("INVALID_TAKE_ID")
            manifest = core.MaterialStore(root=ROOT).export_manifest(args.take_id)
            if args.output:
                out = project_file(args.output)
                if not out.is_relative_to((ROOT / "runtime").resolve()):
                    raise ValueError("OUTPUT_MUST_BE_PROJECT_RUNTIME")
                out.parent.mkdir(parents=True, exist_ok=True)
                with out.open("xb") as handle:
                    handle.write(json_bytes(manifest))
                emit({"status": "verified", "manifest_path": str(out)})
            else:
                emit(manifest)
        elif args.command == "serve":
            if not 1 <= args.port <= 65535:
                raise ValueError("INVALID_PORT")
            # Bind atomically and fail on conflicts; never stop/restart another service.
            store = core.MaterialStore(root=ROOT)
            server = ReviewServer(("127.0.0.1", args.port), store,
                                  {"status": "partial", "note": "Run doctor for live interface checks.",
                                   "h3_invoked": False, "paid_h3_calls": 0,
                                   "recorded_evidence": evidence_snapshot(),
                                   "known_blockers": ["Claude OAuth expired in recorded real call",
                                                      "Clef capability unverified; recorded port not listening",
                                                      "VBench scoring not_run; human metrics missing mmcv/retinaface; existing lightweight cache retained"]})
            emit({"status": "verified", "url": f"http://127.0.0.1:{args.port}",
                  "scope": "read-only review page; model/production acceptance unchanged"})
            sys.stdout.flush()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        return 0
    except ModuleNotFoundError as exc:
        emit({"status": "blocked", "error_code": "CORE_UNAVAILABLE", "message": str(exc)[:500]})
        return 2
    except Exception as exc:
        emit(error_record(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
