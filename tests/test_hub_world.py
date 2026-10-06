"""Consumer-visible world geometry and untrusted sign-text boundaries."""
import json
import re
import unittest

import mc_hub_world as world


_MUTATION = re.compile(r"^(?:setblock|data merge block) (-?\d+) (-?\d+) (-?\d+) ")
_FILL = re.compile(r"^fill (-?\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+) (-?\d+) ")


def mutation_box(command):
    """Read the public Minecraft block command's affected coordinates."""
    fill = _FILL.match(command)
    if fill:
        values = tuple(map(int, fill.groups()))
        return values[:3], values[3:]
    single = _MUTATION.match(command)
    if single:
        position = tuple(map(int, single.groups()))
        return position, position
    return None


def covers(box, position):
    low, high = box
    return all(a <= coordinate <= b for a, coordinate, b in zip(low, position, high))


def sign_data(command):
    # Generator emits the JSON-compatible subset of SNBT, so decode the actual
    # generated command payload rather than looking for source-code fragments.
    return json.JSONDecoder().raw_decode(command[command.index("{"):])[0]


class HubWorldTests(unittest.TestCase):
    def test_all_stable_bays_stay_inside_their_own_chunks(self):
        portal_boxes = []
        for slot_id in range(1, world.MAX_SLOTS + 1):
            x, z = world.bay_origin(slot_id)
            self.assertTrue(0 <= x <= 240)
            self.assertTrue(16 <= z <= 128)
            for command in world.bay_commands({"id": slot_id, "name": f"server-{slot_id}"}):
                box = mutation_box(command)
                if box:
                    for corner in box:
                        self.assertTrue(x <= corner[0] < x + 16, command)
                        self.assertTrue(z <= corner[2] < z + 16, command)
            arguments = dict(item.split("=") for item in world.portal_selector(slot_id).split(","))
            low = tuple(int(arguments[axis]) for axis in ("x", "y", "z"))
            # Positive selector extents cover dx+1 blocks, not merely dx blocks.
            high = tuple(low[index] + int(arguments["d" + axis])
                         for index, axis in enumerate(("x", "y", "z")))
            self.assertTrue(x <= low[0] <= high[0] < x + 16)
            self.assertTrue(z <= low[2] <= high[2] < z + 16)
            for other_low, other_high in portal_boxes:
                self.assertTrue(any(high[i] < other_low[i] or other_high[i] < low[i]
                                    for i in range(3)))
            portal_boxes.append((low, high))
        self.assertEqual(world.bay_origin(16), (240, 16))
        self.assertEqual(world.bay_origin(17), (0, 32))
        self.assertEqual(world.bay_origin(128), (240, 128))

    def test_incremental_status_and_rebuild_preserve_owner_label(self):
        slots = [{"id": slot_id, "name": f"server-{slot_id}"} for slot_id in (1, 16, 17, 128)]
        rebuilt = world.build_commands(slots)
        for slot in slots:
            position = world.user_sign_position(slot["id"])
            for commands in (world.bay_commands(slot), rebuilt):
                touching = [command for command in commands
                            if mutation_box(command) and covers(mutation_box(command), position)]
                self.assertEqual(len(touching), 1)
                self.assertTrue(touching[0].endswith(" keep"))
                payload = sign_data(touching[0])
                self.assertFalse(payload["is_waxed"])
                self.assertFalse(payload["allow_op_features"])
            for state in ("Online", "Offline", "Starting", "Stopping", "Missing", "Error"):
                commands = world.status_commands(slot, state, controllable=True, joinable=True)
                self.assertFalse(any(covers(mutation_box(command), position) for command in commands))

    def test_untrusted_name_and_detail_remain_in_plain_text_components(self):
        name = 'a"},click_event:{command:"stop"}\\\n\r\x00\u2603'
        slot = {"id": 128, "name": name}
        commands = world.bay_commands(slot)
        name_position = (247, 65, 136)
        name_command = next(command for command in commands
                            if command.startswith("setblock ") and mutation_box(command)
                            and covers(mutation_box(command), name_position))
        payload = sign_data(name_command)
        self.assertEqual("".join(line["text"] for line in payload["front_text"]["messages"]), name)
        self.assertTrue(payload["is_waxed"])
        self.assertFalse(payload["allow_op_features"])
        self.assertTrue(all("\n" not in command and "\r" not in command and "\x00" not in command
                            for command in commands))
        detail = '"}\\\n\x00stop'
        updates = world.status_commands(slot, "Online", detail=detail, controllable=True)
        status = sign_data(updates[0])
        self.assertEqual(status["front_text"]["messages"][2]["text"], detail)
        events = [sign_data(command)["front_text"]["messages"][0]["click_event"]
                  for command in updates[1:3]]
        self.assertEqual(events, [
            {"action": "run_command", "command": "trigger hub_start set 128"},
            {"action": "run_command", "command": "trigger hub_stop set 128"},
        ])
        for command in updates[1:3]:
            self.assertTrue(sign_data(command)["allow_op_features"])
            self.assertTrue(sign_data(command)["is_waxed"])
        disabled = world.status_commands(slot, "Online", controllable=False)
        for command in disabled[1:3]:
            self.assertFalse(sign_data(command)["allow_op_features"])
            self.assertTrue(all("click_event" not in line
                                for line in sign_data(command)["front_text"]["messages"]))

    def test_slot_boundaries_and_duplicate_identity_are_rejected(self):
        for invalid in (0, 129, -1, True, "1", 1.0):
            with self.assertRaises(ValueError):
                world.portal_selector(invalid)
        for slots in (
            [{"id": 1, "name": "a"}, {"id": 1, "name": "b"}],
            [{"id": 1, "name": "a"}, {"id": 2, "name": "a"}],
        ):
            for generate in (world.build_commands, world.datapack_files):
                with self.assertRaises(ValueError):
                    generate(slots)
        self.assertEqual(world.build_commands([])[0], "forceload add 0 0 255 175")

    def test_community_area_has_sign_supply_wall_and_moderated_channels(self):
        commands = world.build_commands([])
        self.assertIn("fill 24 64 166 71 67 166 minecraft:polished_blackstone", commands)
        self.assertIn("setblock 20 64 156 minecraft:chest[facing=south]", commands)
        self.assertIn("setblock 80 64 156 minecraft:chest[facing=south]", commands)
        supply = next(command for command in commands if command.startswith("data merge block 20 64 156"))
        self.assertIn('minecraft:can_place_on', supply)
        self.assertIn('minecraft:polished_blackstone', supply)
        message = next(command for command in commands if command.startswith("setblock 24 64 164"))
        payload = sign_data(message)
        self.assertEqual("MESSAGE WALL", payload["front_text"]["messages"][0]["text"])
        self.assertFalse(any("click_event" in line for line in payload["front_text"]["messages"]))

    def test_community_display_text_rejects_control_and_formatting_characters(self):
        for invalid in ("line\nnext", "line\rnext", "line\x00", "line§c"):
            with self.assertRaises(ValueError):
                world._community_sign(0, 64, 0, [invalid])

    def test_rebuild_preserves_community_submission_containers(self):
        fresh = world.build_commands([])
        rebuilt = world.build_commands([], preserve_submissions=True)
        for commands in (fresh, rebuilt):
            self.assertIn("setblock 20 64 156 minecraft:chest[facing=south]" +
                          (" keep" if commands is rebuilt else ""), commands)
            self.assertIn("setblock 80 64 156 minecraft:chest[facing=south]" +
                          (" keep" if commands is rebuilt else ""), commands)
        self.assertTrue(any(command.startswith("data merge block 20 64 156") for command in fresh))
        self.assertFalse(any(command.startswith("data merge block 20 64 156") for command in rebuilt))

    def test_spawn_floor_and_every_fill_fit_default_modification_limit(self):
        commands = world.build_commands([{"id": 128, "name": "last"}])
        spawn_floor = (world.SPAWN[0], world.SPAWN[1] - 1, world.SPAWN[2])
        self.assertTrue(any(mutation_box(command) and covers(mutation_box(command), spawn_floor)
                            for command in commands))
        for command in commands:
            if command.startswith("fill "):
                low, high = mutation_box(command)
                volume = 1
                for start, end in zip(low, high):
                    volume *= end - start + 1
                self.assertLessEqual(volume, 32768, command)


if __name__ == "__main__":
    unittest.main()
