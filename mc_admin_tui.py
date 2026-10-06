from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from rich.markup import escape
from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
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
    TextArea,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option
from textual.widgets._tabbed_content import ContentTabs

from mc_admin_core import (
    DEFAULT_LOCATIONS,
    SETTINGS,
    DiscoveredServer,
    Kit,
    Location,
    PlayerSnapshot,
    ServerConfig,
    ServerSettings,
    all_kits,
    available_runtimes,
    backup_world,
    create_unmined_map,
    serve_unmined_map,
    container_level_name,
    container_status,
    default_backup_dir,
    discover_worlds,
    find_matches,
    human_name,
    human_size,
    list_backups,
    load_custom_kits,
    load_items,
    load_last_server,
    load_locations,
    load_server_configs,
    load_server_settings,
    locations_path_for,
    migrate_legacy_locations,
    parse_kit_items,
    parse_players,
    prune_backups,
    query_player_inventory,
    query_player_snapshot,
    rcon,
    restart_container,
    restore_procedure,
    start_container,
    stop_container,
    rule_name,
    save_custom_kits,
    save_locations,
    save_server_settings,
    scan_servers,
    server_config_for,
    server_stats,
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


class BackupScreen(ModalScreen[None]):
    """World backup manager, opened as a modal from the server info bar."""

    CSS = """
    BackupScreen {
        align: center middle;
    }
    """

    BINDINGS = [("escape", "close", "Close")]

    def __init__(
        self, server: ServerConfig, unmined_client_jar: Path | None = None
    ) -> None:
        super().__init__()
        self.server = server
        self.unmined_client_jar = unmined_client_jar
        self.worlds: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="backup-body"):
            yield Label("BACKUPS", classes="section-title")
            yield Static("Scanning…", id="backup-status")
            yield Label(
                "Each world (with all its dimensions) is streamed out of the "
                "container into ~/minecraft-backups/<server>/ and gzipped. Works "
                "while the server runs; RCON saves first for a clean copy.",
                classes="help",
            )
            with Horizontal(id="backup-row"):
                yield Select(
                    [], prompt="No worlds found", allow_blank=True, id="backup-world"
                )
                yield Input(placeholder="label (optional)", id="backup-label")
                yield Select(
                    [("Keep 5", "5"), ("Keep 10", "10"), ("Keep all", "0")],
                    value="5",
                    allow_blank=False,
                    id="backup-keep",
                )
                yield Button("Back up world", id="backup-run", variant="primary")
            with Horizontal(id="backup-map-row"):
                yield Button("Create uNmINeD map", id="backup-map", variant="primary")
                yield Static(
                    "LAN URL appears after map creation",
                    id="backup-map-url",
                    classes="help",
                )
            with Horizontal(id="backup-list-row"):
                yield Select(
                    [], prompt="No backups yet", allow_blank=True, id="backup-existing"
                )
                yield Button("Prune now", id="backup-prune")
            yield RichLog(id="backup-log", markup=True, wrap=True)
            with Horizontal(classes="button-row"):
                yield Button("Restore how-to", id="backup-restore")
                yield Button("Close", id="backup-close")

    def on_mount(self) -> None:
        self._refresh_backup_list()
        self.refresh_worlds()

    def _refresh_backup_list(self) -> None:
        backups = list_backups(self.server.container)
        select = self.query_one("#backup-existing", Select)
        select.set_options(
            [
                (f"{path.name}  ({path.stat().st_size / 1e6:.1f} MB)", path.name)
                for path in backups
            ]
        )
        select.prompt = f"{len(backups)} backup(s)" if backups else "No backups yet"

        directory = default_backup_dir(self.server.container)
        status = self.query_one("#backup-status", Static)
        if not backups:
            status.update(f"No backups yet.\nDestination: {directory}")
            return
        newest = backups[0]
        total = sum(path.stat().st_size for path in backups) / 1e6
        stamp = datetime.fromtimestamp(newest.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        status.update(
            f"[b]{len(backups)} backup(s)[/b]  ({total:.0f} MB total)\n"
            f"Newest: {newest.name}  ({stamp})\n"
            f"Destination: {directory}"
        )

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.query_one("#backup-log", RichLog).write(f"[dim]{stamp}[/]  {message}")

    @work(thread=True, exclusive=True, group="worlds")
    def refresh_worlds(self) -> None:
        try:
            worlds = discover_worlds(self.server)
            self.app.call_from_thread(self._apply_worlds, worlds, None)
        except Exception as exc:
            self.app.call_from_thread(self._apply_worlds, [], str(exc))

    def _apply_worlds(self, worlds: list[str], error: str | None) -> None:
        select = self.query_one("#backup-world", Select)
        if error:
            select.prompt = "World scan failed"
            self._log(f"[red]World scan failed:[/] {error}")
            return
        self.worlds = worlds
        select.set_options((name, name) for name in worlds)
        select.prompt = "Choose a world" if worlds else "No worlds found"
        if worlds:
            select.value = worlds[0]
        level = container_level_name(self.server)
        self._log(
            f"Detected level-name [b]{level}[/b]; "
            f"{len(worlds)} world folder(s) in the container."
        )

    @on(Button.Pressed, "#backup-run")
    def run_backup(self) -> None:
        world = self.query_one("#backup-world", Select).value
        if not isinstance(world, str) or not world:
            self.notify("Choose a world to back up", severity="warning")
            return
        label = self.query_one("#backup-label", Input).value.strip()
        keep = int(str(self.query_one("#backup-keep", Select).value))
        self.backup_worker(world, label, keep)

    @on(Button.Pressed, "#backup-map")
    def run_map(self) -> None:
        world = self.query_one("#backup-world", Select).value
        if not isinstance(world, str) or not world:
            self.notify("Choose a world to map", severity="warning")
            return
        self.map_worker(world)

    @work(thread=True, exclusive=True, group="map")
    def map_worker(self, world: str) -> None:
        self.app.call_from_thread(self._log, f"[b]uNmINeD map started[/b] ({world})")
        try:
            rcon(self.server, "save-all")
        except Exception as exc:
            self.app.call_from_thread(self._log, f"[yellow]save-all skipped:[/] {exc}")
        try:
            index = create_unmined_map(
                self.server,
                level_name=world,
                java_client_jar=self.unmined_client_jar,
                on_progress=lambda message: self.app.call_from_thread(self._log, message),
            )
        except Exception as exc:
            self.app.call_from_thread(self._map_finished, None, None, str(exc))
            return
        try:
            url = serve_unmined_map(index)
        except Exception as exc:
            self.app.call_from_thread(self._map_finished, index, None, str(exc))
        else:
            self.app.call_from_thread(self._map_finished, index, url, None)

    def _map_finished(
        self, index: Path | None, url: str | None, error: str | None
    ) -> None:
        if index is None:
            self._log(f"[red]Map creation failed:[/] {error}")
            self.notify("Map creation failed", severity="error")
            return
        if error:
            self._log(
                f"[yellow]Map saved locally at [b]{index}[/b], but LAN hosting failed:[/] {error}"
            )
            self.query_one("#backup-map-url", Static).update(
                "LAN hosting failed; see log"
            )
            self.notify("Map created locally; LAN hosting failed", severity="warning")
            return
        assert url is not None
        self.query_one("#backup-map-url", Static).update(url)
        self._log(f"[green]✓[/] LAN map: [b]{url}[/b]")
        self.notify(f"Map available at {url}")


    @work(thread=True, exclusive=True, group="backup")
    def backup_worker(self, world: str, label: str, keep: int) -> None:
        self.app.call_from_thread(self._log, f"[b]Backup started[/b] ({world})")
        try:
            rcon(self.server, "save-all")
        except Exception as exc:
            self.app.call_from_thread(self._log, f"[yellow]save-all skipped:[/] {exc}")
        try:
            path = backup_world(
                self.server,
                level_name=world,
                label=label,
                on_progress=lambda message: self.app.call_from_thread(self._log, message),
            )
            removed = prune_backups(path.parent, keep) if keep else []
            self.app.call_from_thread(self._backup_finished, path, removed, None)
        except Exception as exc:
            self.app.call_from_thread(self._backup_finished, None, [], str(exc))

    def _backup_finished(
        self, path: Path | None, removed: list[Path], error: str | None
    ) -> None:
        if error:
            self._log(f"[red]Backup failed:[/] {error}")
            self.notify("Backup failed", severity="error")
            return
        assert path is not None
        self._log(f"[green]✓[/] Saved [b]{path}[/b]")
        for old in removed:
            self._log(f"[dim]Pruned {old.name}[/]")
        self.notify(f"Backed up {path.name}")
        self._refresh_backup_list()

    @on(Button.Pressed, "#backup-prune")
    def prune_now(self) -> None:
        keep = int(str(self.query_one("#backup-keep", Select).value))
        if not keep:
            self.notify("Set a retention above 'Keep all' to prune", severity="warning")
            return
        removed = prune_backups(default_backup_dir(self.server.container), keep)
        for old in removed:
            self._log(f"[dim]Pruned {old.name}[/]")
        self._refresh_backup_list()
        self.notify(f"Pruned {len(removed)} backup(s)")

    @on(Button.Pressed, "#backup-restore")
    def show_restore(self) -> None:
        log = self.query_one("#backup-log", RichLog)
        log.clear()
        for line in restore_procedure(self.server.container).splitlines():
            log.write(line)

    @on(Button.Pressed, "#backup-close")
    def close(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class SettingsScreen(ModalScreen[tuple[str, ...]]):
    """Edit the properties of the server captured when this modal opens."""

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #settings-body {
        width: 94%;
        max-width: 100;
        height: 92%;
        padding: 0 1;
        background: #161b22;
        border: round #30363d;
    }
    #settings-title, #settings-target, #settings-status {
        height: auto;
    }
    #settings-title {
        color: #79c0ff;
        text-style: bold;
    }
    #settings-status {
        max-height: 3;
        color: #f2cc60;
    }
    #settings-form {
        height: 1fr;
        margin-top: 1;
    }
    #settings-notice, #settings-missing, #settings-block {
        height: auto;
        margin-bottom: 1;
    }
    #settings-block {
        color: #f2cc60;
    }
    .settings-field {
        height: auto;
        margin-bottom: 1;
    }
    .settings-field Label {
        height: auto;
        width: 1fr;
    }
    .settings-field .settings-help {
        color: #8b949e;
    }
    #settings-motd {
        height: 5;
        min-height: 3;
    }
    #settings-buttons, #settings-confirmation {
        height: auto;
    }
    #settings-buttons Button, #settings-confirmation Button {
        min-width: 10;
        width: 1fr;
    }
    #settings-confirmation {
        display: none;
    }
    #settings-confirmation-label {
        width: 2fr;
        height: auto;
        content-align: left middle;
        color: #f2cc60;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close", priority=True)]

    HELP = {
        "motd": "Server-list message. Literal text; Unicode and line breaks are preserved.",
        "max-players": "Maximum number of simultaneous players.",
        "difficulty": "Controls hostile mob strength and survival difficulty.",
        "gamemode": "Default game mode for players joining the world.",
        "force-gamemode": "Apply the default game mode when players join.",
        "pvp": "Allow players to damage other players.",
        "white-list": "Only allow players on the whitelist to join.",
        "enforce-whitelist": "Remove unlisted players when the whitelist is reloaded.",
        "allow-flight": "Allow flight without kicking players for flying.",
        "spawn-monsters": "Allow hostile mob spawning.",
        "spawn-animals": "Allow animal spawning.",
        "spawn-npcs": "Allow NPC spawning.",
        "allow-nether": "Allow access to the Nether.",
        "view-distance": "Server-side view distance, in chunks.",
        "simulation-distance": "Distance for ticking entities, in chunks.",
        "spawn-protection": "Spawn protection radius, in blocks; 0 disables it.",
        "player-idle-timeout": "Idle kick timeout, in minutes; 0 disables it.",
    }

    def __init__(
        self,
        server: ServerConfig,
        on_saved: Callable[[ServerConfig, tuple[str, ...]], None] | None = None,
    ) -> None:
        super().__init__()
        self.server = server
        self._on_saved = on_saved
        self.snapshot: ServerSettings | None = None
        self._loading = False
        self._saving = False
        self._settings_closed = False
        self._updating = False
        self._confirmation: str | None = None
        self._saved_keys: set[str] = set()

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-body"):
            yield Label("SERVER SETTINGS", id="settings-title")
            yield Static(
                f"{self.server.label} · {self.server.runtime}:{self.server.container}",
                markup=False,
                id="settings-target",
            )
            yield Static("Loading server.properties…", markup=False, id="settings-status")
            with VerticalScroll(id="settings-form"):
                yield Static(
                    "Save does not restart the server. Apply changes from Server "
                    "by typing RESTART, then choosing Restart container. Startup "
                    "scripts must preserve server.properties edits.",
                    markup=False,
                    id="settings-notice",
                )
                yield Static("", markup=False, id="settings-block")
                yield Static("", markup=False, id="settings-missing")
                for setting in SETTINGS:
                    with Vertical(id=f"settings-row-{setting.key}", classes="settings-field"):
                        yield Label(f"{setting.label} ({setting.key})")
                        help_text = self.HELP.get(setting.key, "")
                        if setting.minimum is not None and setting.maximum is not None:
                            help_text += f" Range: {setting.minimum}–{setting.maximum}."
                        elif setting.minimum is not None:
                            help_text += f" Minimum: {setting.minimum}."
                        yield Label(help_text, classes="settings-help", markup=False)
                        widget_id = f"settings-{setting.key}"
                        if setting.kind == "text":
                            yield TextArea("", soft_wrap=True, disabled=True, id=widget_id)
                        elif setting.kind in {"bool", "choice"}:
                            choices = ("true", "false") if setting.kind == "bool" else setting.choices
                            yield Select(
                                [(value, value) for value in choices],
                                value=choices[0],
                                allow_blank=False,
                                disabled=True,
                                id=widget_id,
                            )
                        else:
                            yield Input(type="integer", disabled=True, id=widget_id)
            with Horizontal(id="settings-confirmation"):
                yield Static("", markup=False, id="settings-confirmation-label")
                yield Button("Discard", id="settings-discard", variant="warning")
                yield Button("Keep editing", id="settings-keep")
            with Horizontal(id="settings-buttons"):
                yield Button("Save", id="settings-save", variant="primary", disabled=True)
                yield Button("Reload", id="settings-reload")
                yield Button("Close", id="settings-close")

    def on_mount(self) -> None:
        for setting in SETTINGS:
            self.query_one(f"#settings-row-{setting.key}").display = False
        self._begin_load()

    def on_unmount(self) -> None:
        self._settings_closed = True

    def _changes(self) -> dict[str, str]:
        if self.snapshot is None:
            return {}
        changes: dict[str, str] = {}
        for setting in SETTINGS:
            if setting.key not in self.snapshot.values:
                continue
            widget = self.query_one(f"#settings-{setting.key}")
            if isinstance(widget, TextArea):
                value = widget.text
            elif isinstance(widget, Input):
                value = widget.value
            else:
                value = str(widget.value)
            if value != self.snapshot.values[setting.key]:
                changes[setting.key] = value
        return changes

    def _update_controls(self) -> None:
        busy = self._loading or self._saving
        confirming = self._confirmation is not None
        for setting in SETTINGS:
            available = self.snapshot is not None and setting.key in self.snapshot.values
            self.query_one(f"#settings-{setting.key}").disabled = (
                busy or confirming or not available
            )
        self.query_one("#settings-save", Button).disabled = (
            busy or confirming or self.snapshot is None
            or self.snapshot.write_blocked is not None or not self._changes()
        )
        self.query_one("#settings-reload", Button).disabled = busy or confirming
        self.query_one("#settings-close", Button).disabled = self._saving or confirming

    def _set_confirmation(self, operation: str | None) -> None:
        self._confirmation = operation
        self.query_one("#settings-confirmation").display = operation is not None
        if operation is not None:
            label = "Discard edits and close?" if operation == "close" else "Discard edits and reload?"
            self.query_one("#settings-confirmation-label", Static).update(label)
            self.query_one("#settings-keep", Button).focus()
        self._update_controls()

    def _begin_load(self) -> None:
        if self._loading or self._saving:
            return
        self._loading = True
        self.query_one("#settings-status", Static).update("Loading server.properties…")
        self._update_controls()
        self._load_worker(self.app)

    @work(thread=True, exclusive=True, group="settings-load", exit_on_error=False)
    def _load_worker(self, app: App) -> None:
        try:
            snapshot = load_server_settings(self.server)
        except Exception as exc:
            snapshot, error = None, str(exc)
        else:
            error = None
        try:
            app.call_from_thread(self._load_finished, snapshot, error)
        except RuntimeError:
            return  # application stopped while the read was in flight

    def _load_finished(self, snapshot: ServerSettings | None, error: str | None) -> None:
        if self._settings_closed or not self.is_mounted:
            return
        self._loading = False
        if error is not None:
            self.query_one("#settings-status", Static).update(
                f"Read failed: {error}. Use Reload to retry."
            )
            self.query_one("#settings-block", Static).update(f"Read failed: {error}")
            self.query_one("#settings-block").display = True
            self._update_controls()
            return
        assert snapshot is not None
        self._apply_snapshot(snapshot)
        self.query_one("#settings-status", Static).update(
            "Saving blocked by deployment configuration."
            if snapshot.write_blocked else "Ready. Only changed settings will be saved."
        )
        self._update_controls()

    def _apply_snapshot(self, snapshot: ServerSettings) -> None:
        self._updating = True
        self.snapshot = snapshot
        missing: list[str] = []
        for setting in SETTINGS:
            present = setting.key in snapshot.values
            self.query_one(f"#settings-row-{setting.key}").display = present
            if not present:
                missing.append(setting.key)
                continue
            value = snapshot.values[setting.key]
            widget = self.query_one(f"#settings-{setting.key}")
            if isinstance(widget, TextArea):
                widget.load_text(value)
            elif isinstance(widget, Input):
                widget.value = value
            else:
                choices = ("true", "false") if setting.kind == "bool" else setting.choices
                options = [(choice, choice) for choice in choices]
                if value not in choices:
                    options.append((Text(f"Current value: {value}"), value))
                widget.set_options(options)
                widget.value = value
        self.query_one("#settings-target", Static).update(
            f"{self.server.label} · {self.server.runtime}:{self.server.container}\n{snapshot.path}"
        )
        self.query_one("#settings-missing", Static).update(
            "Not present in this server version/configuration (not editable): "
            + ", ".join(missing) if missing else ""
        )
        self.query_one("#settings-missing").display = bool(missing)
        self.query_one("#settings-block", Static).update(snapshot.write_blocked or "")
        self.query_one("#settings-block").display = bool(snapshot.write_blocked)
        self._updating = False

    @on(Input.Changed)
    @on(Select.Changed)
    @on(TextArea.Changed)
    def field_changed(self, event: Input.Changed | Select.Changed | TextArea.Changed) -> None:
        event.stop()
        if self._updating or self._settings_closed or self._loading or self._saving:
            return
        self._update_controls()

    @on(Button.Pressed, "#settings-reload")
    def reload_pressed(self) -> None:
        if self._saving or self._loading or self._confirmation is not None:
            return
        if self._changes():
            self._set_confirmation("reload")
        else:
            self._begin_load()

    @on(Button.Pressed, "#settings-save")
    def save_pressed(self) -> None:
        if self._saving or self._loading or self._confirmation is not None:
            return
        if self.snapshot is None or self.snapshot.write_blocked:
            return
        changes = self._changes()
        if not changes:
            return
        self._saving = True
        self.query_one("#settings-status", Static).update("Saving… please wait; do not close.")
        self._update_controls()
        self._save_worker(self.app, self.snapshot, changes)

    @work(thread=True, exclusive=True, group="settings-save", exit_on_error=False)
    def _save_worker(
        self, app: App, snapshot: ServerSettings, changes: dict[str, str]
    ) -> None:
        try:
            updated = save_server_settings(self.server, snapshot, changes)
        except Exception as exc:
            updated, error = None, str(exc)
        else:
            error = None
        try:
            app.call_from_thread(self._save_finished, updated, tuple(changes), error)
        except RuntimeError:
            return  # application stopped while the write was in flight

    def _save_finished(
        self, snapshot: ServerSettings | None, keys: tuple[str, ...], error: str | None
    ) -> None:
        if self._settings_closed or not self.is_mounted:
            return
        self._saving = False
        if error is not None:
            self.query_one("#settings-status", Static).update(
                f"Save failed: {error}. Edits retained; Reload discards them."
            )
            # Deployment configuration may have changed after the original read.
            self.query_one("#settings-block", Static).update(error)
            self.query_one("#settings-block").display = True
            if self.snapshot is not None and "OVERRIDE_SERVER_PROPERTIES=false" in error:
                self.snapshot = replace(self.snapshot, write_blocked=error)
            self._update_controls()
            return
        assert snapshot is not None
        self._apply_snapshot(snapshot)
        self._saved_keys.update(keys)
        self.query_one("#settings-status", Static).update(
            "Saved. Restart required: close → Server → type RESTART → Restart container."
        )
        self.notify("Settings saved. Restart required; no restart performed.")
        self._update_controls()
        if self._on_saved is not None:
            self._on_saved(self.server, keys)

    @on(Button.Pressed, "#settings-close")
    def close_pressed(self) -> None:
        self.action_close()

    def action_close(self) -> None:
        if self._saving:
            return
        if self._confirmation is not None:
            self._set_confirmation(None)
        elif self._changes():
            self._set_confirmation("close")
        else:
            self._close()

    def _close(self) -> None:
        self._settings_closed = True
        self.dismiss(tuple(setting.key for setting in SETTINGS if setting.key in self._saved_keys))

    @on(Button.Pressed, "#settings-keep")
    def keep_pressed(self) -> None:
        self._set_confirmation(None)

    @on(Button.Pressed, "#settings-discard")
    def discard_pressed(self) -> None:
        if self._saving:
            return
        operation = self._confirmation
        self._set_confirmation(None)
        if operation == "close":
            self._close()
        elif operation == "reload":
            if self.snapshot is not None:
                self._apply_snapshot(self.snapshot)
            self._begin_load()


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

    #server-select {
        width: 1fr;
    }

    #server-scan {
        width: 12;
        margin-right: 1;
    }

    #server-add {
        width: 12;
        margin-left: 1;
    }

    #server-info-bar {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        background: #161b22;
        border-bottom: solid #30363d;
        align-vertical: middle;
    }

    #server-info {
        width: 1fr;
        height: auto;
        min-height: 5;
        padding: 1 2;
        color: #e6edf3;
    }

    #open-backups, #open-settings {
        width: 14;
        min-height: 3;
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

    #player-summary, #world-status, #server-status, #backup-status {
        height: auto;
        min-height: 4;
        border: round #30363d;
        padding: 1 2;
        margin-bottom: 1;
    }

    #item-search {
        margin-bottom: 0;
    }

    #kit-select {
        width: 1fr;
        margin-right: 1;
    }

    #kit-name {
        width: 32;
        margin-top: 1;
    }

    #kit-items {
        margin-top: 1;
    }

    #kit-log {
        height: 1fr;
        min-height: 6;
        border: round #30363d;
        margin-top: 1;
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

    #give-row, #coords-row, #location-row, #raw-row, #restart-row, #stop-row,
    #backup-row, #backup-map-row, #backup-list-row, #kit-row, #kit-save-row {
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

    #restart-confirm, #stop-confirm {
        width: 1fr;
        margin-right: 1;
    }

    #backup-body {
        width: 90%;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        background: #161b22;
        border: round #30363d;
    }

    #backup-body .section-title {
        margin-top: 0;
    }

    #backup-world, #backup-existing {
        width: 1fr;
        margin-right: 1;
    }

    #backup-label {
        width: 24;
        margin-right: 1;
    }

    #backup-keep {
        width: 16;
        margin-right: 1;
    }

    #backup-log {
        height: 12;
        border: round #30363d;
        margin-top: 1;
    }

    #inventory-log {
        height: 8;
        border: round #30363d;
        margin-bottom: 1;
    }

    #server-log {
        height: 8;
        min-height: 3;
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

    Screen.compact #server-bar, Screen.compact #context-bar {
        height: 3;
        margin-bottom: 0;
    }
    Screen.compact #server-info-bar {
        margin-bottom: 0;
    }
    Screen.compact #server-info {
        min-height: 1;
        max-height: 3;
        padding: 0;
    }

    Button.-primary {
        background: #238636;
    }
    """

    BINDINGS = [
        ("ctrl+r", "refresh_players", "Refresh players"),
        Binding("alt+r", "scan_servers", "Rescan servers", priority=True),
        # Plain letters reach editors; modified keys can jump from text fields.
        Binding("g", "show_tab('give-tab')", "Give"),
        Binding("ctrl+g,alt+g", "show_tab('give-tab')", "Give", priority=True, show=False),
        Binding("k", "show_tab('kits-tab')", "Kits"),
        Binding("ctrl+k,alt+k", "show_tab('kits-tab')", "Kits", priority=True, show=False),
        Binding("t", "show_tab('teleport-tab')", "Teleport"),
        Binding("ctrl+t,alt+t", "show_tab('teleport-tab')", "Teleport", priority=True, show=False),
        Binding("w", "show_tab('world-tab')", "World"),
        Binding("ctrl+w,alt+w", "show_tab('world-tab')", "World", priority=True, show=False),
        Binding("s", "show_tab('server-tab')", "Server"),
        Binding("ctrl+s,alt+s", "show_tab('server-tab')", "Server", priority=True, show=False),
        Binding("b", "open_backups", "Backups"),
        Binding("ctrl+b,alt+b", "open_backups", "Backups", priority=True, show=False),
        Binding("ctrl+o", "open_settings", "Settings"),
        Binding("a", "show_tab('activity-tab')", "Activity"),
        Binding("ctrl+a,alt+a", "show_tab('activity-tab')", "Activity", priority=True, show=False),
        Binding("f", "show_tab('fun-tab')", "Fun"),
        Binding("ctrl+f,alt+f", "show_tab('fun-tab')", "Fun", priority=True, show=False),
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        config: ServerConfig | None,
        *,
        unmined_client_jar: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.unmined_client_jar = unmined_client_jar
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
        self.kits: dict[str, Kit] = all_kits()
        self.inventory: list[tuple[int, int, str]] = []
        self._lifecycle_busy = False

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # App priority bindings are considered before modal widget bindings.
        # Keep modal editors and Selects' keys, but preserve Textual's built-in
        # focus traversal so Tab and Shift+Tab still move between modal controls.
        if isinstance(self.screen, ModalScreen):
            return action in {"focus_next", "focus_previous"}
        return super().check_action(action, parameters)

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="app-body"):
            with Horizontal(id="server-bar"):
                yield Button("Scan", id="server-scan")
                yield Select(
                    [],
                    prompt="No server selected",
                    allow_blank=True,
                    id="server-select",
                )
                yield Button("Add", id="server-add")
            with Horizontal(id="server-info-bar"):
                yield Static("No server selected.", id="server-info")
                yield Button("Backups", id="open-backups")
                yield Button("Settings", id="open-settings")
            with Horizontal(id="context-bar"):
                yield Label("TARGET: none", id="target-label")
                yield Select(
                    [],
                    prompt="No active players",
                    allow_blank=True,
                    id="player-select",
                )
                yield Button("Refresh", id="refresh-players")

            with TabbedContent(initial="player-tab"):
                with TabPane("Player", id="player-tab"):
                    yield Static("No active player selected.", id="player-summary")
                    yield Label("INVENTORY", classes="section-title")
                    yield RichLog(id="inventory-log", markup=True, wrap=True)
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

                with TabPane("Kits", id="kits-tab"):
                    yield Label(
                        "Give a bundle of items at once. Built-ins are read-only; "
                        "custom kits are saved to ~/.config/mc-admin-tui/kits.json.",
                        classes="help",
                    )
                    with Horizontal(id="kit-row"):
                        yield Select(
                            [],
                            prompt="No kits",
                            allow_blank=True,
                            id="kit-select",
                        )
                        yield Button("Give kit", id="kit-give", variant="primary")
                        yield Button("Delete kit", id="kit-delete")
                    yield Label("NEW / EDIT KIT", classes="section-title")
                    yield Input(
                        placeholder="KIT NAME  (e.g. mining)",
                        id="kit-name",
                    )
                    yield Input(
                        placeholder="items — e.g. diamond_pickaxe, torch 64, cobblestone 64",
                        id="kit-items",
                    )
                    with Horizontal(id="kit-save-row"):
                        yield Button("Save kit", id="kit-save", variant="primary")
                    yield RichLog(id="kit-log", markup=True, wrap=True)

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
                    with VerticalScroll(id="server-controls"):
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
                            "Start/Restart check deployment property overrides first; "
                            "they do not recreate containers or change deployment options. "
                            "Minecraft may still be loading after the container starts.",
                            classes="help",
                        )
                        with Horizontal(classes="button-row"):
                            yield Button("Start container", id="server-start")
                        yield Label(
                            "Stop requires STOP. Restart requires RESTART. "
                            "Both interrupt connected players.",
                            id="danger-note",
                        )
                        with Horizontal(id="stop-row"):
                            yield Input(placeholder="Type STOP", id="stop-confirm")
                            yield Button("Stop container", id="server-stop")
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
        self._update_kit_options()
        if self.config is not None:
            self._activate_server(self.config)
        self.scan_servers()
        self.set_interval(5, self.refresh_players)
        self.set_interval(10, self.refresh_selected_player)

    def on_resize(self, event: events.Resize) -> None:
        self.default_screen.set_class(event.size.height < 32, "compact")

    def _activate_server(self, config: ServerConfig) -> None:
        self.config = config
        if config.locations_path:
            migrate_legacy_locations(config.locations_path)
            self.locations = load_locations(config.locations_path)
        else:
            self.locations = load_locations(DEFAULT_LOCATIONS)
        self._sync_server_picker()
        self._refresh_server_info()
        self._update_location_options()
        upsert_server_config(config)
        self.refresh_players()
        self.refresh_server_status()
        self.refresh_world_status()

    def _sync_server_picker(self) -> None:
        """Point the server picker at the active server so it shows when collapsed."""
        if self.config is None:
            return
        select = self.query_one("#server-select", Select)
        selected = f"{self.config.runtime}:{self.config.container}"
        select.value = selected if any(
            value == selected for _, value in self._server_options()
        ) else Select.NULL

    @work(thread=True, exclusive=True, group="server-info-bar")
    def _refresh_server_info(self) -> None:
        server = self.config
        if server is None:
            return
        try:
            level = container_level_name(server)
            stats = server_stats(server, level)
        except Exception as exc:
            self.call_from_thread(self._apply_server_info, server, None, str(exc))
            return
        self.call_from_thread(self._apply_server_info, server, stats, None)

    def _apply_server_info(
        self, server: ServerConfig, stats: dict | None, error: str | None
    ) -> None:
        if not self._is_selected_server(server):
            return
        try:
            widget = self.query_one("#server-info", Static)
        except Exception:
            return  # screen torn down while the worker was running
        if self.config is None:
            widget.update("No server selected.")
            return
        if error or stats is None:
            widget.update(f"{self.config.label}\nInfo unavailable: {error}")
            return
        version = stats.get("version") or "unknown"
        state = "running" if stats.get("running") else "stopped"
        uptime = stats.get("uptime") or ""
        if stats.get("running") and not uptime:
            uptime = "unknown"
        players = stats.get("players")
        if stats.get("running"):
            if players is None:
                players_text = "unavailable"
            else:
                max_players = stats.get("max_players") or "?"
                players_text = f"{len(players)}/{max_players}"
        else:
            players_text = "—"
        size = human_size(stats.get("world_size"))
        port = stats.get("host_port")
        network = f"port {port}" if port else "port unmapped"
        lines = [
            f"[b]{escape(self.config.label)}[/b]  ·  {escape(self.config.runtime)}",
            f"[dim]{escape(str(stats.get('image', '')))}[/]",
            f"MC {version}   ·   {state}"
            + (f"   ·   uptime {uptime}" if uptime else ""),
            f"Players {players_text}   ·   World {size}   ·   {network}",
        ]
        if stats.get("motd"):
            lines.append(f"[dim]motd: {escape(str(stats['motd']))}[/]")
        widget.update("\n".join(lines))

    def _server_options(self) -> list[tuple[Text, str]]:
        """Picker options, running servers first; stopped servers dimmed."""
        ordered = sorted(
            self.discovered,
            key=lambda s: (
                0 if s.state == "running" else 1,
                s.container.casefold(),
                s.runtime,
            ),
        )
        options: list[tuple[Text, str]] = []
        for server in ordered:
            label = Text(
                f"{server.container}  ({server.runtime}, {server.state or 'unknown'})"
            )
            if server.state != "running":
                label.stylize("#6e7681")
            options.append((label, f"{server.runtime}:{server.container}"))
        return options

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
        options = self._server_options()
        select.set_options(options)
        select.prompt = f"{len(servers)} server(s) found" if servers else "No servers found"

        info = self.query_one("#server-info", Static)
        if error:
            info.update("Server scan failed.")
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
            selected = f"{self.config.runtime}:{self.config.container}"
            select.value = selected if any(value == selected for _, value in options) else Select.NULL

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
        select.set_options(self._server_options())
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
            self.query_one("#inventory-log", RichLog).clear()

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

    def action_show_tab(self, tab_id: str) -> None:
        # A focused widget keeps re-asserting its own pane via
        # TabPane._on_descendant_focus, which would revert the switch; drop focus
        # first so the new pane can take it.
        self.set_focus(None)
        self.query_one(TabbedContent).active = tab_id
        # Land focus on the tab bar itself, so a single Tab moves into the first
        # field of the newly shown pane instead of the top context bar.
        tabs = self.query_one(TabbedContent).get_child_by_type(ContentTabs)
        tabs.focus()

    def action_open_backups(self) -> None:
        if self.config is None:
            self.notify("Select a server first", severity="warning")
            return
        self.push_screen(BackupScreen(self.config, self.unmined_client_jar))

    @on(Button.Pressed, "#open-backups")
    def open_backups_pressed(self) -> None:
        self.action_open_backups()

    def action_open_settings(self) -> None:
        if isinstance(self.screen, ModalScreen):
            return
        if self.config is None:
            self.notify("Select a server first", severity="warning")
            return
        if self._lifecycle_busy:
            self.notify("Wait for the container operation to finish", severity="warning")
            return
        self.push_screen(SettingsScreen(self.config, self._settings_saved))

    @on(Button.Pressed, "#open-settings")
    def open_settings_pressed(self) -> None:
        self.action_open_settings()

    def _is_selected_server(self, server: ServerConfig) -> bool:
        return self.config is not None and (
            self.config.runtime, self.config.container, self.config.working_dir
        ) == (server.runtime, server.container, server.working_dir)

    def _settings_saved(self, server: ServerConfig, keys: tuple[str, ...]) -> None:
        target = escape(f"{server.runtime}:{server.container}")
        self._log(
            f"[green]Settings saved[/] for {target}: {', '.join(keys)}. "
            "[yellow]Restart required; no restart performed.[/]"
        )
        if self._is_selected_server(server):
            self._refresh_server_info()

    @work(thread=True, exclusive=True, group="snapshot")
    def refresh_selected_player(self) -> None:
        player = self.selected_player
        if not player or self.config is None:
            return
        try:
            snapshot = query_player_snapshot(self.config, player)
            inventory = query_player_inventory(self.config, player)
            self.call_from_thread(self._apply_snapshot, snapshot, None)
            self.call_from_thread(self._apply_inventory, inventory, None)
        except Exception as exc:
            self.call_from_thread(self._apply_snapshot, None, str(exc))
            self.call_from_thread(self._apply_inventory, None, str(exc))

    def _apply_inventory(
        self, inventory: list[tuple[int, int, str]] | None, error: str | None
    ) -> None:
        log = self.query_one("#inventory-log", RichLog)
        log.clear()
        if error:
            log.write(f"Inventory unavailable: {error}")
            return
        self.inventory = inventory or []
        if not self.inventory:
            log.write("Inventory empty.")
            return
        for slot, count, item_id in sorted(self.inventory):
            suffix = f" ×{count}" if count > 1 else ""
            log.write(f"[dim]{slot:>2}[/]  {human_name(item_id)}[b]{suffix}[/]")

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
            Option(Text(human_name(item_id), style="bold"), id=item_id)
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

    def _update_kit_options(self) -> None:
        select = self.query_one("#kit-select", Select)
        self.kits = all_kits()
        options = [
            (f"{name}  ({len(kit.items)} items)", name)
            for name, kit in sorted(self.kits.items())
        ]
        select.set_options(options)
        select.prompt = f"{len(options)} kit(s)" if options else "No kits"

    def _kit_log(self, message: str) -> None:
        self.query_one("#kit-log", RichLog).write(message)

    @on(Button.Pressed, "#kit-give")
    def kit_give(self) -> None:
        player = self._require_player()
        if not player:
            return
        name = self.query_one("#kit-select", Select).value
        if not isinstance(name, str) or name not in self.kits:
            self.notify("Choose a kit", severity="warning")
            return
        self.kit_give_worker(player, name)

    @work(thread=True, exclusive=True, group="kit")
    def kit_give_worker(self, player: str, name: str) -> None:
        config = self.config
        kit = self.kits.get(name)
        if config is None or kit is None:
            return
        given = 0
        errors: list[str] = []
        given_items: list[str] = []
        for entry, qty in kit.items:
            # Entries containing a space are commands (e.g. `gamerule x true`);
            # otherwise they are item ids given `qty` at a time.
            try:
                if " " in entry:
                    rcon(config, entry)
                    given_items.append(entry)
                else:
                    rcon(config, f"give {player} {entry} {qty}")
                    given_items.append(f"{human_name(entry)} ×{qty}")
                given += 1
            except Exception as exc:
                errors.append(f"{entry}: {exc}")
        total = len(kit.items)
        self.call_from_thread(
            self._kit_give_finished,
            player,
            name,
            total,
            errors,
            given_items,
            total - given,
        )

    def _kit_give_finished(
        self,
        player: str,
        name: str,
        total: int,
        errors: list[str],
        given_items: list[str],
        failed: int,
    ) -> None:
        for item in given_items:
            self._kit_log(f"[green]✓[/] {item}")
        if errors:
            self._kit_log(
                f"[yellow]Kit {name} → {player}: {len(given_items)}/{total} items given[/]"
            )
            for message in errors:
                self._kit_log(f"[red]  {message}[/]")
            self.notify(
                f"Kit {name} → {player}: {len(given_items)}/{total} given "
                f"({failed} failed)",
                severity="warning",
            )
            return
        summary = ", ".join(given_items)
        self._kit_log(f"[green]✓[/] Kit {name} → {player}: {summary}")
        self._log(f"[green]✓[/] Gave kit {name} to {player}: {summary}")
        self.notify(f"Gave kit {name} to {player}: {summary}")

    @on(Button.Pressed, "#kit-delete")
    def kit_delete(self) -> None:
        name = self.query_one("#kit-select", Select).value
        if not isinstance(name, str) or name not in self.kits:
            self.notify("Choose a kit", severity="warning")
            return
        if self.kits[name].source != "custom":
            self.notify(f"{name} is a built-in kit and cannot be deleted", severity="warning")
            return
        custom = load_custom_kits()
        custom.pop(name, None)
        save_custom_kits(custom)
        self._update_kit_options()
        self._kit_log(f"[yellow]Deleted kit[/] {name}")
        self.notify(f"Deleted {name}")

    @on(Button.Pressed, "#kit-save")
    def kit_save(self) -> None:
        name = self.query_one("#kit-name", Input).value.strip()
        if not name:
            self.notify("Give the kit a name", severity="warning")
            return
        items = parse_kit_items(self.query_one("#kit-items", Input).value)
        if not items:
            self.notify("Add at least one item", severity="warning")
            return
        custom = load_custom_kits()
        custom[name] = Kit(name=name, items=items, source="custom")
        save_custom_kits(custom)
        self._update_kit_options()
        self.query_one("#kit-select", Select).value = name
        self.query_one("#kit-name", Input).value = ""
        self.query_one("#kit-items", Input).value = ""
        self._kit_log(f"[green]✓[/] Saved kit [b]{name}[/b] ({len(items)} items)")
        self.notify(f"Saved kit {name}")

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
            name = rule_name(self.config, "keepInventory")
            value = rcon(self.config, f"gamerule {name}")
            self.call_from_thread(self._apply_world_status, name, value, None)
        except Exception as exc:
            self.call_from_thread(self._apply_world_status, "keepInventory", "", str(exc))

    def _apply_world_status(
        self, name: str, value: str, error: str | None
    ) -> None:
        if error:
            self.query_one("#world-status", Static).update(
                f"World status unavailable: {error}"
            )
        else:
            self.query_one("#world-status", Static).update(
                f"[b]{name}[/b]\n{value}"
            )

    @on(Button.Pressed, "#world-refresh")
    def world_refresh(self) -> None:
        self.refresh_world_status()

    def _world_action(self, label: str, command: str) -> None:
        self.run_rcon_action(label, command, False)
        if "keep_inventory" in command or "keepInventory" in command:
            self.set_timer(0.5, self.refresh_world_status)

    def _set_keep_inventory(self, value: bool) -> None:
        if self.config is None:
            return
        name = rule_name(self.config, "keepInventory")
        self._world_action(
            f"{name} enabled" if value else f"{name} disabled",
            f"gamerule {name} {'true' if value else 'false'}",
        )

    @on(Button.Pressed, "#keepinv-on")
    def keepinv_on(self) -> None:
        self._set_keep_inventory(True)

    @on(Button.Pressed, "#keepinv-off")
    def keepinv_off(self) -> None:
        self._set_keep_inventory(False)

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

    @on(Button.Pressed, "#server-start")
    def server_start(self) -> None:
        self._begin_lifecycle("start")

    @on(Button.Pressed, "#server-stop")
    def server_stop(self) -> None:
        if self._lifecycle_busy:
            return
        confirm = self.query_one("#stop-confirm", Input)
        if confirm.value.strip() != "STOP":
            self.notify("Type STOP first", severity="warning")
            return
        if self._begin_lifecycle("stop"):
            confirm.value = ""

    @on(Button.Pressed, "#server-restart")
    def server_restart(self) -> None:
        if self._lifecycle_busy:
            return
        confirm = self.query_one("#restart-confirm", Input)
        if confirm.value.strip() != "RESTART":
            self.notify("Type RESTART first", severity="warning")
            return
        if self._begin_lifecycle("restart"):
            confirm.value = ""

    def _set_lifecycle_busy(self, busy: bool) -> None:
        self._lifecycle_busy = busy
        for widget_id in (
            "server-start", "server-stop", "server-restart",
            "stop-confirm", "restart-confirm", "open-settings",
        ):
            self.query_one(f"#{widget_id}").disabled = busy

    def _begin_lifecycle(self, operation: str) -> bool:
        if self._lifecycle_busy or isinstance(self.screen, ModalScreen):
            return False
        server = self.config
        if server is None:
            self.notify("Select a server first", severity="warning")
            return False
        self._set_lifecycle_busy(True)
        self.query_one("#server-status", Static).update(
            Text(f"{server.label}: container {operation} in progress…")
        )
        self._lifecycle_worker(server, operation)
        return True

    @work(thread=True, group="container-lifecycle", exit_on_error=False)
    def _lifecycle_worker(self, server: ServerConfig, operation: str) -> None:
        operations = {
            "start": start_container,
            "stop": stop_container,
            "restart": restart_container,
        }
        try:
            output = operations[operation](server)
        except Exception as exc:
            output, error = "", str(exc)
        else:
            error = None
        try:
            self.call_from_thread(self._lifecycle_finished, server, operation, output, error)
        except RuntimeError:
            return

    def _lifecycle_finished(
        self, server: ServerConfig, operation: str, output: str, error: str | None
    ) -> None:
        self._set_lifecycle_busy(False)
        target = f"{server.runtime}:{server.container}"
        if error:
            self._log(f"[red]Container {operation} failed[/] for {escape(target)}: {escape(error)}")
            self.notify(f"Container {operation} failed: {error}", severity="error", timeout=12)
            if self._is_selected_server(server):
                self.query_one("#server-status", Static).update(
                    Text(f"{target}: {operation} failed\n{error}")
                )
            return
        suffix = (
            "Container stopped."
            if operation == "stop" else "Container running; Minecraft may still be loading."
        )
        self._log(
            f"[yellow]Container {operation} completed[/] for {escape(target)}. {suffix}"
        )
        self.notify(f"{target}: {suffix}")
        self.scan_servers()
        if self._is_selected_server(server):
            self.refresh_server_status()
            self._refresh_server_info()
            self.refresh_players()
            if operation != "stop":
                self.set_timer(4, lambda: self._refresh_after_lifecycle(server))

    def _refresh_after_lifecycle(self, server: ServerConfig) -> None:
        if self._is_selected_server(server):
            self.refresh_server_status()
            self._refresh_server_info()
            self.refresh_players()

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
    parser.add_argument(
        "--unmined-client-jar",
        type=Path,
        help="Minecraft Java client JAR for detailed uNmINeD map colors.",
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
    args = parse_args()
    MinecraftAdminApp(
        config_from_args(args),
        unmined_client_jar=args.unmined_client_jar,
    ).run()


if __name__ == "__main__":
    main()
