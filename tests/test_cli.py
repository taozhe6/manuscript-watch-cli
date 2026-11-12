from __future__ import annotations

import contextlib
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from manuscript_watch_cli.cli import main
from manuscript_watch_cli.ledger import record_snapshot
from manuscript_watch_cli.models import ExitCode, ManuscriptSnapshot, TargetAccount
from manuscript_watch_cli.providers import register_provider
from manuscript_watch_cli.providers.editorial_manager import (
    build_full_page_url,
    parse_author_menu,
    parse_submission_table,
)


class DummyProvider:
    name = "dummy_portal"
    description = "In-memory test provider"

    def __init__(self) -> None:
        self.snapshots: list[ManuscriptSnapshot] = []

    def verify_auth(self, account: TargetAccount, *, timeout: int = 20) -> None:
        return None

    def fetch_submissions(
        self, account: TargetAccount, *, timeout: int = 20
    ) -> list[ManuscriptSnapshot]:
        return list(self.snapshots)


class ManuscriptWatchCliTests(unittest.TestCase):
    def run_cli(self, argv: list[str]) -> tuple[int, str, str]:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_editorial_manager_parsers(self) -> None:
        menu_html = """
        <html><body>
          <fieldset class="datatablecontainer">
            <div class="main_menu_item">
              <a href="auth_pendSubmissions.asp?currentPage=1">Submissions Being Processed</a>
              <span class="count">(2)</span>
            </div>
            <div class="main_menu_item">
              <a href="auth_declined.asp">Declined Revisions</a>
              <span class="count">(0)</span>
            </div>
          </fieldset>
        </body></html>
        """
        cats = parse_author_menu(menu_html, "https://www.editorialmanager.com/gastro/")
        self.assertEqual(len(cats), 2)
        self.assertEqual(cats[0]["label"], "Submissions Being Processed")
        self.assertEqual(cats[0]["count"], 2)
        expanded = build_full_page_url(cats[0]["url"])
        self.assertIn("size1=500", expanded)
        self.assertIn("size2=500", expanded)

        table_html = """
        <html><body>
          <table id="datatable">
            <thead>
              <tr>
                <th>Action Links</th>
                <th>Manuscript Number</th>
                <th>Title</th>
                <th>Initial Date Submitted</th>
                <th>Status Date</th>
                <th>Current Status</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td><a href="#viewAuthorPDFs(9012,'GASTRO-D-26-00123',0,'{ABCD-1234}')">View</a></td>
                <td>GASTRO-D-26-00123</td>
                <td>  Automated   Endoscopy   Benchmark  </td>
                <td>Jan 10, 2026</td>
                <td>Feb 02, 2026</td>
                <td>Under Review</td>
              </tr>
            </tbody>
          </table>
        </body></html>
        """
        acc = TargetAccount(
            journal="gastro",
            name="Gastroenterology",
            username="alice",
            secret="secret",
        )
        snaps = parse_submission_table(
            table_html,
            account=acc,
            category_label="Submissions Being Processed",
        )
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0].manuscript_number, "GASTRO-D-26-00123")
        self.assertEqual(snaps[0].docid, "9012")
        self.assertEqual(snaps[0].guid, "ABCD-1234")
        self.assertEqual(snaps[0].title, "Automated Endoscopy Benchmark")
        self.assertEqual(snaps[0].status, "Under Review")

    def test_ledger_transitions_and_folder_rename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            s1 = ManuscriptSnapshot(
                provider="editorial_manager",
                journal="gastro",
                journal_name="Gastroenterology",
                username="alice",
                category="Submissions Being Processed",
                manuscript_number="MS-2026-01",
                title="Draft Title",
                initial_date_submitted="2026-01-01",
                status_date="2026-01-02",
                status="With Editor",
            )
            r1 = record_snapshot(data_dir, s1, now=datetime(2026, 1, 2, 9, 0, 0))
            self.assertEqual(r1.change, "created")

            r2 = record_snapshot(data_dir, s1, now=datetime(2026, 1, 2, 10, 0, 0))
            self.assertEqual(r2.change, "unchanged")

            s2 = ManuscriptSnapshot(
                provider="editorial_manager",
                journal="gastro",
                journal_name="Gastroenterology",
                username="alice",
                category="Submissions Being Processed",
                manuscript_number="MS-2026-01",
                title="Revised Final Title",
                initial_date_submitted="2026-01-01",
                status_date="2026-01-15",
                status="Under Review",
            )
            r3 = record_snapshot(data_dir, s2, now=datetime(2026, 1, 15, 12, 0, 0))
            self.assertEqual(r3.change, "updated")
            self.assertEqual(r3.previous_status, "With Editor")
            self.assertIn("Revised-Final-Title", r3.history_path)
            self.assertTrue(Path(r3.history_path).exists())

    def test_end_to_end_cli_subcommands_and_exit_codes(self) -> None:
        dummy = DummyProvider()
        register_provider(dummy)

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            data_dir = Path(tmp) / "ledger"

            code, _, _ = self.run_cli(["init", "--config", str(cfg), "-q"])
            self.assertEqual(code, int(ExitCode.OK))
            self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)

            os.environ["TEST_MWATCH_SECRET"] = "top-secret-token"
            try:
                code, _, _ = self.run_cli(
                    [
                        "account",
                        "add",
                        "--config",
                        str(cfg),
                        "--provider",
                        "dummy_portal",
                        "--journal",
                        "demoj",
                        "--name",
                        "Demo Journal",
                        "--username",
                        "researcher",
                        "--password-env",
                        "TEST_MWATCH_SECRET",
                        "--no-verify",
                        "-q",
                    ]
                )
                self.assertEqual(code, int(ExitCode.OK))

                code, out, _ = self.run_cli(
                    ["account", "list", "--config", str(cfg), "--format", "json"]
                )
                self.assertEqual(code, int(ExitCode.OK))
                accounts = json.loads(out)
                self.assertEqual(len(accounts), 1)
                self.assertEqual(accounts[0]["journal"], "demoj")
                self.assertNotIn("top-secret-token", out)

                dummy.snapshots = [
                    ManuscriptSnapshot(
                        provider="dummy_portal",
                        journal="demoj",
                        journal_name="Demo Journal",
                        username="researcher",
                        category="Active",
                        manuscript_number="DEMO-26-001",
                        title="CLI Architecture Study",
                        initial_date_submitted="2026-02-01",
                        status_date="2026-02-03",
                        status="With Editor",
                    )
                ]

                # First check with --exit-code-on-change should return 2 (CHANGED)
                code, out, _ = self.run_cli(
                    [
                        "-q",
                        "check",
                        "--config",
                        str(cfg),
                        "--data-dir",
                        str(data_dir),
                        "--exit-code-on-change",
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(code, int(ExitCode.CHANGED))
                rows = json.loads(out)
                self.assertEqual(rows[0]["change"], "created")

                # Second identical check should return 0 (OK) and empty when --changed-only
                code, out, _ = self.run_cli(
                    [
                        "-q",
                        "check",
                        "--config",
                        str(cfg),
                        "--data-dir",
                        str(data_dir),
                        "--changed-only",
                        "--exit-code-on-change",
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(code, int(ExitCode.OK))
                self.assertEqual(json.loads(out), [])

                # Offline status & history
                code, out, _ = self.run_cli(
                    [
                        "status",
                        "--config",
                        str(cfg),
                        "--data-dir",
                        str(data_dir),
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(code, int(ExitCode.OK))
                st = json.loads(out)
                self.assertEqual(len(st), 1)
                self.assertEqual(st[0]["status"], "With Editor")

                code, out, _ = self.run_cli(
                    [
                        "history",
                        "--config",
                        str(cfg),
                        "--data-dir",
                        str(data_dir),
                        "--format",
                        "tsv",
                    ]
                )
                self.assertEqual(code, int(ExitCode.OK))
                self.assertIn("DEMO-26-001\tWith Editor", out)

                # Doctor detects healthy 0600 config, and flags 0644 config
                code, out, _ = self.run_cli(
                    [
                        "doctor",
                        "--config",
                        str(cfg),
                        "--data-dir",
                        str(data_dir),
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(code, int(ExitCode.OK))
                self.assertTrue(json.loads(out)["healthy"])

                os.chmod(cfg, 0o644)
                code, out, _ = self.run_cli(
                    [
                        "doctor",
                        "--config",
                        str(cfg),
                        "--data-dir",
                        str(data_dir),
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(code, int(ExitCode.USAGE_OR_CONFIG))
                self.assertFalse(json.loads(out)["healthy"])
            finally:
                os.environ.pop("TEST_MWATCH_SECRET", None)


if __name__ == "__main__":
    unittest.main()
