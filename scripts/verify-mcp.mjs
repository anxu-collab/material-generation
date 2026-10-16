#!/usr/bin/env node
// verify-mcp.mjs — Toonflow v2.0.5 原生 MCP 协议客户端实测（initialize → tools/list → 真实非计费工具调用）。
// 说明：这是"协议客户端实测"，不是把 MCP server 挂载进任何全局工具配置；不修改全局 Codex/ZCode。
// dry-run 语义：真实协议握手与安全工具调用照常执行；绝不调用 runAgent/媒体生成/供应商提交等计费能力。
// R2 加固（repair1）：每次 RPC 有 AbortSignal 超时；非 200 HTTP / JSON-RPC error / 工具 isError 一律判失败；
// 永久脚本内完成真实画布/节点 创建→读取→删除 生命周期（用官方工具 schema 发现节点类型，不猜类型名）；
// 无已连接可用页面时先尝试无头 Chrome 真实连接，仍无页面 → exit 3（unavailable，不是 pass）。
// 证据输出：runtime/evidence/deployment/mcp-verify-<ts>.json（token/authorization 一律脱敏，绝不落盘）。
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { spawn } from "node:child_process";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const args = process.argv.slice(2);
if (!args.includes("--dry-run")) {
  console.error("refusing: only --dry-run mode is implemented (billing-capable modes are out of scope this round)");
  process.exit(2);
}
if (args.filter(a => a.startsWith("--")).some(a => a !== "--dry-run")) {
  console.error("refusing: unknown flags; only --dry-run is supported");
  process.exit(2);
}

const RPC_TIMEOUT_MS = 15000;        // bounded per-RPC timeout (R2)
const OVERALL_TIMEOUT_MS = 180000;   // bounded whole test (R2)
const CHROME_WAIT_MS = 45000;        // bounded headless-chrome page-connect attempt
const OPEN_PROJECT_SETTLE_MS = 5000; // page-side project open settle (official flow)
const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const CDP_PORT = 9224;
const NEVER_CALL = ["runAgent", "media generation tools", "provider submits"];
const REQUIRED_TOOLS = ["getAppState", "workspaceFiles", "openProject", "getCanvas", "addNode", "findCanvasNodes", "getCanvasNodes", "deleteNodes"];

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const config = JSON.parse(readFileSync(resolve(root, "config/mcp.json"), "utf8"));
const endpoint = config.server.endpoint;
const settingsPath = resolve(root, config.auth.token_source.path);
const settings = JSON.parse(readFileSync(settingsPath, "utf8"));
const token = settings?.settings?.mcp?.token;
if (typeof token !== "string" || token.length < 32) {
  console.error(`token missing/too short in ${settingsPath} (official rule: length>=32)`);
  process.exit(2);
}

const evidence = { generated_at: new Date().toISOString(), client_mode: "protocol-client (NOT globally mounted)", endpoint, dry_run: true, steps: [], summary: {} };
let sessionId = null;
let exitCode = 0;

function trim(value, max = 1600) {
  const s = typeof value === "string" ? value : JSON.stringify(value);
  return s.length > max ? s.slice(0, max) + `...<trimmed ${s.length - max} chars>` : s;
}

async function rpc(method, params, { notify = false, allowError = false } = {}) {
  const body = { jsonrpc: "2.0", method, ...(notify ? {} : { id: evidence.steps.length + 1 }) };
  if (params !== undefined) body.params = params;
  const headers = {
    "content-type": "application/json",
    "authorization": `Bearer ${token}`, // never logged
    "accept": "application/json, text/event-stream",
    ...(sessionId ? { "mcp-session-id": sessionId } : {}),
  };
  let res, text;
  try {
    res = await fetch(endpoint, { method: "POST", headers, body: JSON.stringify(body), signal: AbortSignal.timeout(RPC_TIMEOUT_MS) });
    text = await res.text();
  } catch (e) {
    const step = { method, transport_error: `${e.name}: ${e.message}` };
    evidence.steps.push(step);
    throw new Error(`rpc ${method} transport failure (bounded ${RPC_TIMEOUT_MS}ms): ${e.name}: ${e.message}`);
  }
  if (!sessionId && res.headers.get("mcp-session-id")) sessionId = res.headers.get("mcp-session-id");
  const contentType = res.headers.get("content-type") ?? "";
  let payload = null;
  if (contentType.includes("text/event-stream")) {
    const datas = text.split(/\r?\n/).filter(l => l.startsWith("data:")).map(l => l.slice(5).trim());
    const last = datas[datas.length - 1];
    if (last && last !== "[DONE]") { try { payload = JSON.parse(last); } catch { payload = { raw: last }; } }
  } else if (text) {
    try { payload = JSON.parse(text); } catch { payload = { raw: text }; }
  }
  const step = { method, http_status: res.status, ...(notify ? {} : { response: payload?.error ? { error: payload.error } : { result: trim(payload?.result) } }) };
  evidence.steps.push(step);
  // MCP Streamable HTTP：通知的规范响应是 202 Accepted；请求（带 id）必须是 200（R2：非成功状态一律拒绝）
  const statusOk = notify ? (res.status === 200 || res.status === 202) : res.status === 200;
  if (!statusOk) {
    throw new Error(`rpc ${method} non-success HTTP ${res.status} (rejected, R2)`);
  }
  if (payload?.error && !allowError) {
    throw new Error(`rpc ${method} JSON-RPC error (rejected, R2): ${JSON.stringify(payload.error).slice(0, 300)}`);
  }
  return { status: res.status, payload };
}

