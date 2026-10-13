"""Start the HUD without a window:  python -m lolhud   (add --demo to run without League).

For the desktop app, run LeagueHUD.exe (or python -m lolhud.gui).
"""

import argparse
import os
import time

from .app import HudApp, start
from .config import Config
from .source import DemoSource, FileSource


def build_app(args):
    config = Config()
    if not os.path.exists(config.path):
        config.save()
    if args.port:
        config.update({"http_port": args.port})
    if args.host:
        config.update({"http_host": args.host, "allow_network": args.host == "0.0.0.0"})
    if args.demo:
        return HudApp(config, DemoSource(speed=args.demo_speed), mode="demo")
    if args.replay:
        return HudApp(config, FileSource(args.replay), mode="replay")
    return HudApp(config)


def add_arguments(parser):
    parser.add_argument("--demo", action="store_true", help="simulate a game instead of reading League")
    parser.add_argument("--demo-speed", type=float, default=8.0, help="demo game seconds per real second")
    parser.add_argument("--replay", metavar="FILE", help="read a saved allgamedata JSON file instead")
    parser.add_argument("--port", type=int, help="web server port (saved to config.json)")
    parser.add_argument("--host", help="interface to listen on; 0.0.0.0 lets other PCs on your network connect")


def main():
    parser = argparse.ArgumentParser(description="Live League of Legends broadcast HUD")
    add_arguments(parser)
    app = build_app(parser.parse_args())
    start(app)
    print("  Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()


if __name__ == "__main__":
    main()
