import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from mc_admin_core import (
    DiscoveredServer,
    Kit,
    Location,
    ServerConfig,
    SETTINGS,
    ServerSettings,
    _properties_from_tar,
    _uptime_text,
    all_kits,
    backup_path,
    backup_world,
    builtin_kits,
    container_env,
    container_image,
    container_level_name,
    container_name,
    container_workdir,
    default_backup_dir,
    find_matches,
    human_name,
    human_size,
    is_minecraft_image,
    items_from_registry,
    list_backups,
    load_custom_kits,
    load_items,
    load_last_server,
    load_locations,
    load_server_settings,
    load_server_configs,
    locations_path_for,
    parse_dimension,
    parse_inventory,
    parse_kit_items,
    parse_players,
    parse_position,
    prune_backups,
    rcon,
    restart_container,
    restore_procedure,
    rule_name,
    safe_filename,
    save_custom_kits,
    save_locations,
    save_server_settings,
    scan_servers,
    server_config_for,
    start_container,
    stop_container,
    upsert_server_config,
)


class CoreTests(unittest.TestCase):
    def test_players(self):
        self.assertEqual(
            parse_players(
                "There are 2 of a max of 20 players online: Alice, CheekyHambone"
            ),
            ["Alice", "CheekyHambone"],
        )
        self.assertEqual(
            parse_players("There are 0 of a max of 20 players online:"), []
        )

    def test_position(self):
        self.assertEqual(
            parse_position(
                "CheekyHambone has the following entity data: "
                "[123.5d, 64.0d, -98.25d]"
            ),
            (123.5, 64.0, -98.25),
        )

    def test_dimension(self):
        self.assertEqual(
            parse_dimension(
                'CheekyHambone has the following entity data: "minecraft:overworld"'
            ),
            "minecraft:overworld",
        )

    def test_alias_search(self):
        items = [
            "minecraft:firework_rocket",
            "minecraft:cobbled_deepslate",
            "minecraft:stone",
        ]
        self.assertEqual(find_matches("fireworks", items)[0], "minecraft:firework_rocket")
        self.assertEqual(
            find_matches("deep slate", items)[0], "minecraft:cobbled_deepslate"
        )

    def test_locations_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "locations.json"
            original = {
                "Home": Location(
                    "Home", 10.5, 64.0, -20.25, "minecraft:overworld"
                )
            }
            save_locations(original, path)
            self.assertEqual(load_locations(path), original)


