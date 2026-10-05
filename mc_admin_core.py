from __future__ import annotations

import gzip
import io
import json
import re
import shutil
import subprocess
import tarfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

APP_DIR = Path.home() / ".config" / "mc-admin-tui"
CACHE_DIR = Path.home() / ".cache" / "mc-admin-tui"
BACKUP_DIR = Path.home() / "minecraft-backups"
DEFAULT_ITEM_CACHE = CACHE_DIR / "items.json"
DEFAULT_LOCATIONS = APP_DIR / "locations.json"
LEGACY_LOCATIONS = APP_DIR / "locations.legacy.json"
DEFAULT_SERVERS = APP_DIR / "servers.json"
DEFAULT_KITS = APP_DIR / "kits.json"

BUILTIN_KITS: dict[str, tuple[tuple[str, int], ...]] = {
    "starter": (
        ("minecraft:torch", 64),
        ("minecraft:oak_log", 32),
        ("minecraft:cobblestone", 64),
        ("minecraft:crafting_table", 1),
        ("minecraft:furnace", 1),
        ("minecraft:cooked_beef", 32),
    ),
    "tools": (
        ("minecraft:netherite_pickaxe", 1),
        ("minecraft:netherite_axe", 1),
        ("minecraft:netherite_shovel", 1),
        ("minecraft:netherite_sword", 1),
        ("minecraft:shield", 1),
    ),
    "nether": (
        ("minecraft:netherite_ingot", 4),
        ("minecraft:obsidian", 10),
        ("minecraft:flint_and_steel", 1),
        ("minecraft:golden_apple", 8),
        ("minecraft:ender_pearl", 16),
    ),
    "pvp": (
        ("minecraft:netherite_sword", 1),
        ("minecraft:bow", 1),
        ("minecraft:arrow", 64),
        ("minecraft:golden_apple", 32),
        ("minecraft:ender_pearl", 16),
        ("minecraft:totem_of_undying", 1),
    ),
    "food": (
        ("minecraft:cooked_beef", 64),
        ("minecraft:golden_carrot", 64),
        ("minecraft:bread", 64),
        ("minecraft:golden_apple", 16),
    ),
    # Enchanted gear uses item components; values are passed straight to `give`.
    "swimming": (
        (
            'minecraft:turtle_helmet[enchantments={"minecraft:protection":4,'
            '"minecraft:respiration":3,"minecraft:aqua_affinity":1,'
            '"minecraft:unbreaking":3,"minecraft:mending":1}]',
            1,
        ),
        (
            'minecraft:netherite_chestplate[enchantments={"minecraft:protection":4,'
            '"minecraft:unbreaking":3,"minecraft:mending":1}]',
            1,
        ),
        (
            'minecraft:netherite_leggings[enchantments={"minecraft:protection":4,'
            '"minecraft:swift_sneak":3,"minecraft:unbreaking":3,'
            '"minecraft:mending":1}]',
            1,
        ),
        (
            'minecraft:netherite_boots[enchantments={"minecraft:protection":4,'
            '"minecraft:depth_strider":3,"minecraft:feather_falling":4,'
            '"minecraft:unbreaking":3,"minecraft:mending":1}]',
            1,
        ),
        (
            'minecraft:trident[enchantments={"minecraft:riptide":3,'
            '"minecraft:impaling":5,"minecraft:unbreaking":3,'
            '"minecraft:mending":1}]',
            1,
        ),
        (
            'minecraft:trident[enchantments={"minecraft:loyalty":3,'
            '"minecraft:channeling":1,"minecraft:impaling":5,'
            '"minecraft:unbreaking":3,"minecraft:mending":1}]',
            1,
        ),
        (
            'minecraft:potion[minecraft:potion_contents='
            '{potion:"minecraft:long_water_breathing"}]',
            6,
        ),
        (
            'minecraft:potion[minecraft:potion_contents='
            '{potion:"minecraft:long_night_vision"}]',
            6,
        ),
        ("minecraft:golden_carrot", 64),
    ),
}

RUNTIME_BINARIES = {"podman": "podman", "docker": "docker"}
RUNTIME_ORDER = ("podman", "docker")
RCON_IMAGE_HINTS = ("itzg/minecraft-server", "itzg/mc-server")
RCON_DEFAULT_PORT = 25575

FALLBACK_ITEMS = [
    "minecraft:netherite_helmet",
    "minecraft:netherite_chestplate",
    "minecraft:netherite_leggings",
    "minecraft:netherite_boots",
    "minecraft:netherite_sword",
    "minecraft:netherite_pickaxe",
    "minecraft:netherite_axe",
    "minecraft:netherite_shovel",
    "minecraft:netherite_hoe",
    "minecraft:elytra",
    "minecraft:mace",
    "minecraft:trident",
    "minecraft:bow",
    "minecraft:crossbow",
    "minecraft:shield",
    "minecraft:totem_of_undying",
    "minecraft:recovery_compass",
    "minecraft:wither_skeleton_skull",
    "minecraft:firework_rocket",
    "minecraft:golden_apple",
    "minecraft:enchanted_golden_apple",
    "minecraft:ender_pearl",
    "minecraft:ender_chest",
    "minecraft:shulker_box",
    "minecraft:experience_bottle",
    "minecraft:enchanted_book",
    "minecraft:book",
    "minecraft:anvil",
    "minecraft:crafting_table",
    "minecraft:torch",
    "minecraft:cooked_beef",
    "minecraft:water_bucket",
    "minecraft:lava_bucket",
    "minecraft:oak_log",
    "minecraft:cobblestone",
    "minecraft:deepslate",
    "minecraft:cobbled_deepslate",
    "minecraft:polished_deepslate",
    "minecraft:deepslate_bricks",
    "minecraft:deepslate_tiles",
    "minecraft:diamond",
    "minecraft:emerald",
    "minecraft:iron_ingot",
    "minecraft:gold_ingot",
    "minecraft:netherite_ingot",
    "minecraft:netherite_upgrade_smithing_template",
    "minecraft:arrow",
    "minecraft:spectral_arrow",
    "minecraft:spyglass",
    "minecraft:goat_horn",
    "minecraft:poisonous_potato",
    "minecraft:dirt",
]

ITEM_ALIASES: dict[str, tuple[str, ...]] = {
    "minecraft:firework_rocket": ("fireworks", "firework", "rocket", "elytra rocket"),
    "minecraft:wither_skeleton_skull": ("wither skull", "wither skeleton head"),
    "minecraft:cobbled_deepslate": ("deep slate", "cobbled deep slate"),
    "minecraft:totem_of_undying": ("totem", "totem of undying"),
}


