"""Lab human scoring table: Clef is advisory; humans select/rank shots before jobs.

Persists run sidecar human-score-table.json, immutable archive under
runtime/human-score-tables/, and append-only index.jsonl.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_ID = "lab-human-score-table/v1"
AUTHORITY = "human_over_clef"
TABLE_NAME = "human-score-table.json"
HTML_NAME = "human-score-table.html"

# Overridable in tests.
ARCHIVE_DIR = None  # set by material-flow to ROOT/runtime/human-score-tables


def _utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(Path(path).read_bytes())


def _sha_obj(obj) -> str:
    return _sha_bytes(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def archive_root(root: Path) -> Path:
    return Path(ARCHIVE_DIR) if ARCHIVE_DIR is not None else (Path(root) / "runtime" / "human-score-tables")


def table_path(run: Path) -> Path:
    return Path(run) / TABLE_NAME


def read_table(run: Path):
    path = table_path(run)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _clef_snapshot(run: Path, review_sha: str, creation_sha: str, case_sha: str, consumer_policy: str):
    """Advisory only. Bound clef-gate if present and bindings match; else clef_unavailable/absent."""
    gate_path = Path(run) / "clef-gate.json"
    if not gate_path.is_file():
        return {"status": "absent", "passed": None, "decisions": {}}, None
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "clef_unavailable", "passed": None, "decisions": {}}, None
    bound = (
        gate.get("review_sha256") == review_sha
        and gate.get("creation_sha256") == creation_sha
        and gate.get("case_sha256") == case_sha
        and gate.get("consumer_policy") == consumer_policy
    )
    if not bound:
        return {"status": "clef_unavailable", "passed": None, "decisions": {}}, _sha_file(gate_path)
    status = gate.get("status") or "decided"
    if status not in ("decided", "clef_unavailable"):
        status = "clef_unavailable" if gate.get("passed") is not True else "decided"
    if gate.get("status") == "clef_unavailable":
        status = "clef_unavailable"
    decisions = gate.get("decisions") if isinstance(gate.get("decisions"), dict) else {}
    passed = gate.get("passed") if isinstance(gate.get("passed"), bool) else None
    # Prefill reference string from batch decisions (advisory columns for the operator).
    return {"status": status, "passed": passed, "decisions": decisions}, _sha_file(gate_path)


def _clef_reference_for_shot(clef: dict) -> str:
    if clef.get("status") == "absent":
        return "clef_absent"
    if clef.get("status") == "clef_unavailable":
        return "clef_unavailable"
    parts = []
    for qid, d in (clef.get("decisions") or {}).items():
        if not isinstance(d, dict):
            continue
        p = d.get("p_yes")
        flag = "pass" if d.get("pass") else "fail"
        if isinstance(p, (int, float)):
            parts.append("%s:%s(p=%.2f)" % (qid, flag, float(p)))
        else:
            parts.append("%s:%s" % (qid, flag))
    batch = "batch_pass" if clef.get("passed") is True else "batch_fail"
    if not parts:
        return batch
    # Keep short: batch flag + first 3 questions
    return batch + "; " + ", ".join(parts[:3])


def build_pending_table(run: Path, root: Path, run_id: str, creation: dict, case: dict,
                        consumer_policy: str, sha_file):
    """Build a pending table prefilled from Clef (or blanks if unavailable)."""
    review_path = Path(run) / "review.json"
    creation_path = Path(run) / "creation.json"
    case_path = Path(root) / creation["_meta"]["case"]
    review_sha = sha_file(review_path)
    creation_sha = sha_file(creation_path)
    case_sha = sha_file(case_path)
    clef, clef_sha = _clef_snapshot(run, review_sha, creation_sha, case_sha, consumer_policy)
    ref = _clef_reference_for_shot(clef)
    shots = []
    for s in creation.get("shots") or []:
        sid = s.get("shot_id")
        if not sid:
            raise ValueError("CREATION_SHOT_MISSING_ID")
        shots.append({
            "shot_id": sid,
            "story_beat": (s.get("story_beat") or "")[:200],
            "clef_reference": ref,
            "user_rank": None,
            "score": None,
            "selected": False,
            "note": "",
        })
    if not shots:
        raise ValueError("CREATION_HAS_NO_SHOTS")
    return {
        "schema": SCHEMA_ID,
        "status": "pending",
        "run_id": run_id,
        "utc": _utc(),
        "authority": AUTHORITY,
        "bindings": {
            "review_sha256": review_sha,
            "creation_sha256": creation_sha,
            "case_sha256": case_sha,
            "consumer_policy": consumer_policy,
            "clef_gate_sha256": clef_sha,
        },
        "clef": clef,
        "shots": shots,
        "selected_ids": [],
        "operator_id": "",
        "submitted_utc": None,
    }


def ensure_pending_table(run: Path, root: Path, run_id: str, creation: dict, case: dict,
                         consumer_policy: str, sha_file):
    """Write pending table if missing or stale vs bindings; never overwrite a submitted table."""
    existing = read_table(run)
    pending = build_pending_table(run, root, run_id, creation, case, consumer_policy, sha_file)
    if existing and existing.get("status") == "submitted":
        # Keep submitted unless bindings drift (caller require will refuse).
        return existing
    if existing and existing.get("status") == "pending":
        if existing.get("bindings") == pending["bindings"] and [
            s["shot_id"] for s in existing.get("shots") or []
        ] == [s["shot_id"] for s in pending["shots"]]:
            return existing
    _write_json(table_path(run), pending)
    write_html(run, pending)
    return pending


def validate_submission(table: dict, run: Path, root: Path, creation: dict, case: dict,
                        consumer_policy: str, sha_file, operator_id: str):
    """Validate operator submission against current bindings and selection rules."""
    if not isinstance(table, dict):
        raise ValueError("TABLE_NOT_OBJECT")
    if table.get("schema") != SCHEMA_ID:
        raise ValueError("SCHEMA_MISMATCH")
    if table.get("authority") != AUTHORITY:
        raise ValueError("AUTHORITY_MUST_BE_human_over_clef")
    op = (operator_id or table.get("operator_id") or "").strip()
    if not op:
        raise ValueError("OPERATOR_ID_REQUIRED")
    expected = build_pending_table(run, root, table.get("run_id") or Path(run).name,
                                   creation, case, consumer_policy, sha_file)
    if table.get("bindings") != expected["bindings"]:
        raise ValueError("BINDINGS_STALE")
    exp_ids = [s["shot_id"] for s in expected["shots"]]
    got = table.get("shots") or []
    if not isinstance(got, list) or [s.get("shot_id") for s in got] != exp_ids:
        raise ValueError("SHOT_SET_MISMATCH")
    selected = []
    ranks = []
    for row in got:
        if not isinstance(row, dict):
            raise ValueError("SHOT_ROW_INVALID")
        sel = row.get("selected") is True
        rank = row.get("user_rank")
        score = row.get("score")
        if sel:
            if not isinstance(rank, int) or rank < 1:
                raise ValueError("SELECTED_NEEDS_RANK:%s" % row.get("shot_id"))
            if not isinstance(score, int) or score < 1 or score > 5:
                raise ValueError("SELECTED_NEEDS_SCORE_1_5:%s" % row.get("shot_id"))
            selected.append(row["shot_id"])
            ranks.append(rank)
        else:
            if rank is not None and (not isinstance(rank, int) or rank < 1):
                raise ValueError("RANK_INVALID:%s" % row.get("shot_id"))
            if score is not None and (not isinstance(score, int) or score < 1 or score > 5):
                raise ValueError("SCORE_INVALID:%s" % row.get("shot_id"))
            if isinstance(rank, int):
                ranks.append(rank)
    if not selected:
        raise ValueError("NEED_AT_LEAST_ONE_SELECTED")
    if len(ranks) != len(set(ranks)):
        raise ValueError("RANKS_MUST_BE_UNIQUE")
    submitted = dict(expected)
    submitted["status"] = "submitted"
    submitted["operator_id"] = op
    submitted["submitted_utc"] = _utc()
    submitted["clef"] = expected["clef"]  # keep advisory snapshot from current clef
    # merge operator fields
    by_id = {r["shot_id"]: r for r in got}
    merged = []
    for base in expected["shots"]:
        src = by_id[base["shot_id"]]
        merged.append({
            "shot_id": base["shot_id"],
            "story_beat": base["story_beat"],
            "clef_reference": base["clef_reference"],
            "user_rank": src.get("user_rank"),
            "score": src.get("score"),
            "selected": src.get("selected") is True,
            "note": src.get("note") if isinstance(src.get("note"), str) else "",
        })
    submitted["shots"] = merged
    submitted["selected_ids"] = [r["shot_id"] for r in merged if r["selected"]]
    # stable order by rank
    submitted["selected_ids"] = [sid for sid, _ in sorted(
        ((r["shot_id"], r["user_rank"]) for r in merged if r["selected"]),
        key=lambda x: x[1])]
    return submitted


def persist_submission(run: Path, root: Path, submitted: dict):
    """Write run sidecar, immutable archive, append ledger. Returns meta."""
    body = dict(submitted)
    body.pop("table_sha256", None)
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = _sha_bytes(raw)
    body["table_sha256"] = digest
    _write_json(table_path(run), body)
    write_html(run, body)
    arch = archive_root(root)
    arch.mkdir(parents=True, exist_ok=True)
    utc_compact = (body.get("submitted_utc") or _utc()).replace(":", "").replace("-", "")
    name = "%s_%s_%s.json" % (utc_compact, body.get("run_id") or Path(run).name, digest[:16])
    arch_path = arch / name
    if not arch_path.exists():
        _write_json(arch_path, body)
    index = arch / "index.jsonl"
    entry = {
        "utc": body.get("submitted_utc") or _utc(),
        "run_id": body.get("run_id"),
        "table_sha256": digest,
        "operator_id": body.get("operator_id"),
        "selected_ids": body.get("selected_ids"),
        "archive_path": str(arch_path.as_posix()),
    }
    with index.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return {"table_sha256": digest, "archive_path": str(arch_path), "selected_ids": body["selected_ids"]}


def require_submitted(run: Path, root: Path, creation: dict, case: dict,
                      consumer_policy: str, sha_file):
    """Fail-closed: submitted table must exist and match current bindings."""
    doc = read_table(run)
    if not doc or doc.get("status") != "submitted":
        raise ValueError(
            "HUMAN_SCORE_REQUIRED: run score-template then score-submit "
            "(material-flow.py score-template --run <id>)")
    if doc.get("schema") != SCHEMA_ID or doc.get("authority") != AUTHORITY:
        raise ValueError("HUMAN_SCORE_INVALID_SCHEMA")
    expected = build_pending_table(run, root, doc.get("run_id") or Path(run).name,
                                   creation, case, consumer_policy, sha_file)
    if doc.get("bindings") != expected["bindings"]:
        raise ValueError("HUMAN_SCORE_BINDINGS_STALE: re-run score-template after review/creation change")
    selected = doc.get("selected_ids") or []
    if not selected or not isinstance(selected, list):
        raise ValueError("HUMAN_SCORE_EMPTY_SELECTION")
    known = {s["shot_id"] for s in creation.get("shots") or []}
    if any(s not in known for s in selected):
        raise ValueError("HUMAN_SCORE_UNKNOWN_SHOT")
    return doc


def list_ledger(root: Path, limit: int = 50):
    index = archive_root(root) / "index.jsonl"
    if not index.is_file():
        return []
    lines = index.read_text(encoding="utf-8").splitlines()
    out = []
    for line in lines[-max(1, limit):]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return list(reversed(out))


def format_table_text(doc: dict) -> str:
    shots = doc.get("shots") or []
    lines = [
        "schema=%s status=%s run=%s authority=%s" % (
            doc.get("schema"), doc.get("status"), doc.get("run_id"), doc.get("authority")),
        "clef.status=%s clef.passed=%s" % (
            (doc.get("clef") or {}).get("status"), (doc.get("clef") or {}).get("passed")),
        "operator=%s selected=%s" % (doc.get("operator_id") or "-", ",".join(doc.get("selected_ids") or []) or "-"),
        "",
        "%-16s %-6s %-5s %-8s %s" % ("shot_id", "rank", "score", "selected", "story_beat/clef_ref"),
        "-" * 72,
    ]
    for s in shots:
        lines.append("%-16s %-6s %-5s %-8s %s | %s" % (
            s.get("shot_id"),
            "-" if s.get("user_rank") is None else s.get("user_rank"),
            "-" if s.get("score") is None else s.get("score"),
            "YES" if s.get("selected") else "no",
            (s.get("story_beat") or "")[:32],
            (s.get("clef_reference") or "")[:40],
        ))
    return "\n".join(lines)


def write_html(run: Path, doc: dict):
    rows = []
    for s in doc.get("shots") or []:
        rows.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                _esc(s.get("shot_id")),
                _esc(s.get("user_rank")),
                _esc(s.get("score")),
                "yes" if s.get("selected") else "no",
                _esc(s.get("note")),
                _esc((s.get("story_beat") or "")[:80]),
                _esc((s.get("clef_reference") or "")[:80]),
            ))
    html = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Human score %s</title>
<style>
body{font-family:sans-serif;margin:24px;background:#111;color:#eee}
table{border-collapse:collapse;width:100%%;font-size:13px}
th,td{border:1px solid #444;padding:6px 8px;vertical-align:top}
th{background:#222}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;background:#333}
.pending{background:#664}
.submitted{background:#264}
</style></head><body>
<h1>Human score table <span class="badge %s">%s</span></h1>
<p>run=<code>%s</code> authority=<code>%s</code> operator=<code>%s</code></p>
<p>clef: status=%s passed=%s (advisory only)</p>
<p>selected_ids: %s</p>
<table>
<thead><tr><th>shot_id</th><th>rank</th><th>score</th><th>selected</th><th>note</th><th>story_beat</th><th>clef_reference</th></tr></thead>
<tbody>
%s
</tbody></table>
<p>Fill via CLI: score-submit --run %s --by &lt;operator&gt; --table &lt;json&gt;</p>
</body></html>
""" % (
        _esc(doc.get("run_id")),
        _esc(doc.get("status")),
        _esc(doc.get("status")),
        _esc(doc.get("run_id")),
        _esc(doc.get("authority")),
        _esc(doc.get("operator_id") or "-"),
        _esc((doc.get("clef") or {}).get("status")),
        _esc((doc.get("clef") or {}).get("passed")),
        _esc(",".join(doc.get("selected_ids") or []) or "-"),
        "\n".join(rows),
        _esc(doc.get("run_id")),
    )
    (Path(run) / HTML_NAME).write_text(html, encoding="utf-8")


def _esc(v):
    if v is None:
        return "-"
    s = str(v)
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))