class DetectionTests(unittest.TestCase):
    def test_minecraft_image(self):
        self.assertTrue(is_minecraft_image("docker.io/itzg/minecraft-server:latest"))
        self.assertTrue(is_minecraft_image("itzg/mc-server:java25"))
        self.assertFalse(is_minecraft_image("docker.io/library/postgres:15"))

    def test_container_helpers(self):
        info = {
            "Names": ["mc_test"],
            "ImageName": "docker.io/itzg/minecraft-server:latest",
            "Config": {
                "Env": ["EULA=TRUE", "RCON_PASSWORD=secret"],
                "WorkingDir": "/data",
            },
            "Mounts": [{"Source": "/vol", "Destination": "/data"}],
        }
        self.assertEqual(container_name(info), "mc_test")
        self.assertEqual(
            container_image(info), "docker.io/itzg/minecraft-server:latest"
        )
        self.assertEqual(container_workdir(info), "/data")
        self.assertEqual(container_env(info)["RCON_PASSWORD"], "secret")

    def test_scan_servers_filters_and_detects(self):
        containers = [
            {
                "Names": ["mc_alpha"],
                "Image": "docker.io/itzg/minecraft-server:latest",
                "State": "running",
                "Status": "Up 2 days",
            },
            {
                "Names": ["db"],
                "Image": "docker.io/library/postgres:15",
                "State": "running",
            },
        ]
        info = {
            "Names": ["mc_alpha"],
            "ImageName": "docker.io/itzg/minecraft-server:latest",
            "Mounts": [{"Source": "/vol", "Destination": "/data"}],
        }
        with patch("mc_admin_core.list_containers", return_value=containers), patch(
            "mc_admin_core.rcon_client_in_container", return_value="rcon-cli"
        ), patch(
            "mc_admin_core._rcon_settings_from_container",
            return_value=("hunter2", 25575, "/data"),
        ), patch(
            "mc_admin_core.inspect_container", return_value=info
        ), patch(
            "mc_admin_core.shutil.which", return_value="/usr/bin/podman"
        ):
            servers = scan_servers(["podman"])

        self.assertEqual(len(servers), 1)
        server = servers[0]
        self.assertEqual((server.runtime, server.container), ("podman", "mc_alpha"))
        self.assertEqual(server.state, "running")
        self.assertEqual(server.rcon_client, "rcon-cli")
        self.assertEqual(server.rcon_password, "hunter2")
        self.assertEqual(server.working_dir, "/data")

    def test_scan_servers_without_rcon_client(self):
        containers = [
            {
                "Names": ["mc_custom"],
                "Image": "itzg/minecraft-server:java25",
                "State": "exited",
            }
        ]
        with patch("mc_admin_core.list_containers", return_value=containers), patch(
            "mc_admin_core.rcon_client_in_container", return_value=None
        ), patch("mc_admin_core.shutil.which", return_value="/usr/bin/podman"):
            servers = scan_servers(["podman"])

        self.assertEqual(len(servers), 1)
        self.assertIsNone(servers[0].rcon_client)
        self.assertIsNone(servers[0].rcon_password)

    def test_rcon_command_shape(self):
        seen: list[list[str]] = []

        def fake_run(args, *, timeout=10):
            seen.append(args)
            return "ok"

        server = ServerConfig(
            runtime="docker",
            container="mc_one",
            rcon_password="pw",
            rcon_port=25575,
            working_dir="/data",
        )
        with patch("mc_admin_core.shutil.which", return_value="/usr/bin/docker"), patch(
            "mc_admin_core.run_process", side_effect=fake_run
        ):
            self.assertEqual(rcon(server, "list"), "ok")

        self.assertEqual(
            seen[0],
            [
                "docker",
                "exec",
                "-w",
                "/data",
                "-i",
                "mc_one",
                "rcon-cli",
                "--password",
                "pw",
                "list",
            ],
        )

    def test_rcon_mcrcon_command_shape(self):
        seen: list[list[str]] = []

        def fake_run(args, *, timeout=10):
            seen.append(args)
            return "ok"

        server = ServerConfig(
            runtime="podman",
            container="mc_two",
            rcon_client="mcrcon",
            rcon_password="pw",
            working_dir="/data",
        )
        with patch("mc_admin_core.shutil.which", return_value="/usr/bin/podman"), patch(
            "mc_admin_core.run_process", side_effect=fake_run
        ):
            rcon(server, "list")

        self.assertEqual(
            seen[0],
            [
                "podman",
                "exec",
                "-w",
                "/data",
                "-i",
                "mc_two",
                "mcrcon",
                "-H",
                "localhost",
                "-P",
                "25575",
                "-p",
                "pw",
                "list",
            ],
        )

    def test_rcon_client_probe_parses_commandv(self):
        seen: list[list[str]] = []

        def fake_run(args, *, timeout=10):
            seen.append(args)
            return "/usr/local/bin/rcon-cli"

        with patch("mc_admin_core.shutil.which", return_value="/usr/bin/podman"), patch(
            "mc_admin_core.run_process", side_effect=fake_run
        ):
            from mc_admin_core import rcon_client_in_container

            self.assertEqual(
                rcon_client_in_container("podman", "mc_one"), "rcon-cli"
            )
        self.assertEqual(
            seen[0][:4], ["podman", "exec", "mc_one", "sh"]
        )

    def test_rcon_client_probe_failure_is_none(self):
        def boom(args, *, timeout=10):
            raise RuntimeError("no sh")

        with patch("mc_admin_core.shutil.which", return_value="/usr/bin/podman"), patch(
            "mc_admin_core.run_process", side_effect=boom
        ):
            from mc_admin_core import rcon_client_in_container

            self.assertIsNone(rcon_client_in_container("podman", "mc_dead"))

    def test_server_config_and_locations_paths(self):
        server = DiscoveredServer(
            runtime="podman", container="mc/my server", rcon_client="rcon-cli"
        )
        config = server_config_for(server)
        self.assertEqual(config.locations_path, locations_path_for("mc/my server"))
        self.assertEqual(safe_filename("mc/my server"), "mc_my_server")
        self.assertEqual(config.label, "mc/my server")

    def test_server_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "servers.json"
            config = ServerConfig(
                runtime="docker",
                container="mc_one",
                rcon_password="pw",
                working_dir="/data",
            )
            upsert_server_config(config, path)
            others = ServerConfig(runtime="podman", container="mc_two")
            upsert_server_config(others, path)

            saved = {item.container: item for item in load_server_configs(path)}
            self.assertEqual(set(saved), {"mc_one", "mc_two"})
            self.assertEqual(saved["mc_one"].rcon_password, "pw")
            self.assertEqual(saved["mc_one"].locations_path, locations_path_for("mc_one"))
            self.assertEqual(load_last_server(path).container, "mc_two")

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["last"]["container"], "mc_two")


