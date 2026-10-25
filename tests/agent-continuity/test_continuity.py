import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[2] / "scripts/agent-continuity.py"
spec = importlib.util.spec_from_file_location("continuity", SOURCE)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


class Continuity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / "repo"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.state = self.project / "local/agent-continuity"
        self.data = {"session_id": "s1", "cwd": str(self.project),
                     "transcript_path": str(self.base / "session.jsonl")}

    def event(self, event, vendor="codex", **kw):
        data = dict(self.data, hook_event_name=event, **kw)
        return C.handle(vendor, data, self.project, self.state)

    def transcript(self, *rows, path=None):
        path = Path(path or self.data["transcript_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return str(path)

    def codex_usage(self, tokens, cumulative=9_000_000):
        return {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": tokens, "cached_input_tokens": tokens // 2 if type(tokens) is int else 0},
            "total_token_usage": {"input_tokens": cumulative}}}}

    def claude_usage(self, input=100, cache=200, creation=300):
        return {"type": "assistant", "message": {"usage": {
            "input_tokens": input, "cache_read_input_tokens": cache,
            "cache_creation_input_tokens": creation, "output_tokens": 50}}}

    def test_codex_uses_latest_input_not_cumulative_or_double_counted_cache(self):
        p = self.transcript(self.codex_usage(400_000), self.codex_usage(20_000))
        self.assertEqual(C.current_tokens("codex", p), 20_000)

    def test_claude_includes_cache_but_not_output(self):
        self.assertEqual(C.current_tokens("claude", self.transcript(self.claude_usage())), 600)

    def test_invalid_missing_and_negative_counters_unavailable(self):
        for row in ({}, self.codex_usage(-1), self.codex_usage("400000")):
            self.assertIsNone(C.current_tokens("codex", self.transcript(row)))
        self.assertIsNone(C.current_tokens("codex", self.base / "missing"))

    def test_bounded_tail_survives_partial_first_line(self):
        p = Path(self.transcript(self.codex_usage(350_000)))
        p.write_text("x" * (C.READ_LIMIT * 2) + "\n" + p.read_text())
        self.assertEqual(C.current_tokens("codex", p), 350_000)

    def test_start_injects_paths_and_role_but_does_not_invent_semantic_state(self):
        out = self.event("SessionStart")
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(self.state / "master.md"), text)
        self.assertIn("350k", text)
        self.assertFalse((self.state / "master.md").exists())
        self.assertFalse((self.state / "checkpoints").exists())

    def test_subagent_start_separates_agents_with_same_parent_session(self):
        a = self.event("SubagentStart", agent_id="a")
        b = self.event("SubagentStart", agent_id="b")
        self.assertNotEqual(a, b)
        self.assertIn("You are a worker", a["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(len(list((self.state / "runtime").glob("*.json"))), 2)

    def test_other_repository_has_no_side_effects_or_context(self):
        other = self.base / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        self.assertEqual(self.event("SessionStart", cwd=str(other)), {})
        self.assertFalse(self.state.exists())

    def test_linked_worktree_uses_shared_state(self):
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Test",
                        "-c", "user.email=test@example.invalid", "commit", "--allow-empty",
                        "-qm", "fixture"], check=True)
        wt = self.base / "linked"
        subprocess.run(["git", "-C", str(self.project), "worktree", "add", "-q", "-b", "linked",
                        str(wt)], check=True)
        self.assertIn("hookSpecificOutput", self.event("SessionStart", cwd=str(wt)))

    def test_warning_once_and_small_context_never_blocks_stop(self):
        self.transcript(self.codex_usage(349_999))
        self.assertEqual(self.event("PostToolUse"), {})
        self.assertEqual(self.event("Stop"), {})
        self.transcript(self.codex_usage(350_000))
        out = self.event("PostToolUse")
        self.assertIn("350000 tokens", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.event("PostToolUse"), {})

    def test_native_claude_uses_child_counter_not_parent(self):
        self.transcript(self.claude_usage(input=800_000))
        self.assertEqual(self.event("PostToolUse", vendor="claude", agent_id="kid"), {})
        parent = Path(self.data["transcript_path"])
        child = parent.parent / parent.stem / "subagents/agent-kid.jsonl"
        self.transcript(self.claude_usage(input=350_000), path=child)
        self.assertIn("hookSpecificOutput", self.event("PostToolUse", vendor="claude", agent_id="kid"))

    def test_native_codex_does_not_apply_parent_counter_to_child(self):
        self.transcript(self.codex_usage(800_000))
        self.assertEqual(self.event("PostToolUse", agent_id="kid"), {})
        p = self.transcript(self.codex_usage(350_000), path=self.base / "child.jsonl")
        self.assertIn("hookSpecificOutput", self.event("PostToolUse", agent_id="kid", agent_transcript_path=p))

    def test_snapshot_preserves_existing_documents_and_marks_missing(self):
        self.state.mkdir(parents=True)
        master = self.state / "master.md"
        master.write_text("current owner; active cleanup\n")
        self.assertEqual(self.event("PreCompact", trigger="auto"), {})
        snap = next((self.state / "snapshots").iterdir())
        self.assertEqual((snap / "master.md").read_bytes(), master.read_bytes())
        receipt = json.loads((snap / "receipt.json").read_text())
        self.assertTrue(receipt["checkpoint_missing"])
        self.assertEqual(master.read_text(), "current owner; active cleanup\n")

    def test_snapshot_both_documents_and_explicit_truncation(self):
        key = C.identity("codex", self.data)
        cp = self.state / "checkpoints" / (key + ".md")
        cp.parent.mkdir(parents=True)
        cp.write_text("checkpoint")
        (self.state / "master.md").write_text("x" * (C.SNAPSHOT_LIMIT + 10))
        self.event("PreCompact")
        snap = next((self.state / "snapshots").iterdir())
        r = json.loads((snap / "receipt.json").read_text())
        self.assertFalse(r["checkpoint_missing"])
        self.assertTrue(r["copied"][0]["truncated"])
        self.assertEqual((snap / cp.name).read_text(), "checkpoint")

    def test_postcompact_recovery_before_next_request_and_one_delivery(self):
        self.event("PostCompact")
        out = self.event("SessionStart", source="compact")
        self.assertIn("hookSpecificOutput", out)
        self.assertEqual(self.event("UserPromptSubmit"), {})

    def test_subagent_recovery_fallback_at_posttool(self):
        self.event("PostCompact", agent_id="child")
        out = self.event("PostToolUse", agent_id="child")
        self.assertIn("Compaction completed", out["hookSpecificOutput"]["additionalContext"])
        self.assertEqual(self.event("PostToolUse", agent_id="child"), {})

    def test_compaction_resets_warning_for_next_window(self):
        self.transcript(self.codex_usage(350_000))
        self.event("PostToolUse")
        self.event("PostCompact")
        self.event("SessionStart", source="compact")
        self.assertIn("hookSpecificOutput", self.event("PostToolUse"))

    def test_stop_requests_checkpoint_once_and_fresh_checkpoint_satisfies_it(self):
        self.transcript(self.codex_usage(350_000))
        self.event("PostToolUse")
        self.assertEqual(self.event("Stop")["decision"], "block")
        self.assertEqual(self.event("Stop", stop_hook_active=True), {})
        cp = self.state / "checkpoints" / (C.identity("codex", self.data) + ".md")
        cp.parent.mkdir()
        cp.write_text("handoff")
        os.utime(cp, (time.time() + 1, time.time() + 1))
        self.assertEqual(self.event("Stop"), {})

    def test_cli_bad_json_is_advisory_and_never_reads_extra_commands(self):
        r = subprocess.run([sys.executable, str(SOURCE), "--vendor", "codex"],
                           input="not json", text=True, capture_output=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), {})
        self.assertIn("agent-continuity", r.stderr)


if __name__ == "__main__":
    unittest.main()
