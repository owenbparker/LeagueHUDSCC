import os
import tempfile
import unittest

from lolhud.config import Config
from lolhud.discover import find_huds


class DiscoverTests(unittest.TestCase):
    def test_finds_the_gfx_hud_not_observers(self):
        from lolhud.app import HudApp, start
        with tempfile.TemporaryDirectory() as d:
            config = Config(os.path.join(d, "config.json"))
            config.update({"http_port": 8812, "https_port": 8814, "output_dir": os.path.join(d, "out")})
            app = HudApp(config)
            app.updater.check_in_background = lambda: None
            start(app)
            try:
                def is_gfx(state):
                    return "detail" in state and (state.get("app") or {}).get("role") != "observer"
                # "This PC" is 127.0.0.2: the HUD on 127.0.0.1 is on its network.
                hits = find_huds(8812, is_gfx, addresses=["127.0.0.2"])
                self.assertEqual([h["ip"] for h in hits], ["127.0.0.1"])
                app.role = "observer"
                self.assertEqual(find_huds(8812, is_gfx, addresses=["127.0.0.2"]), [])
            finally:
                app.stop()


if __name__ == "__main__":
    unittest.main()
