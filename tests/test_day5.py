"""Day 5: verify CLI defaults, safe approval, interruption, and bounded fleet work.

Mock the provider-facing Harness for CLI tests and use synchronized real worker
threads for concurrency tests. Never spend API quota in the offline suite.
"""

import contextlib
import io
import threading
import unittest
from unittest.mock import Mock, patch

from chiikawa import cli, run_fleet


class CliTests(unittest.TestCase):
    """Exercise main(argv) and the presentation/approval boundary."""

    def test_headless_defaults_and_explicit_arguments(self):
        """Use yolo for a supplied prompt and forward all documented CLI controls."""
        with patch.object(cli, "Harness") as factory:
            self.assertEqual(cli.main(["-p", "task"]), 0)
            self.assertEqual(factory.call_args.args, (".",))
            self.assertEqual(factory.call_args.kwargs["policy"].mode, "yolo")
            self.assertIsNone(factory.call_args.kwargs["isolation"])
            self.assertEqual(factory.call_args.kwargs["profile"], "standard")
            self.assertEqual(factory.call_args.kwargs["sandbox_network"], "deny")
            factory.return_value.run.assert_called_once_with("task")
        with patch.object(cli, "Harness") as factory:
            cli.main(["--prompt", "task", "-d", "scratch", "-m", "model", "--mode", "read-only", "--max-turns", "9"])
            self.assertEqual(factory.call_args.args, ("scratch",))
            self.assertEqual(factory.call_args.kwargs["model"], "model")
            self.assertEqual(factory.call_args.kwargs["max_turns"], 9)
            self.assertEqual(factory.call_args.kwargs["policy"].mode, "read-only")

    def test_interactive_banner_safe_default_and_eof(self):
        """Show model/mode/workspace, ignore blank input, and reuse one Harness until EOF."""
        output = io.StringIO()
        with patch.object(cli, "Harness") as factory, patch("builtins.input", side_effect=["", "one", "two", EOFError]), \
             contextlib.redirect_stdout(output):
            factory.return_value.model = "gpt-6-astra"
            factory.return_value.workdir = "/scratch"
            factory.return_value.isolation = "jail"
            self.assertEqual(cli.main([]), 0)
        self.assertEqual(factory.call_args.kwargs["policy"].mode, "safe")
        self.assertEqual([entry.args for entry in factory.return_value.run.call_args_list], [("one",), ("two",)])
        self.assertIn("gpt-6-astra", output.getvalue())
        self.assertIn("jail directory: /scratch", output.getvalue())

    def test_resume_precedes_run_and_missing_session_fails(self):
        """Never silently start a new conversation when --resume cannot find one."""
        with patch.object(cli, "Harness") as factory:
            self.assertEqual(cli.main(["--resume", "-p", "continue"]), 0)
            self.assertEqual([entry[0] for entry in factory.return_value.method_calls], ["resume", "run"])
        with patch.object(cli, "Harness") as factory, contextlib.redirect_stderr(io.StringIO()):
            factory.return_value.resume.return_value = False
            with self.assertRaises(SystemExit) as caught:
                cli.main(["--resume", "-p", "continue"])
            self.assertEqual(caught.exception.code, 2)
            factory.return_value.run.assert_not_called()

    def test_interrupt_stops_run_and_reports_resume(self):
        """Return standard interrupt status rather than reusing an unfinished context."""
        output = io.StringIO()
        with patch.object(cli, "Harness") as factory, contextlib.redirect_stderr(output):
            factory.return_value.run.side_effect = KeyboardInterrupt
            self.assertEqual(cli.main(["-p", "task"]), 130)
        self.assertIn("session log is safe", output.getvalue())
        self.assertIn("--resume", output.getvalue())

    def test_errors_have_nonzero_status(self):
        """Surface a useful failure without a misleading successful process exit."""
        with patch.object(cli, "Harness", side_effect=RuntimeError("missing endpoint")), \
             contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(cli.main(["-p", "task"]), 1)
        self.assertIn("missing endpoint", output.getvalue())

    def test_approval_requires_y_and_refuses_eof(self):
        """Empty input, arbitrary truthy strings, and EOF all deny approval."""
        for answer, allowed in (("y", True), (" Y ", True), ("", False), ("yes", False), ("n", False)):
            with patch("builtins.input", return_value=answer), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.approve({"name": "write_file", "args": {}}, "write"), allowed)
        with patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(cli.approve({"name": "bash", "args": {}}, "run"))
        command = "echo " + "x" * 300 + " ; echo review-this-tail"
        with patch("builtins.input", return_value="n"), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertFalse(cli.approve({"name": "bash", "args": {"command": command}}, "run"))
        self.assertIn(command, output.getvalue())

    def test_event_output_is_bounded_and_excludes_provider_state(self):
        """Print one call line and only the first result line, with dimming on terminals."""
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with patch.object(output, "isatty", return_value=True):
                cli.print_event("assistant", {"text": "hello", "provider_output": "secret"})
                cli.print_event("tool_start", {"name": "write_file", "args": {"content": "x" * 1000}})
                cli.print_event("tool_end", {"text": "first\nsecond"})
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertLess(len(lines[1]), 210)
        self.assertIn("\033[2mfirst\033[0m", lines[2])
        self.assertNotIn("secret", output.getvalue())
        self.assertNotIn("second", output.getvalue())


class FleetTests(unittest.TestCase):
    """Prove concurrent execution, ordering, and exception isolation."""

    def test_jobs_overlap_and_results_stay_in_input_order(self):
        """Synchronize two workers so sequential execution would fail this test."""
        barrier = threading.Barrier(2)
        second_done = threading.Event()
        seen = []
        guard = threading.Lock()

        def factory(workdir):
            """Build a new harness-shaped worker for each independent directory."""
            def run(task):
                """Force the second input to finish before the first."""
                barrier.wait(timeout=3)
                if workdir == "first":
                    self.assertTrue(second_done.wait(timeout=3))
                else:
                    second_done.set()
                with guard:
                    seen.append(workdir)
                return task + " report"
            return Mock(run=run)

        jobs = [{"name": name, "workdir": name, "task": name} for name in ("first", "second")]
        results = run_fleet(jobs, factory, max_workers=2)
        self.assertEqual([result["name"] for result in results], ["first", "second"])
        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(set(seen), {"first", "second"})

    def test_factory_and_run_exceptions_do_not_abort_other_jobs(self):
        """Return stable error reports for either construction or execution failures."""
        def factory(workdir):
            """Fail in two different worker lifecycle stages."""
            if workdir == "factory":
                raise ValueError("bad workspace")
            return Mock(run=Mock(side_effect=RuntimeError("bad run"))) if workdir == "run" else Mock(run=lambda task: task)
        jobs = [{"name": name, "workdir": name, "task": "ok"} for name in ("factory", "run", "success")]
        self.assertEqual(run_fleet(jobs, factory), [
            {"name": "factory", "ok": False, "report": "ValueError: bad workspace"},
            {"name": "run", "ok": False, "report": "RuntimeError: bad run"},
            {"name": "success", "ok": True, "report": "ok"}])

    def test_empty_fleet_and_worker_validation(self):
        """An empty fleet creates no harnesses; invalid executor sizes fail clearly."""
        factory = Mock()
        self.assertEqual(run_fleet([], factory), [])
        factory.assert_not_called()
        with self.assertRaises(ValueError):
            run_fleet([], factory, max_workers=0)


if __name__ == "__main__":
    unittest.main()
