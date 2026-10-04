import tempfile
import unittest
from pathlib import Path

from mc_admin_core import (
    Location,
    find_matches,
    load_locations,
    parse_dimension,
    parse_players,
    parse_position,
    save_locations,
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


if __name__ == "__main__":
    unittest.main()
