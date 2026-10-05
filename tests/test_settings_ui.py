import asyncio
import unittest

from textual.app import App
from textual.widgets import TextArea

from mc_admin_tui import SettingsScreen
from test_core import _SettingsContainer


class SettingsScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_modal_can_close_reopen_and_shut_down(self):
        # Stateful container transport keeps real properties parsing/persistence;
        # this regression concerns screen teardown, not forwarding mocked values.
        with _SettingsContainer(b"motd=Before\n") as container:
            app = App()
            async with app.run_test(size=(100, 40)) as pilot:
                root = app.screen
                modal = SettingsScreen(container.server)
                await app.push_screen(modal)
                await app.workers.wait_for_complete()
                modal.query_one("#settings-motd", TextArea).load_text("After\nSecond line")
                await pilot.pause()
                await pilot.click("#settings-save")
                await app.workers.wait_for_complete()
                await asyncio.wait_for(pilot.press("escape"), timeout=3)
                await pilot.pause()
                self.assertIs(app.screen, root)

                reopened = SettingsScreen(container.server)
                await app.push_screen(reopened)
                await app.workers.wait_for_complete()
                self.assertEqual(
                    reopened.query_one("#settings-motd", TextArea).text,
                    "After\nSecond line",
                )
                await asyncio.wait_for(pilot.press("escape"), timeout=3)
                await pilot.pause()
                self.assertIs(app.screen, root)


if __name__ == "__main__":
    unittest.main()