def _world_tar(level_name: str = "world") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for rel in ("level.dat", "region/r.0.0.mca", "DIM-1/region/r.0.0.mca"):
            payload = b"data" * 100
            info = tarfile.TarInfo(f"{level_name}/{rel}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


class BackupTests(unittest.TestCase):
    def test_prune_keeps_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            made = [
                directory / f"world-2026010{i}-000000.tar.gz" for i in range(5)
            ]
            for path in made:
                path.write_bytes(b"x")

            removed = prune_backups(directory, 2)
            self.assertEqual(
                {p.name for p in removed},
                {made[0].name, made[1].name, made[2].name},
            )
            self.assertEqual(
                {p.name for p in directory.glob("*.tar.gz")},
                {made[3].name, made[4].name},
            )

    def test_prune_keep_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "world-1.tar.gz").write_bytes(b"x")
            self.assertEqual(prune_backups(directory, 0), [])

    def test_backup_path_and_list(self):
        cfg = ServerConfig(runtime="podman", container="mc test")
        path = backup_path(cfg, level_name="my world", label="before upgrade")
        self.assertEqual(path.parent, default_backup_dir("mc test"))
        self.assertIn("my_world-", path.name)
        self.assertTrue(path.name.endswith("-before_upgrade.tar.gz"))

    def test_backup_world_streams_and_gzips(self):
        tar_bytes = _world_tar("world")
        cfg = ServerConfig(runtime="podman", container="mc_one")

        class FakeProc:
            returncode = 0

            def __init__(self):
                self.stdout = io.BytesIO(tar_bytes)
                self.command = None

            def communicate(self, timeout=None):
                return b"", b""

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.tar.gz"
            fake = FakeProc()

            def fake_popen(argv, **kwargs):
                fake.command = argv
                return fake

            with patch("mc_admin_core.shutil.which", return_value="/usr/bin/podman"), patch(
                "mc_admin_core.subprocess.Popen", side_effect=fake_popen
            ), patch("mc_admin_core.backup_path", return_value=target):
                result = backup_world(cfg, level_name="world", timeout=30)

            self.assertEqual(result, target)
            self.assertEqual(fake.command[:3], ["podman", "cp", "mc_one:/data/world"])
            with tarfile.open(target, "r:gz") as archive:
                names = archive.getnames()
            self.assertIn("world/level.dat", names)
            self.assertTrue(any(name.startswith("world/DIM-1") for name in names))

    def test_backup_world_failure_removes_partial(self):
        cfg = ServerConfig(runtime="docker", container="mc_two")

        class FakeProc:
            returncode = 1

            def __init__(self):
                self.stdout = io.BytesIO(b"")

            def communicate(self, timeout=None):
                return b"", b"boom"

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out.tar.gz"
            with patch("mc_admin_core.shutil.which", return_value="/usr/bin/docker"), patch(
                "mc_admin_core.subprocess.Popen", return_value=FakeProc()
            ), patch("mc_admin_core.backup_path", return_value=target):
                with self.assertRaises(RuntimeError):
                    backup_world(cfg, level_name="world", timeout=30)
            self.assertFalse(target.exists())

    def test_container_level_name_parses_properties(self):
        cfg = ServerConfig(runtime="podman", container="mc_three")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            payload = b"online-mode=true\nlevel-name=my_world\n"
            info = tarfile.TarInfo("server.properties")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

        with patch(
            "mc_admin_core._copy_path_stream", return_value=buffer.getvalue()
        ):
            self.assertEqual(container_level_name(cfg), "my_world")

    def test_container_level_name_defaults(self):
        cfg = ServerConfig(runtime="podman", container="mc_four")
        with patch(
            "mc_admin_core._copy_path_stream", side_effect=RuntimeError("gone")
        ):
            self.assertEqual(container_level_name(cfg), "world")

    def test_list_backups_empty_and_populated(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(list_backups("mc_five", base), [])
            directory = default_backup_dir("mc_five", base)
            directory.mkdir(parents=True)
            (directory / "world-2.tar.gz").write_bytes(b"x")
            (directory / "world-1.tar.gz").write_bytes(b"x")
            self.assertEqual(
                [p.name for p in list_backups("mc_five", base)],
                ["world-2.tar.gz", "world-1.tar.gz"],
            )


class KitTests(unittest.TestCase):
    def test_builtin_kits_present(self):
        names = {kit.name for kit in builtin_kits()}
        self.assertIn("starter", names)
        for kit in builtin_kits():
            self.assertTrue(kit.items)
            self.assertEqual(kit.source, "builtin")

    def test_parse_kit_items(self):
        self.assertEqual(
            parse_kit_items("diamond_pickaxe, torch 64, cobblestone 64"),
            (
                ("minecraft:diamond_pickaxe", 1),
                ("minecraft:torch", 64),
                ("minecraft:cobblestone", 64),
            ),
        )
        self.assertEqual(
            parse_kit_items("minecraft:stone 32\napple 5"),
            (("minecraft:stone", 32), ("minecraft:apple", 5)),
        )
        self.assertEqual(parse_kit_items("  "), ())
        # clamps quantity into the valid give range
        self.assertEqual(parse_kit_items("stone 99999"), (("minecraft:stone", 6400),))

    def test_custom_kit_roundtrip_and_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kits.json"
            custom = {
                "mining": Kit(
                    name="mining",
                    items=(("minecraft:diamond_pickaxe", 1), ("minecraft:torch", 64)),
                )
            }
            save_custom_kits(custom, path)
            loaded = load_custom_kits(path)
            self.assertEqual(loaded["mining"].items, custom["mining"].items)

            # custom kit overrides a built-in of the same name
            override = {"starter": Kit(name="starter", items=(("minecraft:dirt", 1),))}
            save_custom_kits(override, path)
            merged = all_kits(path)
            self.assertEqual(merged["starter"].items, (("minecraft:dirt", 1),))
            self.assertEqual(merged["starter"].source, "custom")
            self.assertIn("pvp", merged)

    def test_cli_server_kit_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_custom_kits(Path(tmp) / "nope.json"), {})


