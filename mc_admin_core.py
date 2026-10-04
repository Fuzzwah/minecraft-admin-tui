from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_CONTAINER = "mc_do_not_die"
APP_DIR = Path.home() / ".config" / "mc-admin-tui"
CACHE_DIR = Path.home() / ".cache" / "mc-admin-tui"
DEFAULT_ITEM_CACHE = CACHE_DIR / "items.json"
DEFAULT_LOCATIONS = APP_DIR / "locations.json"

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
class Config:
    container: str
    registry: Path | None = None


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


def rcon(container: str, command: str, *, timeout: int = 8) -> str:
    return run_process(
        ["podman", "exec", "-i", container, "rcon-cli", command],
        timeout=timeout,
    )


def podman(container: str, *args: str, timeout: int = 30) -> str:
    return run_process(["podman", *args, container], timeout=timeout)


def container_status(container: str) -> str:
    return run_process(
        [
            "podman",
            "inspect",
            "--format",
            "{{.State.Status}} | started {{.State.StartedAt}}",
            container,
        ]
    )


def tail_logs(container: str, lines: int = 100) -> str:
    return run_process(["podman", "logs", "--tail", str(lines), container], timeout=15)


def restart_container(container: str) -> str:
    return run_process(["podman", "restart", container], timeout=90)


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


def query_player_snapshot(container: str, player: str) -> PlayerSnapshot:
    def get(path: str) -> str:
        return rcon(container, f"data get entity {player} {path}")

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