@dataclass(frozen=True)
class ServerConfig:
    runtime: str
    container: str
    display_name: str = ""
    rcon_client: str = "rcon-cli"
    rcon_password: str | None = None
    rcon_port: int = RCON_DEFAULT_PORT
    working_dir: str | None = None
    locations_path: Path | None = None
    registry: Path | None = None

    @property
    def label(self) -> str:
        return self.display_name or self.container



@dataclass(frozen=True)
class ServerSetting:
    key: str
    label: str
    kind: str
    choices: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


SETTINGS: tuple[ServerSetting, ...] = (
    ServerSetting("motd", "MOTD", "text"),
    ServerSetting("max-players", "Maximum players", "int", minimum=1, maximum=2147483647),
    ServerSetting("difficulty", "Difficulty", "choice", ("peaceful", "easy", "normal", "hard")),
    ServerSetting("gamemode", "Game mode", "choice", ("survival", "creative", "adventure", "spectator")),
    ServerSetting("force-gamemode", "Force game mode", "bool"),
    ServerSetting("pvp", "PvP", "bool"),
    ServerSetting("white-list", "Whitelist", "bool"),
    ServerSetting("enforce-whitelist", "Enforce whitelist", "bool"),
    ServerSetting("allow-flight", "Allow flight", "bool"),
    ServerSetting("spawn-monsters", "Spawn monsters", "bool"),
    ServerSetting("spawn-animals", "Spawn animals", "bool"),
    ServerSetting("spawn-npcs", "Spawn NPCs", "bool"),
    ServerSetting("allow-nether", "Allow Nether", "bool"),
    ServerSetting("view-distance", "View distance", "int", minimum=2, maximum=32),
    ServerSetting("simulation-distance", "Simulation distance", "int", minimum=2, maximum=32),
    ServerSetting("spawn-protection", "Spawn protection", "int", minimum=0, maximum=2147483647),
    ServerSetting("player-idle-timeout", "Player idle timeout", "int", minimum=0, maximum=2147483647),
)


@dataclass(frozen=True)
class ServerSettings:
    path: str
    raw: bytes
    values: dict[str, str]
    write_blocked: str | None = None

@dataclass(frozen=True)
class DiscoveredServer:
    runtime: str
    container: str
    image: str = ""
    state: str = ""
    status: str = ""
    rcon_client: str | None = None
    rcon_password: str | None = None
    rcon_port: int = RCON_DEFAULT_PORT
    working_dir: str | None = None
    source: str = "image"


@dataclass
class PlayerSnapshot:
    player: str
    health: float | None = None
    food: int | None = None
    xp_level: int | None = None
    pos: tuple[float, float, float] | None = None
    dimension: str | None = None


@dataclass(frozen=True)
class Location:
    name: str
    x: float
    y: float
    z: float
    dimension: str = "minecraft:overworld"


@dataclass(frozen=True)
class Kit:
    name: str
    items: tuple[tuple[str, int], ...]
    source: str = "custom"


def run_process(args: list[str], *, timeout: int = 10) -> str:
    proc = subprocess.run(
        args,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail or f"{args[0]} exited {proc.returncode}")
    return proc.stdout.strip()


def runtime_binary(runtime: str) -> str:
    binary = RUNTIME_BINARIES.get(runtime, runtime)
    if not shutil.which(binary):
        raise RuntimeError(f"'{binary}' is not available on this host")
    return binary


def available_runtimes() -> list[str]:
    return [
        runtime
        for runtime in RUNTIME_ORDER
        if shutil.which(RUNTIME_BINARIES[runtime])
    ]


def list_containers(runtime: str) -> list[dict]:
    raw = run_process(
        [runtime_binary(runtime), "ps", "-a", "--format", "json"], timeout=30
    )
    if not raw:
        return []
    data = json.loads(raw)
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else [data]


def inspect_container(runtime: str, container: str) -> dict:
    raw = run_process(
        [runtime_binary(runtime), "inspect", "--format", "json", container],
        timeout=20,
    )
    data = json.loads(raw)
    if isinstance(data, list):
        return data[0] if data else {}
    return data if isinstance(data, dict) else {}


def container_name(info: dict) -> str:
    names = info.get("Names") or []
    if isinstance(names, str):
        names = [names]
    if names:
        return str(names[0])
    return str(info.get("Name") or "").lstrip("/")


def container_image(info: dict) -> str:
    return str(
        info.get("ImageName")
        or (info.get("Config") or {}).get("Image")
        or info.get("Image")
        or ""
    )


def container_env(info: dict) -> dict[str, str]:
    entries = (info.get("Config") or {}).get("Env") or []
    env: dict[str, str] = {}
    for entry in entries:
        key, _, value = str(entry).partition("=")
        if key:
            env[key.upper()] = value
    return env


def container_workdir(info: dict) -> str | None:
    mounts = info.get("Mounts") or []
    if isinstance(mounts, dict):
        mounts = [mounts]
    for mount in mounts:
        if isinstance(mount, dict) and mount.get("Destination") == "/data":
            return "/data"
    workdir = (info.get("Config") or {}).get("WorkingDir")
    return str(workdir) if workdir else None


def is_minecraft_image(image: str) -> bool:
    lowered = image.lower()
    return any(hint in lowered for hint in RCON_IMAGE_HINTS)


def rcon_client_in_container(runtime: str, container: str) -> str | None:
    probe = (
        "for c in rcon-cli mcrcon rcon; do "
        'p=$(command -v "$c") && { echo "$p"; exit 0; }; done; exit 1'
    )
    try:
        output = run_process(
            [runtime_binary(runtime), "exec", container, "sh", "-c", probe],
            timeout=15,
        )
    except (RuntimeError, subprocess.SubprocessError):
        return None
    first = output.splitlines()[0].strip() if output else ""
    return Path(first).name if first else None


