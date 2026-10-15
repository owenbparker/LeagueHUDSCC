## SCC League HUD

Controls the SCC League HUD app (GFX Controller or Observer Client) from Companion.

**Setup**

1. In the League HUD app, open the **Companion** section. It shows the address and port to use.
2. If Companion runs on a different PC, tick **Let Companion on another PC control this app** there.
3. Add an **SCC League HUD** connection in Companion and enter that address and port.

Add one connection per app you want to control (for example, the GFX PC and an observer PC).

**Actions**

- **Setting: on / off / toggle**: webcams, objective timers, claimed objective counters, first blood animation, background tint, HUD on game.
- **Background video: set / nudge**: playback speed and brightness.
- **Run**: sync all clients to 0:00, sync GFX to my time (observer only), turn on time control, preview first blood.

**Feedbacks**

- **Setting is on**: turns the button green while a setting is on (also: time control on, game connected, HUD on game).

**Variables**

`game_time`, `this_pc_game_time`, `match_time_status`, `role`, `bg_speed`, `bg_brightness`, `tricode_left`, `tricode_right`, `kills_left`, `kills_right`, `gold_left`, `gold_right`.

The **Presets** tab has a ready-made button for everything.
