import io
import json
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mc_admin_core import (
    DiscoveredServer,
    Kit,
    Location,
    ServerConfig,
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
    list_backups,
    load_custom_kits,
    load_last_server,
    load_locations,
    load_server_configs,
    locations_path_for,
    parse_dimension,
    parse_inventory,
    parse_kit_items,
    parse_players,
    parse_position,
    prune_backups,
    rcon,
    restore_procedure,
    rule_name,
    safe_filename,
    save_custom_kits,
    save_locations,
    scan_servers,
    server_config_for,
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


class BindingTests(unittest.TestCase):
    def test_tab_hotkeys_bound(self):
        from mc_admin_tui import MinecraftAdminApp

        keymap: dict[str, str] = {}
        for binding in MinecraftAdminApp.BINDINGS:
            if isinstance(binding, tuple):
                key, action = binding[0], binding[1]
            else:
                key, action = binding.key, binding.action
            for key_part in str(key).split(","):
                keymap[key_part] = action
        expected = {
            "g": "give-tab",
            "k": "kits-tab",
            "t": "teleport-tab",
            "w": "world-tab",
            "s": "server-tab",
            "a": "activity-tab",
            "f": "fun-tab",
        }
        for letter, tab in expected.items():
            for prefix in ("", "ctrl+", "alt+"):
                action = keymap.get(f"{prefix}{letter}")
                self.assertEqual(
                    action, f"show_tab('{tab}')", f"{prefix}{letter} -> {action}"
                )
        for prefix in ("", "ctrl+", "alt+"):
            self.assertEqual(keymap[f"{prefix}b"], "open_backups")
        self.assertEqual(keymap["alt+r"], "scan_servers")


if __name__ == "__main__":
    unittest.main()