def _rcon_settings_from_container(
    runtime: str, container: str
) -> tuple[str | None, int | None, str | None]:
    # server.properties is the authoritative source (it is what the running server
    # actually uses); the .rcon-cli.yaml/.env a server writes can go stale, so they
    # are only a fallback.
    command = (
        "rc=/data/server.properties; [ -f \"$rc\" ] && "
        "{ echo '###auth'; grep -E '^rcon\\.(password|port)=' \"$rc\"; }; "
        "for f in /data/.rcon-cli.yaml /data/.rcon-cli.env "
        "$HOME/.rcon-cli.yaml $HOME/.rcon-cli.env; do "
        '[ -f "$f" ] && { echo "###$f"; cat "$f"; }; done; exit 0'
    )
    try:
        raw = run_process(
            [runtime_binary(runtime), "exec", container, "sh", "-c", command],
            timeout=15,
        )
    except (RuntimeError, subprocess.SubprocessError):
        return None, None, None

    password: str | None = None
    port: int | None = None
    working_dir: str | None = None
    section = ""
    for line in raw.splitlines():
        if line.startswith("###"):
            section = line[3:].strip()
            if section != "auth" and password is not None and working_dir is None:
                working_dir = str(Path(section).parent)
            continue
        key, _, value = line.partition("=")
        if not value and ":" in line and section != "auth":
            key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key in ("rcon.password", "password") and value and password is None:
            password = value
        elif key in ("rcon.port", "port") and value.isdigit() and port is None:
            port = int(value)
    return password, port, working_dir


def discover_servers(runtime: str) -> list[DiscoveredServer]:
    if not shutil.which(RUNTIME_BINARIES.get(runtime, runtime)):
        return []
    try:
        containers = list_containers(runtime)
    except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError):
        return []

    servers: list[DiscoveredServer] = []
    for info in containers:
        image = container_image(info)
        if not is_minecraft_image(image):
            continue
        name = container_name(info)
        if not name:
            continue
        client = rcon_client_in_container(runtime, name)
        password = port = working_dir = None
        source = "image"
        if client:
            password, port, working_dir = _rcon_settings_from_container(runtime, name)
            source = "rcon-client"
            if working_dir is None:
                try:
                    working_dir = container_workdir(inspect_container(runtime, name))
                except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError):
                    working_dir = None
            if password is None:
                try:
                    env = container_env(inspect_container(runtime, name))
                except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError):
                    env = {}
                password = env.get("RCON_PASSWORD") or None
                if env.get("RCON_PORT", "").isdigit():
                    port = int(env["RCON_PORT"])
        servers.append(
            DiscoveredServer(
                runtime=runtime,
                container=name,
                image=image,
                state=str(info.get("State") or "").lower(),
                status=str(info.get("Status") or ""),
                rcon_client=client,
                rcon_password=password,
                rcon_port=port or RCON_DEFAULT_PORT,
                working_dir=working_dir,
                source=source,
            )
        )
    return servers


def scan_servers(runtimes: Iterable[str] | None = None) -> list[DiscoveredServer]:
    selected = list(runtimes) if runtimes is not None else available_runtimes()
    servers: list[DiscoveredServer] = []
    for runtime in selected:
        servers.extend(discover_servers(runtime))
    return servers


def server_config_for(
    server: DiscoveredServer, *, registry: Path | None = None
) -> ServerConfig:
    return ServerConfig(
        runtime=server.runtime,
        container=server.container,
        display_name=server.container,
        rcon_client=server.rcon_client or "rcon-cli",
        rcon_password=server.rcon_password,
        rcon_port=server.rcon_port,
        working_dir=server.working_dir,
        locations_path=locations_path_for(server.container),
        registry=registry,
    )


def rcon(server: ServerConfig, command: str, *, timeout: int = 8) -> str:
    args = [runtime_binary(server.runtime), "exec"]
    if server.working_dir:
        args += ["-w", server.working_dir]
    args += ["-i", server.container, server.rcon_client]
    if server.rcon_client == "mcrcon":
        args += ["-H", "localhost", "-P", str(server.rcon_port)]
        if server.rcon_password:
            args += ["-p", server.rcon_password]
    else:
        if server.rcon_password:
            args += ["--password", server.rcon_password]
        if server.rcon_port != RCON_DEFAULT_PORT:
            args += ["--port", str(server.rcon_port)]
    args.append(command)
    return run_process(args, timeout=timeout)


def container_command(server: ServerConfig, *args: str, timeout: int = 30) -> str:
    return run_process(
        [runtime_binary(server.runtime), *args, server.container], timeout=timeout
    )


def container_status(server: ServerConfig) -> str:
    info = inspect_container(server.runtime, server.container)
    state = info.get("State") or {}
    status = str(state.get("Status") or "unknown")
    started = str(state.get("StartedAt") or "")
    image = container_image(info)
    detail = f"{status} | started {started}" if started else status
    return f"{detail}\n{image}".strip()


def tail_logs(server: ServerConfig, lines: int = 100) -> str:
    return container_command(server, "logs", "--tail", str(lines), timeout=15)


def start_container(server: ServerConfig) -> str:
    _, blocked = _settings_context(server)
    if blocked:
        raise RuntimeError(blocked)
    return container_command(server, "start", timeout=90)


def _stop_timeout(server: ServerConfig) -> int:
    info = inspect_container(server.runtime, server.container)
    configured = (info.get("Config") or {}).get("StopTimeout")
    return max(60, int(configured or 0))


def stop_container(server: ServerConfig) -> str:
    grace = _stop_timeout(server)
    return container_command(server, "stop", "--time", str(grace), timeout=grace + 30)


def restart_container(server: ServerConfig) -> str:
    _, blocked = _settings_context(server)
    if blocked:
        raise RuntimeError(blocked)
    grace = _stop_timeout(server)
    return container_command(server, "restart", "--time", str(grace), timeout=grace + 30)


def server_stats(server: ServerConfig, level_name: str = "world") -> dict:
    """Best-effort server metadata: MC version, uptime, players, world size, motd."""
    stats: dict = {}
    running = False
    started_at = None
    try:
        info = inspect_container(server.runtime, server.container)
        state = info.get("State") or {}
        running = str(state.get("Status") or "").lower() == "running"
        started_at = state.get("StartedAt") or None
        stats["image"] = container_image(info)
    except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        stats["error"] = str(exc)
        return stats

    stats["running"] = running
    stats["started_at"] = started_at

    try:
        props = _properties_from_tar(_copy_path_stream(server, "/data/server.properties"))
    except (RuntimeError, subprocess.SubprocessError):
        props = {}
    stats["motd"] = props.get("motd", "")
    stats["max_players"] = props.get("max-players", "")

    mcxbox = None
    try:
        mcxbox = _properties_from_tar(
            _copy_path_stream(server, "/data/config/mcxbox.properties")
        )
    except (RuntimeError, subprocess.SubprocessError):
        mcxbox = None
    if mcxbox and (mcxbox.get("MOTD") or mcxbox.get("VERSION")):
        stats["message"] = mcxbox.get("MOTD", "")
        stats["version"] = mcxbox.get("VERSION", "")
    else:
        stats["message"] = ""
        stats["version"] = _version_from_container(server)

    if running:
        try:
            listing = rcon(server, "list")
            online = parse_players(listing)
            match = re.search(
                r"of a max of (\d+) players online", listing, re.IGNORECASE
            )
            stats["players"] = online
            stats["max_players"] = match.group(1) if match else props.get(
                "max-players", ""
            )
        except Exception:
            stats["players"] = None
    else:
        stats["players"] = None

    try:
        stats["world_size"] = world_size_bytes(server, level_name)
    except (RuntimeError, subprocess.SubprocessError):
        stats["world_size"] = None

    if started_at:
        stats["uptime"] = _uptime_text(started_at)
    return stats


