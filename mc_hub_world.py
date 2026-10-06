"""Pure command/data-pack generation for the vanilla Minecraft 26.3 hub.

Stable IDs 1..128 occupy separate 16x16 chunks: sixteen columns, eight rows,
starting at z=16. The walking floor is y=63 and spawn is (8, 64, 8).
Managed signs are waxed; the oak sign at each bay's (12, 64, 5) offset is
user-owned and is never overwritten, including during an explicit rebuild.

Commands require loaded overworld chunks. Before executing the remainder of
build_commands, deployment must issue its initial forceload command and wait
until every chunk in FORCELOAD_BOUNDS is loaded (execute if loaded). A standalone
bay_commands likewise needs its chunk loaded after its first command. Loading
is asynchronous; pure generation cannot wait for the Minecraft server.

The controller authenticates UUIDs before assigning the hub_admin player tag.
Only that tag permits temporary survival in the small label-edit area; everyone
else remains in adventure. Blocks cannot be mined even in that editing area.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

MAX_SLOTS = 128
DATA_PACK_FORMAT = 121
SPAWN = (8, 64, 8)
FORCELOAD_BOUNDS = (0, 0, 255, 143)


def _json(value: object) -> str:
    # JSON is a subset of modern SNBT. Escaping prevents command/newline injection
    # and keeps even control characters inside one quoted string token.
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _id(slot_id: int) -> int:
    if type(slot_id) is not int or not 1 <= slot_id <= MAX_SLOTS:
        raise ValueError(f"slot id must be an integer from 1 to {MAX_SLOTS}")
    return slot_id


def _slot(slot: Mapping[str, object]) -> tuple[int, str]:
    slot_id = _id(slot["id"])
    name = slot["name"]
    if not isinstance(name, str) or not name:
        raise ValueError("slot name must be a nonempty string")
    return slot_id, name


def _slots(slots: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    result = list(slots)
    ids: set[int] = set()
    names: set[str] = set()
    for slot in result:
        slot_id, name = _slot(slot)
        if slot_id in ids or name in names:
            raise ValueError("slot ids and container names must be unique")
        ids.add(slot_id)
        names.add(name)
    return sorted(result, key=lambda slot: slot["id"])


def bay_origin(slot_id: int) -> tuple[int, int]:
    """Return the inclusive x/z corner of a stable bay's chunk."""
    index = _id(slot_id) - 1
    return (index % 16) * 16, 16 + (index // 16) * 16


def portal_selector(slot_id: int) -> str:
    """Return selector arguments (without @a or brackets) for the open arch."""
    x, z = bay_origin(slot_id)
    return f"x={x + 6},y=64,z={z + 10},dx=3,dy=3,dz=2"


def user_sign_position(slot_id: int) -> tuple[int, int, int]:
    """Return the reserved, unwaxed owner-label position."""
    x, z = bay_origin(slot_id)
    return x + 12, 64, z + 5


def _component(text: str, color: str = "black", command: str = "") -> dict:
    component = {"text": text, "color": color}
    if command:
        component["click_event"] = {"action": "run_command", "command": command}
    return component


def _sign_data(lines: Iterable[str], *, color: str = "black", command: str = "",
               waxed: bool = True) -> str:
    text = list(lines)
    if len(text) > 4:
        raise ValueError("signs have at most four lines")
    text += [""] * (4 - len(text))
    messages = [_component(line, color, command if index == 0 else "")
                for index, line in enumerate(text)]
    side = {"messages": messages, "color": "black", "has_glowing_text": waxed}
    return _json({"is_waxed": waxed, "allow_op_features": bool(command),
                  "front_text": side, "back_text": side})


def _sign(x: int, y: int, z: int, lines: Iterable[str], **options) -> str:
    return (f"setblock {x} {y} {z} minecraft:oak_sign[rotation=8]"
            + _sign_data(lines, **options))


def _display_lines(text: str, count: int = 4, width: int = 16) -> list[str]:
    """Keep names legible on a sign; elide only beyond its physical capacity."""
    lines = [text[start:start + width] for start in range(0, count * width, width)]
    if len(text) > count * width:
        lines[-1] = lines[-1][:-3] + "..."
    return lines


def status_commands(slot: Mapping[str, object], state: str, detail: str = "",
                    players: int = 0, controllable: bool = False,
                    joinable: bool = False) -> list[str]:
    """Update only managed status/control signs and a powered status lamp.

    The signs are a convenience, not authorization: the controller validates
    live player UUIDs and explicit target policy for every trigger request.
    Online lamps indicate a running destination; joinability is shown separately.
    """
    slot_id, _ = _slot(slot)
    if not isinstance(state, str) or not isinstance(detail, str):
        raise ValueError("state and detail must be strings")
    if type(players) is not int or players < 0:
        raise ValueError("players must be a nonnegative integer")
    x, z = bay_origin(slot_id)
    color = {"online": "dark_green", "offline": "dark_red", "starting": "gold",
             "stopping": "gold", "error": "dark_red", "missing": "dark_gray"}.get(
                 state.casefold(), "dark_gray")
    commands = [
        f"data merge block {x + 9} 65 {z + 8} " + _sign_data(
            [state[:16], f"{players} players", detail[:16],
             "Walk in to join" if joinable else "Not joinable"], color=color),
    ]
    for offset, action, label, sign_color in (
        (3, "start", "START", "dark_green"), (12, "stop", "STOP", "dark_red")
    ):
        commands.append(f"data merge block {x + offset} 65 {z + 8} " + _sign_data(
            [label if controllable else "View only", "Admin only" if controllable else "No controls",
             f"Bay {slot_id}", "Right click" if controllable else ""],
            color=sign_color if controllable else "dark_gray",
            command=f"trigger hub_{action} set {slot_id}" if controllable else ""))
    online = state.casefold() == "online"
    commands.extend([
        f"setblock {x + 7} 63 {z + 13} minecraft:"
        + ("redstone_block" if online else "polished_andesite"),
        f"setblock {x + 7} 64 {z + 13} minecraft:redstone_lamp[lit="
        + ("true]" if online else "false]"),
    ])
    return commands


def bay_commands(slot: Mapping[str, object]) -> list[str]:
    """Create one bay without changing adjacent bays or its existing owner sign."""
    slot_id, name = _slot(slot)
    x, z = bay_origin(slot_id)
    commands = [
        f"forceload add {x} {z} {x + 15} {z + 15}",
        f"fill {x} 63 {z} {x + 15} 63 {z + 15} minecraft:stone_bricks",
        f"fill {x + 3} 63 {z + 3} {x + 12} 63 {z + 14} minecraft:polished_andesite",
        f"fill {x + 6} 63 {z + 9} {x + 9} 63 {z + 12} minecraft:purple_concrete",
        f"fill {x + 5} 64 {z + 11} {x + 5} 68 {z + 11} minecraft:crying_obsidian",
        f"fill {x + 10} 64 {z + 11} {x + 10} 68 {z + 11} minecraft:crying_obsidian",
        f"fill {x + 5} 69 {z + 11} {x + 10} 69 {z + 11} minecraft:crying_obsidian",
        f"setblock {x + 5} 68 {z + 10} minecraft:sea_lantern",
        f"setblock {x + 10} 68 {z + 10} minecraft:sea_lantern",
    ]
    # Pedestals and managed signs are outside both portal and label-edit volumes.
    for offset in (3, 7, 9, 12):
        commands.append(f"setblock {x + offset} 64 {z + 8} minecraft:stone_bricks")
    commands.extend([
        _sign(x + 7, 65, z + 8, _display_lines(name)),
        _sign(x + 9, 65, z + 8, ["Offline", "0 players", "", "Not joinable"], color="dark_red"),
        _sign(x + 3, 65, z + 8, ["View only", "No controls", f"Bay {slot_id}", ""]),
        _sign(x + 12, 65, z + 8, ["View only", "No controls", f"Bay {slot_id}", ""]),
        f"setblock {x + 11} 64 {z + 5} minecraft:stone_bricks",
        _sign(x + 11, 65, z + 5, ["Owner label ->", "Admins: stand", "here to edit", "blank oak sign"]),
        # 'keep' preserves any existing block and its block-entity text verbatim.
        _sign(x + 12, 64, z + 5, [], waxed=False) + " keep",
    ])
    commands.extend(status_commands(slot, "Offline"))
    return commands


def build_commands(slots: Iterable[Mapping[str, object]]) -> list[str]:
    """Generate the full platform/plaza and requested stable bays explicitly.

    All 128 bay chunks have floor and stay loaded, even if no containers have
    been discovered yet. No fill operates above the walking floor in a label
    position, so full regeneration preserves the user-owned signs as well.
    """
    ordered = _slots(slots)
    commands = ["forceload add 0 0 255 143"]
    # Nine separate fills stay below the default max_block_modifications=32768.
    for z in range(0, 144, 16):
        commands.append(f"fill 0 63 {z} 255 63 {z + 15} minecraft:stone_bricks")
    commands.extend([
        "fill 0 64 0 255 67 0 minecraft:barrier",
        "fill 0 64 143 255 67 143 minecraft:barrier",
        "fill 0 64 1 0 67 142 minecraft:barrier",
        "fill 255 64 1 255 67 142 minecraft:barrier",
        "fill 4 63 4 12 63 12 minecraft:quartz_block",
        "setblock 5 63 5 minecraft:sea_lantern",
        "setblock 11 63 5 minecraft:sea_lantern",
        "setblock 5 63 11 minecraft:sea_lantern",
        "setblock 11 63 11 minecraft:sea_lantern",
        _sign(8, 64, 12, ["SERVER HUB", "Walk into arches", "to request join", "See bay status"], color="dark_blue"),
        _sign(11, 64, 12, ["Waxed controls", "Admins only", "Owner labels:", "stand by sign"]),
        "setworldspawn 8 64 8",
        "spawnpoint @a 8 64 8",
    ])
    for slot in ordered:
        commands.extend(bay_commands(slot))
    return commands


def datapack_files(slots: Iterable[Mapping[str, object]]) -> dict[str, str]:
    """Return UTF-8 file contents relative to a data-pack root (format 121.0).

    Requests are queued in each player's scores until consumed by RCON. A
    pending request cannot be overwritten by walking through another portal.
    No function executes host commands, starts a container or grants OP.
    """
    ordered = _slots(slots)
    load = [
        "scoreboard objectives add hub_join trigger",
        "scoreboard objectives add hub_start trigger",
        "scoreboard objectives add hub_stop trigger",
        "scoreboard objectives add hub_cooldown dummy",
        "gamerule minecraft:advance_time false",
        "gamerule minecraft:advance_weather false",
        "gamerule minecraft:spawn_mobs false",
        "gamerule minecraft:mob_griefing false",
        "gamerule minecraft:projectiles_can_break_blocks false",
        "gamerule minecraft:fire_spread_radius_around_player 0",
        "gamerule minecraft:pvp false",
        "gamerule minecraft:fall_damage false",
        "gamerule minecraft:fire_damage false",
        "gamerule minecraft:drowning_damage false",
        "gamerule minecraft:freeze_damage false",
        "gamerule minecraft:keep_inventory true",
        "gamerule minecraft:respawn_radius 0",
        "gamerule minecraft:send_command_feedback false",
        "time set day",
        "weather clear",
        "defaultgamemode adventure",
    ]
    tick = [
        "tag @a remove hub_label_editor",
        "execute as @a run attribute @s minecraft:block_break_speed base set 0",
        "effect give @a minecraft:resistance 2 4 true",
        "effect give @a minecraft:saturation 2 0 true",
        "effect give @a minecraft:regeneration 2 4 true",
        "spawnpoint @a 8 64 8",
        "scoreboard players add @a hub_cooldown 0",
        "scoreboard players remove @a[scores={hub_cooldown=1..}] hub_cooldown 1",
    ]
    for action in ("join", "start", "stop"):
        tick.extend([
            f"scoreboard players add @a hub_{action} 0",
            f"scoreboard players enable @a[scores={{hub_{action}=0}}] hub_{action}",
        ])
    files = {
        "pack.mcmeta": _json({"pack": {"description": "Vanilla server-selection hub (Minecraft 26.3)",
                                     "min_format": [DATA_PACK_FORMAT, 0],
                                     "max_format": [DATA_PACK_FORMAT, 0]}}) + "\n",
        "data/minecraft/tags/function/load.json": _json({"values": ["hub:load"]}) + "\n",
        "data/minecraft/tags/function/tick.json": _json({"values": ["hub:tick"]}) + "\n",
        "data/hub/function/load.mcfunction": "\n".join(load) + "\n",
    }
    for slot in ordered:
        slot_id, _ = _slot(slot)
        x, z = bay_origin(slot_id)
        tick.append(
            "execute in minecraft:overworld as @a[" + portal_selector(slot_id)
            + ",scores={hub_join=0,hub_cooldown=0}] run function hub:enter/" + str(slot_id))
        tick.append(
            f"execute in minecraft:overworld run tag @a[tag=hub_admin,"
            f"x={x + 11},y=64,z={z + 4},dx=2,dy=2,dz=2] add hub_label_editor")
        files[f"data/hub/function/enter/{slot_id}.mcfunction"] = (
            f"scoreboard players set @s hub_join {slot_id}\n"
            "scoreboard players set @s hub_cooldown 100\n")
    tick.extend([
        "gamemode adventure @a[tag=!hub_label_editor,gamemode=!adventure]",
        "gamemode survival @a[tag=hub_label_editor,gamemode=!survival]",
    ])
    files["data/hub/function/tick.mcfunction"] = "\n".join(tick) + "\n"
    return files