// tools/call 包装（R2 严格模式，协调方 2026-10-11 反馈后统一）：传输/协议错误、工具 isError、
// 缺少合法 result.content 一律抛错——不存在"带着错误继续算成功"的放行路径
async function callTool(name, argsObj) {
  const r = await rpc("tools/call", { name, arguments: argsObj === undefined ? {} : argsObj });
  const result = r.payload?.result;
  const textOut = result?.content?.[0]?.text;
  if (result?.isError === true) {
    throw new Error(`tool ${name} isError (rejected, R2 strict): ${trim(typeof textOut === "string" ? textOut : JSON.stringify(result), 300)}`);
  }
  if (!result || !Array.isArray(result.content) || result.content.length === 0 || typeof textOut !== "string") {
    throw new Error(`tool ${name} returned no valid result.content (rejected, R2 strict)`);
  }
  let parsed = null;
  try { parsed = JSON.parse(textOut); } catch { /* 文本型结果保持原样；需要形状的调用方必须显式校验 parsed */ }
  return { textOut, parsed };
}

// 无头 Chrome 真实页面连接（只在无已连接页面时才启动；自启的实例由 finally 关闭）
async function connectPageViaChrome() {
  let spawned = null;
  let targets = null;
  try { targets = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`, { signal: AbortSignal.timeout(3000) })).json(); } catch { targets = null; }
  if (!targets) {
    if (!(await existsFile(CHROME))) return { connected: false, chrome: null, reason: `no connected page and chrome not found at ${CHROME}` };
    spawned = spawn(CHROME, [
      "--headless=new", `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${resolve(root, "runtime/tmp/verify-mcp-chrome-profile")}`,
      "--no-first-run", "--no-default-browser-check", "--window-size=1440,900", "--disable-gpu", "about:blank",
    ], { stdio: "ignore" });
    for (let i = 0; i < 30; i++) {
      await sleep(500);
      if (spawned.exitCode !== null) return { connected: false, chrome: spawned, reason: `chrome exited early (code ${spawned.exitCode})` };
      try { targets = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`, { signal: AbortSignal.timeout(2000) })).json(); break; } catch {}
    }
    if (!targets) return { connected: false, chrome: spawned, reason: "chrome CDP endpoint not ready" };
  }
  const page = targets.find(t => t.type === "page");
  if (!page) return { connected: false, chrome: spawned, reason: "no page target in chrome" };
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error("cdp websocket failed")); });
  const pending = new Map();
  let msgId = 0;
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
  };
  const send = (method, params = {}) => new Promise(resolveMsg => {
    const id = ++msgId; pending.set(id, resolveMsg); ws.send(JSON.stringify({ id, method, params }));
  });
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Page.navigate", { url: "http://127.0.0.1:3000/" });
  // 等待 Vue 应用挂载并建立 MCP 控制连接（有界）
  const deadline = Date.now() + CHROME_WAIT_MS;
  let connections = 0;
  while (Date.now() < deadline) {
    await sleep(3000);
    try {
      const st = await callTool("getAppState", {});
      connections = st.parsed?.connections?.length ?? 0;
      if (connections > 0) { ws.close(); return { connected: true, chrome: spawned, reason: null }; }
    } catch { /* server not answering yet; keep bounded wait */ }
  }
  ws.close();
  return { connected: false, chrome: spawned, reason: `page loaded but no MCP connection appeared within ${CHROME_WAIT_MS}ms` };
}

