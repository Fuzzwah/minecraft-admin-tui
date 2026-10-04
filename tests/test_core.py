import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mc_admin_core import (
    DiscoveredServer,
    Location,
    ServerConfig,
    container_env,
    container_image,
    container_name,
    container_workdir,
    find_matches,
    is_minecraft_image,
    load_last_server,
    load_locations,
    load_server_configs,
    locations_path_for,
    parse_dimension,
    parse_players,
    parse_position,
    rcon,
    safe_filename,
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


if __name__ == "__main__":
    unittest.main()
