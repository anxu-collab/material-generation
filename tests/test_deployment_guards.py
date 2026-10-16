# test_deployment_guards.py — Phase1 部署守卫单测（无网络、无服务、无生成）
# 两层：静态合同检查 + R1 真实负面测试（真实 Bun 夹具证明 stop 身份门禁 fail-closed）。
import json
import re
import subprocess
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN_REFS = re.compile(r"laox_account|h3_pipeline", re.IGNORECASE)
BUN = ROOT / "vendor" / "tools" / "bun-windows-x64" / "bun.exe"
CANON_ENTRY = ROOT / "scripts" / "toonflow-loopback.ts"
STOP_SCRIPT = ROOT / "scripts" / "stop-toonflow.ps1"
START_SCRIPT = ROOT / "scripts" / "start-toonflow.ps1"
INFO_FILE = ROOT / "runtime" / "toonflow-data" / "server-info.json"
GUARD_HARNESS = ROOT / "runtime" / "tmp" / "guard-fn-harness.ps1"


def run_ps(args, timeout=90):
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"] + args,
        capture_output=True, text=True, timeout=timeout,
    )


class ConfigTests(unittest.TestCase):
    def test_configs_parseable_and_source_aligned(self):
        for name in ("deployment.json", "mcp.json", "vbench_capabilities.json"):
            data = json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict, name)
        dep = json.loads((ROOT / "config" / "deployment.json").read_text(encoding="utf-8"))
        self.assertEqual(dep["toonflow"]["tag"], "v2.0.5")
        self.assertEqual(dep["toonflow"]["commit"], "2c7fb257affb65777110fe4c6e60cd3c09187d9b")
        self.assertEqual(dep["toonflow"]["install_command"], "bun install --frozen-lockfile")
        self.assertEqual(dep["vbench"]["commit"], "fd18b3d055cb0fc6f066ca90fe2c3c8cbb698490")
        self.assertEqual(dep["budget"]["paid_generation"], 0)
        self.assertEqual(dep["budget"]["model_weights_downloads"], 0)
        mcp = json.loads((ROOT / "config" / "mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp["client_mode"], "protocol-client")
        self.assertTrue(mcp["server"]["endpoint"].startswith("http://127.0.0.1:"))
        self.assertTrue(any("runAgent" in entry for entry in mcp["dry_run"]["never_call"]))
        caps = json.loads((ROOT / "config" / "vbench_capabilities.json").read_text(encoding="utf-8"))
        names = [m["name"] for m in caps["priority_metrics"]]
        self.assertEqual(names, ["subject_consistency", "motion_smoothness", "Human_Anatomy", "Human_Identity"])
        for m in caps["priority_metrics"]:
            self.assertTrue(m["execution_status"].startswith("not_run"), "no metric may claim execution this round")

    def test_vbench_requirements_pinned_minimal(self):
        lines = [l.strip() for l in (ROOT / "config" / "vbench-requirements.txt").read_text(encoding="utf-8").splitlines()]
        pins = [l for l in lines if l and not l.startswith("#")]
        self.assertTrue(pins)
        for pin in pins:
            self.assertIn("==", pin, f"unpinned dependency: {pin}")
        joined = "\n".join(l for l in lines if not l.startswith("#"))
        full = "\n".join(lines)
        self.assertIn("torch==2.3.1+cu121", full, "torch must be CUDA wheel (documented install command)")
        self.assertNotIn("mmdet", joined)
        self.assertNotIn("llava", joined.lower())
        # 唯一允许的网络地址是 PyTorch 官方 CUDA 轮子索引
        urls = re.findall(r"https?://\S+", joined)
        self.assertTrue(all(u.startswith("https://download.pytorch.org/whl/") for u in urls), urls)


class ScriptGuardTests(unittest.TestCase):
    def test_deploy_ps1_dry_run_default_and_no_paid_calls(self):
        text = (ROOT / "scripts" / "deploy.ps1").read_text(encoding="utf-8")
        self.assertIn("[switch]$DryRun", text)
        self.assertIn("frozen-lockfile", text)
        self.assertNotIn("runAgent", text)
        self.assertNotIn("mediaGeneration", text)

    def test_verify_mcp_dry_run_only_and_redaction(self):
        text = (ROOT / "scripts" / "verify-mcp.mjs").read_text(encoding="utf-8")
        self.assertIn('includes("--dry-run")', text)
        self.assertIn("never logged", text)          # authorization header comment
        self.assertIn("never written to evidence", text)  # evidence redaction note
        self.assertIn('"runAgent"', text)
        self.assertNotIn("process.env.TOONFLOW_MCP_TOKEN =", text)  # token comes from settings.json, not echoed
        # R2: bounded RPC timeouts + real node lifecycle + error rejection must stay in the permanent script
        self.assertIn("AbortSignal.timeout", text)
        self.assertIn("addNode", text)
        self.assertIn("deleteNodes", text)
        self.assertIn("findCanvasNodes", text)
        self.assertIn("openProject", text)

    def test_check_vbench_guards(self):
        text = (ROOT / "scripts" / "check-vbench.py").read_text(encoding="utf-8")
        self.assertIn("--no-download", text)
        self.assertIn("--cuda-only", text)
        self.assertIn("FAST REJECT", text)
        self.assertIn("this check never scores", text)
        self.assertIn("not_run", text)

    def test_start_stop_process_boundaries(self):
        start = START_SCRIPT.read_text(encoding="utf-8-sig")
        self.assertIn("Get-NetTCPConnection -LocalPort", start)
        self.assertIn("-WindowStyle Hidden", start)
        self.assertIn("refusing to start", start)
        self.assertIn("Confirm-OwnProcess", start)
        self.assertIn("creation_time", start)
        self.assertIn("toonflow-loopback.ts", start)
        self.assertIn("loopback-only verified", start)
        # R1: reuse path must fail closed on incomplete/unparseable/forged record identity
        self.assertIn("IsNullOrWhiteSpace", start)
        self.assertIn("RoundtripKind", start)
        stop = STOP_SCRIPT.read_text(encoding="utf-8-sig")
        self.assertIn("not ours", stop)
        self.assertIn('ProcessName -ne "bun"', stop)
        self.assertIn("same-exe different entry", stop)
        self.assertIn("stale PID reuse", stop)
        # R1: record-side gate — mandatory fields + canonical project exe/entry, fail closed
        self.assertIn("# === IDENTITY FUNCTIONS BEGIN", stop)
        self.assertIn("identity incomplete", stop)
        self.assertIn("not the canonical project bun", stop)
        self.assertIn("unparseable", stop)

    def test_loopback_wrapper_contract(self):
        wrapper = (ROOT / "scripts" / "toonflow-loopback.ts").read_text(encoding="utf-8")
        self.assertIn('from "../vendor/toonflow/apps/server/src/app"', wrapper)  # native createApp, no service rewrite
        self.assertIn("127.0.0.1", wrapper)
        self.assertIn("agentsRoot", wrapper)          # local team resources reuse
        self.assertIn("initializeMcpRuntime", wrapper)  # native MCP init reuse
        self.assertNotIn("express()", wrapper)        # must not build a new service
        deploy = (ROOT / "scripts" / "deploy.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("build:teams", deploy)

    def test_no_protected_project_references(self):
        own = list((ROOT / "scripts").glob("*")) + list((ROOT / "config").glob("*"))
        for f in own:
            self.assertIsNone(FORBIDDEN_REFS.search(f.read_text(encoding="utf-8", errors="ignore")), f"protected path referenced in {f.name}")

    def test_gitignore_covers_third_party_and_runtime(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        for needed in ("vendor/", "runtime/cache/", "runtime/venvs/", "runtime/evidence/", "runtime/toonflow-data/"):
            self.assertIn(needed, text)


class GuardIdentityRealNegativeTests(unittest.TestCase):
    """R1 真实负面测试（review-1）：真实自有 Bun 夹具 + 伪造 server-info 记录，
    逐项证明身份门禁拒绝（fail closed）且不碰无害夹具；真实记录在 finally 恢复。
    夹具保持存活直至本类清理；全程不停止任何非自有进程。"""

    @classmethod
    def setUpClass(cls):
        if not BUN.exists():
            raise unittest.SkipTest("project bun not installed; fixture unavailable")
        cls._saved_info = INFO_FILE.read_bytes() if INFO_FILE.exists() else None
        cls._real_pid = None
        if cls._saved_info:
            try:
                cls._real_pid = json.loads(cls._saved_info.decode("utf-8-sig")).get("pid")
            except Exception:
                cls._real_pid = None
        cls._real_was_alive = bool(cls._real_pid) and run_ps(
            ["-Command", f"Write-Host ([bool](Get-Process -Id {cls._real_pid} -ErrorAction SilentlyContinue))"]
        ).stdout.strip().lower() == "true"

        # fixtureA: 普通 -e 长睡眠（命令行不含规范 entry）；fixtureB: 命令行内嵌规范 entry 路径字符串
        code_a = "setInterval(function(){}, 60000)"
        code_b = "/* guard fixture marker %s */ setInterval(function(){}, 60000)" % str(CANON_ENTRY)
        cls.fixtures = {}
        for name, code in (("A", code_a), ("B", code_b)):
            proc = subprocess.Popen([str(BUN), "-e", code],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            meta = None
            for _ in range(20):
                time.sleep(0.5)
                if proc.poll() is not None:
                    raise AssertionError(f"fixture {name} died before metadata capture")
                r = run_ps(["-Command",
                            "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\").CreationDate.ToString('o')" % proc.pid])
                if r.returncode == 0 and re.match(r"^\d{4}-", r.stdout.strip()):
                    meta = r.stdout.strip()
                    break
            if not meta:
                proc.kill()
                raise AssertionError(f"fixture {name} metadata unavailable")
            cls.fixtures[name] = {"pid": proc.pid, "proc": proc, "creation_time": meta}

    @classmethod
    def tearDownClass(cls):
        # 无害夹具由 harness 统一清理（先验证仍存活再终止）
        for name, fx in cls.fixtures.items():
            if fx["proc"].poll() is None:
                fx["proc"].terminate()
        for name, fx in cls.fixtures.items():
            try:
                fx["proc"].wait(timeout=10)
            except subprocess.TimeoutExpired:
                fx["proc"].kill()
        # 真实记录按字节恢复
        if cls._saved_info is not None:
            INFO_FILE.write_bytes(cls._saved_info)
        elif INFO_FILE.exists():
            INFO_FILE.unlink()

    def _record(self, fixture="B", **over):
        fx = self.fixtures[fixture]
        rec = {"pid": fx["pid"], "port": 3000, "mcp_port": 10588,
               "entry": str(CANON_ENTRY), "exe": str(BUN), "creation_time": fx["creation_time"]}
        for k, v in over.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v
        return rec

    def _run_stop_script(self, rec):
        INFO_FILE.write_text(json.dumps(rec), encoding="ascii")
        try:
            return run_ps(["-File", str(STOP_SCRIPT)])
        finally:
            # 每次伪造运行后立即按字节恢复真实记录（fail-safe，不等 tearDownClass）
            if self._saved_info is not None:
                INFO_FILE.write_bytes(self._saved_info)
            elif INFO_FILE.exists():
                INFO_FILE.unlink()

    def _assert_refused(self, r, fixture, needle):
        self.assertNotEqual(r.returncode, 0, "stop script must exit nonzero on refusal; got 0")
        # PS5.1 错误输出按 80 列折行且会在词中插换行：去掉全部空白后匹配
        err = re.sub(r"\s+", "", (r.stderr or "") + (r.stdout or ""))
        self.assertIn("refusing", err)
        self.assertIn(re.sub(r"\s+", "", needle), err)
        self.assertIsNone(self.fixtures[fixture]["proc"].poll(), f"fixture {fixture} must stay alive after refusal")

    def _assert_real_server_preserved(self):
        if self._real_pid and self._real_was_alive:
            r = run_ps(["-Command", f"Write-Host ([bool](Get-Process -Id {self._real_pid} -ErrorAction SilentlyContinue))"])
            self.assertEqual(r.stdout.strip().lower(), "true", "real recorded server process must remain alive")

    def test_r1_different_entry_refused(self):
        r = self._run_stop_script(self._record("A"))
        self._assert_refused(r, "A", "different entry")
        self._assert_real_server_preserved()

    def test_r1_empty_entry_refused(self):
        r = self._run_stop_script(self._record("B", entry=""))
        self._assert_refused(r, "B", "identity incomplete")

    def test_r1_missing_exe_refused(self):
        r = self._run_stop_script(self._record("B", exe=None))
        self._assert_refused(r, "B", "identity incomplete")

    def test_r1_missing_time_refused(self):
        r = self._run_stop_script(self._record("B", creation_time=None))
        self._assert_refused(r, "B", "identity incomplete")

    def test_r1_forged_exe_path_refused(self):
        r = self._run_stop_script(self._record("B", exe=r"C:\Windows\System32\cmd.exe"))
        self._assert_refused(r, "B", "not the canonical project bun")

    def test_r1_stale_creation_time_refused(self):
        from datetime import datetime, timedelta
        # .NET "o" 格式带 7 位小数秒，Python3.10 fromisoformat 只认 6 位：先归一化
        raw = re.sub(r"(\.\d{6})\d+", r"\1", self.fixtures["B"]["creation_time"])
        ct = datetime.fromisoformat(raw) - timedelta(seconds=60)
        r = self._run_stop_script(self._record("B", creation_time=ct.isoformat()))
        self._assert_refused(r, "B", "stale PID reuse")

    def test_r1_unparseable_time_refused(self):
        r = self._run_stop_script(self._record("B", creation_time="not-a-date"))
        self._assert_refused(r, "B", "unparseable")

    def test_r1_positive_control_via_real_function(self):
        # 直调 stop 脚本内的真实身份函数（只读，不停止任何进程）：完整一致的记录必须通过
        rec = self._record("B")
        r = run_ps(["-File", str(GUARD_HARNESS), "-TargetPid", str(rec["pid"]), "-InfoJson", json.dumps(rec)])
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIsNone(out["reason"], f"genuine record must pass the real guard; got {out['reason']!r}")
        self.assertIsNone(self.fixtures["B"]["proc"].poll())

    def test_r1_record_restored_byte_identical(self):
        self.assertEqual(INFO_FILE.read_bytes() if INFO_FILE.exists() else None, self._saved_info)


class StartReusePositiveTests(unittest.TestCase):
    """R1 正向复用测试（协调方 2026-10-11 实证反馈）：真实服务运行中再次启动必须
    复用同一 PID（登记 pid 为 int 的真实记录必须通过 Confirm-OwnProcess），且不删除/重写 server-info.json。"""

    def test_second_start_reuses_same_pid_and_keeps_record(self):
        if not INFO_FILE.exists():
            self.skipTest("no running service recorded; reuse path requires live server")
        before = INFO_FILE.read_bytes()
        pid_before = json.loads(before.decode("utf-8-sig"))["pid"]
        self.assertIsInstance(pid_before, int, "real record carries pid as int")
        r = run_ps(["-File", str(START_SCRIPT)], timeout=240)
        self.assertEqual(r.returncode, 0, (r.stderr or "")[-400:])
        self.assertEqual(r.stdout.strip().splitlines()[-1], str(pid_before),
                         "second start must reuse the same live pid")
        self.assertEqual(INFO_FILE.read_bytes(), before,
                         "server-info.json must not be deleted/rewritten on healthy reuse")
        alive = run_ps(["-Command", f"Write-Host ([bool](Get-Process -Id {pid_before} -ErrorAction SilentlyContinue))"])
        self.assertEqual(alive.stdout.strip().lower(), "true", "reused server process must stay alive")


if __name__ == "__main__":
    unittest.main()
