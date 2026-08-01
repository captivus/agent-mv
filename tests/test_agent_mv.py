#!/usr/bin/env python3
"""Tests for agent-mv.

Every test runs against throwaway HOME / CLAUDE_CONFIG_DIR / CODEX_HOME
directories, so no real agent history is read or written. The real claude-mv
and codex-mv are used where they are installed; tests that need them are
skipped otherwise.
"""

import importlib.machinery
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT_MV = os.path.join(os.path.dirname(HERE), "agent-mv")

_MODULE = None


def agent_mv_module():
    global _MODULE
    if _MODULE is None:
        import importlib.util
        spec = importlib.util.spec_from_loader(
            "agent_mv",
            importlib.machinery.SourceFileLoader("agent_mv", AGENT_MV))
        _MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_MODULE)
    return _MODULE


def have(tool):
    return shutil.which(tool) is not None


class Fixture:
    """A project with history for whichever agents a test asks for."""

    SESSION_ID = "019f0000-0000-7000-8000-0000000000a1"

    def __init__(self, tmp, claude=True, codex=True):
        self.tmp = tmp
        self.claude_home = os.path.join(tmp, "claude-home")
        self.codex_home = os.path.join(tmp, "codex-home")
        os.makedirs(os.path.join(self.claude_home, "projects"), exist_ok=True)
        os.makedirs(self.codex_home, exist_ok=True)
        self.old = os.path.join(tmp, "proj")
        self.new = os.path.join(tmp, "proj-renamed")
        os.makedirs(self.old, exist_ok=True)
        with open(os.path.join(self.old, "marker.txt"), "w") as fh:
            fh.write("marker\n")

        self.claude_dir = None
        if claude:
            mod = agent_mv_module()
            self.claude_dir = os.path.join(self.claude_home, "projects",
                                           mod.claude_encode(self.old))
            os.makedirs(self.claude_dir, exist_ok=True)
            with open(os.path.join(self.claude_dir, "session.jsonl"), "w") as fh:
                fh.write('{"type":"user"}\n')

        self.rollout = None
        self.db = None
        if codex:
            day = os.path.join(self.codex_home, "sessions", "2026", "08", "01")
            os.makedirs(day, exist_ok=True)
            self.rollout = os.path.join(
                day, f"rollout-2026-08-01T00-00-00-{self.SESSION_ID}.jsonl")
            meta = {"timestamp": "2026-08-01T00:00:00.000Z",
                    "type": "session_meta",
                    "payload": {"session_id": self.SESSION_ID,
                                "id": self.SESSION_ID, "cwd": self.old,
                                "originator": "codex-tui",
                                "cli_version": "0.146.0", "source": "cli",
                                "model_provider": "openai",
                                "history_mode": "legacy",
                                "base_instructions": {"text": "t"},
                                "context_window": {"window_id": "w"}}}
            with open(self.rollout, "w") as fh:
                fh.write(json.dumps(meta) + "\n")
                fh.write(json.dumps({"type": "event_msg", "payload": {
                    "type": "user_message", "message": "hi"}}) + "\n")
            self.db = os.path.join(self.codex_home, "state_5.sqlite")
            con = sqlite3.connect(self.db)
            with con:
                con.execute(
                    "CREATE TABLE threads (id TEXT PRIMARY KEY,"
                    " rollout_path TEXT NOT NULL, cwd TEXT NOT NULL,"
                    " title TEXT NOT NULL DEFAULT '',"
                    " preview TEXT NOT NULL DEFAULT 'x',"
                    " created_at INTEGER NOT NULL DEFAULT 0,"
                    " updated_at INTEGER NOT NULL DEFAULT 0,"
                    " source TEXT NOT NULL DEFAULT 'cli',"
                    " model_provider TEXT NOT NULL DEFAULT 'openai',"
                    " sandbox_policy TEXT NOT NULL DEFAULT '{}',"
                    " approval_mode TEXT NOT NULL DEFAULT 'on-request')")
                con.execute("INSERT INTO threads (id, rollout_path, cwd)"
                            " VALUES (?,?,?)",
                            (self.SESSION_ID, self.rollout, self.old))
            con.close()
            with open(os.path.join(self.codex_home, "config.toml"), "w") as fh:
                fh.write(f'model = "gpt-5"\n\n[projects."{self.old}"]\n'
                         'trust_level = "trusted"\n')

    def env(self):
        env = dict(os.environ)
        env["CLAUDE_CONFIG_DIR"] = self.claude_home
        env["CODEX_HOME"] = self.codex_home
        env["HOME"] = os.path.join(self.tmp, "fakehome")
        os.makedirs(env["HOME"], exist_ok=True)
        return env

    def run(self, *extra):
        return subprocess.run(
            [sys.executable, AGENT_MV, "-y", "--no-color", *extra,
             self.old, self.new],
            capture_output=True, text=True, env=self.env())

    def claude_new_dir(self):
        mod = agent_mv_module()
        return os.path.join(self.claude_home, "projects",
                            mod.claude_encode(self.new))

    def codex_cwd(self):
        with open(self.rollout, encoding="utf-8") as fh:
            return json.loads(fh.readline())["payload"]["cwd"]

    def db_cwds(self):
        con = sqlite3.connect(self.db)
        try:
            return sorted(r[0] for r in con.execute("SELECT cwd FROM threads"))
        finally:
            con.close()


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-mv-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_claude_path_encoding_matches_claude_code(self):
        mod = agent_mv_module()
        self.assertEqual(mod.claude_encode("/home/alice/my_project.v2"),
                         "-home-alice-my-project-v2")
        self.assertEqual(mod.claude_encode("/home/alice/Ray Dalio"),
                         "-home-alice-Ray-Dalio")

    def test_detects_only_the_agents_that_have_history(self):
        mod = agent_mv_module()
        f = Fixture(self.tmp, claude=True, codex=False)
        env = f.env()
        os.environ["CLAUDE_CONFIG_DIR"] = env["CLAUDE_CONFIG_DIR"]
        os.environ["CODEX_HOME"] = env["CODEX_HOME"]
        try:
            self.assertIsNotNone(mod.claude_history(f.old))
            self.assertEqual(mod.codex_sessions(f.old), 0)
        finally:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            os.environ.pop("CODEX_HOME", None)

    def test_dry_run_changes_nothing(self):
        f = Fixture(self.tmp)
        r = f.run("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(f.old))
        self.assertFalse(os.path.exists(f.new))
        self.assertTrue(os.path.isdir(f.claude_dir))
        self.assertEqual(f.codex_cwd(), f.old)
        self.assertIn("Dry-run complete", r.stderr)

    def test_refuses_when_a_needed_tool_is_missing(self):
        """Moving the directory without the tool would orphan that history."""
        f = Fixture(self.tmp)
        env = f.env()
        env["PATH"] = os.path.join(self.tmp, "empty-path")
        os.makedirs(env["PATH"], exist_ok=True)
        r = subprocess.run(
            [sys.executable, AGENT_MV, "-y", "--no-color", f.old, f.new],
            capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not installed", r.stderr)
        self.assertTrue(os.path.isdir(f.old), "must not move the directory")

    def test_missing_source(self):
        f = Fixture(self.tmp)
        r = subprocess.run(
            [sys.executable, AGENT_MV, "-y", "--no-color",
             os.path.join(self.tmp, "nope"), f.new],
            capture_output=True, text=True, env=f.env())
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Source directory not found", r.stderr)

    def test_existing_destination(self):
        f = Fixture(self.tmp)
        os.makedirs(f.new, exist_ok=True)
        r = f.run()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Destination already exists", r.stderr)

    @unittest.skipUnless(have("codex-mv"), "codex-mv must be installed")
    def test_uses_the_agents_own_session_description_when_it_has_one(self):
        """A pid is not actionable; the terminal and conversation are.

        codex-mv exposes describe_live(); agent-mv must use it rather than
        falling back to listing raw pids.
        """
        f = Fixture(self.tmp)
        fake_bin = os.path.join(self.tmp, "fakebin2")
        os.makedirs(fake_bin, exist_ok=True)
        fake = os.path.join(fake_bin, "codex")
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\nsleep 30\n")
        os.chmod(fake, 0o755)
        proc = subprocess.Popen([fake], cwd=f.old,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.4)
            r = f.run("--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("session(s)", r.stderr,
                          "should use codex-mv's session grouping")
            self.assertIn("process(es)", r.stderr)
        finally:
            proc.kill()
            proc.wait()

    def test_guard_says_how_to_close_the_blocking_session(self):
        """Even without a rich description, the output must be actionable."""
        f = Fixture(self.tmp, claude=True, codex=False)
        fake_bin = os.path.join(self.tmp, "fakebin3")
        os.makedirs(fake_bin, exist_ok=True)
        fake = os.path.join(fake_bin, "claude")
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\nsleep 30\n")
        os.chmod(fake, 0o755)
        proc = subprocess.Popen([fake], cwd=f.old,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.4)
            r = f.run("--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("close it: kill", r.stderr)
            self.assertIn(str(proc.pid), r.stderr)
        finally:
            proc.kill()
            proc.wait()

    def test_refuses_while_an_agent_is_running_in_the_project(self):
        f = Fixture(self.tmp)
        fake_bin = os.path.join(self.tmp, "fakebin")
        os.makedirs(fake_bin, exist_ok=True)
        fake = os.path.join(fake_bin, "claude")
        with open(fake, "w") as fh:
            fh.write("#!/bin/sh\nsleep 30\n")
        os.chmod(fake, 0o755)
        proc = subprocess.Popen([fake], cwd=f.old,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        try:
            time.sleep(0.4)
            r = f.run()
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("agent is running", r.stderr)
            self.assertTrue(os.path.isdir(f.old), "must not move the directory")
            self.assertTrue(os.path.isdir(f.claude_dir))
        finally:
            proc.kill()
            proc.wait()


@unittest.skipUnless(have("claude-mv") and have("codex-mv"),
                     "claude-mv and codex-mv must be installed")
class EndToEndTests(unittest.TestCase):
    """The whole point: one command, both agents follow the project."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-mv-e2e-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_moves_the_project_and_repoints_both_agents(self):
        f = Fixture(self.tmp)
        r = f.run()
        self.assertEqual(r.returncode, 0, r.stderr)

        self.assertTrue(os.path.isdir(f.new))
        self.assertFalse(os.path.exists(f.old))
        self.assertTrue(os.path.exists(os.path.join(f.new, "marker.txt")))

        self.assertTrue(os.path.isdir(f.claude_new_dir()),
                        "Claude history must follow the project")
        self.assertFalse(os.path.isdir(f.claude_dir))

        self.assertEqual(f.codex_cwd(), f.new)
        self.assertEqual(f.db_cwds(), [f.new])
        with open(os.path.join(f.codex_home, "config.toml")) as fh:
            self.assertIn(f'[projects."{f.new}"]', fh.read())

    def test_works_when_only_one_agent_has_history(self):
        f = Fixture(self.tmp, claude=True, codex=False)
        r = f.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(f.new))
        self.assertTrue(os.path.isdir(f.claude_new_dir()))

    def test_renames_a_project_no_agent_knows_about(self):
        f = Fixture(self.tmp, claude=False, codex=False)
        r = f.run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isdir(f.new))
        self.assertIn("No agent history found", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
