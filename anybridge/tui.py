"""Minimal keyboard-first Textual front-end for AnyBridge."""

from __future__ import annotations

from shutil import which

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .launcher import AGENT_COMMANDS, LaunchError, launch_agent
from .logo import WHITE, wordmark
from .repositories import RepositoryError, RepositoryStore
from .sites import SiteStore, SiteStoreError


class Logo(Static):
    """The smallest faithful terminal-native AnyBridge wordmark."""

    def render(self):
        return wordmark(size="small", color=WHITE)


class AddSiteScreen(ModalScreen[bool]):
    """Keyboard-only form for adding a saved site or repository."""

    CSS = """
    AddSiteScreen {
        align: center middle;
        background: #050505 85%;
    }

    #add-dialog {
        width: 56;
        height: 13;
        padding: 1 2;
        border: solid #4a4a4a;
        background: #090909;
    }

    #add-title {
        height: 1;
        margin-bottom: 1;
        color: #f4f4f4;
        text-style: bold;
    }

    AddSiteScreen Input {
        width: 100%;
        margin-bottom: 1;
        border: tall #333333;
        background: #101010;
        color: #f4f4f4;
    }

    AddSiteScreen Input:focus {
        border: tall #f4f4f4;
    }

    #add-error {
        height: 1;
        color: #a8a8a8;
    }

    #add-keys {
        height: 1;
        color: #626262;
        text-align: center;
    }
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(
        self,
        store: SiteStore | RepositoryStore,
        *,
        kind: str = "site",
    ) -> None:
        super().__init__()
        self.store = store
        self.kind = kind

    def compose(self) -> ComposeResult:
        with Vertical(id="add-dialog"):
            title = "SAVE A SITE" if self.kind == "site" else "SAVE A REPOSITORY"
            placeholder = (
                "https://example.com"
                if self.kind == "site"
                else "https://github.com/owner/repository"
            )
            yield Static(title, id="add-title")
            yield Input(placeholder="name", id="site-name")
            yield Input(placeholder=placeholder, id="site-url")
            yield Static("", id="add-error")
            yield Static("enter next/save     esc back", id="add-keys")

    def on_mount(self) -> None:
        self.query_one("#site-name", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "site-name":
            self.query_one("#site-url", Input).focus()
            return
        try:
            self.store.save(
                self.query_one("#site-name", Input).value,
                self.query_one("#site-url", Input).value,
            )
        except (SiteStoreError, RepositoryError) as error:
            self.query_one("#add-error", Static).update(str(error))
            return
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class SavedSitesScreen(ModalScreen[None]):
    """Compact saved-site and repository manager."""

    CSS = """
    SavedSitesScreen {
        align: center middle;
        background: #050505 85%;
    }

    #sites-dialog {
        width: 58;
        height: 16;
        padding: 1 2;
        border: solid #4a4a4a;
        background: #090909;
    }

    #sites-title {
        height: 1;
        color: #f4f4f4;
        text-style: bold;
    }

    #saved-list {
        width: 100%;
        height: 9;
        margin-top: 1;
        background: #090909;
        scrollbar-size: 0 0;
    }

    #saved-list > .option-list--option {
        padding: 0 1;
        color: #bdbdbd;
    }

    #saved-list > .option-list--option-highlighted {
        background: #f4f4f4;
        color: #050505;
    }

    #sites-status {
        height: 1;
        color: #777777;
    }

    #sites-keys {
        height: 1;
        color: #626262;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("a", "add", show=False),
        Binding("r", "add_repository", show=False),
        Binding("d", "delete", show=False),
        Binding("escape", "back", show=False),
        Binding("q", "back", show=False),
    ]

    def __init__(
        self,
        store: SiteStore,
        repository_store: RepositoryStore | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.repository_store = repository_store or RepositoryStore()
        self._entries: dict[str, tuple[str, str]] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="sites-dialog"):
            yield Static("SAVED", id="sites-title")
            yield OptionList(id="saved-list", compact=True)
            yield Static("", id="sites-status")
            yield Static("a site     r repo     d delete     esc back", id="sites-keys")

    def on_mount(self) -> None:
        self._reload()

    def _reload(self) -> None:
        options = self.query_one("#saved-list", OptionList)
        options.clear_options()
        self._entries.clear()
        try:
            sites = self.store.list()
            repositories = self.repository_store.list()
        except (SiteStoreError, RepositoryError) as error:
            options.add_option(Option("Unable to read saved items", disabled=True))
            self.query_one("#sites-status", Static).update(str(error))
            return
        if not sites and not repositories:
            options.add_option(Option("No saved items", disabled=True))
        else:
            for index, site in enumerate(sites):
                option_id = f"site-{index}"
                self._entries[option_id] = ("site", site.name)
                options.add_option(
                    Option(f"web  {site.name:<14}[dim]{site.url}[/dim]", id=option_id)
                )
            for index, repository in enumerate(repositories):
                option_id = f"repository-{index}"
                self._entries[option_id] = ("repository", repository.name)
                options.add_option(
                    Option(
                        f"git  {repository.name:<14}[dim]{repository.url}[/dim]",
                        id=option_id,
                    )
                )
            options.highlighted = 0
        options.focus()

    def action_add(self) -> None:
        self.app.push_screen(AddSiteScreen(self.store), self._after_add)

    def action_add_repository(self) -> None:
        self.app.push_screen(
            AddSiteScreen(self.repository_store, kind="repository"),
            self._after_add,
        )

    def _after_add(self, saved: bool | None) -> None:
        if saved:
            self.query_one("#sites-status", Static).update("Saved")
            self._reload()

    def action_delete(self) -> None:
        options = self.query_one("#saved-list", OptionList)
        if options.highlighted is None:
            return
        option = options.get_option_at_index(options.highlighted)
        entry = self._entries.get(option.id or "")
        if not entry:
            return
        kind, name = entry
        try:
            if kind == "site":
                self.store.remove(name)
            else:
                self.repository_store.remove(name)
        except (SiteStoreError, RepositoryError) as error:
            self.query_one("#sites-status", Static).update(str(error))
            return
        self.query_one("#sites-status", Static).update(f'Removed "{name}"')
        self._reload()

    def action_back(self) -> None:
        self.dismiss()


