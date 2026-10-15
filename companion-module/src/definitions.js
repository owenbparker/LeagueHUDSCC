// What the League HUD app can be told to do (mirrors lolhud/companion.py).

const TOGGLES = {
	hud_on_game: 'HUD on game (the HUD window over League)',
	cams_on: 'Webcams: show on the HUD',
	cams_auto: 'Webcams: switch to the players in the action',
	cams_director: "Webcams: read this PC's screen",
	first_blood: 'Animation: first blood',
	counter_dragons: 'Claimed objectives: dragons',
	counter_heralds: 'Claimed objectives: Rift Heralds',
	counter_grubs: 'Claimed objectives: void grubs',
	timers_auto: 'Objective timers: auto (only the next few)',
	timer_grubs: 'Objective timer: Void Grubs',
	timer_dragon: 'Objective timer: Dragon',
	timer_herald: 'Objective timer: Rift Herald',
	timer_elder: 'Objective timer: Elder Drake',
	timer_baron: 'Objective timer: Baron',
	bg_tint: 'Background video: tint with team colours',
}

// On/off states that can only be read (feedbacks), not toggled.
const STATES = {
	time_control: "Time control on (League's Replay API)",
	connected: 'Game connected',
}

const VALUES = {
	bg_speed: { name: 'Background video: playback speed', min: 0.1, max: 3, step: 0.05, unit: 'x' },
	bg_brightness: { name: 'Background video: brightness', min: 0.2, max: 2.5, step: 0.05, unit: '' },
	timers_auto_count: { name: 'Objective timers: how many auto shows (1-3)', min: 1, max: 3, step: 1, unit: '' },
}

const COMMANDS = {
	sync_all: 'Match time: sync all clients to 0:00',
	sync_gfx: 'Match time: sync GFX to my time (Observer Client)',
	enable_time_control: 'Match time: turn on time control',
	preview_fb_left: 'Animation: preview first blood, left team',
	preview_fb_right: 'Animation: preview first blood, right team',
}

const VARIABLES = {
	game_time: 'Game time (GFX PC: the HUD clock)',
	this_pc_game_time: "This PC's League client time (Match time)",
	match_time_status: 'Match time: last sync result',
	role: 'Client (GFX or Observer)',
	bg_speed: 'Background video playback speed',
	bg_brightness: 'Background video brightness',
	timers_auto_count: 'How many objective timers auto shows',
	tricode_left: 'Left team tri-code',
	tricode_right: 'Right team tri-code',
	kills_left: 'Left team kills',
	kills_right: 'Right team kills',
	gold_left: 'Left team gold',
	gold_right: 'Right team gold',
}

const choices = (obj) => Object.entries(obj).map(([id, label]) => ({ id, label }))

module.exports = { TOGGLES, STATES, VALUES, COMMANDS, VARIABLES, choices }
