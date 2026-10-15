// Bitfocus Companion module for the SCC League HUD app.
//
// It polls the app's GET /api/companion a few times a second (for the
// feedbacks and variables) and sends actions to POST /api/companion. See
// lolhud/companion.py in the app.

const { InstanceBase, Regex, runEntrypoint, InstanceStatus, combineRgb } = require('@companion-module/base')
const { TOGGLES, STATES, VALUES, COMMANDS, VARIABLES, choices } = require('./definitions')

const GREEN = combineRgb(47, 199, 140)
const DARK = combineRgb(20, 22, 25)
const WHITE = combineRgb(255, 255, 255)
const BLACK = combineRgb(0, 0, 0)

class LeagueHudInstance extends InstanceBase {
	async init(config) {
		this.config = config
		this.data = null
		this.failed = null // not known yet
		this.setActionDefinitions(this.actions())
		this.setFeedbackDefinitions(this.feedbacks())
		this.setVariableDefinitions(Object.entries(VARIABLES).map(([variableId, name]) => ({ variableId, name })))
		this.setPresetDefinitions(this.presets())
		this.startPolling()
	}

	async destroy() {
		clearTimeout(this.timer)
		this.stopped = true
	}

	async configUpdated(config) {
		this.config = config
		this.startPolling()
	}

	getConfigFields() {
		return [
			{
				type: 'static-text',
				id: 'info',
				width: 12,
				label: '',
				value:
					"Enter the address shown in the League HUD app's Companion section (127.0.0.1 if the app runs on this PC).",
			},
			{ type: 'textinput', id: 'host', label: 'League HUD PC address', width: 8, default: '127.0.0.1', regex: Regex.HOSTNAME },
			{ type: 'textinput', id: 'port', label: 'Port', width: 4, default: '8777', regex: Regex.PORT },
			{ type: 'number', id: 'poll', label: 'Check every (ms)', width: 4, default: 250, min: 100, max: 5000 },
		]
	}

	base() {
		return `http://${(this.config.host || '127.0.0.1').trim()}:${Number(this.config.port) || 8777}`
	}

	// --- talking to the app ----------------------------------------------

	startPolling() {
		clearTimeout(this.timer)
		this.updateStatus(InstanceStatus.Connecting)
		const tick = async () => {
			if (this.stopped) return
			await this.refresh()
			this.timer = setTimeout(tick, Math.max(100, Number(this.config.poll) || 250))
		}
		tick()
	}

	async refresh() {
		try {
			const r = await fetch(this.base() + '/api/companion', { signal: AbortSignal.timeout(2000) })
			if (!r.ok) throw new Error(`HTTP ${r.status}`)
			this.apply(await r.json())
			if (this.failed !== false) this.updateStatus(InstanceStatus.Ok)
			this.failed = false
		} catch (e) {
			if (!this.failed) {
				this.log('warn', `Can't reach the League HUD app at ${this.base()}: ${e.message}`)
				this.updateStatus(InstanceStatus.ConnectionFailure, e.message)
			}
			this.failed = true
		}
	}

	apply(data) {
		this.data = data
		this.setVariableValues(data.variables || {})
		this.checkFeedbacks('setting_on')
	}

