import json
import unittest
from unittest.mock import patch

from textual.widgets import TabbedContent

from mc_admin_core import ServerConfig
from mc_admin_tui import MinecraftAdminApp


class PlayerActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_bonk_sends_title_json_to_selected_player(self):
        app = MinecraftAdminApp(None)
        with patch.object(MinecraftAdminApp, "scan_servers"), \
             patch("mc_admin_tui.rcon", return_value="Title displayed") as send:
            async with app.run_test(size=(120, 40)) as pilot:
                app.config = ServerConfig("podman", "mc_test")
                app.selected_player = "BeatiBubbles"
                app.query_one(TabbedContent).active = "fun-tab"
                await pilot.pause()
                await pilot.click("#fun-bonk")
                await app.workers.wait_for_complete()
                await pilot.pause()

                self.assertEqual(send.call_count, 1)
                command = send.call_args.args[1]
                prefix, payload = command.split(" title ", 1)
                self.assertEqual(prefix, "title BeatiBubbles")
                self.assertEqual(
                    json.loads(payload),
                    {"text": "BONK!", "color": "gold", "bold": True},
                )


if __name__ == "__main__":
    unittest.main()