class AnyBridgeTUI(App[str | None]):
    """A compact agent picker controlled entirely from the keyboard."""

    TITLE = "anybridge"
    SUB_TITLE = "web + git bridge"
    ENABLE_COMMAND_PALETTE = False

    CSS = """
    Screen {
        align: center middle;
        background: #050505;
        color: #f4f4f4;
        overflow: hidden;
    }

    #page {
        width: 64;
        max-width: 96%;
        height: 18;
    }

    #logo {
        width: 100%;
        height: 7;
        color: #f4f4f4;
        text-align: center;
    }

    #tagline {
        width: 100%;
        height: 1;
        color: #bdbdbd;
        text-align: center;
    }

    #prompt {
        width: 100%;
        height: 1;
        margin-top: 1;
        color: #8c8c8c;
        text-align: center;
    }

    #agent-list {
        width: 46;
        height: 5;
        margin: 1 9 0 9;
        padding: 0 1;
        border: solid #303030;
        background: #090909;
        scrollbar-size: 0 0;
    }

    #agent-list > .option-list--option {
        padding: 0 1;
        color: #bdbdbd;
    }

    #agent-list > .option-list--option-highlighted {
        background: #f4f4f4;
        color: #050505;
        text-style: bold;
    }

    #agent-list > .option-list--option-disabled {
        color: #4d4d4d;
    }

    #keys {
        width: 100%;
        height: 1;
        margin-top: 1;
        color: #626262;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("s", "saved_sites", show=False),
        Binding("escape", "quit", show=False),
        Binding("q", "quit", show=False),
    ]

    AGENTS = (
        ("claude", "Claude Code", "claude"),
        ("codex", "Codex", "codex"),
    )

    def __init__(
        self,
        message: str | None = None,
        store: SiteStore | None = None,
        repository_store: RepositoryStore | None = None,
    ) -> None:
        super().__init__()
        self.message = message
        self.store = store or SiteStore()
        self.repository_store = repository_store or RepositoryStore()
        self.available_agents = {
            key: key in AGENT_COMMANDS and which(command) is not None
            for key, _, command in self.AGENTS
        }
        self.selected_agent = next(
            (key for key, _, _ in self.AGENTS if self.available_agents[key]),
            None,
        )

    def compose(self) -> ComposeResult:
        options = []
        for key, name, _ in self.AGENTS:
            available = self.available_agents[key]
            if available:
                status = "[dim]ready[/dim]"
            elif key not in AGENT_COMMANDS:
                status = "[dim]coming soon[/dim]"
            else:
                status = "[dim]not installed[/dim]"
            options.append(
                Option(
                    f"{name:<18}{status}",
                    id=key,
                    disabled=not available,
                )
            )

        with Center():
            with Vertical(id="page"):
                yield Logo(id="logo")
                yield Static(
                    "any website, within reach of any agent",
                    id="tagline",
                )
                yield Label(self.message or "choose your agent", id="prompt")
                yield OptionList(*options, id="agent-list", compact=True)
                yield Static(
                    "↑↓ select   enter open   s saved → d delete   q exit",
                    id="keys",
                )

    def on_mount(self) -> None:
        option_list = self.query_one("#agent-list", OptionList)
        first_available = next(
            (index for index, (key, _, _) in enumerate(self.AGENTS) if self.available_agents[key]),
            None,
        )
        option_list.highlighted = first_available
        option_list.focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self.selected_agent = event.option.id

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Open the selected agent while keeping AnyBridge visible."""
        self.selected_agent = event.option.id
        if not self.selected_agent:
            return
        try:
            launch_agent(self.selected_agent)
            self.query_one("#prompt", Label).update(
                f"{self.selected_agent} opened in a new terminal"
            )
        except LaunchError as error:
            self.query_one("#prompt", Label).update(str(error))
        self.query_one("#agent-list", OptionList).focus()

    def action_saved_sites(self) -> None:
        self.push_screen(SavedSitesScreen(self.store, self.repository_store))


def main() -> None:
    """Keep the AnyBridge picker open while agents run in other terminals."""
    AnyBridgeTUI().run()


if __name__ == "__main__":
    main()
