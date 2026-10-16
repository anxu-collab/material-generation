#!/usr/bin/env python
# check-vbench.py — VBench/VBench2 基础环境真实检查（Phase1）。
# 约束：--no-download 任何情况下不下载模型权重；--cuda-only 时 CUDA 不可用必须快速拒绝（非零退出、不产生分数）；
#       CPU 推理回退禁止；缺权重的指标一律 not_run，不造假分。
import argparse
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENVPY = ROOT / "runtime" / "venvs" / "vbench" / "Scripts" / "python.exe"

# 缓存只落目标目录内（在任何 torch import 之前设置）
for key, sub in [("VBENCH_CACHE_DIR", "runtime/cache/vbench"), ("VBENCH2_CACHE_DIR", "runtime/cache/vbench2"),
                 ("HF_HOME", "runtime/cache/hf"), ("TORCH_HOME", "runtime/cache/torch")]:
    os.environ.setdefault(key, str(ROOT / sub))

results = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "cwd": os.getcwd(), "checks": {}, "exit_code": 0}


def record(name, ok, detail):
    results["checks"][name] = {"ok": bool(ok), "detail": detail}
    print(("[PASS] " if ok else "[FAIL] ") + name + " :: " + str(detail))
    if not ok:
        results["exit_code"] = 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-download", action="store_true", help="hard guarantee: never download model weights (always honored)")
    ap.add_argument("--cuda-only", action="store_true", help="fast-reject (exit 2) when CUDA is unavailable; no CPU fallback, no scores")
    a = ap.parse_args()

    # 1) venv 边界：解释器必须在目标目录内
    record("venv-in-target", str(Path(sys.executable)).startswith(str(ROOT)), f"python={sys.executable} version={sys.version.split()[0]}")

    # 2) torch + CUDA
    import torch  # noqa: E402
    cuda_ok = torch.cuda.is_available()
    record("torch-import", True, f"torch={torch.__version__} cuda_build={torch.version.cuda}")
    record("torch-cuda", cuda_ok, f"available={cuda_ok} device_count={torch.cuda.device_count()}"
           + (f" device0={torch.cuda.get_device_name(0)}" if cuda_ok else ""))
    if a.cuda_only and not cuda_ok:
        print("FAST REJECT: CUDA unavailable and --cuda-only set; refusing CPU fallback and any scoring")
        dump(exit_code=2)

    # 3) import vbench（VBench 原版）
    vbench_dir = ROOT / "vendor" / "vbench"
    sys.path.insert(0, str(vbench_dir))
    try:
        import vbench  # noqa: F401
        record("import-vbench", True, f"VBench class ok, dimensions={len(vbench.VBench(None, '/dev/null', str(ROOT / 'runtime/tmp/vbench-probe')).build_full_dimension_list())}")
    except Exception as e:
        record("import-vbench", False, f"{type(e).__name__}: {e}")

    # 4) vbench CLI --help（真实子进程）
    cli_code = "import sys; sys.path.insert(0, '.'); sys.argv=['vbench', '--help']; from vbench.cli.vbench import main; main()"
    try:
        r = subprocess.run([sys.executable, "-c", cli_code], cwd=str(vbench_dir),
                           capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        record("vbench-cli-help", r.returncode == 0 and "usage" in (r.stdout + r.stderr).lower(),
               f"exit={r.returncode} stdout_head={(r.stdout or r.stderr)[:120]!r}")
    except Exception as e:
        record("vbench-cli-help", False, f"{type(e).__name__}: {e}")

    # 5) import vbench2（VBench-2.0 子目录）
    vbench2_dir = vbench_dir / "VBench-2.0"
    sys.path.insert(0, str(vbench2_dir))
    try:
        import vbench2  # noqa: F401
        record("import-vbench2", True, f"VBench2 class ok, dimensions={len(vbench2.VBench2(None, '/dev/null', str(ROOT / 'runtime/tmp/vbench2-probe')).build_full_dimension_list())}")
    except Exception as e:
        record("import-vbench2", False, f"{type(e).__name__}: {e}")

    # 6) 优先指标模块 import 探测（只记录能力事实；失败=blocked_deps，不算部署失败）
    for mod, name in [("vbench.subject_consistency", "subject_consistency"),
                      ("vbench.motion_smoothness", "motion_smoothness")]:
        try:
            importlib.import_module(mod)
            results["checks"][f"dim-import-{name}"] = {"ok": True, "detail": "importable with minimal deps"}
        except Exception as e:
            results["checks"][f"dim-import-{name}"] = {"ok": None, "detail": f"blocked_deps {type(e).__name__}: {str(e)[:160]}"}
    # vbench2 维度必须按包内子模块导入（R3 修复：spec_from_file_location 裸执行文件时相对导入必失败，
    # 会把"探针方法学伪影"误报成缺依赖；package import 才能暴露真实缺口）
    for mod, name in [("vbench2.human_anatomy", "Human_Anatomy"), ("vbench2.human_identity", "Human_Identity")]:
        try:
            importlib.import_module(mod)
            results["checks"][f"dim-import-{name}"] = {"ok": True, "detail": "importable as vbench2 submodule"}
        except Exception as e:
            results["checks"][f"dim-import-{name}"] = {"ok": None, "detail": f"blocked_deps {type(e).__name__}: {str(e)[:160]}"}

    # 7) 权重状态：本检查不下载、不打分。轻量权重可以已在缓存；7B 不在本检查范围内。
    weights = {}
    for key in ("VBENCH_CACHE_DIR", "VBENCH2_CACHE_DIR"):
        p = Path(os.environ[key])
        count = len(list(p.rglob("*"))) if p.exists() else 0
        weights[key] = {"path": str(p), "entries": count}
    results["weights"] = weights
    results["metrics_execution"] = "not_run (this check never scores; lightweight weights may be present, 7B models excluded)"

    dump(exit_code=results["exit_code"])


def dump(exit_code):
    results["exit_code"] = exit_code
    out = ROOT / "runtime" / "evidence" / "deployment"
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"vbench-check-{int(time.time())}.json"
    f.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"exit_code": exit_code, "evidence_file": str(f)}, ensure_ascii=False))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
