"""Approval scope, real dispatch, inheritance, revocation, and terminal input."""

import contextlib
import io
import os
import signal
from pathlib import Path
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import Mock, patch

from chiikawa import Approval, Harness, Policy, cli, provider
from chiikawa.commands import Commands
import test_commands


def reply(*calls):
    return {"text": "DONE" if not calls else "", "tool_calls": [
        {"name": name, "args": args, "call_id": str(index)}
        for index, (name, args) in enumerate(calls)]}


class PermissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_ask_requires_a_fresh_decision_for_every_tool(self):
        decision = Mock(side_effect=[True, False] * 7)
        policy = Policy("ask", decision)
        for name in ("read_file", "list_files", "grep", "write_file", "bash", "remember", "spawn_agent"):
            call = {"name": name, "args": {}}
            self.assertIsNone(policy.check(call))
            self.assertIsNotNone(policy.check(call))
        self.assertEqual(decision.call_count, 14)
        self.assertEqual(policy.mode, "ask")

    def test_all_and_revocation_preserve_denials_and_reject_truthy_values(self):
        call = {"name": "write_file", "args": {}}
        callback = Mock(return_value=Approval.ALL)
        policy = Policy("ask", callback)
        self.assertIsNone(policy.check(call))
        self.assertIsNone(policy.check({"name": "bash", "args": {"command": "echo ok"}}))
        self.assertIn("dangerous", policy.check({"name": "bash", "args": {"command": "sudo ls"}}))
        callback.assert_called_once()
        policy.mode = "ask"
        callback.return_value = False
        self.assertIsNotNone(policy.check(call))
        policy.mode = "read-only"
        callback.reset_mock()
        self.assertIsNotNone(policy.check(call))
        callback.assert_not_called()
        for value in ("all", "a", "yes", 1, {}, None):
            self.assertIsNotNone(Policy("ask", lambda *_: value).check(call))
        with self.assertRaises(ValueError):
            policy.mode = "typo"
        self.assertEqual(policy.mode, "read-only")

    def test_shared_concurrent_policy_prompts_only_once_for_all(self):
        callback = Mock(return_value=Approval.ALL)
        policy = Policy("ask", callback)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(policy.check, [{"name": "write_file", "args": {}}] * 20))
        self.assertEqual(results, [None] * 20)
        callback.assert_called_once()

    def test_model_receives_updated_approval_facts_after_all(self):
        agent = Harness(self.root, policy=Policy("ask", lambda *_: Approval.ALL))
        with patch.object(provider, "complete", side_effect=[
            reply(("list_files", {})), reply(),
        ]) as complete:
            agent.run("inspect")
        self.assertIn('"approval_policy": "ask"', complete.call_args_list[0].args[1])
        self.assertIn('"approval_policy": "yolo"', complete.call_args_list[1].args[1])

    def test_denied_operation_does_not_execute_and_once_is_not_cached(self):
        callback = Mock(side_effect=[True, False])
        agent = Harness(self.root, policy=Policy("ask", callback))
        calls = reply(("write_file", {"path": "yes", "content": "saved"}),
                      ("write_file", {"path": "no", "content": "blocked"}))
        with patch.object(provider, "complete", side_effect=[calls, reply()]):
            agent.run("write two files")
        self.assertEqual((self.root / "yes").read_text(), "saved")
        self.assertFalse((self.root / "no").exists())
        self.assertEqual(callback.call_count, 2)
        self.assertTrue(any(m.get("text", "").startswith("BLOCKED:") for m in agent.messages))

    def test_all_reaches_child_and_new_conversations_but_not_resumed_launch(self):
        callback = Mock(return_value=Approval.ALL)
        agent = Harness(self.root, policy=Policy("ask", callback))
        with patch.object(provider, "complete", side_effect=[
            reply(("spawn_agent", {"task": "write child file"})),
            reply(("write_file", {"path": "child", "content": "saved"})), reply(), reply(),
        ]):
            agent.run("delegate")
        self.assertTrue((self.root / "child").exists())
        callback.assert_called_once()
        saved = agent.session_path
        commands = Commands(agent, lambda name, model, **options: Harness(self.root, provider=name, model=model, **options))
        with contextlib.redirect_stdout(io.StringIO()):
            for command in ("/new", "/model another-model", "/provider openrouter"):
                commands.handle(command)
                self.assertIs(commands.harness.policy, agent.policy)
                self.assertEqual(commands.harness.policy.mode, "yolo")
            commands.handle("/permissions ask")
        self.assertEqual(agent.policy.mode, "ask")
        fresh = Harness(self.root, policy=Policy("safe"))
        fresh.resume(saved)
        self.assertEqual(fresh.policy.mode, "safe")
        self.assertIsNotNone(fresh.policy.check({"name": "write_file", "args": {}}))

    def test_menu_status_and_permissions_do_not_change_conversation(self):
        agent = Harness(self.root, policy=Policy("safe"))
        agent.messages.append({"role": "user", "text": "keep this"})
        commands = Commands(agent, Mock())
        self.assertEqual([x[0] for x in commands.candidates("/perm")], ["/permissions"])
        self.assertEqual([x[0] for x in commands.candidates("/permissions a")],
                         ["/permissions ask", "/permissions all"])
        with contextlib.redirect_stdout(io.StringIO()) as output:
            commands.handle("/permissions all")
            commands.handle("/status")
            commands.handle("/permissions bogus")
            self.assertEqual(agent.policy.mode, "yolo")
            commands.handle("/permissions read-only")
            self.assertEqual(agent.policy.mode, "read-only")
            commands.handle("/permissions ask")
        self.assertIn("Mode: yolo", output.getvalue())
        self.assertIn("until exit", output.getvalue())
        self.assertEqual(agent.messages, [{"role": "user", "text": "keep this"}])
        self.assertIn('"approval_policy": "ask"', agent.system)
        commands.make_harness.assert_not_called()

    def test_cli_choices_and_ask_flag(self):
        for answer, expected in (("y", True), ("a", Approval.ALL), (" A ", Approval.ALL),
                                 ("", False), ("n", False), ("all", False)):
            with patch("builtins.input", return_value=answer), contextlib.redirect_stdout(io.StringIO()):
                self.assertIs(cli.approve({"name": "read_file", "args": {}}, "ask"), expected)
        with patch("builtins.input", side_effect=KeyboardInterrupt), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                cli.approve({"name": "bash", "args": {}}, "ask")
        with patch.object(cli, "Harness") as factory:
            self.assertEqual(cli.main(["--mode", "ask", "-p", "inspect"]), 0)
            self.assertEqual(factory.call_args.kwargs["policy"].mode, "ask")


