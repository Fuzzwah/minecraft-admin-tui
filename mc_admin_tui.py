from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from mc_admin_core import (
    DEFAULT_LOCATIONS,
    DiscoveredServer,
    Location,
    PlayerSnapshot,
    ServerConfig,
    available_runtimes,
    container_status,
    find_matches,
    human_name,
    load_items,
    load_last_server,
    load_locations,
    load_server_configs,
    locations_path_for,
    migrate_legacy_locations,
    parse_players,
    query_player_snapshot,
    rcon,
    restart_container,
    save_locations,
    scan_servers,
    server_config_for,
    tail_logs,
    upsert_server_config,
)


class AddServerScreen(ModalScreen[tuple[str, str] | None]):
    """Manually register a server that the scan cannot see."""

    CSS = """
    AddServerScreen {
        align: center middle;
    }

    #add-server-body {
        width: 60;
        height: auto;
        padding: 1 2;
        background: #161b22;
        border: round #30363d;
    }

    #add-server-body Input {
        margin-bottom: 1;
    }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        runtimes = available_runtimes() or ["podman"]
        with Vertical(id="add-server-body"):
            yield Label("Register a server", classes="section-title")
            yield Select(
                [(runtime, runtime) for runtime in runtimes],
                value=runtimes[0],
                allow_blank=False,
                id="add-runtime",
            )
            yield Input(placeholder="Container name", id="add-container")
            with Horizontal(classes="button-row"):
                yield Button("Add", id="add-confirm", variant="primary")
                yield Button("Cancel", id="add-cancel")

    @on(Button.Pressed, "#add-confirm")
    def confirm(self) -> None:
        runtime = self.query_one("#add-runtime", Select).value
        container = self.query_one("#add-container", Input).value.strip()
        if not isinstance(runtime, str) or not container:
            self.notify("Runtime and container name required", severity="warning")
            return
        self.dismiss((runtime, container))

    @on(Button.Pressed, "#add-cancel")
    def cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class MinecraftAdminApp(App[None]):
    TITLE = "Minecraft Admin"
    SUB_TITLE = "Server cockpit"

    CSS = """
    Screen {
        background: #0d1117;
        color: #e6edf3;
    }

    Header {
        background: #161b22;
    }

    #app-body {
        height: 1fr;
        padding: 0 1;
    }

    #server-bar {
        height: 4;
        padding: 0 1;
        margin-bottom: 1;
        background: #161b22;
        border-bottom: solid #30363d;
        align-vertical: middle;
    }

    #server-label {
        width: 16;
        content-align: left middle;
        color: #7ee787;
        text-style: bold;
    }

    #server-select {
        width: 1fr;
        margin-right: 1;
    }

    #server-scan, #server-add {
        width: 12;
        margin-left: 1;
    }

    #context-bar {
        height: 4;
        padding: 0 1;
        margin-bottom: 1;
        background: #161b22;
        border-bottom: solid #30363d;
        align-vertical: middle;
    }

    #target-label {
        width: 24;
        content-align: left middle;
        color: #79c0ff;
        text-style: bold;
    }

    #player-select {
        width: 1fr;
        margin-right: 1;
    }

    #refresh-players {
        width: 14;
    }

    TabbedContent {
        height: 1fr;
    }

    TabPane {
        padding: 1 2;
    }

    .section-title {
        color: #8b949e;
        text-style: bold;
        margin: 1 0 0 0;
    }

    .help {
        color: #8b949e;
        margin-bottom: 1;
    }

    .button-row {
        height: auto;
        margin: 1 0 0 0;
    }

    .button-row Button {
        margin-right: 1;
        min-width: 16;
    }

    #player-summary, #world-status, #server-status {
        height: auto;
        min-height: 4;
        border: round #30363d;
        padding: 1 2;
        margin-bottom: 1;
    }

    #item-search {
        margin-bottom: 0;
    }

    #matches {
        height: 1fr;
        min-height: 10;
        border: round #30363d;
        margin-top: 1;
    }

    #matches:focus {
        border: round #58a6ff;
    }

    #selected-item {
        height: 2;
        color: #79c0ff;
        padding-top: 1;
    }

    #give-row, #coords-row, #location-row, #raw-row, #restart-row {
        height: auto;
        margin-top: 1;
    }

    #quantity {
        width: 12;
        margin-right: 1;
    }

    #give-item, #tp-coords, #raw-send {
        width: 1fr;
    }

    #tp-x, #tp-y, #tp-z {
        width: 1fr;
        margin-right: 1;
    }

    #tp-dimension {
        width: 28;
    }

    #tp-player-select, #saved-location-select {
        width: 1fr;
        margin-right: 1;
    }

    #bookmark-name {
        width: 1fr;
        margin-right: 1;
    }

    #raw-command {
        width: 1fr;
        margin-right: 1;
    }

    #restart-confirm {
        width: 1fr;
        margin-right: 1;
    }

    #server-log {
        height: 1fr;
        min-height: 10;
        border: round #30363d;
        margin-top: 1;
    }

    #activity-log {
        height: 1fr;
        border: round #30363d;
    }

    #danger-note, #fun-danger-note {
        color: #f2cc60;
        margin-top: 1;
    }

    Button.-primary {
        background: #238636;
    }
    """

    BINDINGS = [
        ("ctrl+r", "refresh_players", "Refresh players"),
        ("ctrl+s", "scan_servers", "Scan servers"),
        ("ctrl+g", "give", "Give"),
        ("ctrl+f", "focus_search", "Search"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: ServerConfig | None) -> None:
        super().__init__()
        self.config = config
        self.discovered: list[DiscoveredServer] = []
        self.preferred = (
            config or load_last_server() or (load_server_configs() or [None])[0]
        )
        self.items, self.catalogue_source = load_items(
            self.preferred.registry if self.preferred else None
        )
        self.matches: list[str] = []
        self.selected_item: str | None = None
        self.selected_player: str | None = None
        self.online_players: list[str] = []
        self.snapshot: PlayerSnapshot | None = None
        self.locations: dict[str, Location] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="app-body"):
            with Horizontal(id="server-bar"):
                yield Label("SERVER: none", id="server-label")
                yield Select(
                    [],
                    prompt="No server selected",
                    allow_blank=True,
                    id="server-select",
                )
                yield Button("Scan", id="server-scan")
                yield Button("Add", id="server-add")
            with Horizontal(id="context-bar"):
                yield Label("TARGET: none", id="target-label")
                yield Select(
                    [],
                    prompt="No active players",
                    allow_blank=True,
                    id="player-select",
                )
                yield Button("Refresh", id="refresh-players")

            with TabbedContent(initial="players-tab"):
                with TabPane("Players", id="players-tab"):
                    yield Static("No active player selected.", id="player-summary")
                    yield Label(
                        "The selected player remains the target as you move between tabs.",
                        classes="help",
                    )
                    with Horizontal(classes="button-row"):
                        yield Button("Refresh status", id="player-refresh")
                        yield Button("Heal", id="player-heal")
                        yield Button("Feed", id="player-feed")
                        yield Button("Clear effects", id="player-clear-effects")
                    with Horizontal(classes="button-row"):
                        yield Button("Survival", id="gm-survival")
                        yield Button("Creative", id="gm-creative")
                        yield Button("Spectator", id="gm-spectator")
                        yield Button("+10 levels", id="xp-10")

                with TabPane("Give", id="give-tab"):
                    yield Label("ITEM SEARCH", classes="section-title")
                    yield Input(
                        placeholder="Try: netherite sword, deep slate, fireworks…",
                        id="item-search",
                    )
                    yield OptionList(id="matches")
                    yield Label("", id="selected-item")
                    yield Label("QUANTITY", classes="section-title")
                    with Horizontal(id="give-row"):
                        yield Input("1", type="integer", id="quantity")
                        yield Button(
                            "Give to selected player",
                            id="give-item",
                            variant="primary",
                        )

                with TabPane("Teleport", id="teleport-tab"):
                    yield Label("PLAYER → PLAYER", classes="section-title")
                    with Horizontal(classes="button-row"):
                        yield Select(
                            [],
                            prompt="Choose destination player",
                            allow_blank=True,
                            id="tp-player-select",
                        )
                        yield Button("Teleport", id="tp-player")

                    yield Label("COORDINATES", classes="section-title")
                    with Horizontal(id="coords-row"):
                        yield Input(placeholder="X", id="tp-x")
                        yield Input(placeholder="Y", id="tp-y")
                        yield Input(placeholder="Z", id="tp-z")
                        yield Select(
                            [
                                ("Current dimension", "current"),
                                ("Overworld", "minecraft:overworld"),
                                ("Nether", "minecraft:the_nether"),
                                ("The End", "minecraft:the_end"),
                            ],
                            value="current",
                            allow_blank=False,
                            id="tp-dimension",
                        )
                    yield Button("Teleport to coordinates", id="tp-coords")

                    yield Label("SAVED LOCATIONS", classes="section-title")
                    with Horizontal(id="location-row"):
                        yield Select(
                            [],
                            prompt="No saved locations",
                            allow_blank=True,
                            id="saved-location-select",
                        )
                        yield Button("Go", id="location-go")
                        yield Button("Delete", id="location-delete")
                    with Horizontal(classes="button-row"):
                        yield Input(
                            placeholder="Name current location…",
                            id="bookmark-name",
                        )
                        yield Button("Save current", id="location-save-current")

                with TabPane("World", id="world-tab"):
                    yield Static(
                        "Use Refresh to read the current keepInventory setting.",
                        id="world-status",
                    )
                    with Horizontal(classes="button-row"):
                        yield Button("Refresh", id="world-refresh")
                        yield Button("keepInventory ON", id="keepinv-on", variant="primary")
                        yield Button("keepInventory OFF", id="keepinv-off")
                    yield Label("TIME", classes="section-title")
                    with Horizontal(classes="button-row"):
                        yield Button("Day", id="time-day")
                        yield Button("Noon", id="time-noon")
                        yield Button("Night", id="time-night")
                        yield Button("Midnight", id="time-midnight")
                    yield Label("WEATHER", classes="section-title")
                    with Horizontal(classes="button-row"):
                        yield Button("Clear", id="weather-clear")
                        yield Button("Rain", id="weather-rain")
                        yield Button("Thunder", id="weather-thunder")
                    yield Label("DIFFICULTY", classes="section-title")
                    with Horizontal(classes="button-row"):
                        yield Button("Peaceful", id="diff-peaceful")
                        yield Button("Easy", id="diff-easy")
                        yield Button("Normal", id="diff-normal")
                        yield Button("Hard", id="diff-hard")

                with TabPane("Server", id="server-tab"):
                    yield Static("Server status not loaded.", id="server-status")
                    with Horizontal(classes="button-row"):
                        yield Button("Refresh status", id="server-refresh")
                        yield Button("Save all", id="server-save")
                        yield Button("Tail logs", id="server-tail")
                    yield Label("RAW RCON", classes="section-title")
                    with Horizontal(id="raw-row"):
                        yield Input(
                            placeholder="e.g. say Dinner in 5 minutes",
                            id="raw-command",
                        )
                        yield Button("Send", id="raw-send")
                    yield RichLog(id="server-log", markup=False, wrap=True)
                    yield Label(
                        "Restart requires typing RESTART. This restarts the "
                        "container on its runtime.",
                        id="danger-note",
                    )
                    with Horizontal(id="restart-row"):
                        yield Input(placeholder="Type RESTART", id="restart-confirm")
                        yield Button("Restart container", id="server-restart")

                with TabPane("Activity", id="activity-tab"):
                    yield RichLog(id="activity-log", markup=True, wrap=True)

                with TabPane("Fun", id="fun-tab"):
                    yield Label(
                        "Actions here target the globally selected player.",
                        classes="help",
                    )
                    with Horizontal(classes="button-row"):
                        yield Button("Totem particles", id="fun-totem")
                        yield Button("Ding!", id="fun-ding")
                        yield Button("Glow 60s", id="fun-glow")
                    with Horizontal(classes="button-row"):
                        yield Button("BONK title", id="fun-bonk")
                        yield Button("Summon chicken", id="fun-chicken")
                        yield Button("⚠ Lightning strike", id="fun-lightning")
                    yield Label(
                        "Lightning can hurt the selected player. The others are mostly harmless.",
                        id="fun-danger-note",
                    )

        yield Footer()

    def on_mount(self) -> None:
        self._log(
            f"[dim]Item catalogue:[/] {len(self.items)} items from {self.catalogue_source}"
        )
        self._update_matches("")
        self._update_location_options()
        if self.config is not None:
            self._activate_server(self.config)
        self.scan_servers()
        self.set_interval(5, self.refresh_players)
        self.set_interval(10, self.refresh_selected_player)

    def _activate_server(self, config: ServerConfig) -> None:
        self.config = config
        if config.locations_path:
            migrate_legacy_locations(config.locations_path)
            self.locations = load_locations(config.locations_path)
        else:
            self.locations = load_locations(DEFAULT_LOCATIONS)
        self.query_one("#server-label", Label).update(f"SERVER: {config.label}")
        self._update_location_options()
        upsert_server_config(config)
        self.refresh_players()
        self.refresh_server_status()
        self.refresh_world_status()

    @work(thread=True, exclusive=True, group="scan")
    def scan_servers(self) -> None:
        try:
            servers = scan_servers()
            self.call_from_thread(self._apply_scan, servers, None)
        except Exception as exc:
            self.call_from_thread(self._apply_scan, [], str(exc))

    def _apply_scan(self, servers: list[DiscoveredServer], error: str | None) -> None:
        self.discovered = servers
        select = self.query_one("#server-select", Select)
        options = [
            (
                f"{server.container}  ({server.runtime}, {server.state or 'unknown'})",
                f"{server.runtime}:{server.container}",
            )
            for server in servers
        ]
        select.set_options(options)
        select.prompt = f"{len(servers)} server(s) found" if servers else "No servers found"

        label = self.query_one("#server-label", Label)
        if error:
            label.update("SERVER: scan failed")
            self._log(f"[red]Server scan failed:[/] {error}")
            return
        self._log(
            f"[dim]Scan:[/] found {len(servers)} Minecraft container(s) "
            f"across {len(available_runtimes())} runtime(s)"
        )
        if servers and self.config is None:
            first = next(
                (
                    s
                    for s in servers
                    if self.preferred
                    and (s.runtime, s.container)
                    == (self.preferred.runtime, self.preferred.container)
                ),
                None,
            )
            if first is not None:
                merged = replace(
                    server_config_for(first),
                    registry=self.preferred.registry if self.preferred else None,
                    locations_path=self.preferred.locations_path
                    or server_config_for(first).locations_path,
                )
                self._activate_server(merged)
                return
            first = next(
                (s for s in servers if s.state == "running" and s.rcon_client),
                servers[0],
            )
            self._activate_server(server_config_for(first))
        elif self.config is not None:
            select.value = f"{self.config.runtime}:{self.config.container}"

    @on(Select.Changed, "#server-select")
    def server_selected(self, event: Select.Changed) -> None:
        if not isinstance(event.value, str):
            return
        runtime, _, container = event.value.partition(":")
        match = next(
            (
                server
                for server in self.discovered
                if server.runtime == runtime and server.container == container
            ),
            None,
        )
        if match is None:
            return
        if self.config and (self.config.runtime, self.config.container) == (
            runtime,
            container,
        ):
            return
        self._log(f"[bold]Switching to[/] {container} ({runtime})")
        self._activate_server(server_config_for(match))

    @on(Button.Pressed, "#server-scan")
    def scan_pressed(self) -> None:
        self.scan_servers()

    def action_scan_servers(self) -> None:
        self.scan_servers()

    @on(Button.Pressed, "#server-add")
    def add_server_pressed(self) -> None:
        self.push_screen(AddServerScreen(), self._add_manual_server)

    def _add_manual_server(self, selection: tuple[str, str] | None) -> None:
        if not selection:
            return
        runtime, container = selection
        container = container.strip()
        if not container:
            self.notify("Container name required", severity="warning")
            return
        server = DiscoveredServer(
            runtime=runtime, container=container, state="unknown", source="manual"
        )
        self.discovered.append(server)
        config = server_config_for(server)
        upsert_server_config(config)
        select = self.query_one("#server-select", Select)
        select.set_options(
            [
                (
                    f"{item.container}  ({item.runtime}, {item.state or 'unknown'})",
                    f"{item.runtime}:{item.container}",
                )
                for item in self.discovered
            ]
        )
        select.value = f"{runtime}:{container}"
        self._activate_server(config)
        self._log(f"[green]✓[/] Added server {container} ({runtime})")

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            self.query_one("#activity-log", RichLog).write(
                f"[dim]{stamp}[/]  {message}"
            )
        except Exception:
            pass

    def _require_player(self) -> str | None:
        if not self.selected_player:
            self.notify("No active player selected", severity="warning")
            return None
        return self.selected_player

    @on(Select.Changed, "#player-select")
    def global_player_changed(self, event: Select.Changed) -> None:
        if not isinstance(event.value, str):
            return
        self.selected_player = event.value
        self.query_one("#target-label", Label).update(f"TARGET: {event.value}")
        self._update_teleport_player_options()
        self.refresh_selected_player()

    @on(Button.Pressed, "#refresh-players")
    def refresh_players_pressed(self) -> None:
        self.refresh_players()

    @work(thread=True, exclusive=True, group="players")
    def refresh_players(self) -> None:
        if self.config is None:
            return
        try:
            players = parse_players(rcon(self.config, "list"))
            self.call_from_thread(self._apply_players, players, None)
        except Exception as exc:
            self.call_from_thread(self._apply_players, [], str(exc))

    def _apply_players(self, players: list[str], error: str | None) -> None:
        self.online_players = players
        select = self.query_one("#player-select", Select)
        previous = self.selected_player
        select.set_options((name, name) for name in players)

        if previous in players:
            select.value = previous
            self.selected_player = previous
        elif players:
            select.value = players[0]
            self.selected_player = players[0]
            self.query_one("#target-label", Label).update(f"TARGET: {players[0]}")
        else:
            self.selected_player = None
            self.snapshot = None
            self.query_one("#target-label", Label).update("TARGET: none")
            self.query_one("#player-summary", Static).update(
                "No active player selected."
            )

        select.prompt = "No active players" if not players else "Select player"
        self._update_teleport_player_options()

        if error:
            self._log(f"[red]RCON player refresh failed:[/] {error}")

    def _update_teleport_player_options(self) -> None:
        destination = self.query_one("#tp-player-select", Select)
        options = [
            (name, name)
            for name in self.online_players
            if name != self.selected_player
        ]
        destination.set_options(options)
        destination.prompt = (
            "Choose destination player" if options else "No other players online"
        )

    def action_refresh_players(self) -> None:
        self.refresh_players()

    def action_focus_search(self) -> None:
        self.query_one("#item-search", Input).focus()

    def action_give(self) -> None:
        self._give_current_item()

    @work(thread=True, exclusive=True, group="snapshot")
    def refresh_selected_player(self) -> None:
        player = self.selected_player
        if not player or self.config is None:
            return
        try:
            snapshot = query_player_snapshot(self.config, player)
            self.call_from_thread(self._apply_snapshot, snapshot, None)
        except Exception as exc:
            self.call_from_thread(self._apply_snapshot, None, str(exc))

    def _apply_snapshot(
        self, snapshot: PlayerSnapshot | None, error: str | None
    ) -> None:
        if error:
            self.query_one("#player-summary", Static).update(
                f"{self.selected_player or 'Player'}\nStatus unavailable: {error}"
            )
            return
        if not snapshot or snapshot.player != self.selected_player:
            return

        self.snapshot = snapshot
        if snapshot.pos:
            x, y, z = snapshot.pos
            pos = f"{x:.1f}, {y:.1f}, {z:.1f}"
        else:
            pos = "unknown"

        health = "?" if snapshot.health is None else f"{snapshot.health:g}"
        food = "?" if snapshot.food is None else str(snapshot.food)
        xp = "?" if snapshot.xp_level is None else str(snapshot.xp_level)
        dim = snapshot.dimension or "unknown"

        self.query_one("#player-summary", Static).update(
            f"[b]{snapshot.player}[/b]\n"
            f"Health {health}   Food {food}   XP {xp}\n"
            f"{dim}   {pos}"
        )

    def _run_player_action(
        self, label: str, command: str, *, refresh: bool = True
    ) -> None:
        player = self._require_player()
        if not player:
            return
        self.run_rcon_action(label, command.format(player=player), refresh)

    @work(thread=True)
    def run_rcon_action(self, label: str, command: str, refresh: bool = False) -> None:
        if self.config is None:
            self.call_from_thread(
                self._rcon_action_finished,
                label,
                command,
                "",
                "No server selected",
                False,
            )
            return
        try:
            output = rcon(self.config, command)
            self.call_from_thread(
                self._rcon_action_finished, label, command, output, None, refresh
            )
        except Exception as exc:
            self.call_from_thread(
                self._rcon_action_finished, label, command, "", str(exc), False
            )

    def _rcon_action_finished(
        self,
        label: str,
        command: str,
        output: str,
        error: str | None,
        refresh: bool,
    ) -> None:
        if error:
            self._log(f"[red]✗ {label}:[/] {error}")
            self.notify(f"{label} failed", severity="error")
            return
        suffix = f" — {output}" if output else ""
        self._log(f"[green]✓[/] {label}[dim]{suffix}[/]")
        self.notify(label)
        if refresh:
            self.refresh_selected_player()

    @on(Button.Pressed, "#player-refresh")
    def player_refresh(self) -> None:
        self.refresh_selected_player()

    @on(Button.Pressed, "#player-heal")
    def player_heal(self) -> None:
        self._run_player_action(
            "Healed player",
            "effect give {player} minecraft:instant_health 1 10 true",
        )

    @on(Button.Pressed, "#player-feed")
    def player_feed(self) -> None:
        self._run_player_action(
            "Fed player",
            "effect give {player} minecraft:saturation 1 20 true",
        )

    @on(Button.Pressed, "#player-clear-effects")
    def player_clear_effects(self) -> None:
        self._run_player_action("Cleared effects", "effect clear {player}")

    @on(Button.Pressed, "#gm-survival")
    def gm_survival(self) -> None:
        self._run_player_action("Set Survival", "gamemode survival {player}")

    @on(Button.Pressed, "#gm-creative")
    def gm_creative(self) -> None:
        self._run_player_action("Set Creative", "gamemode creative {player}")

    @on(Button.Pressed, "#gm-spectator")
    def gm_spectator(self) -> None:
        self._run_player_action("Set Spectator", "gamemode spectator {player}")

    @on(Button.Pressed, "#xp-10")
    def xp_10(self) -> None:
        self._run_player_action(
            "Added 10 levels", "experience add {player} 10 levels"
        )

    def _update_matches(self, query: str) -> None:
        self.matches = find_matches(query, self.items)
        options = [
            Option(
                Text.assemble(
                    (human_name(item_id), "bold"),
                    ("   ", ""),
                    (item_id, "dim"),
                ),
                id=item_id,
            )
            for item_id in self.matches
        ]
        option_list = self.query_one("#matches", OptionList)
        option_list.set_options(options)
        if options:
            option_list.highlighted = 0

    @on(Input.Changed, "#item-search")
    def item_search_changed(self, event: Input.Changed) -> None:
        self.selected_item = None
        self.query_one("#selected-item", Label).update("")
        self._update_matches(event.value)

    @on(Input.Submitted, "#item-search")
    def item_search_submitted(self, event: Input.Submitted) -> None:
        if self.matches:
            self._select_item(self.matches[0])
            self.query_one("#quantity", Input).focus()

    @on(OptionList.OptionSelected, "#matches")
    def match_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self._select_item(str(event.option_id))
            self.query_one("#quantity", Input).focus()

    def _select_item(self, item_id: str) -> None:
        self.selected_item = item_id
        self.query_one("#selected-item", Label).update(f"Selected: {item_id}")

    @on(Button.Pressed, "#give-item")
    def give_pressed(self) -> None:
        self._give_current_item()

    def _give_current_item(self) -> None:
        player = self._require_player()
        if not player:
            return

        item_id = self.selected_item
        if not item_id:
            typed = self.query_one("#item-search", Input).value.strip()
            if typed in self.items:
                item_id = typed
        if not item_id:
            self.notify("Select an item from the matches first", severity="warning")
            return

        try:
            qty = int(self.query_one("#quantity", Input).value.strip())
        except ValueError:
            self.notify("Quantity must be a number", severity="error")
            return

        if not 1 <= qty <= 6400:
            self.notify("Quantity must be between 1 and 6400", severity="error")
            return

        self.run_rcon_action(
            f"Gave {qty} × {human_name(item_id)} to {player}",
            f"give {player} {item_id} {qty}",
            False,
        )

    @on(Button.Pressed, "#tp-player")
    def teleport_to_player(self) -> None:
        player = self._require_player()
        if not player:
            return
        value = self.query_one("#tp-player-select", Select).value
        if not isinstance(value, str):
            self.notify("Choose a destination player", severity="warning")
            return
        self.run_rcon_action(
            f"Teleported {player} → {value}",
            f"tp {player} {value}",
            True,
        )

    @on(Button.Pressed, "#tp-coords")
    def teleport_to_coords(self) -> None:
        player = self._require_player()
        if not player:
            return
        x = self.query_one("#tp-x", Input).value.strip()
        y = self.query_one("#tp-y", Input).value.strip()
        z = self.query_one("#tp-z", Input).value.strip()
        if not all((x, y, z)):
            self.notify("Enter X, Y and Z", severity="warning")
            return

        try:
            float(x)
            float(y)
            float(z)
        except ValueError:
            self.notify("Coordinates must be numeric", severity="error")
            return

        dimension = self.query_one("#tp-dimension", Select).value
        if dimension == "current":
            command = f"tp {player} {x} {y} {z}"
        else:
            command = f"execute in {dimension} run tp {player} {x} {y} {z}"
        self.run_rcon_action(
            f"Teleported {player} to {x}, {y}, {z}",
            command,
            True,
        )

    def _update_location_options(self) -> None:
        select = self.query_one("#saved-location-select", Select)
        select.set_options((name, name) for name in sorted(self.locations))
        select.prompt = (
            "Choose saved location" if self.locations else "No saved locations"
        )

    @on(Button.Pressed, "#location-save-current")
    def save_current_location(self) -> None:
        player = self._require_player()
        if not player:
            return
        name = self.query_one("#bookmark-name", Input).value.strip()
        if not name:
            self.notify("Give the location a name", severity="warning")
            return
        if not self.snapshot or self.snapshot.player != player or not self.snapshot.pos:
            self.notify("Refresh player status first", severity="warning")
            return

        x, y, z = self.snapshot.pos
        location = Location(
            name=name,
            x=x,
            y=y,
            z=z,
            dimension=self.snapshot.dimension or "minecraft:overworld",
        )
        self.locations[name] = location
        save_locations(self.locations)
        self._update_location_options()
        self.query_one("#saved-location-select", Select).value = name
        self.query_one("#bookmark-name", Input).value = ""
        self._log(
            f"[green]✓[/] Saved location [b]{name}[/b] from {player}"
        )
        self.notify(f"Saved {name}")

    @on(Button.Pressed, "#location-go")
    def go_saved_location(self) -> None:
        player = self._require_player()
        if not player:
            return
        name = self.query_one("#saved-location-select", Select).value
        if not isinstance(name, str) or name not in self.locations:
            self.notify("Choose a saved location", severity="warning")
            return
        loc = self.locations[name]
        command = (
            f"execute in {loc.dimension} run tp {player} "
            f"{loc.x:g} {loc.y:g} {loc.z:g}"
        )
        self.run_rcon_action(
            f"Teleported {player} → {name}",
            command,
            True,
        )

    @on(Button.Pressed, "#location-delete")
    def delete_saved_location(self) -> None:
        name = self.query_one("#saved-location-select", Select).value
        if not isinstance(name, str) or name not in self.locations:
            self.notify("Choose a saved location", severity="warning")
            return
        del self.locations[name]
        save_locations(self.locations)
        self._update_location_options()
        self._log(f"[yellow]Deleted saved location[/] {name}")
        self.notify(f"Deleted {name}")

    @work(thread=True, exclusive=True, group="world")
    def refresh_world_status(self) -> None:
        if self.config is None:
            return
        try:
            value = rcon(self.config, "gamerule keepInventory")
            self.call_from_thread(self._apply_world_status, value, None)
        except Exception as exc:
            self.call_from_thread(self._apply_world_status, "", str(exc))

    def _apply_world_status(self, value: str, error: str | None) -> None:
        if error:
            self.query_one("#world-status", Static).update(
                f"World status unavailable: {error}"
            )
        else:
            self.query_one("#world-status", Static).update(
                f"[b]keepInventory[/b]\n{value}"
            )

    @on(Button.Pressed, "#world-refresh")
    def world_refresh(self) -> None:
        self.refresh_world_status()

    def _world_action(self, label: str, command: str) -> None:
        self.run_rcon_action(label, command, False)
        if command.startswith("gamerule keepInventory"):
            self.set_timer(0.5, self.refresh_world_status)

    @on(Button.Pressed, "#keepinv-on")
    def keepinv_on(self) -> None:
        self._world_action("keepInventory enabled", "gamerule keepInventory true")

    @on(Button.Pressed, "#keepinv-off")
    def keepinv_off(self) -> None:
        self._world_action("keepInventory disabled", "gamerule keepInventory false")

    @on(Button.Pressed, "#time-day")
    def time_day(self) -> None:
        self._world_action("Set time to day", "time set day")

    @on(Button.Pressed, "#time-noon")
    def time_noon(self) -> None:
        self._world_action("Set time to noon", "time set noon")

    @on(Button.Pressed, "#time-night")
    def time_night(self) -> None:
        self._world_action("Set time to night", "time set night")

    @on(Button.Pressed, "#time-midnight")
    def time_midnight(self) -> None:
        self._world_action("Set time to midnight", "time set midnight")

    @on(Button.Pressed, "#weather-clear")
    def weather_clear(self) -> None:
        self._world_action("Weather: clear", "weather clear")

    @on(Button.Pressed, "#weather-rain")
    def weather_rain(self) -> None:
        self._world_action("Weather: rain", "weather rain")

    @on(Button.Pressed, "#weather-thunder")
    def weather_thunder(self) -> None:
        self._world_action("Weather: thunder", "weather thunder")

    @on(Button.Pressed, "#diff-peaceful")
    def diff_peaceful(self) -> None:
        self._world_action("Difficulty: peaceful", "difficulty peaceful")

    @on(Button.Pressed, "#diff-easy")
    def diff_easy(self) -> None:
        self._world_action("Difficulty: easy", "difficulty easy")

    @on(Button.Pressed, "#diff-normal")
    def diff_normal(self) -> None:
        self._world_action("Difficulty: normal", "difficulty normal")

    @on(Button.Pressed, "#diff-hard")
    def diff_hard(self) -> None:
        self._world_action("Difficulty: hard", "difficulty hard")

    @work(thread=True, exclusive=True, group="server-status")
    def refresh_server_status(self) -> None:
        if self.config is None:
            return
        try:
            value = container_status(self.config)
            self.call_from_thread(self._apply_server_status, value, None)
        except Exception as exc:
            self.call_from_thread(self._apply_server_status, "", str(exc))

    def _apply_server_status(self, value: str, error: str | None) -> None:
        if self.config is None:
            self.query_one("#server-status", Static).update("No server selected.")
            return
        header = f"{self.config.label} ({self.config.runtime})"
        if error:
            self.query_one("#server-status", Static).update(
                f"Container: {self.config.container}\nStatus unavailable: {error}"
            )
        else:
            self.query_one("#server-status", Static).update(
                f"[b]{header}[/b]\n{value}"
            )

    @on(Button.Pressed, "#server-refresh")
    def server_refresh(self) -> None:
        self.refresh_server_status()

    @on(Button.Pressed, "#server-save")
    def server_save(self) -> None:
        self.run_rcon_action("Saved world", "save-all", False)

    @on(Button.Pressed, "#server-tail")
    def server_tail(self) -> None:
        self.refresh_server_logs()

    @work(thread=True, exclusive=True, group="server-logs")
    def refresh_server_logs(self) -> None:
        if self.config is None:
            return
        try:
            output = tail_logs(self.config, 100)
            self.call_from_thread(self._apply_server_logs, output, None)
        except Exception as exc:
            self.call_from_thread(self._apply_server_logs, "", str(exc))

    def _apply_server_logs(self, output: str, error: str | None) -> None:
        log = self.query_one("#server-log", RichLog)
        log.clear()
        if error:
            log.write(f"Failed to read logs: {error}")
            return
        for line in output.splitlines():
            log.write(line)

    @on(Input.Submitted, "#raw-command")
    def raw_submit(self, event: Input.Submitted) -> None:
        self._send_raw_command()

    @on(Button.Pressed, "#raw-send")
    def raw_send(self) -> None:
        self._send_raw_command()

    def _send_raw_command(self) -> None:
        field = self.query_one("#raw-command", Input)
        command = field.value.strip()
        if not command:
            return
        field.value = ""
        self.run_rcon_action(f"RCON: {command}", command, False)

    @on(Button.Pressed, "#server-restart")
    def server_restart(self) -> None:
        confirm = self.query_one("#restart-confirm", Input)
        if confirm.value.strip() != "RESTART":
            self.notify("Type RESTART first", severity="warning")
            return
        confirm.value = ""
        self.restart_server_container()

    @work(thread=True, exclusive=True, group="restart")
    def restart_server_container(self) -> None:
        if self.config is None:
            return
        try:
            output = restart_container(self.config)
            self.call_from_thread(self._restart_finished, output, None)
        except Exception as exc:
            self.call_from_thread(self._restart_finished, "", str(exc))

    def _restart_finished(self, output: str, error: str | None) -> None:
        if error:
            self._log(f"[red]Container restart failed:[/] {error}")
            self.notify("Restart failed", severity="error")
            return
        self._log(f"[yellow]Container restarted[/] {output}")
        self.notify("Minecraft container restarted")
        self.set_timer(2, self.refresh_server_status)
        self.set_timer(4, self.refresh_players)

    @on(Button.Pressed, "#fun-totem")
    def fun_totem(self) -> None:
        self._run_player_action(
            "Totem particle burst",
            "execute at {player} run particle minecraft:totem_of_undying ~ ~1 ~ "
            "0.5 1 0.5 0.2 80 force {player}",
            refresh=False,
        )

    @on(Button.Pressed, "#fun-ding")
    def fun_ding(self) -> None:
        self._run_player_action(
            "Ding!",
            "execute at {player} run playsound minecraft:block.note_block.pling "
            "master {player} ~ ~ ~ 1 2",
            refresh=False,
        )

    @on(Button.Pressed, "#fun-glow")
    def fun_glow(self) -> None:
        self._run_player_action(
            "Glow for 60 seconds",
            "effect give {player} minecraft:glowing 60 0 true",
            refresh=True,
        )

    @on(Button.Pressed, "#fun-bonk")
    def fun_bonk(self) -> None:
        self._run_player_action(
            "BONK title",
            'title {player} title {"text":"BONK!","color":"gold","bold":true}',
            refresh=False,
        )

    @on(Button.Pressed, "#fun-chicken")
    def fun_chicken(self) -> None:
        self._run_player_action(
            "Summoned chicken",
            "execute at {player} run summon minecraft:chicken ~ ~1 ~",
            refresh=False,
        )

    @on(Button.Pressed, "#fun-lightning")
    def fun_lightning(self) -> None:
        self._run_player_action(
            "Lightning strike",
            "execute at {player} run summon minecraft:lightning_bolt ~ ~ ~",
            refresh=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Textual Minecraft server admin TUI (podman/docker)"
    )
    parser.add_argument(
        "--runtime",
        choices=["podman", "docker"],
        help="Container runtime for --container (default: auto-detect).",
    )
    parser.add_argument(
        "--container",
        help="Container name; skips the startup scan when combined with --runtime."
    )
    parser.add_argument(
        "--rcon-client",
        default="rcon-cli",
        help="rcon client binary inside the container (default: rcon-cli).",
    )
    parser.add_argument(
        "--password",
        help="RCON password (otherwise read from the container config/env).",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        help="Minecraft generated reports/registries.json; imported and cached.",
    )
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> ServerConfig | None:
    if not args.container:
        return None

    runtime = args.runtime
    if runtime is None:
        available = available_runtimes()
        runtime = available[0] if available else "podman"
        if args.container:
            for candidate in available:
                if any(
                    server.container == args.container
                    for server in scan_servers([candidate])
                ):
                    runtime = candidate
                    break

    container = args.container or ""
    if args.container and not any(
        server.container == args.container for server in scan_servers([runtime])
    ):
        return ServerConfig(
            runtime=runtime,
            container=container,
            display_name=container,
            rcon_client=args.rcon_client,
            rcon_password=args.password,
            locations_path=locations_path_for(container),
            registry=args.registry,
        )

    server = next(
        (
            server
            for server in scan_servers([runtime])
            if server.container == args.container
        ),
        None,
    )
    config = server_config_for(
        server
        or DiscoveredServer(runtime=runtime, container=container),
        registry=args.registry,
    )
    if args.password:
        config = replace(config, rcon_password=args.password)
    if args.rcon_client and args.rcon_client != "rcon-cli":
        config = replace(config, rcon_client=args.rcon_client)
    return config


def main() -> None:
    MinecraftAdminApp(config_from_args(parse_args())).run()


if __name__ == "__main__":
    main()
