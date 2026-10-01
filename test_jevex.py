import asyncio
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import jevex


MODELS = {
    "fast-v2": {"displayName": "Fast", "description": "Focused tasks", "isDefault": False},
    "strong-v3": {"displayName": "Strong", "description": "Difficult tasks", "isDefault": True},
}


class JevexTests(unittest.TestCase):
    def test_jev_choice_uses_live_model_ids(self):
        reply = {"answers": {"tier": {"type": "choice", "choice": "fast-v2"}}}
        with patch("jevex.urlopen", return_value=io.BytesIO(json.dumps(reply).encode())) as request:
            self.assertEqual(jevex.choose_model("Fix a typo", "secret", MODELS), "fast-v2")
        body = json.loads(request.call_args.args[0].data)
        self.assertEqual(set(body["questions"]["tier"]["criteria"]), set(MODELS))
        self.assertEqual(body["state"], "Fix a typo")

    def test_unknown_jev_model_falls_back_to_codex_default(self):
        reply = {"answers": {"tier": {"type": "choice", "choice": "unlisted"}}}
        settings = {"excluded_models": set(), "known_models": set(MODELS)}
        with patch("jevex.available_models", return_value=MODELS), patch("jevex.load_settings", return_value=settings), patch.dict("jevex.os.environ", {"JEV_API_KEY": "secret"}), patch("jevex.urlopen", return_value=io.BytesIO(json.dumps(reply).encode())):
            model, source, _ = jevex.route("Fix a typo")
        self.assertEqual(model, "strong-v3")
        self.assertIn("invalid model choice", source)

    def test_new_model_triggers_review_once_and_exclusion_holds(self):
        with patch("jevex.available_models", return_value=MODELS), patch("jevex.load_settings", return_value={"excluded_models": set(), "known_models": {"fast-v2"}}):
            with self.assertRaisesRegex(ValueError, "new models"):
                jevex.route("Fix a typo")
        with patch("jevex.available_models", return_value=MODELS), patch("jevex.load_settings", return_value={"excluded_models": {"fast-v2"}, "known_models": set(MODELS)}):
            model, _, _ = jevex.route("Fix a typo")
        self.assertEqual(model, "strong-v3")

    def test_resume_passes_same_session_with_new_model(self):
        self.assertEqual(
            jevex.codex_command("Next task", "fast-v2", "session-123"),
            ["codex", "exec", "resume", "--json", "--model", "fast-v2", "session-123", "Next task"],
        )

    def test_usage_log_tracks_model_switch_without_storing_prompt(self):
        class Process:
            def __init__(self, turn):
                self.stdout = iter([
                    json.dumps({"type": "thread.started", "thread_id": "session-123"}),
                    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100 * turn, "cached_input_tokens": 40 * turn}}),
                ])

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def wait(self):
                return 0

        turns = iter((1, 2))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_STATE_HOME": directory}), patch("jevex.subprocess.Popen", side_effect=lambda *args, **kwargs: Process(next(turns))):
            jevex.run_codex("secret prompt", "fast-v2")
            jevex.run_codex("secret prompt", "strong-v3", "session-123", source="jev")
            records = jevex.usage_records()
            self.assertEqual([r["previous_model"] for r in records], [None, "fast-v2"])
            self.assertEqual(records[-1]["usage"]["cached_input_tokens"], 40)
            self.assertEqual(records[-1]["usage"]["input_tokens"], 100)
            self.assertNotIn("secret prompt", jevex.usage_path().read_text())
            self.assertEqual(jevex.usage_path().stat().st_mode & 0o777, 0o600)

    def test_tui_mounts(self):
        from jevex_tui import JevexApp
        from textual.widgets import Input

        async def check():
            settings = {"excluded_models": set(), "known_models": set(MODELS)}
            sent = []
            with patch("jevex_tui.available_models", return_value=MODELS), patch("jevex_tui.load_settings", return_value=settings), patch.object(JevexApp, "action_send", lambda app: sent.append(app.query_one("#prompt", Input).value)):
                async with JevexApp().run_test() as pilot:
                    await pilot.pause()
                    box = pilot.app.query_one("#prompt", Input)
                    self.assertIsNotNone(pilot.app.query_one("#transcript"))
                    box.value = "long prompt " * 30
                    await pilot.press("enter")
                    await pilot.pause()
                    self.assertEqual(sent, ["long prompt " * 30])

        asyncio.run(check())

    def test_first_run_saves_model_opt_out_and_masked_key(self):
        from jevex_tui import JevexApp, KeyPicker, ModelPicker
        from textual.widgets import Input, SelectionList

        async def check():
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}, clear=False), patch("jevex_tui.available_models", return_value=MODELS):
                os.environ.pop("JEV_API_KEY", None)
                async with JevexApp().run_test() as pilot:
                    await pilot.pause()
                    self.assertIsInstance(pilot.app.screen, ModelPicker)
                    pilot.app.screen.query_one(SelectionList).deselect("fast-v2")
                    await pilot.click("#save")
                    await pilot.pause()
                    self.assertIsInstance(pilot.app.screen, KeyPicker)
                    pilot.app.screen.query_one(Input).value = "test-secret"
                    await pilot.click("#key-save")
                    await pilot.pause()
                settings = jevex.load_settings()
                self.assertEqual(settings["excluded_models"], {"fast-v2"})
                self.assertEqual(settings["known_models"], set(MODELS))
                self.assertEqual(jevex.load_key(), "test-secret")
                self.assertEqual(jevex.key_path().stat().st_mode & 0o777, 0o600)

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