@unittest.skipUnless(os.name == "posix", "Requires a POSIX terminal")
class PermissionTerminalTests(unittest.TestCase):
    start = test_commands.TerminalTests.start
    cleanup_terminal = test_commands.TerminalTests.cleanup_terminal
    read_until = test_commands.TerminalTests.read_until
    send = test_commands.TerminalTests.send
    assert_restored = test_commands.TerminalTests.assert_restored

    def start_permissions(self):
        self.start(script="""import tempfile
from unittest.mock import patch
from chiikawa import cli, provider
def complete(model, system, messages, tools, **kwargs):
    if messages[-1]['role'] == 'user':
        return {'text': '', 'tool_calls': [
            {'name': 'write_file', 'call_id': '1', 'args': {'path': 'first', 'content': 'one'}},
            {'name': 'write_file', 'call_id': '2', 'args': {'path': 'second', 'content': 'two'}}]}
    return {'text': 'TASK-DONE', 'tool_calls': []}
with tempfile.TemporaryDirectory() as root, patch.object(provider, 'complete', side_effect=complete):
    raise SystemExit(cli.main(['-d', root, '--mode', 'ask']))
""")

    def test_once_all_new_conversation_and_revoke_in_real_terminal(self):
        self.start_permissions()
        self.send("write\r")
        self.read_until(b"approve write_file? [y/a/N]")
        self.send("y\r")
        self.read_until(b"approve write_file? [y/a/N]")
        self.send("a\r")
        self.read_until(b"chiikawa> ")
        self.send("/new\r")
        self.read_until(b"New conversation")
        self.send("write again\r")
        result = self.read_until(b"TASK-DONE")
        self.assertNotIn(b"approve write_file?", result)
        self.read_until(b"chiikawa> ")
        self.send("/permissions ask\r")
        self.read_until(b"Permissions: ask")
        self.send("write third\r")
        self.read_until(b"approve write_file? [y/a/N]")
        self.send("n\r")
        self.read_until(b"approve write_file? [y/a/N]")
        self.send("\r")
        self.read_until(b"chiikawa> ")
        self.send("/exit\r")
        self.assert_restored()

    def test_ctrl_c_at_approval_restores_terminal(self):
        self.start_permissions()
        self.send("write\r")
        self.read_until(b"approve write_file? [y/a/N]")
        # This PTY has no controlling session; deliver the signal a real terminal
        # sends for Ctrl-C, as the existing terminal interruption tests do.
        self.process.send_signal(signal.SIGINT)
        self.assert_restored(expected=130)

    def test_permissions_picker_filter_and_keyboard_selection(self):
        self.start_permissions()
        self.send("/perm\t")
        self.read_until(b"ask (current)")
        self.send("a")
        self.read_until(b"chiikawa> /permissions a")
        self.send("\x1b[B\r")
        self.read_until(b"Permissions: all")
        self.send("/exit\r")
        self.assert_restored()
