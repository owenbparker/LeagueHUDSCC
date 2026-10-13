# League HUD (SCC)

A live, LCS-style top bar for League of Legends broadcasts. It reads the game while you spectate and gives you the numbers two ways:

1. **A ready-made overlay**: a 1920×1080 browser source built from `design/lol-hud-SCC27.psd`, using the same positions and fonts. Drop it into OBS or vMix and you're done.
2. **Data for vMix / GT Title Designer**: a CSV (plus JSON and XML) that updates every second, as a file and as a URL, so you can drive your own GT title.

It also has a control panel for team names, logos, series score and sides.

![Overlay](docs/overlay.png)

## What it shows

| On the bar | Field name(s) |
|---|---|
| Team tricode, logo, colour | `TRI-CODE LEFT`, `LOGO LEFT`, `COLOR LEFT` (hex, e.g. #0a6d4c) (and `RIGHT`) |
| Turrets destroyed | `TURRETS LEFT` |
| Team gold, and the lead | `GOLD LEFT` (31.0K), `GOLD RAW LEFT` (31012), `GOLD DIFF LEFT` (+2.9K, only on the leading side) |
| Kills | `KILLS LEFT` |
| Game clock | `TIMER` |
| Void grubs | `VOID GRUB LEFT` |
| Series pips | `BO3 1 LEFT` (image path, lit when won), `BO3 1 LEFT WIN` (1/0) |
| Objective timers | `NEXT GRUBS`, `NEXT DRAGON`, `NEXT HERALD`, `NEXT ELDER`, `NEXT BARON`: countdown, `LIVE`, or blank once the objective is gone for the game (elder: blank until a team takes soul). Also blank when switched off in the control panel. `NEXT DRAGON IS ELDER` |
| Buffs | `BARON BUFF SIDE`, `BARON BUFF TIME`, `ELDER BUFF SIDE`, `ELDER BUFF TIME` |
| Other objectives | `DRAGONS LEFT`, `DRAGON TYPES LEFT` (Infernal,Ocean), `HERALDS`, `BARONS`, `ELDERS`, `ATAKHANS`, `INHIBS` |
| Players 1–5 left, 6–10 right | `P1 NAME`, `P1 CHAMPION`, `P1 CHAMPION ICON`, `P1 LEVEL`, `P1 KDA`, `P1 CS`, `P1 DEAD`, `P1 RESPAWN` |

The field names match your PSD layer names (`KILLS LEFT`, `GOLD LEFT`, `TRI-CODE LEFT`, `TIMER`, `VOID GRUB LEFT`…), so if you build the GT title from the PSD the names already line up.

## Install on Windows

1. Download this project as a zip (on GitHub: **Code -> Download ZIP**) and **extract it** (right-click -> Extract All). Don't run the installer from inside the zip.
2. Double-click **`install.bat`**.

It copies everything to `C:\LeagueHUD\LeagueHUD`, checks for Python (and offers to install it with winget if missing), and puts two shortcuts on your desktop: **League HUD** and **League HUD (Demo)**. Run it again any time to update; your settings, uploaded logos and output files are kept.

## Setup

You need **Python 3.9 or newer** ([python.org](https://www.python.org/downloads/); tick "Add Python to PATH" when installing). Nothing else to install.

Run it on **the PC that is spectating the game**. League only shares live game data with the machine running the game client.

- **Try it without League:** double-click `start-demo.bat`. It plays a simulated game so you can set up your scenes.
- **For a real game:** double-click `start-hud.bat`, then spectate (or replay) the match.

Then open the control panel at <http://localhost:8777/control>.

From a terminal: `python -m lolhud` (or `python -m lolhud --demo`). Options: `--port 8777`, `--host 0.0.0.0` (lets other PCs on your network read it), `--demo-speed 8`, `--replay saved_allgamedata.json`.

## Option A: the built-in overlay (OBS or vMix)

Add a **Browser** source / **Web Browser** input:

- URL: `http://localhost:8777/overlay`
- Size: 1920 × 1080, transparent background

Optional switches on the end of the URL:

| | |
|---|---|
| `?timers=0` | hide all objective timers in the top left (to hide individual ones, use the control panel) |
| `?diff=0` | hide the gold-lead tab (it slides out in the leading team's colour under their gold, and back in when the lead flips or disappears) |
| `?dragons=1` | show each team's dragons as coloured dots in the lower strip |
| `?idle=0` | hide the bar when no game is running |

Combine them with `&`, e.g. `/overlay?timers=0&dragons=1`. The team panels, the bar's background lines (blending from one team's colour to the other) and the series pips all follow the team colours set in the control panel. The clock ticks smoothly and freezes if the game is paused.

## Option B: GT Title Designer + vMix Data Sources

1. In GT Title Designer, build your title. Easiest: import `design/lol-hud-SCC27.psd` and keep the layer names.
2. In vMix: **Settings → Data Sources → Add**, then either:
   - **Excel/CSV**: point it at `output\hud.csv` inside this folder, or
   - **JSON** (or **XML**): URL `http://localhost:8777/hud.json` (or `/hud.xml`).
   Set the refresh to 1 second.
3. Open your title's **Data Source** bindings and connect each field. Logos and series pips are image paths, so bind them to image layers.

The CSV is one header row plus one data row. The file is swapped in whole each time, so vMix never reads half a file. In XML, spaces and dashes in field names become `_` (`TRI_CODE_LEFT`).

If vMix runs on a different PC, start the HUD with `--host 0.0.0.0` and use `http://<spectating-PC-IP>:8777/hud.json`.

## The control panel

- **Teams:** tricode, name, logo (upload or pick), team colour (colour picker, or type a hex code), series wins (+/−).
- **Swap teams left/right:** swaps names, logos and scores.
- **Left team is on red side:** by default the left of the bar is blue side. Tick this when the left team plays red.
- **Series:** Bo1 / Bo3 / Bo5 (pips follow).
- **Objective timers:** switch Void Grubs, Dragon, Rift Herald, Elder Drake and Baron on or off. Each shows while its objective is counting down or LIVE, and drops off once it's gone for the game; the rest close up.
- **Gold:** see below.
- A live preview, and every field with its current value (click a name to copy it).

Settings are saved to `config.json` and survive restarts.

## About gold

League's game client doesn't share other players' gold (LCS gets it from Riot's private esports feed). The HUD offers three options:

- **Estimate** (default): works out earned gold from game time, CS, kills, assists, turrets and barons, and never goes below the value of the items a team holds. Usually within about 10%. If it runs high or low against the in-game scoreboard, change **Estimate adjustment** (e.g. 1.05).
- **Items:** the value of items held. Always a little low, since it ignores unspent gold.
- **Manual:** type the numbers in.

Item prices and champion icons come from Riot's Data Dragon. They are downloaded on first run and cached in `output/`.

## Things to check against the current patch

- Riot moves objective spawn times between seasons. Set them in `config.json` under `timers` (seconds). Defaults follow the 2026 season: dragon 5:00 (respawn 5:00), grubs 8:00 (one camp of three, gone at 14:45), herald 15:00 (once, gone at 19:45), baron 20:00 (respawn 6:00), elder 6:00 after soul and between elders.
- Void grubs and Atakhan are counted from any game event whose name contains "Horde"/"Grub" or "Atakhan". If Riot adds an event the HUD doesn't use yet, the control panel lists it under "New game events not used yet". Send it over and it can be added.

## Files

```
lolhud/          the app (Python, standard library only)
  source.py        reads the game (Riot Live Client Data API) or runs the demo game
  state.py         turns game data into HUD fields
  gold.py          gold estimate + Data Dragon prices/icons
  outputs.py       CSV / JSON / XML
  app.py           web server, control panel API
web/overlay.html   the broadcast overlay
web/control.html   the control panel
web/assets/        images exported from the PSD (bar, its background lines, team panels, logos, pips)
tools/split_bar.py rebuilds the bar and line images from design/hud_bar.png
web/fonts/         Heathergreen, Inter
design/            the original PSD
tests/             python -m unittest discover -s tests
```
