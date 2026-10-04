from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

APP_DIR = Path.home() / ".config" / "mc-admin-tui"
CACHE_DIR = Path.home() / ".cache" / "mc-admin-tui"
DEFAULT_ITEM_CACHE = CACHE_DIR / "items.json"
DEFAULT_LOCATIONS = APP_DIR / "locations.json"
LEGACY_LOCATIONS = APP_DIR / "locations.legacy.json"
DEFAULT_SERVERS = APP_DIR / "servers.json"

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
    command = (
        "for f in /data/.rcon-cli.yaml /data/.rcon-cli.env "
        '$HOME/.rcon-cli.yaml $HOME/.rcon-cli.env; do '
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
    for line in raw.splitlines():
        if line.startswith("###"):
            if password is not None and working_dir is None:
                working_dir = str(Path(line[3:].strip()).parent)
            continue
        key, _, value = line.partition("=")
        if not value and ":" in line:
            key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key == "password" and value and password is None:
            password = value
        elif key == "port" and value.isdigit() and port is None:
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


def restart_container(server: ServerConfig) -> str:
    return container_command(server, "restart", timeout=90)


def human_name(item_id: str) -> str:
    namespace, _, path = item_id.partition(":")
    words = path.replace("_", " ").replace("/", " / ")
    return words if namespace == "minecraft" else f"{words}  [{namespace}]"


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


def load_items(registry: Path | None) -> tuple[list[str], str]:
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

    return sorted(FALLBACK_ITEMS), "built-in starter catalogue"


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
