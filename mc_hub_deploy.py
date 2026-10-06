"""Deploy an isolated vanilla 26.3 hub without granting Minecraft host access."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tarfile
import time
import uuid

from mc_admin_core import (
    ServerConfig, _copy_path_stream, _parse_properties, _podman_properties_owner,
    _property_file_from_tar, container_env, container_image, container_name,
    inspect_container, is_minecraft_image, list_containers, load_server_settings,
    run_process,
)

DEFAULT_CONFIG = Path.home() / ".config/mc-admin-tui/hub.json"
DEFAULT_STATE = Path.home() / ".local/share/mc-admin-tui/hub-state.json"


def private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _extract_archive(container: str, directory: str, buffer: io.BytesIO) -> None:
    info = inspect_container("podman", container)
    if (info.get("State") or {}).get("Status") == "running":
        command = ["podman", "exec", "-i", container, "tar", "-xpf", "-", "-C", directory]
    else:
        if (info.get("HostConfig") or {}).get("UsernsMode") not in ("", "private", None):
            raise RuntimeError("Cannot write data in a custom Podman user namespace")
        mount = next((mount for mount in info.get("Mounts", [])
                      if mount.get("Destination") == directory and mount.get("Source")), None)
        if mount is None:
            raise RuntimeError("Stopped-container writes require an exact persistent data mount")
        command = ["podman", "unshare", "tar", "-xpf", "-", "-C", mount["Source"]]
    result = subprocess.run(command, input=buffer.getvalue(), capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip() or "Cannot copy hub data")


def copy_files(container: str, directory: str, files: dict[str, str]) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Archive paths must be relative and cannot traverse directories")
            member = tarfile.TarInfo(relative.as_posix())
            member.mode = 0o644
            member.uid = member.gid = 1000
            body = content.encode("utf-8")
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    _extract_archive(container, directory, buffer)


def install_datapack(hub_name: str, files: dict[str, str]) -> None:
    info = inspect_container("podman", hub_name)
    if (info.get("State") or {}).get("Status") != "running":
        raise RuntimeError("Hub must be running before installing its datapack")
    settings = load_server_settings(ServerConfig("podman", hub_name))
    level = settings.values.get("level-name", "world")
    if not level or "/" in level or "\\" in level or level in (".", ".."):
        raise RuntimeError("Unsafe hub world directory")
    destination = f"/data/{level}/datapacks/hub"
    run_process(["podman", "exec", hub_name, "mkdir", "-p", destination])
    copy_files(hub_name, destination, files)


def configure_transfer_target(name: str) -> None:
    """Enable incoming transfers only while a trusted, authenticated target is stopped."""
    info = inspect_container("podman", name)
    env = container_env(info)
    if (info.get("State") or {}).get("Status") not in ("exited", "created", "stopped"):
        raise RuntimeError("Stop the destination before enabling incoming transfers")
    if not is_minecraft_image(container_image(info)) or env.get("VERSION") != "26.3" or env.get("TYPE", "VANILLA").upper() != "VANILLA":
        raise RuntimeError("Destination must be a vanilla Minecraft 26.3 container")
    server = ServerConfig("podman", name)
    snapshot = load_server_settings(server)
    if snapshot.write_blocked:
        raise RuntimeError(snapshot.write_blocked)
    if snapshot.values.get("online-mode") != "true":
        raise RuntimeError("Destination must authenticate players with online-mode=true")
    if snapshot.values.get("accepts-transfers") == "true":
        return
    values, records = _parse_properties(snapshot.raw)
    if "accepts-transfers" not in values:
        raise RuntimeError("Destination is missing the 26.3 accepts-transfers property")
    record = next(record for record in reversed(records) if record.key == "accepts-transfers")
    body = snapshot.raw[:record.start] + b"accepts-transfers=true" + record.newline + snapshot.raw[record.end:]
    latest = _copy_path_stream(server, snapshot.path)
    original_body, original = _property_file_from_tar(latest)
    if original_body != snapshot.raw:
        raise RuntimeError("Destination properties changed while preparing transfers; try again")
    if original.mode & 0o222 == 0:
        raise RuntimeError("Destination properties are read-only")
    uid, gid = _podman_properties_owner(server, snapshot.path)
    member = tarfile.TarInfo("server.properties")
    member.uid, member.gid, member.mode, member.mtime = uid, gid, original.mode, original.mtime
    member.size = len(body)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.addfile(member, io.BytesIO(body))
    # Extraction has no compare-and-swap operation. The controller serializes
    # its own lifecycle requests, but an external writer can still race.
    _extract_archive(name, "/data", buffer)
    if load_server_settings(server).values.get("accepts-transfers") != "true":
        raise RuntimeError("Destination did not retain its incoming-transfer setting")


def ensure_port_available(address: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((address, port))


def wait_for_hub(config: dict, timeout: int = 240):
    from mc_hub import RconClient
    end = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < end:
        client = RconClient(config["rcon_host"], config["rcon_port"], config["rcon_password"])
        try:
            result = client.command("list")
            if "players online" in result:
                return client
        except (OSError, RuntimeError) as error:
            last_error = error
        client.close()
        time.sleep(2)
    raise RuntimeError(f"Hub did not become ready: {last_error}")


def checked_command(client, command: str) -> str:
    try:
        result = client.command(command)
    except (OSError, RuntimeError) as error:
        raise RuntimeError(f"Hub command transport failed: {command}: {error}") from error
    failures = ("Unknown or incomplete command", "Incorrect argument", "Expected ",
                "Unclosed ", "Invalid ", "not loaded", "outside of the world", "Too many blocks")
    if any(failure.lower() in result.lower() for failure in failures):
        raise RuntimeError(f"Hub command failed: {command}\n{result}")
    return result


def wait_for_chunks(client, bounds: tuple[int, int, int, int], timeout: int = 180) -> None:
    pending = {(x, z) for x in range(bounds[0], bounds[2] + 1, 16)
               for z in range(bounds[1], bounds[3] + 1, 16)}
    end = time.monotonic() + timeout
    while pending and time.monotonic() < end:
        for x, z in tuple(pending):
            result = client.command(f"execute if loaded {x} 64 {z}")
            if "Test passed" in result:
                pending.remove((x, z))
        if pending:
            time.sleep(1)
    if pending:
        raise RuntimeError(f"Hub generation timed out waiting for {len(pending)} chunks")


def install_service(config_path: Path, state_path: Path) -> None:
    service_dir = Path.home() / ".config/systemd/user"
    service_dir.mkdir(parents=True, exist_ok=True)
    checkout = Path(__file__).resolve().parent
    # systemd parses quoted arguments, not a shell; double percent escapes specifiers.
    def quote(value) -> str:
        return json.dumps(str(value).replace("%", "%%"))
    text = (
        "[Unit]\nDescription=Minecraft 26.3 hub controller\nAfter=network-online.target\n\n"
        "[Service]\nType=simple\n"
        f"WorkingDirectory={str(checkout).replace('%', '%%')}\n"
        f"ExecStart=/usr/bin/python3 -m mc_hub --config {quote(config_path)} --state {quote(state_path)} --start-hub run\n"
        # The container is independent of the controller. Preserve its rootless
        # port forwarder when restarting just the controller.
        "KillMode=process\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=30\n\n"
        "[Install]\nWantedBy=default.target\n"
    )
    (service_dir / "mc-hub-controller.service").write_text(text)
    run_process(["systemctl", "--user", "daemon-reload"])
    run_process(["systemctl", "--user", "enable", "--now", "mc-hub-controller.service"], timeout=30)


def deploy(args) -> None:
    from mc_hub_world import FORCELOAD_BOUNDS, SPAWN, build_commands, datapack_files
    config_path, state_path = args.config.expanduser().resolve(), args.state.expanduser().resolve()
    admins = []
    for entry in args.admin:
        identifier, separator, name = entry.partition(":")
        if not separator or not name or not all(c.isalnum() or c == "_" for c in name) or len(name) > 16:
            raise ValueError("--admin must be UUID:PlayerName")
        admins.append({"uuid": str(uuid.UUID(identifier)), "name": name})
    if not admins:
        raise ValueError("At least one authenticated administrator is required")
    if config_path.exists():
        config = json.loads(config_path.read_text())
        if config["hub"]["container"] != args.container:
            raise RuntimeError("Existing hub config names a different container")
        if not args.rebuild:
            raise RuntimeError("Hub config already exists; use --rebuild to explicitly regenerate managed world geometry")
        state = json.loads(state_path.read_text()) if state_path.exists() else {"slots": {}, "generated": []}
    else:
        if any(container_name(c) == args.container for c in list_containers("podman")):
            raise RuntimeError("Container already exists without this hub config; refusing to adopt it")
        ensure_port_available(args.bind_address, args.port)
        ensure_port_available("127.0.0.1", args.rcon_port)
        config = {"hub": {"container": args.container, "rcon_host": "127.0.0.1", "rcon_port": args.rcon_port,
                          "rcon_password": secrets.token_urlsafe(32), "game_port": args.port},
                  "address": args.address, "admins": [admin["uuid"] for admin in admins], "targets": {},
                  "features": {"community_messages": True},
                  "poll_seconds": 1, "scan_seconds": 10}
        state = {"slots": {}, "generated": []}
        volume = args.container + "_data"
        run_process(["podman", "volume", "create", volume])
        image = args.image
        generator = json.dumps({"layers": [{"block": "minecraft:bedrock", "height": 1},
            {"block": "minecraft:stone", "height": 124}, {"block": "minecraft:dirt", "height": 2},
            {"block": "minecraft:grass_block", "height": 1}], "biome": "minecraft:plains"})
        env = {"EULA": "TRUE", "TYPE": "VANILLA", "VERSION": "26.3", "MEMORY": "1G",
               "SKIP_SERVER_PROPERTIES": "true", "OVERRIDE_SERVER_PROPERTIES": "false",
               "ENABLE_RCON": "true", "RCON_PASSWORD": config["hub"]["rcon_password"]}
        properties = {
            "level-name": "world", "level-type": "minecraft:flat", "generator-settings": generator,
            "gamemode": "adventure", "force-gamemode": "true", "difficulty": "peaceful", "pvp": "false",
            "online-mode": "true", "enable-rcon": "true", "rcon.password": config["hub"]["rcon_password"],
            "rcon.port": "25575", "server-port": "25565", "white-list": "true", "enforce-whitelist": "true",
            "accepts-transfers": "true", "motd": "Server Control Hub - Minecraft 26.3",
            "spawn-protection": "0", "max-players": "20", "view-distance": "8", "simulation-distance": "4",
        }
        command = ["podman", "create", "--name", args.container, "--restart", "unless-stopped", "--stop-timeout", "120",
                   "-p", f"{args.bind_address}:{args.port}:25565", "-p", f"127.0.0.1:{args.rcon_port}:25575",
                   "-v", f"{volume}:/data"]
        for key, value in env.items():
            command.extend(["-e", key + "=" + value])
        command.append(image)
        run_process(command, timeout=120)
        copy_files(args.container, "/data", {
            "whitelist.json": json.dumps(admins),
            "server.properties": "".join(f"{key}={value}\n" for key, value in properties.items()),
        })
        private_json(config_path, config)
    features = config.get("features", {})
    if not isinstance(features, dict) or type(features.get("community_messages", True)) is not bool:
        raise ValueError("features.community_messages must be a boolean")
    community_messages = features.get("community_messages", True)
    inventory = list_containers("podman")
    for item in sorted(inventory, key=container_name):
        name = container_name(item)
        if name == args.container or not is_minecraft_image(container_image(item)):
            continue
        if name not in state["slots"]:
            next_id = max(state["slots"].values(), default=0) + 1
            if next_id > 128:
                raise RuntimeError("Hub has reached its 128 stable-bay limit")
            state["slots"][name] = next_id
        info = inspect_container("podman", name)
        env = container_env(info)
        editable = env.get("OVERRIDE_SERVER_PROPERTIES", "").lower() == "false" or env.get("SKIP_SERVER_PROPERTIES", "").lower() == "true"
        eligible = env.get("VERSION") == "26.3" and env.get("TYPE", "VANILLA").upper() == "VANILLA" and editable and not any(part in name.lower() for part in ("rollback", "test"))
        config["targets"].setdefault(name, {"control": eligible})
    private_json(config_path, config)
    private_json(state_path, state)
    run_process(["podman", "start", args.container], timeout=60)
    client = wait_for_hub(config["hub"])
    try:
        slots = [{"id": identifier, "name": name} for name, identifier in sorted(state["slots"].items(), key=lambda pair: pair[1])]
        install_datapack(args.container, datapack_files(slots))
        checked_command(client, "reload")
        commands = build_commands(slots, community_messages=community_messages,
                                  preserve_submissions=args.rebuild)
        checked_command(client, commands[0])
        wait_for_chunks(client, FORCELOAD_BOUNDS)
        for command in commands[1:]:
            checked_command(client, command)
        checked_command(client, f"setworldspawn {SPAWN[0]} {SPAWN[1]} {SPAWN[2]}")
        checked_command(client, "gamerule minecraft:respawn_radius 0")
        checked_command(client, "gamerule minecraft:advance_time false")
        checked_command(client, "gamerule minecraft:advance_weather false")
        checked_command(client, "time set noon")
        checked_command(client, "weather clear")
        checked_command(client, "save-all flush")
        state["generated"] = list(state["slots"].values())
        private_json(state_path, state)
    finally:
        client.close()
    if not args.no_service:
        install_service(config_path, state_path)
    print(f"Hub ready: {args.address}:{config['hub']['game_port']} (Minecraft 26.3)")
    print(f"Config: {config_path}\nState: {state_path}")
    print("Existing destinations were not restarted. Start a stopped enrolled destination from its hub sign to enable transfers.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--container", default="mc_hub")
    parser.add_argument("--address", required=True, help="Destination host/IP reachable from players")
    parser.add_argument("--bind-address", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--rcon-port", type=int, default=25579)
    parser.add_argument("--image", default="docker.io/itzg/minecraft-server:java25")
    parser.add_argument("--admin", action="append", required=True, metavar="UUID:NAME")
    parser.add_argument("--rebuild", action="store_true", help="Explicitly rebuild managed geometry; retains custom label signs")
    parser.add_argument("--no-service", action="store_true")
    args = parser.parse_args()
    try:
        deploy(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Hub deployment failed: {error}\n")


if __name__ == "__main__":
    main()