	async send(body) {
		try {
			const r = await fetch(this.base() + '/api/companion', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body),
				signal: AbortSignal.timeout(5000),
			})
			const data = await r.json()
			if (!r.ok || data.error) throw new Error(data.error || `HTTP ${r.status}`)
			this.apply(data)
		} catch (e) {
			this.log('error', `League HUD: ${e.message}`)
		}
	}

	// --- definitions -----------------------------------------------------

	actions() {
		return {
			setting: {
				name: 'Setting: on / off / toggle',
				options: [
					{ type: 'dropdown', id: 'setting', label: 'Setting', default: 'cams_on', choices: choices(TOGGLES) },
					{
						type: 'dropdown',
						id: 'mode',
						label: 'Do',
						default: 'toggle',
						choices: [
							{ id: 'toggle', label: 'Toggle' },
							{ id: 'on', label: 'On' },
							{ id: 'off', label: 'Off' },
						],
					},
				],
				callback: (e) => this.send({ action: e.options.mode, id: e.options.setting }),
			},
			value_set: {
				name: 'Set a value (background video, auto timers count)',
				options: [
					{ type: 'dropdown', id: 'value', label: 'Value', default: 'bg_speed', choices: valueChoices() },
					{ type: 'number', id: 'amount', label: 'To (speed 0.1-3, brightness 0.2-2.5 = 20-250%, auto timers 1-3)', default: 1, min: 0.1, max: 3, step: 0.05 },
				],
				callback: (e) => this.send({ action: 'set', id: e.options.value, value: e.options.amount }),
			},
			value_adjust: {
				name: 'Nudge a value up / down',
				options: [
					{ type: 'dropdown', id: 'value', label: 'Value', default: 'bg_speed', choices: valueChoices() },
					{ type: 'number', id: 'step', label: 'By (negative to lower)', default: 0.05, min: -3, max: 3, step: 0.05 },
				],
				callback: (e) => this.send({ action: 'adjust', id: e.options.value, value: e.options.step }),
			},
			run: {
				name: 'Run',
				options: [{ type: 'dropdown', id: 'command', label: 'Command', default: 'sync_all', choices: choices(COMMANDS) }],
				callback: (e) => this.send({ action: 'run', id: e.options.command }),
			},
		}
	}

	feedbacks() {
		return {
			setting_on: {
				type: 'boolean',
				name: 'Setting is on',
				description: 'Lights the button while a setting is on',
				defaultStyle: { bgcolor: GREEN, color: BLACK },
				options: [
					{
						type: 'dropdown',
						id: 'setting',
						label: 'Setting',
						default: 'cams_on',
						choices: choices({ ...TOGGLES, ...STATES }),
					},
				],
				callback: (fb) => !!(this.data && this.data.toggles && this.data.toggles[fb.options.setting]),
			},
		}
	}

	presets() {
		const presets = {}
		const style = (text) => ({ text, size: '14', color: WHITE, bgcolor: DARK })
		const lit = (setting) => [{ feedbackId: 'setting_on', options: { setting }, style: { bgcolor: GREEN, color: BLACK } }]
		for (const [id, label] of Object.entries(TOGGLES)) {
			const [category, name] = label.includes(': ') ? label.split(': ') : ['HUD', label]
			presets[`toggle_${id}`] = {
				type: 'button',
				category,
				name: label,
				style: style(name.length > 24 ? name.replace(/ \(.*\)$/, '') : name),
				steps: [{ down: [{ actionId: 'setting', options: { setting: id, mode: 'toggle' } }], up: [] }],
				feedbacks: lit(id),
			}
		}
		// [category, button text, lit while this is on]
		const runs = {
			sync_all: ['Match time', 'Sync all\n0:00', null],
			sync_gfx: ['Match time', 'Sync GFX\nto me', null],
			enable_time_control: ['Match time', 'Time control', 'time_control'],
			preview_fb_left: ['Animation', 'First blood\npreview L', null],
			preview_fb_right: ['Animation', 'First blood\npreview R', null],
		}
		for (const [id, [category, text, feedback]] of Object.entries(runs)) {
			presets[`run_${id}`] = {
				type: 'button',
				category,
				name: COMMANDS[id],
				style: style(text),
				steps: [{ down: [{ actionId: 'run', options: { command: id } }], up: [] }],
				feedbacks: feedback ? lit(feedback) : [],
			}
		}
		presets.clock = {
			type: 'button',
			category: 'Match time',
			name: "This PC's game time",
			style: style(`Game\n$(${this.label}:this_pc_game_time)`),
			steps: [],
			feedbacks: [],
		}
		for (const [id, v] of Object.entries(VALUES)) {
			for (const [dir, sign] of [
				['down', -1],
				['up', 1],
			]) {
				presets[`${id}_${dir}`] = {
					type: 'button',
					category: id === 'timers_auto_count' ? 'Objective timers' : 'Background video',
					name: `${v.name} ${dir}`,
					style: style(`${{ bg_speed: 'Speed', bg_brightness: 'Bright', timers_auto_count: 'Auto timers' }[id]} ${sign < 0 ? '−' : '+'}\n$(${this.label}:${id})`),
					steps: [{ down: [{ actionId: 'value_adjust', options: { value: id, step: sign * v.step * (v.step < 1 ? 2 : 1) } }], up: [] }],
					feedbacks: [],
				}
			}
		}
		return presets
	}
}

function valueChoices() {
	return Object.entries(VALUES).map(([id, v]) => ({ id, label: v.name }))
}

runEntrypoint(LeagueHudInstance, [])
