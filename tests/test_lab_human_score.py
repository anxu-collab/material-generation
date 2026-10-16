"""Lab human-score-table/v1: template, submit, hard gate, advisory Clef."""
import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("material_flow", ROOT / "scripts" / "material-flow.py")
mf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mf)
lhs = mf.lab_human_score


def _sha(p):
    return mf._sha_file(p)


class LabHumanScoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run = self.root / "runtime" / "material-flow" / "unit-run"
        self.run.mkdir(parents=True)
        self.case_path = self.root / "cases" / "unit" / "case.json"
        self.case_path.parent.mkdir(parents=True)
        case = {"case_id": "unit", "industry": "generic", "product_name": "P", "functions": ["f"]}
        self.case_path.write_text(json.dumps(case), encoding="utf-8")
        creation = {
            "_meta": {"case": "cases/unit/case.json"},
            "shots": [
                {"shot_id": "s1", "story_beat": "open box"},
                {"shot_id": "s2", "story_beat": "use product"},
                {"shot_id": "s3", "story_beat": "result"},
            ],
        }
        mf._write(self.run / "creation.json", creation)
        review = {
            "shots": [{"shot_id": x, "verdict": "ok"} for x in ("s1", "s2", "s3")],
            "batch": {"batch_verdict": "pass"},
            "_meta": {
                "consumer_policy": mf.CONSUMER_POLICY,
                "creation_sha256": _sha(self.run / "creation.json"),
                "case_sha256": _sha(self.case_path),
            },
        }
        mf._write(self.run / "review.json", review)
        # point ROOT / RUNS into tmp
        self.patches = [
            mock.patch.object(mf, "ROOT", self.root),
            mock.patch.object(mf, "RUNS", self.root / "runtime" / "material-flow"),
            mock.patch.object(lhs, "ARCHIVE_DIR", self.root / "runtime" / "human-score-tables"),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _creation_case(self):
        creation = mf._read(self.run / "creation.json")
        case = mf._read(self.root / creation["_meta"]["case"])
        return creation, case

    def test_template_without_clef_is_unavailable_or_absent(self):
        with mock.patch.object(mf, "_run_dir", return_value=self.run):
            mf.cmd_score_template(argparse.Namespace(run="unit-run"))
        doc = lhs.read_table(self.run)
        self.assertEqual(doc["status"], "pending")
        self.assertEqual(doc["authority"], "human_over_clef")
        self.assertEqual(doc["clef"]["status"], "absent")
        self.assertEqual(len(doc["shots"]), 3)
        self.assertTrue((self.run / "human-score-table.html").is_file())

    def test_template_prefills_from_bound_clef_gate(self):
        review_sha = _sha(self.run / "review.json")
        creation_sha = _sha(self.run / "creation.json")
        case_sha = _sha(self.case_path)
        mf._write(self.run / "clef-gate.json", {
            "schema": "lab-clef-gate/v1",
            "status": "decided",
            "passed": False,
            "review_sha256": review_sha,
            "creation_sha256": creation_sha,
            "case_sha256": case_sha,
            "consumer_policy": mf.CONSUMER_POLICY,
            "decisions": {"consumer_fit": {"choice": "no", "p_yes": 0.1, "pass": False}},
        })
        with mock.patch.object(mf, "_run_dir", return_value=self.run):
            mf.cmd_score_template(argparse.Namespace(run="unit-run"))
        doc = lhs.read_table(self.run)
        self.assertEqual(doc["clef"]["status"], "decided")
        self.assertIs(doc["clef"]["passed"], False)
        self.assertIn("batch_fail", doc["shots"][0]["clef_reference"])

    def test_submit_requires_selection_unique_ranks_and_operator(self):
        creation, case = self._creation_case()
        pending = lhs.ensure_pending_table(
            self.run, self.root, "unit-run", creation, case, mf.CONSUMER_POLICY, _sha)
        pending["shots"][0].update(selected=True, user_rank=1, score=5, note="keep")
        pending["shots"][1].update(selected=True, user_rank=1, score=4)  # dup rank
        with self.assertRaises(ValueError) as ctx:
            lhs.validate_submission(pending, self.run, self.root, creation, case,
                                    mf.CONSUMER_POLICY, _sha, "op1")
        self.assertIn("RANKS_MUST_BE_UNIQUE", str(ctx.exception))
        pending["shots"][1].update(user_rank=2)
        with self.assertRaises(ValueError):
            lhs.validate_submission(pending, self.run, self.root, creation, case,
                                    mf.CONSUMER_POLICY, _sha, "")
        pending["shots"][0]["selected"] = False
        pending["shots"][1]["selected"] = False
        with self.assertRaisesRegex(ValueError, "NEED_AT_LEAST_ONE"):
            lhs.validate_submission(pending, self.run, self.root, creation, case,
                                    mf.CONSUMER_POLICY, _sha, "op1")

    def test_submit_persists_archive_and_unblocks_require(self):
        creation, case = self._creation_case()
        pending = lhs.ensure_pending_table(
            self.run, self.root, "unit-run", creation, case, mf.CONSUMER_POLICY, _sha)
        pending["shots"][0].update(selected=True, user_rank=2, score=4)
        pending["shots"][2].update(selected=True, user_rank=1, score=5, note="best")
        mf._write(self.run / "human-score-table.json", pending)
        with mock.patch.object(mf, "_run_dir", return_value=self.run):
            mf.cmd_score_submit(argparse.Namespace(run="unit-run", by="alice", table=None))
        doc = lhs.read_table(self.run)
        self.assertEqual(doc["status"], "submitted")
        self.assertEqual(doc["selected_ids"], ["s3", "s1"])  # by rank
        self.assertTrue(doc.get("table_sha256"))
        arch = Path(lhs.archive_root(self.root))
        self.assertTrue((arch / "index.jsonl").is_file())
        archives = list(arch.glob("*_unit-run_*.json"))
        self.assertEqual(len(archives), 1)
        table = mf._require_human_score_table(self.run)
        self.assertEqual(table["selected_ids"], ["s3", "s1"])
        with self.assertRaises(SystemExit):
            mf._require_human_score_table(self.run, shots_filter={"s2"})

    def test_clef_failed_does_not_block_when_human_submitted(self):
        creation, case = self._creation_case()
        review_sha = _sha(self.run / "review.json")
        mf._write(self.run / "clef-gate.json", {
            "status": "decided", "passed": False,
            "review_sha256": review_sha,
            "creation_sha256": _sha(self.run / "creation.json"),
            "case_sha256": _sha(self.case_path),
            "consumer_policy": mf.CONSUMER_POLICY,
            "decisions": {},
        })
        pending = lhs.ensure_pending_table(
            self.run, self.root, "unit-run", creation, case, mf.CONSUMER_POLICY, _sha)
        pending["shots"][0].update(selected=True, user_rank=1, score=3)
        submitted = lhs.validate_submission(
            pending, self.run, self.root, creation, case, mf.CONSUMER_POLICY, _sha, "bob")
        lhs.persist_submission(self.run, self.root, submitted)
        # old hard clef check would fail; human table must pass
        mf._require_human_score_table(self.run)
        # _require_gates still needs plan review pass — our review meta matches
        # but _validate_review_output needs full skill contracts; skip calling _require_gates here.

    def test_score_show_prints_table(self):
        creation, case = self._creation_case()
        lhs.ensure_pending_table(
            self.run, self.root, "unit-run", creation, case, mf.CONSUMER_POLICY, _sha)
        with mock.patch.object(mf, "_run_dir", return_value=self.run):
            mf.cmd_score_show(argparse.Namespace(run="unit-run", ledger=False))


if __name__ == "__main__":
    unittest.main()
