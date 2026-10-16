// toonflow-loopback.ts — v3 合同追加的唯一启动包装（scripts/，自有文件）。
// 依据（源码独立复核）：apps/server/src/index.ts 的 app.listen(realPort) 无 hostname 参数（默认全网卡/IPv6 ::）；
// createApp 为 apps/server/src/app.ts 原生导出。本包装不复制业务逻辑、不改 vendor：
// 仅以原生 createApp 组装生产构建产物，并把监听显式固定为 127.0.0.1，复用原生 MCP 初始化与
// agentsRoot（本地团队首装，保留用户修改）。其余行为与官方 start:server（bun build/server/index.js）一致。
import { resolve } from "node:path";
import { createApp } from "../vendor/toonflow/apps/server/src/app";
import config from "../vendor/toonflow/electrobun.config";

const startTime = Date.now();
const realPort = 3000;
const appDirectory = resolve(import.meta.dirname, "../vendor/toonflow");
const dataDirectory = process.env.TOONFLOW_DATA_DIR ?? resolve(appDirectory, "data");
const app = await createApp({
  webRoot: resolve(appDirectory, "build/web"),
  appVersion: config.app.version,
  dataDirectory,
  toolsRoot: resolve(appDirectory, "build/tools"),
  nodesRoot: resolve(appDirectory, "build/nodes"),
  extRoot: resolve(appDirectory, "build/ext"),
  providersRoot: resolve(appDirectory, "build/providers"),
  skillsRoot: resolve(appDirectory, "build/skills"),
  // 原生本地团队资源：build/agents 由官方 `bun run build:teams` 产出；initializePlugins 首装到 data/agents 并保留用户修改。
  agentsRoot: resolve(appDirectory, "build/agents"),
});
const { initializeMcpRuntime } = await import("../vendor/toonflow/apps/server/src/utils/mcp/runtime");
const { startMobileLinkRuntime } = await import("../vendor/toonflow/apps/server/src/utils/mobileLink");
app.listen(realPort, "127.0.0.1", async () => {
  await initializeMcpRuntime(app, `http://127.0.0.1:${realPort}`, resolve(appDirectory, "build/mcp/stdio.js"));
  await startMobileLinkRuntime(app).catch(error => console.error("设备互联启动失败：", error));
  console.log(`[服务启动成功][loopback]: http://127.0.0.1:${realPort}`);
  console.log(`[启动耗时]: ${(Date.now() - startTime).toFixed(2)}ms`);
});
