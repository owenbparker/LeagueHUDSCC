"""Start the HUD:  python -m lolhud   (add --demo to run without League)."""

import argparse
import os

from .app import HudApp, serve
from .config import Config
from .source import DemoSource, FileSource


def main():
    parser = argparse.ArgumentParser(description="Live League of Legends broadcast HUD")
    parser.add_argument("--demo", action="store_true", help="simulate a game instead of reading League")
    parser.add_argument("--demo-speed", type=float, default=8.0, help="demo game seconds per real second")
    parser.add_argument("--replay", metavar="FILE", help="read a saved allgamedata JSON file instead")
    parser.add_argument("--port", type=int, help="web server port (default from config.json)")
    parser.add_argument("--host", help="interface to listen on; 0.0.0.0 lets other PCs on your network connect")
    args = parser.parse_args()

    config = Config()
    if not os.path.exists(config.path):
        config.save()
    cfg = config.get()
    source = None
    if args.demo:
        source = DemoSource(speed=args.demo_speed)
    elif args.replay:
        source = FileSource(args.replay)

    app = HudApp(config, source)
    host, port = args.host or cfg["http_host"], args.port or cfg["http_port"]
    server = serve(app, host, port)
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"""
  League HUD is running{' (DEMO GAME)' if args.demo else ''}
  Control panel : http://{shown}:{port}/control
  OBS / vMix browser source (1920x1080): http://{shown}:{port}/overlay
  Data for vMix data sources : http://{shown}:{port}/hud.csv  (also /hud.json, /hud.xml)
  Files written to : {config.output_dir()}
  Ctrl+C to stop.
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        server.server_close()


if __name__ == "__main__":
    main()