function existsFile(p) {
  return import("node:fs").then(fs => fs.existsSync(p));
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

let fixtureDirName = null;
let fixtureDirAbs = null;
let fixtureTarget = null;   // 创建节点时保存的 fixture target；cleanup 只复用它，绝不重新取 connections[0]
let createdNodeIds = [];
let nodesDeleted = false;
let chromeOwned = null;
let workspaceRoot = null;

async function main() {
  // 1) initialize（官方 SDK v2 Streamable HTTP；优先 2025-06-18，失败回退 2025-03-26）
  let init = await rpc("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "laox-director-lab-verify", version: "0.1.0" } }, { allowError: true });
  if (init.payload?.error) init = await rpc("initialize", { protocolVersion: "2025-03-26", capabilities: {}, clientInfo: { name: "laox-director-lab-verify", version: "0.1.0" } });
  const serverInfo = init.payload?.result?.serverInfo;
  const negotiated = init.payload?.result?.protocolVersion;
  if (!serverInfo) { console.error("initialize failed:", trim(init.payload)); exitCode = 1; return; }
  evidence.summary.initialize = { serverInfo, protocolVersion: negotiated };

  // 2) notifications/initialized
  await rpc("notifications/initialized", undefined, { notify: true });

  // 3) tools/list + 必需工具与官方 schema 在场断言（不猜工具名）
  const tl = await rpc("tools/list", {});
  const tools = tl.payload?.result?.tools ?? [];
  const toolNames = tools.map(t => t.name);
  evidence.summary.tools_list = { count: toolNames.length, names: toolNames };
  if (toolNames.length === 0) { console.error("tools/list returned 0 tools"); exitCode = 1; return; }
  const missing = REQUIRED_TOOLS.filter(t => !toolNames.includes(t));
  if (missing.length) { console.error(`required tools missing from tools/list: ${missing.join(", ")}`); exitCode = 1; return; }

  // 4) tools/call getAppState（读取工作区根与页面连接；callTool 严格模式：isError/缺 result.content 即抛）
  const st = await callTool("getAppState", {});
  workspaceRoot = st.parsed?.workspaceRoot;
  const connections0 = st.parsed?.connections ?? [];
  if (!workspaceRoot) { console.error("getAppState did not return workspaceRoot"); exitCode = 1; return; }
  evidence.summary.getAppState = { workspaceRoot, connections: connections0.length };

  // 5) workspaceFiles 真实文件生命周期（受控临时目录，结束即删；严格模式，任一失败即失败）
  const stamp = new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14);
  const probeDir = `mcp-verify-${stamp}`;
  const dirTarget = { directory: workspaceRoot };
  const wfArgs = action => ({ target: dirTarget, args: action });
  try {
    await callTool("workspaceFiles", wfArgs({ action: "mkdir", path: probeDir }));
    const probeContent = "laox-director-lab mcp verify probe";
    await callTool("workspaceFiles", wfArgs({ action: "writeBinary", path: `${probeDir}/probe.txt`, base64: Buffer.from(probeContent).toString("base64") }));
    const rd = await callTool("workspaceFiles", wfArgs({ action: "readBinary", path: `${probeDir}/probe.txt` }));
    if (typeof rd.parsed?.base64 !== "string" || Buffer.from(rd.parsed.base64, "base64").toString() !== probeContent) {
      throw new Error("readBinary did not roundtrip probe content (parsed shape invalid)");
    }
    await callTool("workspaceFiles", wfArgs({ action: "remove", path: probeDir, recursive: true }));
    evidence.summary.workspace_files = { mkdir_ok: true, write_ok: true, read_ok: true, write_read_roundtrip: true, remove_ok: true, probe_dir: probeDir };
  } catch (e) {
    evidence.summary.workspace_files = { status: "failed", probe_dir: probeDir, error: String(e.message) };
    console.error("workspaceFiles lifecycle failed:", e.message);
    exitCode = 1;
  }

  // 6) 真实画布/节点生命周期（R2 核心；需要已连接页面；无页面→先无头 Chrome 连接，仍无→exit 3 unavailable）
  let connections = connections0;
  let chromeResult = { connected: false, chrome: null, reason: "existing coordinator page already connected" };
  if (connections.length === 0) {
    chromeResult = await connectPageViaChrome();
    chromeOwned = chromeResult.chrome;
    if (chromeResult.connected) {
      const st2 = await callTool("getAppState", {});
      connections = st2.parsed?.connections ?? [];
    }
  }
  if (connections.length === 0) {
    evidence.summary.canvas_nodes = { status: "unavailable", reason: chromeResult.reason ?? "no connected page", exit_code_meaning: "nonzero=unavailable, not pass" };
    console.error(`canvas/node lifecycle unavailable: ${chromeResult.reason ?? "no connected page"} (exit 3)`);
    exitCode = 3;
    return;
  }
  const conn = connections[0];
  fixtureDirName = `mcp-verify-node-${stamp}`;
  fixtureDirAbs = workspaceRoot.endsWith("\\") || workspaceRoot.endsWith("/") ? `${workspaceRoot}${fixtureDirName}` : `${workspaceRoot}\\${fixtureDirName}`;
  const nodeLabel = `mcp verify temp probe ${stamp}`;
  try {
    // 6a) 建受控项目目录并让页面打开它（EK-6 正确形态：args.directory，不带 target）
    await callTool("workspaceFiles", { target: dirTarget, args: { action: "mkdir", path: fixtureDirName } });
    await callTool("openProject", { args: { directory: fixtureDirAbs } });
    await sleep(OPEN_PROJECT_SETTLE_MS);
    const st3 = await callTool("getAppState", {});
    const conn3 = (st3.parsed?.connections ?? []).find(c => c.id === conn.id) ?? (st3.parsed?.connections ?? [])[0];
    const canvasId = conn3?.state?.canvasId ?? conn3?.canvasId;
    const connDir = conn3?.directory ?? fixtureDirAbs;
    if (!canvasId) throw new Error(`no canvasId on connection after openProject (directory=${connDir})`);
    if (fixtureDirAbs && String(connDir).replace(/\\/g, "/").toLowerCase() !== String(fixtureDirAbs).replace(/\\/g, "/").toLowerCase()) {
      throw new Error(`connection directory '${connDir}' is not our fixture '${fixtureDirAbs}'`);
    }
    const target = { connectionId: conn3.id, directory: connDir, canvasId };
    fixtureTarget = target; // cleanup 必须复用创建时保存的 fixture target，绝不重新拿 connections[0]

    // 6b) 画布读取 + 官方 nodeTypes 发现（真实 schema，不猜类型名；parsed 形状必须可解析）
    const gc = await callTool("getCanvas", { target, args: {} });
    if (!gc.parsed) throw new Error("getCanvas result unparseable (R2 strict)");
    const baselineNodes = gc.parsed?.nodes?.length ?? gc.parsed?.nodeCount ?? null;
    const types = await callTool("getCanvas", { target, args: { include: ["nodeTypes"] } });
    const availableNodeTypes = (types.parsed?.availableNodeTypes ?? types.parsed?.nodeTypes ?? []).map(t => t?.type ?? t).filter(Boolean);
    if (availableNodeTypes.length === 0) throw new Error("no availableNodeTypes returned by real page (parsed shape invalid)");
    const textType = availableNodeTypes.find(t => /text/i.test(String(t))) ?? availableNodeTypes[0];

    // 6c) 增：真实 addNode
    await callTool("addNode", { target, args: { type: textType, position: { x: 120, y: 120 }, label: nodeLabel } });

    // 6d) 读：findCanvasNodes（形状必须为 nodes 数组且非空）+ getCanvasNodes（必须实际读到我们的节点 ID）
    const find1 = await callTool("findCanvasNodes", { target, args: { query: nodeLabel } });
    if (!Array.isArray(find1.parsed?.nodes)) throw new Error("findCanvasNodes result missing nodes array (R2 strict)");
    const found = find1.parsed.nodes;
    if (found.length === 0) throw new Error("findCanvasNodes found 0 nodes after addNode");
    createdNodeIds = found.map(n => n.id ?? n.nodeId).filter(Boolean);
    if (createdNodeIds.length === 0) throw new Error("found nodes carry no ids");
    const detail = await callTool("getCanvasNodes", { target, args: { nodeIds: createdNodeIds } });
    const detailText = JSON.stringify(detail.parsed ?? detail.textOut);
    const readBack = createdNodeIds.filter(id => detailText.includes(id)).length;
    if (readBack !== createdNodeIds.length) {
      throw new Error(`getCanvasNodes read back ${readBack}/${createdNodeIds.length} created ids (R2 strict)`);
    }

    // 6e) 删：真实 deleteNodes + 复查（parsed 缺失=失败，不得默认空数组算通过）
    await callTool("deleteNodes", { target, args: { nodeIds: createdNodeIds } });
    nodesDeleted = true;
    const find2 = await callTool("findCanvasNodes", { target, args: { query: nodeLabel } });
    if (!Array.isArray(find2.parsed?.nodes)) throw new Error("findCanvasNodes(after delete) result missing nodes array (R2 strict)");
    const remaining = find2.parsed.nodes;
    if (remaining.length !== 0) throw new Error(`cleanup verification failed: ${remaining.length} probe nodes still found`);
    const gc2 = await callTool("getCanvas", { target, args: {} });
    if (!gc2.parsed) throw new Error("final getCanvas result unparseable (R2 strict)");
    const afterNodes = gc2.parsed?.nodes?.length ?? gc2.parsed?.nodeCount ?? null;
    if (afterNodes !== null && baselineNodes !== null && afterNodes !== baselineNodes) {
      throw new Error(`canvas node count after cleanup (${afterNodes}) != baseline (${baselineNodes})`);
    }
    evidence.summary.canvas_nodes = {
      status: "verified",
      fixture_dir: fixtureDirName, canvas_id: canvasId, chosen_node_type: textType,
      available_node_types_count: availableNodeTypes.length,
      added: true, found_after_add: createdNodeIds.length, read_back_ids: readBack,
      deleted: true, found_after_delete: remaining.length,
      canvas_nodes_baseline: baselineNodes, canvas_nodes_after_cleanup: afterNodes,
      page_connection: chromeResult.connected ? "headless chrome (spawned by this script, closed in finally)" : "pre-existing page connection",
    };
  } catch (e) {
    evidence.summary.canvas_nodes = { status: "failed", fixture_dir: fixtureDirName, error: String(e.message) };
    console.error("canvas/node lifecycle failed:", e.message);
    exitCode = 1;
  }

  // 7) 防线自证：本脚本任何路径都不得出现计费工具调用
  evidence.summary.never_called = NEVER_CALL;
}

async function cleanup() {
  // 只清理自有测试产物：未删净的测试节点（复用创建时保存的 fixture target，绝不碰其他页面）+
  // 受控 fixture 目录 + 自启 Chrome；清理失败不得以 exit 0 收场（协调方 2026-10-11 反馈）
  const errors = [];
  try {
    if (createdNodeIds.length > 0 && !nodesDeleted && fixtureTarget) {
      await callTool("deleteNodes", { target: fixtureTarget, args: { nodeIds: createdNodeIds } });
    }
  } catch (e) { errors.push(`node cleanup: ${e.message}`); }
  try {
    if (workspaceRoot && fixtureDirName) {
      await callTool("workspaceFiles", { target: { directory: workspaceRoot }, args: { action: "remove", path: fixtureDirName, recursive: true } });
    }
  } catch (e) { errors.push(`fixture dir cleanup: ${e.message}`); }
  try { if (chromeOwned && chromeOwned.exitCode === null) chromeOwned.kill(); } catch (e) { errors.push(`chrome cleanup: ${e.message}`); }
  if (errors.length) {
    evidence.summary.cleanup_notes = errors;
    if (exitCode === 0) exitCode = 1; // cleanup 失败禁止 exit 0
  }
}

let finished = false;
function finish() {
  if (finished) return;
  finished = true;
  evidence.summary.exit_code = exitCode;
  evidence.redaction = "token / Authorization header never written to evidence";
  const dir = resolve(root, "runtime/evidence/deployment");
  mkdirSync(dir, { recursive: true });
  const file = resolve(dir, `mcp-verify-${Date.now()}.json`);
  writeFileSync(file, JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify({ exit_code: exitCode, initialize: evidence.summary.initialize, tools_count: evidence.summary.tools_list?.count, getAppState: evidence.summary.getAppState, workspace_files: evidence.summary.workspace_files, canvas_nodes: evidence.summary.canvas_nodes ?? null, evidence_file: file }, null, 2));
  // Windows 上带活跃 keep-alive socket 时立即 process.exit 会触发 libuv 断言崩溃：
  // 先设退出码让事件循环自然排空；5s 宽限（unref，不自身保活）后才强制退出。
  process.exitCode = exitCode;
  const force = setTimeout(() => process.exit(exitCode), 5000);
  if (typeof force.unref === "function") force.unref();
}

const overallTimer = setTimeout(() => {
  console.error(`overall test timeout ${OVERALL_TIMEOUT_MS}ms (bounded, R2)`);
  exitCode = 1;
  finish();
}, OVERALL_TIMEOUT_MS);

main()
  .then(() => cleanup())
  .catch(async e => { console.error("fatal:", e); exitCode = 1; await cleanup(); })
  .finally(() => { clearTimeout(overallTimer); finish(); });