def _uptime_text(started_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(started_at)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return ""
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _version_from_container(server: ServerConfig) -> str:
    """Read the reported MC version from the itzg env or server.properties."""
    try:
        env = container_env(inspect_container(server.runtime, server.container))
    except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError):
        env = {}
    if env.get("VERSION"):
        return env["VERSION"]
    try:
        props = _properties_from_tar(
            _copy_path_stream(server, "/data/server.properties")
        )
    except (RuntimeError, subprocess.SubprocessError):
        return ""
    # Vanilla jars write the version into the properties as a comment line.
    for line in props.get("_raw", "").splitlines():
        match = re.search(r"version[= ]+([0-9][\w.\-]*)", line, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def container_running(server: ServerConfig) -> bool:
    try:
        info = inspect_container(server.runtime, server.container)
    except (RuntimeError, json.JSONDecodeError, subprocess.SubprocessError):
        return False
    return str((info.get("State") or {}).get("Status") or "").lower() == "running"


def world_size_bytes(server: ServerConfig, level_name: str = "world") -> int | None:
    """World size in bytes.

    Running containers use `du -sb` (cheap, one syscall pass). Stopped containers
    cannot be exec'd, so the tar stream is counted instead — correct but O(size).
    """
    if container_running(server):
        try:
            output = run_process(
                [
                    runtime_binary(server.runtime),
                    "exec",
                    server.container,
                    "du",
                    "-sb",
                    f"/data/{level_name}",
                ],
                timeout=30,
            )
            return int(output.split()[0])
        except (RuntimeError, ValueError, subprocess.SubprocessError):
            return None

    argv = [
        runtime_binary(server.runtime),
        "cp",
        f"{server.container}:/data/{level_name}",
        "-",
    ]
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    total = 0
    while chunk := proc.stdout.read(1 << 20):
        total += len(chunk)
    proc.wait()
    return total if proc.returncode == 0 else None


def human_size(count: int | None) -> str:
    if not count:
        return "?"
    value = float(count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def human_name(item_id: str) -> str:
    """Readable label for an item id, tolerating component suffixes like [enchantments=…]."""
    base, _, components = item_id.partition("[")
    namespace, _, path = base.partition(":")
    words = path.replace("_", " ").replace("/", " / ")
    name = words if namespace == "minecraft" else f"{words}  [{namespace}]"
    if components:
        name = f"{name} [+components]"
    return name


def search_key(value: str) -> str:
    value = value.casefold().strip()
    value = value.removeprefix("minecraft:")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def score_item(query: str, item_id: str) -> tuple[int, int, int, str] | None:
    q = search_key(query)
    if not q:
        return (9, 0, len(item_id), item_id)

    item_path = item_id.split(":", 1)[-1]
    name = search_key(item_path)
    aliases = tuple(search_key(alias) for alias in ITEM_ALIASES.get(item_id, ()))
    searchable = (name, *aliases)

    for candidate_index, candidate in enumerate(searchable):
        if q == candidate:
            return (0, candidate_index, len(candidate), item_id)
        if candidate.startswith(q):
            return (1, candidate_index, len(candidate), item_id)

        compact_q = q.replace(" ", "")
        compact_candidate = candidate.replace(" ", "")
        if compact_candidate.startswith(compact_q):
            return (2, candidate_index, len(candidate), item_id)

        pos = candidate.find(q)
        if pos >= 0:
            return (3, pos + candidate_index, len(candidate), item_id)

        positions: list[int] = []
        for token in q.split():
            token_pos = candidate.find(token)
            if token_pos < 0:
                break
            positions.append(token_pos)
        else:
            return (4, sum(positions) + candidate_index, len(candidate), item_id)

    return None


def find_matches(query: str, items: Iterable[str], limit: int = 20) -> list[str]:
    scored = []
    for item_id in items:
        score = score_item(query, item_id)
        if score is not None:
            scored.append((score, item_id))
    scored.sort(key=lambda pair: pair[0])
    return [item_id for _, item_id in scored[:limit]]


def items_from_registry(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    registry = data.get("minecraft:item")
    if not registry:
        raise ValueError("No minecraft:item registry found in registries.json")
    return sorted(registry.get("entries", {}).keys())


def _bundled_registry() -> Path | None:
    path = Path(__file__).with_name("registries.json")
    return path if path.exists() else None


def load_items(registry: Path | None) -> tuple[list[str], str]:
    """Resolve the item catalogue: explicit registry → cache → bundled → fallback."""
    if registry:
        items = items_from_registry(registry)
        DEFAULT_ITEM_CACHE.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_ITEM_CACHE.write_text(json.dumps(items, indent=2), encoding="utf-8")
        return items, f"registry: {registry}"

    if DEFAULT_ITEM_CACHE.exists():
        return (
            json.loads(DEFAULT_ITEM_CACHE.read_text(encoding="utf-8")),
            str(DEFAULT_ITEM_CACHE),
        )

    bundled = _bundled_registry()
    if bundled:
        try:
            return items_from_registry(bundled), f"bundled registry: {bundled.name}"
        except (ValueError, json.JSONDecodeError):
            pass

    return sorted(FALLBACK_ITEMS), "built-in starter catalogue"


def registry_entities(path: Path) -> list[str]:
    """Entity type ids from a registries.json (for summon/entity pickers)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    registry = data.get("minecraft:entity_type")
    if not registry:
        raise ValueError("No minecraft:entity_type registry found")
    return sorted(registry.get("entries", {}).keys())


def bundled_registry_path() -> Path | None:
    return _bundled_registry()


def parse_players(output: str) -> list[str]:
    match = re.search(r"players online:\s*(.*)$", output, re.IGNORECASE)
    if not match:
        return []
    tail = match.group(1).strip()
    return [name.strip() for name in tail.split(",") if name.strip()] if tail else []


def _data_payload(output: str) -> str:
    if "data:" in output:
        return output.split("data:", 1)[1].strip()
    return output.strip()


def parse_number(output: str) -> float | None:
    payload = _data_payload(output)
    match = re.search(r"-?\d+(?:\.\d+)?", payload)
    return float(match.group(0)) if match else None


def parse_position(output: str) -> tuple[float, float, float] | None:
    payload = _data_payload(output)
    match = re.search(r"\[([^\]]+)\]", payload)
    if not match:
        return None

    values = []
    for part in match.group(1).split(","):
        cleaned = part.strip().rstrip("dDfF")
        try:
            values.append(float(cleaned))
        except ValueError:
            return None
    return tuple(values) if len(values) == 3 else None


def parse_dimension(output: str) -> str | None:
    payload = _data_payload(output).strip().strip('"')
    match = re.search(r"minecraft:[a-z0-9_]+", payload)
    return match.group(0) if match else (payload or None)


def parse_inventory(output: str) -> list[tuple[int, int, str]]:
    """Parse `data get entity <p> Inventory` semi-NBT into (slot, count, item id).

    Item entries carry nested `components` maps, so whole-entry brace matching is
    unreliable; anchor on each `id` and read the nearest preceding `count`/`Slot`.
    """
    payload = _data_payload(output)
    if "has no items" in payload:
        return []
    entries: list[tuple[int, int, str]] = []
    for match in re.finditer(r'id:\s*"([^"]+)"', payload):
        item_id = match.group(1)
        before = payload[: match.start()]
        slot = None
        for slot_match in re.finditer(r"Slot:\s*(-?\d+)b", before):
            slot = int(slot_match.group(1))
        if slot is None:
            continue
        count = 1
        for count_match in re.finditer(r"(?<![\w:])count:\s*(\d+)", before):
            count = int(count_match.group(1))
        entries.append((slot, count, item_id))
    return entries


def query_player_inventory(
    server: ServerConfig, player: str
) -> list[tuple[int, int, str]]:
    return parse_inventory(rcon(server, f"data get entity {player} Inventory"))


def query_player_snapshot(server: ServerConfig, player: str) -> PlayerSnapshot:
    def get(path: str) -> str:
        return rcon(server, f"data get entity {player} {path}")

    health = parse_number(get("Health"))
    food = parse_number(get("foodLevel"))
    xp = parse_number(get("XpLevel"))
    pos = parse_position(get("Pos"))
    dimension = parse_dimension(get("Dimension"))

    return PlayerSnapshot(
        player=player,
        health=health,
        food=int(food) if food is not None else None,
        xp_level=int(xp) if xp is not None else None,
        pos=pos,
        dimension=dimension,
    )


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return cleaned or "server"


def locations_path_for(container: str, base: Path = APP_DIR) -> Path:
    return base / "locations" / f"{safe_filename(container)}.json"


def migrate_legacy_locations(path: Path) -> None:
    if path.exists() or not LEGACY_LOCATIONS.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(LEGACY_LOCATIONS.read_text(encoding="utf-8"), encoding="utf-8")
    LEGACY_LOCATIONS.rename(LEGACY_LOCATIONS.with_suffix(".imported.json"))


def load_locations(path: Path = DEFAULT_LOCATIONS) -> dict[str, Location]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, Location] = {}
    for entry in raw:
        location = Location(**entry)
        result[location.name] = location
    return result


def save_locations(
    locations: dict[str, Location], path: Path = DEFAULT_LOCATIONS
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(locations[name]) for name in sorted(locations)]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _server_to_dict(config: ServerConfig) -> dict:
    data = {
        "runtime": config.runtime,
        "container": config.container,
        "display_name": config.display_name,
        "rcon_client": config.rcon_client,
        "rcon_password": config.rcon_password,
        "rcon_port": config.rcon_port,
        "working_dir": config.working_dir,
    }
    if config.registry:
        data["registry"] = str(config.registry)
    return data


def _server_from_dict(data: dict) -> ServerConfig:
    container = str(data.get("container", "")).strip()
    registry = data.get("registry")
    return ServerConfig(
        runtime=str(data.get("runtime", "podman")),
        container=container,
        display_name=str(data.get("display_name", "")),
        rcon_client=str(data.get("rcon_client", "rcon-cli")),
        rcon_password=data.get("rcon_password") or None,
        rcon_port=int(data.get("rcon_port", RCON_DEFAULT_PORT)),
        working_dir=data.get("working_dir") or None,
        locations_path=locations_path_for(container),
        registry=Path(registry) if registry else None,
    )


def load_server_configs(path: Path = DEFAULT_SERVERS) -> list[ServerConfig]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    entries = raw.get("servers", []) if isinstance(raw, dict) else raw
    configs = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("container"):
            configs.append(_server_from_dict(entry))
    return configs


def load_last_server(path: Path = DEFAULT_SERVERS) -> ServerConfig | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("last"), dict):
        return None
    if not raw["last"].get("container"):
        return None
    return _server_from_dict(raw["last"])


def upsert_server_config(config: ServerConfig, path: Path = DEFAULT_SERVERS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    key = (config.runtime, config.container)
    others = [
        existing
        for existing in load_server_configs(path)
        if (existing.runtime, existing.container) != key
    ]
    servers = [_server_to_dict(existing) for existing in others]
    servers.append(_server_to_dict(config))
    servers.sort(key=lambda item: (item["runtime"], item["container"]))
    payload = {"last": _server_to_dict(config), "servers": servers}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def builtin_kits() -> list[Kit]:
    return [
        Kit(name=name, items=items, source="builtin")
        for name, items in sorted(BUILTIN_KITS.items())
    ]


def load_custom_kits(path: Path = DEFAULT_KITS) -> dict[str, Kit]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    entries = raw.get("kits", []) if isinstance(raw, dict) else raw
    kits: dict[str, Kit] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        items = tuple(
            (str(pair[0]), int(pair[1]))
            for pair in entry.get("items", [])
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        )
        if items:
            kits[str(entry["name"])] = Kit(
                name=str(entry["name"]), items=items, source="custom"
            )
    return kits


def save_custom_kits(kits: dict[str, Kit], path: Path = DEFAULT_KITS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "kits": [
            {"name": kit.name, "items": [list(pair) for pair in kit.items]}
            for kit in (kits[name] for name in sorted(kits))
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def all_kits(path: Path = DEFAULT_KITS) -> dict[str, Kit]:
    """Custom kits override built-ins of the same name."""
    kits = {kit.name: kit for kit in builtin_kits()}
    kits.update(load_custom_kits(path))
    return kits


def parse_kit_items(spec: str) -> tuple[tuple[str, int], ...]:
    """Parse 'minecraft:stone 64, apple 5, diamond' into (item, qty) pairs."""
    items: list[tuple[str, int]] = []
    for chunk in re.split(r"[,\n]+", spec):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            item_id, qty = parts[0].strip(), int(parts[1])
        else:
            item_id, qty = chunk, 1
        if not item_id:
            continue
        if ":" not in item_id:
            item_id = f"minecraft:{item_id}"
        items.append((item_id, max(1, min(qty, 6400))))
    return tuple(items)


def restore_procedure(container: str) -> str:
    """Manual restore steps, shown in the Backups modal (no automated restore UI)."""
    return f"""\
Restore is manual — order matters.

  C={container}
  A=~/minecraft-backups/$C/<world>-<stamp>[-label].tar.gz

1. Stop the server (never restore under a live server).
     <runtime> stop $C
2. Find the host path backing /data:
     VOL=$(<runtime> inspect --format '{{{{range .Mounts}}}}{{{{if eq .Destination "/data"}}}}{{{{.Source}}}}{{{{end}}}}{{{{end}}}}' $C)
3. Move the old world aside, then extract at the PARENT of the world dir:
     mv "$VOL/<world>" "$VOL/<world>.bak.$(date +%s)"
     tar -xzf "$A" -C "$VOL"
4. Drop the stale lock vanilla refuses to load past:
     rm -f "$VOL/<world>/session.lock"
5. Start it:
     <runtime> start $C

Notes:
- Extract at /data (the parent), not into <world>/ — else you get <world>/<world>/.
- The archive's top dir is the level-name at backup time; keep it matching the
  server's current level-name (rename if the world was renamed).
- A live snapshot can capture session.lock; deleting it (and optionally
  level.dat_old) is what stops "world is locked".
- Inspect first: tar -tzf "$A" | head
- If /data is a rootful/system volume outside your home, extraction needs matching
  permissions — step 2 prints the path so you can check first.
"""


def rule_name(server: ServerConfig, canonical: str) -> str:
    """Resolve a canonical gamerule name to the spelling this server accepts.

    Gamerule spelling varies (camelCase in older/other builds, snake_case in newer
    ones) and `gamerule` with no argument is not a valid listing command, so each
    candidate is queried and the first that returns a value wins.
    """
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", canonical).lower()
    for candidate in dict.fromkeys((canonical, snake)):
        try:
            output = rcon(server, f"gamerule {candidate}")
        except Exception:
            continue
        lowered = output.lower()
        if "incorrect" in lowered or "unknown" in lowered or "usage" in lowered:
            continue
        if "game rule" in lowered or "currently set" in lowered:
            return candidate
    return canonical


@dataclass(frozen=True)
class _PropertyRecord:
    key: str
    start: int
    end: int
    newline: bytes


def _properties_text(raw: bytes) -> str:
    # Modern Minecraft uses UTF-8; older Java properties may contain Latin-1.
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _unescape_property(text: str) -> str:
    result: list[str] = []
    index = 0
    escapes = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}
    while index < len(text):
        char = text[index]
        index += 1
        if char != "\\":
            result.append(char)
            continue
        if index == len(text):
            raise ValueError("unfinished escape")
        char = text[index]
        index += 1
        if char == "u":
            digits = text[index:index + 4]
            if len(digits) != 4 or not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                raise ValueError("invalid Unicode escape")
            result.append(chr(int(digits, 16)))
            index += 4
        else:
            # Java also accepts unknown escapes, dropping the backslash.
            result.append(escapes.get(char, char))
    value = "".join(result)
    if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
        try:
            value = value.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
        except UnicodeDecodeError:
            raise ValueError("unpaired Unicode surrogate") from None
    if "\0" in value:
        raise ValueError("NUL character")
    return value


def _parse_properties(raw: bytes) -> tuple[dict[str, str], list[_PropertyRecord]]:
    """Parse Java properties, retaining byte ranges for lossless targeted edits."""
    # splitlines() also splits form feed, which Java treats as key whitespace.
    lines = re.findall(rb"[^\r\n]*(?:\r\n|\r|\n|$)", raw)
    if lines and not lines[-1]:
        lines.pop()
    encoding = "utf-8"
    try:
        raw.decode(encoding)
    except UnicodeDecodeError:
        encoding = "latin-1"
    values: dict[str, str] = {}
    records: list[_PropertyRecord] = []
    offset = 0
    index = 0
    while index < len(lines):
        start = offset
        line_number = index + 1
        physical = lines[index]
        offset += len(physical)
        index += 1
        text = physical.rstrip(b"\r\n").decode(encoding)
        logical = text.lstrip(" \t\f")
        if not logical or logical.startswith(("#", "!")):
            continue
        try:
            while (len(logical) - len(logical.rstrip("\\"))) % 2:
                if index == len(lines):
                    raise ValueError("unfinished continuation")
                physical = lines[index]
                offset += len(physical)
                index += 1
                logical = logical[:-1] + physical.rstrip(b"\r\n").decode(encoding).lstrip(" \t\f")
            key_end = 0
            while key_end < len(logical):
                char = logical[key_end]
                if char in "=:\t \f":
                    break
                key_end += 2 if char == "\\" else 1
            value_start = key_end
            if value_start < len(logical):
                if logical[value_start] in " \t\f":
                    while value_start < len(logical) and logical[value_start] in " \t\f":
                        value_start += 1
                if value_start < len(logical) and logical[value_start] in "=:":
                    value_start += 1
                while value_start < len(logical) and logical[value_start] in " \t\f":
                    value_start += 1
            key = _unescape_property(logical[:key_end])
            value = _unescape_property(logical[value_start:])
        except ValueError as exc:
            # Never include file contents: unknown properties can be credentials.
            raise ValueError(
                f"Malformed server.properties at line {line_number}: {exc}. "
                "Repair the file outside the TUI, then reload."
            ) from None
        newline = physical[len(physical.rstrip(b"\r\n")):]
        values[key] = value
        records.append(_PropertyRecord(key, start, offset, newline))
    return values, records


def _escape_property(value: str) -> str:
    result: list[str] = []
    escapes = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r", "\f": "\\f"}
    for char in value:
        if char in escapes:
            result.append(escapes[char])
        elif char in " =:#!":
            result.append("\\" + char)
        elif ord(char) < 32 or ord(char) > 126:
            # ASCII output is safe for both Java's legacy and UTF-8 readers.
            encoded = char.encode("utf-16-be")
            result.extend(
                f"\\u{int.from_bytes(encoded[index:index + 2], 'big'):04x}"
                for index in range(0, len(encoded), 2)
            )
        else:
            result.append(char)
    return "".join(result)


def _property_file_from_tar(
    buf: bytes, member_name: str = "server.properties"
) -> tuple[bytes, tarfile.TarInfo]:
    try:
        with tarfile.open(fileobj=io.BytesIO(buf), mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) != 1 or members[0].name.rsplit("/", 1)[-1] != member_name:
                raise ValueError("The copy stream does not contain exactly one properties file.")
            member = members[0]
            if not member.isfile() or member.issparse():
                raise ValueError("server.properties must be a regular file, not a link or special file.")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError("Cannot read the properties file from the copy stream.")
            return source.read(), member
    except (tarfile.TarError, OSError, EOFError):
        raise ValueError("Invalid properties copy stream; reload after checking the container data volume.") from None


def _settings_context(server: ServerConfig) -> tuple[str, str | None]:
    try:
        info = inspect_container(server.runtime, server.container)
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
        raise RuntimeError("Cannot inspect the container; check runtime access and reload server settings.") from None
    directory = server.working_dir or container_workdir(info) or "/data"
    path = directory.rstrip("/") + "/server.properties"
    blocked = None
    env = container_env(info)
    if is_minecraft_image(container_image(info)) and not (
        env.get("OVERRIDE_SERVER_PROPERTIES", "").casefold() == "false"
        or env.get("SKIP_SERVER_PROPERTIES", "").casefold() == "true"
    ):
        blocked = (
            "This image manages server.properties on startup. Add "
            "OVERRIDE_SERVER_PROPERTIES=false to the deployment and recreate "
            "the container retaining its data volume before saving settings or starting/restarting it."
        )
    return path, blocked


def _read_settings_file(server: ServerConfig, path: str) -> tuple[bytes, tarfile.TarInfo]:
    try:
        buf = _copy_path_stream(server, path)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        raise RuntimeError(
            "Cannot read server.properties using container cp; check the data path, "
            "file permissions and runtime access, then reload."
        ) from None
    return _property_file_from_tar(buf)


def _podman_properties_owner(server: ServerConfig, path: str) -> tuple[int, int]:
    """Podman cp normalizes tar ownership, so query the actual file separately."""
    try:
        info = inspect_container(server.runtime, server.container)
        binary = runtime_binary(server.runtime)
        if (info.get("State") or {}).get("Status") == "running":
            command = [binary, "exec", server.container, "stat", "-c", "%u %g", "--", path]
        else:
            # In the default rootless namespace, volume owners match container IDs.
            # Refuse custom mappings rather than silently assign a different owner.
            if (info.get("HostConfig") or {}).get("UsernsMode") not in ("", "private", None):
                raise ValueError("custom user namespace")
            mounts = [
                mount for mount in info.get("Mounts", [])
                if path.startswith(str(mount.get("Destination", "")).rstrip("/") + "/")
                and mount.get("Source")
            ]
            mount = max(mounts, key=lambda value: len(value["Destination"]))
            source = str(Path(mount["Source"]) / path[len(mount["Destination"]):].lstrip("/"))
            command = [binary, "unshare", "stat", "-c", "%u %g", "--", source]
        uid, gid = run_process(command, timeout=20).split()
        return int(uid), int(gid)
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        raise RuntimeError(
            "Cannot determine properties file ownership safely. For stopped Podman "
            "containers, use a persistent data mount and the default user namespace, "
            "or start the container before editing. No settings were written."
        ) from None


def load_server_settings(server: ServerConfig) -> ServerSettings:
    path, blocked = _settings_context(server)
    raw, _ = _read_settings_file(server, path)
    values, _ = _parse_properties(raw)
    return ServerSettings(path, raw, values, blocked)


def _validate_setting(setting: ServerSetting, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{setting.label} must be text.")
    if setting.kind == "bool" and value not in ("true", "false"):
        raise ValueError(f"{setting.label} must be true or false.")
    if setting.kind == "choice" and value not in setting.choices:
        raise ValueError(f"{setting.label} must be one of: {', '.join(setting.choices)}.")
    if setting.kind == "int":
        if not re.fullmatch(r"[0-9]{1,10}", value):
            raise ValueError(f"{setting.label} must be a whole number.")
        number = int(value)
        if number < setting.minimum or number > setting.maximum:
            raise ValueError(f"{setting.label} must be between {setting.minimum} and {setting.maximum}.")
    if setting.kind == "text":
        if any((ord(char) < 32 and char not in "\t\n\r") or 127 <= ord(char) <= 159
               or 0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("MOTD cannot contain NUL, control characters or unpaired Unicode surrogates.")
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    return value


def save_server_settings(
    server: ServerConfig, snapshot: ServerSettings, changes: dict[str, str]
) -> ServerSettings:
    """Apply validated edits; restart is manual.

    cp has no atomic compare-and-swap: an external writer can still race between
    the final read and extraction. Raw comparison catches edits before that gap.
    """
    values, records = _parse_properties(snapshot.raw)
    allowed = {setting.key: setting for setting in SETTINGS}
    updates: dict[str, str] = {}
    for key, value in changes.items():
        if key not in allowed:
            raise ValueError("Only the listed server settings may be changed.")
        if key not in values:
            raise ValueError(f"{allowed[key].label} is absent from this server.properties; reload without adding defaults.")
        validated = _validate_setting(allowed[key], value)
        if validated != values[key]:
            updates[key] = validated
    if not updates:
        return snapshot
    path, blocked = _settings_context(server)
    if path != snapshot.path:
        raise RuntimeError("The server data path changed; reload settings before saving.")
    if blocked:
        raise RuntimeError(blocked)
    last_records = {record.key: record for record in records}
    edited = sorted((last_records[key] for key in updates), key=lambda record: record.start)
    chunks: list[bytes] = []
    offset = 0
    for record in edited:
        chunks.append(snapshot.raw[offset:record.start])
        line = f"{_escape_property(record.key)}={_escape_property(updates[record.key])}"
        chunks.append(line.encode("ascii") + record.newline)
        offset = record.end
    chunks.append(snapshot.raw[offset:])
    payload = b"".join(chunks)
    latest, original = _read_settings_file(server, path)
    if latest != snapshot.raw:
        raise RuntimeError("server.properties changed outside the TUI; reload settings before saving.")
    if original.mode & 0o222 == 0:
        raise RuntimeError("server.properties is read-only; correct its permissions outside the TUI, then reload.")
    target = tarfile.TarInfo("server.properties")
    for attribute in ("mode", "uid", "gid", "uname", "gname", "mtime"):
        setattr(target, attribute, getattr(original, attribute))
    if server.runtime == "podman":
        target.uid, target.gid = _podman_properties_owner(server, path)
        target.uname = target.gname = ""
    target.size = len(payload)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.addfile(target, io.BytesIO(payload))
    try:
        proc = subprocess.run(
            [runtime_binary(server.runtime), "cp",
             "--archive=false" if server.runtime == "podman" else "-a",
             "-", f"{server.container}:{path.rsplit('/', 1)[0] or '/'}"],
            input=buffer.getvalue(), capture_output=True, timeout=120,
        )
    except (RuntimeError, OSError, subprocess.SubprocessError):
        raise RuntimeError(
            "Cannot write server.properties using container cp; check volume permissions "
            "and read-only mounts. Reload before retrying: the copy may have partially completed."
        ) from None
    if proc.returncode:
        raise RuntimeError(
            "Cannot write server.properties using container cp; check volume permissions "
            "and read-only mounts. Reload before retrying: the copy may have partially completed."
        )
    saved = load_server_settings(server)
    if any(saved.values.get(key) != value for key, value in updates.items()):
        raise RuntimeError("The copied server.properties did not retain the changes; another process may manage it. Reload settings.")
    return saved


def _properties_from_tar(buf: bytes, member_name: str = "server.properties") -> dict:
    """Best-effort decoded metadata; malformed files must not break status views."""
    try:
        raw, _ = _property_file_from_tar(buf, member_name)
        values, _ = _parse_properties(raw)
    except ValueError:
        return {}
    return {**values, "_raw": _properties_text(raw)}


def container_level_name(server: ServerConfig) -> str:
    """Read level-name from server.properties, defaulting to 'world'."""
    try:
        buf = _copy_path_stream(server, "/data/server.properties")
    except (RuntimeError, subprocess.SubprocessError):
        return "world"
    value = _properties_from_tar(buf).get("level-name", "")
    return value or "world"


def _copy_path_stream(server: ServerConfig, path: str) -> bytes:
    """Stream a container path as a tar archive to memory (runtime-generic)."""
    proc = subprocess.run(
        [runtime_binary(server.runtime), "cp", f"{server.container}:{path}", "-"],
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise RuntimeError(detail or f"{server.runtime} cp exited {proc.returncode}")
    return proc.stdout


def _container_has_path(server: ServerConfig, path: str) -> bool:
    try:
        return len(_copy_path_stream(server, path)) > 0
    except (RuntimeError, subprocess.SubprocessError):
        return False


def discover_worlds(server: ServerConfig) -> list[str]:
    """Find world directories inside the container's data volume.

    Running containers are listed with a shell; stopped containers (where exec is
    impossible) fall back to the level-name from server.properties, confirmed by
    probing for its level.dat.
    """
    try:
        listing = run_process(
            [
                runtime_binary(server.runtime),
                "exec",
                server.container,
                "sh",
                "-c",
                'for d in /data/*/; do [ -f "${d}level.dat" ] && basename "$d"; done',
            ],
            timeout=20,
        )
    except (RuntimeError, subprocess.SubprocessError):
        listing = ""
    worlds = [line.strip() for line in listing.splitlines() if line.strip()]
    if worlds:
        return worlds

    level = container_level_name(server)
    if level and _container_has_path(server, f"/data/{level}/level.dat"):
        return [level]
    return []


def default_backup_dir(container: str, base: Path = BACKUP_DIR) -> Path:
    return base / safe_filename(container)


def backup_path(
    server: ServerConfig, *, level_name: str, when: datetime | None = None, label: str = ""
) -> Path:
    stamp = (when or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{safe_filename(label)}" if label else ""
    directory = default_backup_dir(server.container)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{safe_filename(level_name)}-{stamp}{suffix}.tar.gz"


def backup_world(
    server: ServerConfig,
    *,
    level_name: str = "world",
    label: str = "",
    on_progress: Callable[[str], None] | None = None,
    timeout: int = 900,
) -> Path:
    """Back up one world directory (all dimensions) to a gzipped tar.

    Every world lives in a single directory (level-name); nether/end data is
    nested under it, so one archive captures the full world. The path is streamed
    out with `<runtime> cp ... -`, which works for running and stopped containers
    alike and needs no shell inside the container beyond the initial level-name read.
    """
    log = on_progress or (lambda _message: None)
    destination = backup_path(server, level_name=level_name, label=label)
    source = f"/data/{level_name}"

    log(f"Backing up {source} from {server.container} …")
    argv = [
        runtime_binary(server.runtime),
        "cp",
        f"{server.container}:{source}",
        "-",
    ]
    with destination.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6) as gz:
            proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            assert proc.stdout is not None
            while chunk := proc.stdout.read(1 << 20):
                gz.write(chunk)
            _, stderr = proc.communicate(timeout=timeout)
    if proc.returncode != 0:
        destination.unlink(missing_ok=True)
        detail = (stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(detail or f"{server.runtime} cp exited {proc.returncode}")

    size = destination.stat().st_size
    log(f"Wrote {destination.name} ({size / 1e6:.1f} MB)")
    return destination


def prune_backups(directory: Path, keep: int) -> list[Path]:
    """Delete oldest backups beyond the newest `keep`; returns the deleted paths.

    `keep` <= 0 disables pruning (retain everything).
    """
    if keep <= 0:
        return []
    archives = sorted(directory.glob("*.tar.gz"))
    removed = []
    for path in archives[: max(0, len(archives) - keep)]:
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def list_backups(container: str, base: Path = BACKUP_DIR) -> list[Path]:
    directory = default_backup_dir(container, base)
    if not directory.exists():
        return []
    # Names carry a sortable UTC timestamp, so newest-first is name-descending.
    return sorted(directory.glob("*.tar.gz"), reverse=True)
