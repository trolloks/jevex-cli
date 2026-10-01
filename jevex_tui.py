"""The Jevex terminal UI."""

from threading import Thread

from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, RichLog, SelectionList, Static

from jevex import available_models, load_key, load_settings, route, run_codex, save_key, save_settings


class CatalogResult(Message):
    def __init__(self, models=None, settings=None, error=None):
        super().__init__()
        self.models = models
        self.settings = settings
        self.error = error


class ModelPicker(ModalScreen[set[str] | None]):
    def __init__(self, models, excluded, first_run=False):
        super().__init__()
        self.models = models
        self.excluded = excluded
        self.first_run = first_run

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static("CHOOSE MODELS", classes="dialog-title")
            yield Static("Selected models are eligible for Jev routing. Space to toggle.", classes="dialog-note")
            yield SelectionList[str](
                *((f"{item.get('displayName') or model}  ·  {model}", model, model not in self.excluded)
                  for model, item in self.models.items()),
                id="choices",
            )
            yield Static("Select at least one model.", id="dialog-hint")
            with Horizontal(classes="dialog-actions"):
                yield Button("SAVE", id="save", variant="primary")
                yield Button("QUIT" if self.first_run else "CANCEL", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#choices", SelectionList).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        selected = set(self.query_one("#choices", SelectionList).selected)
        if selected:
            self.dismiss(set(self.models) - selected)
        else:
            self.query_one("#dialog-hint", Static).update("Select at least one model to continue.")


class KeyPicker(ModalScreen[str | None]):
    def compose(self) -> ComposeResult:
        with Vertical(id="key-dialog"):
            yield Static("JEV API KEY", classes="dialog-title")
            yield Static("Paste your key. It stays in a local file readable only by you.", classes="dialog-note")
            yield Input(password=True, placeholder="Paste key here", id="key-input")
            yield Static("You can add or change this later with Ctrl+K.", id="key-hint")
            with Horizontal(classes="dialog-actions"):
                yield Button("SAVE KEY", id="key-save", variant="primary")
                yield Button("LATER", id="key-later")

    def on_mount(self) -> None:
        self.query_one("#key-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "key-later":
            self.dismiss(None)
            return
        key = self.query_one("#key-input", Input).value.strip()
        if key:
            self.dismiss(key)
        else:
            self.query_one("#key-hint", Static).update("Paste a key, or choose Later.")


class JevexApp(App):
    TITLE = "Jevex"
    CSS = """
Screen { background: #10111a; color: #e6e7ef; }
#main { height: 1fr; }
#top { height: 3; padding: 0 2; background: #191a29; align-vertical: middle; }
#brand { width: 1fr; color: #c9b5ff; text-style: bold; }
#route { width: auto; color: #80e5d4; text-style: bold; }
#transcript { height: 1fr; padding: 1 3; background: #10111a; scrollbar-color: #55506f; }
#status { height: 2; padding: 0 2; background: #191a29; color: #98a0bb; content-align-vertical: middle; }
#composer { height: 5; padding: 1 2; background: #191a29; }
#prompt { width: 1fr; height: 3; background: #222336; border: none; color: #f3f2fa; padding: 0 1; }
#prompt:focus { background: #2c2d48; }
#send { width: 10; height: 3; margin-left: 1; background: #7658b8; color: #ffffff; border: none; text-style: bold; }
#send:hover { background: #9370d3; }
Footer { background: #191a29; color: #98a0bb; }
ModelPicker, KeyPicker { align: center middle; background: #080910 80%; }
#dialog, #key-dialog { width: 76; padding: 1 2; background: #1c1d2d; }
#dialog { height: 80%; max-height: 27; }
#key-dialog { height: 16; }
.dialog-title { height: 2; color: #d3c2ff; text-style: bold; }
.dialog-note { height: 2; color: #aeb3c7; }
#choices { height: 1fr; background: #25263a; }
#dialog-hint, #key-hint { height: 2; color: #80e5d4; }
#key-input { height: 3; background: #25263a; border: none; }
.dialog-actions { height: 3; align-horizontal: right; }
.dialog-actions Button { width: 13; margin-left: 1; }
    """
    BINDINGS = [
        Binding("ctrl+m", "models", "Models", priority=True),
        Binding("ctrl+k", "key", "API key", priority=True),
        Binding("ctrl+l", "clear_log", "Clear view", priority=True),
        Binding("ctrl+q", "quit", "Quit", priority=True),
    ]

    def __init__(self, session=None, model=None):
        super().__init__()
        self.session = session
        self.forced_model = model
        self.models = None
        self.busy = False

    def compose(self) -> ComposeResult:
        with Vertical(id="main"):
            with Horizontal(id="top"):
                yield Static("JEVEX", id="brand")
                yield Static("AUTO", id="route")
            yield RichLog(id="transcript", wrap=True, markup=False, auto_scroll=True)
            yield Static("Loading Codex models…", id="status")
            with Horizontal(id="composer"):
                yield Input(id="prompt", placeholder="Ask Codex…  Enter to send")
                yield Button("SEND", id="send", variant="primary", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self.query_one("#transcript", RichLog).write(Text("  Ready when you are.", style="#9299b1"))
        Thread(target=self.load_catalog, daemon=True).start()

    def load_catalog(self) -> None:
        try:
            self.post_message(CatalogResult(available_models(), load_settings()))
        except Exception as exc:
            self.post_message(CatalogResult(error=str(exc)))

    def on_catalog_result(self, message: CatalogResult) -> None:
        if message.error:
            self.query_one("#status", Static).update(f"Model catalog error: {message.error}")
            return
        self.models = message.models
        settings = message.settings
        if settings is None or set(self.models) - settings["known_models"]:
            self.query_one("#status", Static).update("Review the available models to continue")
            self.push_screen(ModelPicker(self.models, settings["excluded_models"] if settings else set(), settings is None), self.settings_saved)
        else:
            self.ready()

    def ready(self) -> None:
        self.query_one("#send", Button).disabled = False
        self.query_one("#status", Static).update(f"Ready  ·  session {self.session or 'new'}")

    def settings_saved(self, excluded: set[str] | None) -> None:
        if excluded is None:
            if load_settings() is None:
                self.exit()
            return
        try:
            save_settings(excluded, set(self.models))
        except OSError as exc:
            self.query_one("#status", Static).update(f"Could not save model selection: {exc}")
            return
        self.ready()
        if not load_key():
            self.push_screen(KeyPicker(), self.key_saved)
        else:
            self.query_one("#prompt", Input).focus()

    def key_saved(self, key: str | None) -> None:
        if key:
            try:
                save_key(key)
            except OSError as exc:
                self.query_one("#status", Static).update(f"Could not save API key: {exc}")
                return
            self.query_one("#status", Static).update("Jev key saved  ·  ready")
        self.query_one("#prompt", Input).focus()

    def action_models(self) -> None:
        if self.models and not self.busy:
            settings = load_settings()
            self.push_screen(ModelPicker(self.models, settings["excluded_models"] if settings else set(), settings is None), self.settings_saved)

    def action_key(self) -> None:
        if not self.busy:
            self.push_screen(KeyPicker(), self.key_saved)

    def action_clear_log(self) -> None:
        self.query_one("#transcript", RichLog).clear()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send":
            self.action_send()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "prompt":
            self.action_send()

    def action_send(self) -> None:
        if self.busy or self.query_one("#send", Button).disabled:
            return
        box = self.query_one("#prompt", Input)
        prompt = box.value.strip()
        if not prompt:
            return
        box.clear()
        log = self.query_one("#transcript", RichLog)
        log.write(Text("\n  YOU", style="bold #80e5d4"))
        log.write(Text(prompt, style="#e6e7ef"))
        self.busy = True
        self.query_one("#send", Button).disabled = True
        self.query_one("#status", Static).update("Choosing a model…")
        self.run_turn(prompt)

    @work(thread=True, exclusive=True)
    def run_turn(self, prompt: str) -> None:
        try:
            model, source, _ = route(prompt, self.forced_model, self.models)
            self.call_from_thread(self.show_route, model, source)
            session, code = run_codex(prompt, model, self.session, lambda event: self.call_from_thread(self.show_event, event), source)
        except Exception as exc:
            self.call_from_thread(self.show_error, str(exc))
            return
        self.call_from_thread(self.finish_turn, session, code)

    def show_route(self, model: str, source: str) -> None:
        self.query_one("#route", Static).update(model)
        self.query_one("#status", Static).update(f"Working  ·  {source}")
        self.query_one("#transcript", RichLog).write(Text(f"  ↳ {model}  ·  {source}", style="#aa96d6"))

    def show_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "thread.started":
            self.session = event.get("thread_id", self.session)
        elif kind == "item.started" and event.get("item", {}).get("type") == "command_execution":
            command = event["item"].get("command", "")
            self.query_one("#transcript", RichLog).write(Text(f"  $ {command[:140]}", style="#858ba2"))
        elif kind == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message" and item.get("text"):
                log = self.query_one("#transcript", RichLog)
                log.write(Text("\n  CODEX", style="bold #c9b5ff"))
                log.write(Markdown(item["text"]))
        elif kind == "jevex.stderr":
            self.query_one("#transcript", RichLog).write(Text(event["message"], style="#e5a0a9"))
        elif kind == "jevex.usage":
            usage = event["record"]["usage"]
            total, cached = usage.get("input_tokens", 0), usage.get("cached_input_tokens", 0)
            if total:
                line = f"  CACHE  {cached:,} / {total:,} input tokens reused ({cached / total:.0%})"
                if event["previous_cached"] is not None:
                    line += f"  ·  {cached - event['previous_cached']:+,} vs previous turn"
                self.query_one("#transcript", RichLog).write(Text(line, style="#80e5d4"))
        elif kind == "turn.failed":
            self.query_one("#transcript", RichLog).write(Text(str(event.get("error", "Turn failed")), style="#e5a0a9"))

    def show_error(self, message: str) -> None:
        self.query_one("#transcript", RichLog).write(Text(f"  ERROR  {message}", style="bold #e5a0a9"))
        self.finish_turn(self.session, 1)

    def finish_turn(self, session: str | None, code: int) -> None:
        self.session = session
        self.busy = False
        self.query_one("#send", Button).disabled = False
        self.query_one("#status", Static).update(f"{'Ready' if code == 0 else f'Codex exited {code}'}  ·  session {session or 'new'}")
        self.query_one("#prompt", Input).focus()