class HelperTests(unittest.TestCase):
    def test_parse_inventory(self):
        raw = (
            "Fuzzywah has the following entity data: "
            '[{count: 1, Slot: 0b, id: "minecraft:iron_axe"}, '
            '{components: {"minecraft:stored_enchantments": '
            '{"minecraft:unbreaking": 3}}, count: 1, Slot: 2b, '
            'id: "minecraft:enchanted_book"}, '
            '{count: 64, Slot: 1b, id: "minecraft:firework_rocket"}]'
        )
        self.assertEqual(
            parse_inventory(raw),
            [
                (0, 1, "minecraft:iron_axe"),
                (2, 1, "minecraft:enchanted_book"),
                (1, 64, "minecraft:firework_rocket"),
            ],
        )
        self.assertEqual(parse_inventory("Fuzzywah has no items"), [])

    def test_parse_inventory_uses_nearest_count(self):
        # nested components carry their own count-like keys; the entry count wins
        raw = (
            '[{components: {"minecraft:fireworks": {flight_duration: 3b}}, '
            'count: 64, Slot: 1b, id: "minecraft:firework_rocket"}]'
        )
        self.assertEqual(
            parse_inventory(raw), [(1, 64, "minecraft:firework_rocket")]
        )

    def test_human_size(self):
        self.assertEqual(human_size(None), "?")
        self.assertEqual(human_size(0), "?")
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(1024), "1.0 KB")
        self.assertEqual(human_size(1523861362), "1.4 GB")

    def test_uptime_text(self):
        now = datetime.now(timezone.utc)
        started = (now - timedelta(days=4, hours=16, minutes=11)).isoformat()
        self.assertEqual(_uptime_text(started), "4d 16h 11m")
        short = (now - timedelta(minutes=5)).isoformat()
        self.assertEqual(_uptime_text(short), "5m")
        self.assertEqual(_uptime_text("not a date"), "")

    def test_properties_from_tar(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            payload = b"#comment\nlevel-name=my_world\nmotd=Hello\n"
            info = tarfile.TarInfo("server.properties")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        props = _properties_from_tar(buffer.getvalue())
        self.assertEqual(props["level-name"], "my_world")
        self.assertEqual(props["motd"], "Hello")
        self.assertNotIn("#comment", props)


class RuleTests(unittest.TestCase):
    def test_rule_name_probes_candidates(self):
        cfg = ServerConfig(runtime="podman", container="mc")
        seen = []

        def fake_rcon(server, command, **kwargs):
            seen.append(command)
            if command == "gamerule keepInventory":
                return "Incorrect argument for command"
            if command == "gamerule keep_inventory":
                return "Game rule keep_inventory is currently set to true"
            raise RuntimeError("no")

        with patch("mc_admin_core.rcon", side_effect=fake_rcon):
            self.assertEqual(rule_name(cfg, "keepInventory"), "keep_inventory")
        self.assertEqual(seen[0], "gamerule keepInventory")

    def test_rule_name_falls_back_when_absent(self):
        cfg = ServerConfig(runtime="podman", container="mc")
        with patch("mc_admin_core.rcon", side_effect=RuntimeError("unknown rule")):
            self.assertEqual(rule_name(cfg, "keepInventory"), "keepInventory")

    def test_human_name_strips_components(self):
        self.assertEqual(
            human_name("minecraft:turtle_helmet[enchantments={\"minecraft:x\":1}]"),
            "turtle helmet [+components]",
        )
        self.assertEqual(human_name("minecraft:diamond"), "diamond")

    def test_swimming_kit_present_and_wellformed(self):
        kit = builtin_kits()
        names = {k.name for k in kit}
        self.assertIn("swimming", names)
        swimming = next(k for k in kit if k.name == "swimming")
        self.assertEqual(len(swimming.items), 9)
        for entry, qty in swimming.items:
            self.assertGreaterEqual(qty, 1)
            self.assertTrue(
                entry.startswith("minecraft:") or " " in entry,
                f"unexpected kit entry: {entry}",
            )
        # enchanted gear must use component syntax so `give` preserves it
        self.assertTrue(
            any("[enchantments=" in entry for entry, _ in swimming.items)
        )

    def test_restore_procedure_mentions_container(self):
        text = restore_procedure("mc_test")
        self.assertIn("C=mc_test", text)
        self.assertIn("session.lock", text)
        self.assertIn("tar -xzf", text)


class RegistryTests(unittest.TestCase):
    def test_bundled_registry_present(self):
        from mc_admin_core import bundled_registry_path, registry_entities

        path = bundled_registry_path()
        self.assertIsNotNone(path, "registries.json should ship with the app")
        items, source = load_items(None)
        self.assertGreater(len(items), 100)
        self.assertIn("minecraft:diamond", items)
        self.assertIn("registries.json", source)
        entities = registry_entities(path)
        self.assertIn("minecraft:creeper", entities)

    def test_items_from_registry_missing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.json"
            path.write_text('{"minecraft:block": {}}', encoding="utf-8")
            with self.assertRaises(ValueError):
                items_from_registry(path)


class ContainerLifecycleTests(unittest.TestCase):
    def test_managed_image_without_required_options_cannot_start_or_restart(self):
        server = ServerConfig("podman", "mc_one")
        info = {"Config": {"Image": "itzg/minecraft-server", "Env": ["RCON_PASSWORD=DO_NOT_LEAK"]}}
        with patch("mc_admin_core.inspect_container", return_value=info), patch("mc_admin_core.run_process") as run:
            for operation in (start_container, restart_container):
                with self.subTest(operation=operation.__name__), self.assertRaisesRegex(RuntimeError, "OVERRIDE_SERVER_PROPERTIES=false") as error:
                    operation(server)
                self.assertNotIn("DO_NOT_LEAK", str(error.exception))
            run.assert_not_called()

    def test_inspection_failure_does_not_launch_container(self):
        with patch("mc_admin_core.inspect_container", side_effect=RuntimeError("DO_NOT_LEAK")), patch(
            "mc_admin_core.run_process"
        ) as run:
            for operation in (start_container, restart_container):
                with self.subTest(operation=operation.__name__), self.assertRaisesRegex(RuntimeError, "Cannot inspect") as error:
                    operation(ServerConfig("docker", "mc_one"))
                self.assertNotIn("DO_NOT_LEAK", str(error.exception))
            run.assert_not_called()


def _settings_tar(raw: bytes, kind=tarfile.REGTYPE, mode=0o640, owner=(1001, 1002)) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("server.properties")
        member.type = kind
        member.mode = mode
        member.uid, member.gid = owner
        member.uname, member.gname = "minecraft", "minecraft"
        member.mtime = 123456789
        if kind == tarfile.REGTYPE:
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        else:
            member.linkname = "/etc/unrelated"
            archive.addfile(member)
    return buffer.getvalue()


class _SettingsContainer:
    """Deterministic cp transport with a persistent in-memory container file."""

    def __init__(self, raw=b"motd=Old\npvp=true\nmax-players=20\n", runtime="podman"):
        self.raw = raw
        self.server = ServerConfig(runtime=runtime, container="mc_settings")
        self.info = {
            "State": {"Status": "running"},
            "Config": {
                "Image": "itzg/minecraft-server:latest",
                "Env": ["OVERRIDE_SERVER_PROPERTIES=false"],
                "WorkingDir": "/data",
            }
        }
        self.reads = 0
        self.writes = []
        self.commands = []
        self.mode = 0o640
        self.kind = tarfile.REGTYPE
        self.owner = (1001, 1002)
        self.copy_owner = self.owner
        self.fail_read = False
        self.fail_write = False
        self.write_exception = None
        self.after_write = None
        self.written_member = None
        self.stack = ExitStack()

    def __enter__(self):
        self.stack.enter_context(patch("mc_admin_core.inspect_container", side_effect=lambda *_: self.info))
        self.stack.enter_context(patch("mc_admin_core.shutil.which", return_value="/usr/bin/runtime"))
        self.stack.enter_context(patch("mc_admin_core.subprocess.run", side_effect=self.run))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def run(self, args, **kwargs):
        if "stat" in args and ("exec" in args or "unshare" in args):
            return subprocess.CompletedProcess(args, 0, f"{self.owner[0]} {self.owner[1]}", "")
        self.commands.append(args)
        if "input" not in kwargs:
            self.reads += 1
            if self.fail_read:
                return subprocess.CompletedProcess(args, 1, b"", b"rcon.password=DO_NOT_LEAK")
            return subprocess.CompletedProcess(args, 0, _settings_tar(self.raw, self.kind, self.mode, self.copy_owner), b"")
        self.writes.append(kwargs["input"])
        if self.write_exception:
            raise self.write_exception
        if self.fail_write:
            return subprocess.CompletedProcess(args, 1, b"", b"rcon.password=DO_NOT_LEAK")
        with tarfile.open(fileobj=io.BytesIO(kwargs["input"])) as archive:
            self.written_member = archive.getmembers()[0]
            self.raw = archive.extractfile(self.written_member).read()
            self.owner = (
                (0, 0) if self.server.runtime == "podman" and "--archive=false" not in args
                else (self.written_member.uid, self.written_member.gid)
            )
        if self.after_write is not None:
            self.raw = self.after_write
        return subprocess.CompletedProcess(args, 0, b"", b"")


class ServerSettingsTests(unittest.TestCase):
    def test_java_properties_decoding_and_duplicates(self):
        raw = (
            b"  # comment\\\r\n\t!another comment\r\n"
            b"motd : Hello\\u0020\\u00e9\\uD83D\\uDE00\\nline\\tend\r\n"
            b"pvp=true\r\npvp false\r\n"
            b"escaped\\ key\\:\\==value\\=with\\:separators  \r\n"
            b"continued=one\\\r\n \t\ftwo\\\r\n  three\r\n"
            b"empty\r\nunknown\\q=slash\\\\and\\fpage\r"
        )
        with _SettingsContainer(raw) as container:
            loaded = load_server_settings(container.server)
        self.assertEqual(loaded.raw, raw)
        self.assertEqual(loaded.values["motd"], "Hello é😀\nline\tend")
        self.assertEqual(loaded.values["pvp"], "false")
        self.assertEqual(loaded.values["escaped key:="], "value=with:separators  ")
        self.assertEqual(loaded.values["continued"], "onetwothree")
        self.assertEqual(loaded.values["empty"], "")
        self.assertEqual(loaded.values["unknownq"], "slash\\and\fpage")
        self.assertEqual(loaded.path, "/data/server.properties")
        self.assertIsNone(loaded.write_blocked)
        self.assertEqual(container.writes, [])

    def test_save_preserves_all_unrelated_bytes_and_effective_duplicate(self):
        raw = (
            b"# comment\r\npvp=true\r\nrcon.password=secret\\=keep  \r\n"
            b"motd=earlier duplicate\r\n !important\r\nmotd : before\\\r\n"
            b"  continuation\r\npvp : false\r\nunknown= untouched  "
        )
        motd = " leading é😀\\path\nSecond\tline:=#! "
        expected = (
            b"# comment\r\npvp=true\r\nrcon.password=secret\\=keep  \r\n"
            b"motd=earlier duplicate\r\n !important\r\n"
            b"motd=\\ leading\\ \\u00e9\\ud83d\\ude00\\\\path\\nSecond\\tline\\:\\=\\#\\!\\ \r\n"
            b"pvp=true\r\nunknown= untouched  "
        )
        for runtime in ("podman", "docker"):
            with self.subTest(runtime=runtime), _SettingsContainer(raw, runtime) as container:
                loaded = load_server_settings(container.server)
                saved = save_server_settings(container.server, loaded, {"motd": motd, "pvp": "true"})
                self.assertEqual(container.raw, expected)
                self.assertEqual(saved.raw, expected)
                self.assertEqual(saved.values["motd"], motd)
                self.assertEqual(saved.values["pvp"], "true")
                self.assertEqual(saved.values["rcon.password"], "secret=keep  ")
                self.assertEqual(len(container.writes), 1)
                member = container.written_member
                self.assertEqual((member.name, member.mode, member.uid, member.gid),
                                 ("server.properties", 0o640, 1001, 1002))

    def test_podman_save_preserves_actual_owner_not_normalized_copy_owner(self):
        for running in (True, False):
            with self.subTest(running=running), _SettingsContainer() as container:
                container.copy_owner = (0, 0)
                container.info["State"]["Status"] = "running" if running else "exited"
                container.info["Mounts"] = [{"Source": "/persistent", "Destination": "/data"}]
                loaded = load_server_settings(container.server)
                saved = save_server_settings(container.server, loaded, {"motd": "Updated"})
                self.assertEqual(saved.values["motd"], "Updated")
                self.assertEqual(container.owner, (1001, 1002))

    def test_motd_line_endings_are_normalized_and_eof_is_preserved(self):
        with _SettingsContainer(b"motd=before") as container:
            loaded = load_server_settings(container.server)
            saved = save_server_settings(container.server, loaded, {"motd": "one\r\ntwo\rthree"})
            self.assertEqual(saved.values["motd"], "one\ntwo\nthree")
            self.assertEqual(container.raw, b"motd=one\\ntwo\\nthree")

    def test_native_unicode_and_legacy_latin1_are_read_without_rewriting(self):
        for raw in ("motd=café\n".encode(), b"motd=caf\xe9\n"):
            with self.subTest(raw=raw), _SettingsContainer(raw) as container:
                loaded = load_server_settings(container.server)
                self.assertEqual(loaded.values["motd"], "café")
                self.assertIs(save_server_settings(container.server, loaded, {"motd": "café"}), loaded)
                self.assertEqual(container.raw, raw)
                self.assertEqual(container.writes, [])

    def test_working_directory_priority_and_fallback(self):
        for configured, detected, expected in (
            ("/custom/", "/detected", "/custom/server.properties"),
            (None, "/detected", "/detected/server.properties"),
            (None, "", "/data/server.properties"),
            ("/", "/detected", "/server.properties"),
        ):
            with self.subTest(expected=expected), _SettingsContainer() as container:
                container.server = ServerConfig("docker", "mc_settings", working_dir=configured)
                container.info["Config"]["WorkingDir"] = detected
                self.assertEqual(load_server_settings(container.server).path, expected)

    def test_managed_image_blocks_without_safe_environment_flag(self):
        for env in ([], ["OVERRIDE_SERVER_PROPERTIES=true"], ["SKIP_SERVER_PROPERTIES=false"],
                    ["OVERRIDE_SERVER_PROPERTIES=not-false"]):
            with self.subTest(env=env), _SettingsContainer() as container:
                container.info["Config"]["Env"] = env + ["RCON_PASSWORD=DO_NOT_LEAK"]
                loaded = load_server_settings(container.server)
                self.assertIn("OVERRIDE_SERVER_PROPERTIES=false", loaded.write_blocked)
                self.assertIn("retaining its data volume", loaded.write_blocked)
                self.assertNotIn("DO_NOT_LEAK", loaded.write_blocked)
                with self.assertRaisesRegex(RuntimeError, "recreate"):
                    save_server_settings(container.server, loaded, {"motd": "new"})
                self.assertEqual(container.writes, [])

    def test_safe_environment_flags_are_case_insensitive(self):
        for env in (["override_server_properties=FALSE"], ["skip_server_properties=TrUe"]):
            with self.subTest(env=env), _SettingsContainer() as container:
                container.info["Config"]["Env"] = env
                loaded = load_server_settings(container.server)
                self.assertIsNone(loaded.write_blocked)
                self.assertEqual(save_server_settings(container.server, loaded, {"motd": "new"}).values["motd"], "new")

    def test_generic_images_are_editable_without_itzg_flags(self):
        with _SettingsContainer() as container:
            container.info["Config"] = {"Image": "local/minecraft-custom", "WorkingDir": "/data"}
            loaded = load_server_settings(container.server)
            self.assertIsNone(loaded.write_blocked)
            saved = save_server_settings(container.server, loaded, {"pvp": "false"})
            self.assertEqual(saved.values["pvp"], "false")

    def test_policy_and_path_are_checked_again_before_save(self):
        for mutation, error in (
            (lambda c: c.info["Config"].update(Env=[]), "startup"),
            (lambda c: c.info["Config"].update(WorkingDir="/other"), "data path changed"),
        ):
            with self.subTest(error=error), _SettingsContainer() as container:
                loaded = load_server_settings(container.server)
                mutation(container)
                with self.assertRaisesRegex(RuntimeError, error):
                    save_server_settings(container.server, loaded, {"motd": "new"})
                self.assertEqual(container.writes, [])

    def test_no_changes_or_same_values_never_copy_into_container(self):
        with _SettingsContainer() as container:
            container.info["Config"]["Env"] = []
            loaded = load_server_settings(container.server)
            for changes in ({}, {"motd": "Old", "pvp": "true"}):
                self.assertIs(save_server_settings(container.server, loaded, changes), loaded)
            self.assertEqual(container.writes, [])
            self.assertEqual(container.reads, 1)

    def test_batch_validation_rejects_without_any_write(self):
        raw = (
            b"motd=Old\npvp=true\nmax-players=20\ndifficulty=easy\ngamemode=survival\n"
            b"view-distance=10\nsimulation-distance=10\nspawn-protection=16\nplayer-idle-timeout=0\n"
        )
        invalid = (
            {"rcon.password": "new"}, {"allow-flight": "true"}, {"pvp": "TRUE"},
            {"pvp": "false "}, {"difficulty": "extreme"}, {"difficulty": "2"},
            {"gamemode": "invalid"}, {"max-players": "0"}, {"max-players": "2147483648"},
            {"max-players": "-1"}, {"max-players": "2.5"}, {"max-players": " 20"},
            {"view-distance": "1"}, {"view-distance": "33"},
            {"simulation-distance": "1"}, {"simulation-distance": "33"},
            {"spawn-protection": "-1"}, {"spawn-protection": "2147483648"},
            {"player-idle-timeout": "-1"}, {"player-idle-timeout": "2147483648"},
            {"motd": "NUL\0"}, {"motd": "form\f"}, {"motd": "delete\x7f"},
            {"motd": "control\x85"}, {"motd": "\ud800"}, {"motd": 123},
        )
        with _SettingsContainer(raw) as container:
            loaded = load_server_settings(container.server)
            for changes in invalid:
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    save_server_settings(container.server, loaded, {"motd": "otherwise valid", **changes})
            self.assertEqual(container.writes, [])
            self.assertEqual(container.reads, 1)
            self.assertEqual(container.raw, raw)

    def test_valid_numeric_boundaries(self):
        raw = (b"max-players=20\nview-distance=10\nsimulation-distance=10\n"
               b"spawn-protection=16\nplayer-idle-timeout=10\n")
        with _SettingsContainer(raw) as container:
            loaded = load_server_settings(container.server)
            for changes in (
                {"max-players": "1", "view-distance": "2", "simulation-distance": "2",
                 "spawn-protection": "0", "player-idle-timeout": "0"},
                {"max-players": "2147483647", "view-distance": "32", "simulation-distance": "32",
                 "spawn-protection": "2147483647", "player-idle-timeout": "2147483647"},
            ):
                loaded = save_server_settings(container.server, loaded, changes)
                for key, value in changes.items():
                    self.assertEqual(loaded.values[key], value)

    def test_concurrent_edit_is_not_overwritten(self):
        with _SettingsContainer() as container:
            loaded = load_server_settings(container.server)
            container.raw += b"# external edit\n"
            latest = container.raw
            with self.assertRaisesRegex(RuntimeError, "changed outside"):
                save_server_settings(container.server, loaded, {"motd": "new"})
            self.assertEqual(container.writes, [])
            self.assertEqual(container.raw, latest)

    def test_mutable_snapshot_values_cannot_inject_or_hide_file_properties(self):
        with _SettingsContainer() as container:
            loaded = load_server_settings(container.server)
            loaded.values["allow-flight"] = "false"
            loaded.values["motd"] = "fake"
            with self.assertRaisesRegex(ValueError, "absent"):
                save_server_settings(container.server, loaded, {"allow-flight": "true"})
            saved = save_server_settings(container.server, loaded, {"motd": "fake"})
            self.assertEqual(saved.values["motd"], "fake")
            self.assertEqual(len(container.writes), 1)

    def test_malformed_files_fail_without_leaking_unknown_values(self):
        for raw in (
            b"rcon.password=DO_NOT_LEAK\\u123\nmotd=old\n",
            b"motd=old\\", b"motd=old\\\n",
            b"rcon.password=DO_NOT_LEAK\\ud800\n",
            b"rcon.password=DO_NOT_LEAK\0\n",
        ):
            with self.subTest(raw=raw), _SettingsContainer(raw) as container:
                with self.assertRaisesRegex(ValueError, "Malformed server.properties") as error:
                    load_server_settings(container.server)
                self.assertNotIn("DO_NOT_LEAK", str(error.exception))
                snapshot = ServerSettings("/data/server.properties", raw, {})
                with self.assertRaises(ValueError):
                    save_server_settings(container.server, snapshot, {"motd": "new"})
                self.assertEqual(container.writes, [])
                self.assertEqual(_properties_from_tar(_settings_tar(raw)), {})

    def test_non_regular_files_are_rejected_on_load_and_recheck(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind), _SettingsContainer() as container:
                loaded = load_server_settings(container.server)
                container.kind = kind
                with self.assertRaisesRegex(ValueError, "regular file"):
                    load_server_settings(container.server)
                with self.assertRaisesRegex(ValueError, "regular file"):
                    save_server_settings(container.server, loaded, {"motd": "new"})
                self.assertEqual(container.writes, [])

    def test_readonly_mode_does_not_attempt_write(self):
        with _SettingsContainer() as container:
            loaded = load_server_settings(container.server)
            container.mode = 0o444
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                save_server_settings(container.server, loaded, {"motd": "new"})
            self.assertEqual(container.writes, [])

    def test_copy_failures_are_actionable_and_do_not_leak_output(self):
        with _SettingsContainer() as container:
            container.fail_read = True
            with self.assertRaisesRegex(RuntimeError, "Cannot read") as error:
                load_server_settings(container.server)
            self.assertNotIn("DO_NOT_LEAK", str(error.exception))
            self.assertEqual(container.writes, [])
        with _SettingsContainer() as container:
            loaded = load_server_settings(container.server)
            container.fail_read = True
            with self.assertRaisesRegex(RuntimeError, "Cannot read"):
                save_server_settings(container.server, loaded, {"motd": "new"})
            self.assertEqual(container.writes, [])
        with _SettingsContainer() as container:
            loaded = load_server_settings(container.server)
            container.fail_write = True
            with self.assertRaisesRegex(RuntimeError, "read-only mounts") as error:
                save_server_settings(container.server, loaded, {"motd": "new"})
            self.assertNotIn("DO_NOT_LEAK", str(error.exception))
            self.assertEqual(container.raw, loaded.raw)
            self.assertEqual(len(container.writes), 1)
        with _SettingsContainer() as container:
            loaded = load_server_settings(container.server)
            container.write_exception = subprocess.TimeoutExpired("DO_NOT_LEAK", 120)
            with self.assertRaisesRegex(RuntimeError, "partially completed") as error:
                save_server_settings(container.server, loaded, {"motd": "new"})
            self.assertNotIn("DO_NOT_LEAK", str(error.exception))

    def test_invalid_or_ambiguous_copy_streams_are_never_written(self):
        empty = io.BytesIO()
        with tarfile.open(fileobj=empty, mode="w"):
            pass
        ambiguous = io.BytesIO()
        with tarfile.open(fileobj=ambiguous, mode="w") as archive:
            for _ in range(2):
                member = tarfile.TarInfo("server.properties")
                member.size = 9
                archive.addfile(member, io.BytesIO(b"motd=old\n"))
        wrong_name = io.BytesIO()
        with tarfile.open(fileobj=wrong_name, mode="w") as archive:
            member = tarfile.TarInfo("not-server.properties")
            member.size = 9
            archive.addfile(member, io.BytesIO(b"motd=old\n"))
        for stream in (b"invalid", empty.getvalue(), ambiguous.getvalue(), wrong_name.getvalue()):
            with self.subTest(stream=stream[:10]), _SettingsContainer() as container:
                loaded = load_server_settings(container.server)
                with patch("mc_admin_core._copy_path_stream", return_value=stream):
                    with self.assertRaises(ValueError):
                        save_server_settings(container.server, loaded, {"motd": "new"})
                    with self.assertRaises(ValueError):
                        load_server_settings(container.server)
                self.assertEqual(container.writes, [])
                self.assertEqual(_properties_from_tar(stream), {})

    def test_all_catalogued_boolean_and_choice_values_are_editable(self):
        settings = [setting for setting in SETTINGS if setting.kind in ("bool", "choice")]
        raw = "".join(
            f"{setting.key}={'false' if setting.kind == 'bool' else setting.choices[0]}\n"
            for setting in settings
        ).encode()
        with _SettingsContainer(raw) as container:
            loaded = load_server_settings(container.server)
            changes = {setting.key: "true" for setting in settings if setting.kind == "bool"}
            loaded = save_server_settings(container.server, loaded, changes)
            for key in changes:
                self.assertEqual(loaded.values[key], "true")
            for setting in settings:
                for choice in setting.choices:
                    loaded = save_server_settings(container.server, loaded, {setting.key: choice})
                    self.assertEqual(loaded.values[setting.key], choice)

    def test_reloaded_file_not_assumed_to_match_write(self):
        with _SettingsContainer() as container:
            loaded = load_server_settings(container.server)
            container.after_write = b"motd=startup managed\n"
            with self.assertRaisesRegex(RuntimeError, "did not retain"):
                save_server_settings(container.server, loaded, {"motd": "new"})

    def test_metadata_parser_is_tolerant_and_decodes_motd(self):
        self.assertEqual(_properties_from_tar(b"not a tar"), {})
        self.assertEqual(_properties_from_tar(_settings_tar(b"motd=a\\u00e9\\nnext\n"))["motd"], "aé\nnext")
        self.assertEqual(_properties_from_tar(_settings_tar(b"", tarfile.SYMTYPE)), {})


if __name__ == "__main__":
    unittest.main()
