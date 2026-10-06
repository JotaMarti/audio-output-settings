#!/usr/bin/env python3
"""Audio Output Settings - per-output sample rate / bit depth for PipeWire.

Writes:
  ~/.config/wireplumber/wireplumber.conf.d/60-pw-output-settings.conf  (per-output rules)
  ~/.config/pipewire/pipewire.conf.d/60-pw-output-settings.conf        (buffer size)
  ~/.config/pipewire/client.conf.d/60-pw-output-settings.conf          (resampler quality)
  ~/.config/pipewire/pipewire-pulse.conf.d/60-pw-output-settings.conf  (resampler quality)
and restarts the PipeWire user services to apply them.
"""
import glob
import json
import os
import re
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

APP_ID = "io.github.jmc.PwOutputSettings"
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
STATE_FILE = os.path.join(CONFIG_HOME, "pw-output-settings", "settings.json")
CONF_NAME = "60-pw-output-settings.conf"
WP_CONF = os.path.join(CONFIG_HOME, "wireplumber", "wireplumber.conf.d", CONF_NAME)
PW_CONF = os.path.join(CONFIG_HOME, "pipewire", "pipewire.conf.d", CONF_NAME)
CLIENT_CONF = os.path.join(CONFIG_HOME, "pipewire", "client.conf.d", CONF_NAME)
PULSE_CONF = os.path.join(CONFIG_HOME, "pipewire", "pipewire-pulse.conf.d", CONF_NAME)

STANDARD_RATES = [44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000, 705600, 768000]
DEFAULT_AUTO_RATES = [44100, 48000, 88200, 96000, 176400, 192000]
FORMAT_LABELS = {
    "S16LE": "16-bit",
    "S24LE": "24-bit (packed)",
    "S24_32LE": "24-bit (in 32)",
    "S32LE": "32-bit",
    "F32LE": "32-bit float",
}
ALSA_TO_SPA = {"S16_LE": "S16LE", "S24_3LE": "S24LE", "S24_LE": "S24_32LE", "S32_LE": "S32LE", "FLOAT_LE": "F32LE"}
QUANTA = [128, 256, 512, 1024, 2048, 4096]
DEFAULT_STATE = {"quantum": 1024, "resample_quality": 4, "outputs": {}}


def fmt_rate(rate):
    khz = rate / 1000
    return f"{khz:g} kHz"


# ---------------------------------------------------------------- PipeWire

def list_outputs():
    """Return ALSA output sinks with their supported rates/formats."""
    try:
        dump = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10).stdout)
    except Exception:
        return []
    outputs = []
    for obj in dump:
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Audio/Sink" or not str(props.get("node.name", "")).startswith("alsa_output."):
            continue
        rates, formats = _supported(info, props)
        outputs.append({
            "name": props["node.name"],
            "description": props.get("node.description") or props.get("node.nick") or props["node.name"],
            "card": props.get("alsa.card", props.get("api.alsa.pcm.card")),
            "device": props.get("alsa.device"),
            "rates": rates,
            "formats": formats,
        })
    outputs.sort(key=lambda o: o["description"])
    return outputs


def _supported(info, props):
    rates, formats = set(), []
    for fmt in (info.get("params") or {}).get("EnumFormat") or []:
        f = fmt.get("format")
        if isinstance(f, dict):
            cands = [v for k, v in f.items() if k != "default"]
        elif isinstance(f, str):
            cands = [f]
        else:
            cands = []
        for c in cands:
            if c in FORMAT_LABELS and c not in formats:
                formats.append(c)
        r = fmt.get("rate")
        if isinstance(r, dict):
            if "min" in r and "max" in r:
                rates.update(x for x in STANDARD_RATES if r["min"] <= x <= r["max"])
            rates.update(v for k, v in r.items() if k.startswith("alt") and isinstance(v, int))
        elif isinstance(r, int):
            rates.add(r)
    # Read the hardware's own capabilities from /proc: PipeWire's EnumFormat is narrowed by
    # our own audio.format override, so it can't tell us what else the device supports.
    card = props.get("alsa.card", props.get("api.alsa.pcm.card"))
    stream = f"/proc/asound/card{card}/stream0"
    if card is not None and os.path.exists(stream):
        # USB: exact rates and formats per altset.
        with open(stream) as fh:
            text = fh.read().split("Capture:")[0]
        exact = {int(x) for line in re.findall(r"Rates: (.*)", text) for x in re.findall(r"\d+", line)}
        if exact:
            rates = exact
        hw = [ALSA_TO_SPA[f] for f in re.findall(r"Format: (\S+)", text) if f in ALSA_TO_SPA]
        if hw:
            formats = list(dict.fromkeys(hw))
    elif card is not None:
        # HDA: codec lists sample bits; 20/24/32-bit samples travel in a 32-bit container.
        bits = set()
        for path in glob.glob(f"/proc/asound/card{card}/codec#*"):
            with open(path) as fh:
                for line in re.findall(r"PCM:\s*\n\s*bits \[[^\]]*\]: ([\d ]+)", fh.read()):
                    bits.update(int(b) for b in line.split())
        hw = (["S16LE"] if 16 in bits else []) + (["S32LE"] if bits & {20, 24, 32} else [])
        if hw:
            formats = hw
    if not rates:
        rates = {44100, 48000}
    order = list(FORMAT_LABELS)
    formats.sort(key=order.index)
    return sorted(rates), formats


def hw_status(output):
    """What the hardware is actually running at right now."""
    card, dev = output["card"], output["device"]
    paths = [f"/proc/asound/card{card}/pcm{dev}p/sub0/hw_params"] if dev is not None else \
        glob.glob(f"/proc/asound/card{card}/pcm*p/sub0/hw_params")
    for path in paths:
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            continue
        if text.strip() == "closed":
            continue
        fmt = re.search(r"format: (\S+)", text)
        rate = re.search(r"rate: (\d+)", text)
        if fmt and rate:
            bits = re.search(r"(\d+)", fmt.group(1))
            return f"Playing at {fmt_rate(int(rate.group(1)))} · {bits.group(1) if bits else '?'}-bit ({fmt.group(1)})"
    return "Idle"


# ---------------------------------------------------------------- config

def load_state():
    try:
        with open(STATE_FILE) as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}
    return {**DEFAULT_STATE, **state}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def write_config(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as fh:
        json.dump(state, fh, indent=2)

    header = "# Generated by Audio Output Settings - edits will be overwritten.\n"
    rules = []
    for name, o in state["outputs"].items():
        props = []
        if o.get("format"):
            props.append(f'audio.format = "{o["format"]}"')
        if o.get("rate"):
            props.append(f"audio.rate = {o['rate']}")
            props.append(f"audio.allowed-rates = [ {o['rate']} ]")
        elif o.get("allowed_rates"):
            props.append(f"audio.allowed-rates = [ {' '.join(map(str, sorted(o['allowed_rates'])))} ]")
        if not props:
            continue
        body = "\n".join(f"        {p}" for p in props)
        rules.append(
            "  {\n"
            f'    matches = [ {{ node.name = "{name}" }} ]\n'
            "    actions = {\n      update-props = {\n"
            f"{body}\n"
            "      }\n    }\n  }"
        )
    _write(WP_CONF, header + "monitor.alsa.rules = [\n" + "\n".join(rules) + "\n]\n")

    # Graph-wide fallbacks: allow every rate any output wants, so switching is possible.
    all_rates = {48000}
    for o in state["outputs"].values():
        all_rates.update([o["rate"]] if o.get("rate") else o.get("allowed_rates") or [])
    q = state["quantum"]
    _write(PW_CONF, header +
           "context.properties = {\n"
           f"    default.clock.allowed-rates = [ {' '.join(map(str, sorted(all_rates)))} ]\n"
           f"    default.clock.quantum = {q}\n"
           f"    default.clock.max-quantum = {max(q, 2048)}\n"
           "}\n")
    rq = f"stream.properties = {{\n    resample.quality = {state['resample_quality']}\n}}\n"
    _write(CLIENT_CONF, header + rq)
    _write(PULSE_CONF, header + rq)


def remove_config():
    for path in (WP_CONF, PW_CONF, CLIENT_CONF, PULSE_CONF, STATE_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def restart_audio():
    # WirePlumber 0.5 can abort in its shutdown path (GLib invalid_closure_notify), which
    # pops an Ubuntu crash report. Killing it skips that path; its state is saved on change.
    subprocess.run(["systemctl", "--user", "kill", "-s", "SIGKILL", "wireplumber"], capture_output=True)
    return subprocess.run(["systemctl", "--user", "restart", "wireplumber", "pipewire", "pipewire-pulse"],
                          capture_output=True, text=True)


# ---------------------------------------------------------------- UI

class OutputGroup(Adw.PreferencesGroup):
    def __init__(self, output, saved, on_change):
        super().__init__(title=output["description"], description=output["name"])
        self.output = output
        self.on_change = on_change

        self.status = Adw.ActionRow(title="Now", subtitle="…")
        self.status.add_prefix(Gtk.Image.new_from_icon_name("audio-speakers-symbolic"))
        self.add(self.status)

        # Sample rate: auto (follow source within allowed rates) or a fixed rate.
        self.rate_values = [0] + output["rates"]
        self.rate_row = Adw.ComboRow(
            title="Sample rate",
            subtitle="Auto follows the source rate, so nothing is resampled",
            model=Gtk.StringList.new(["Auto"] + [fmt_rate(r) for r in output["rates"]]),
        )
        rate = saved.get("rate", 0)
        self.rate_row.set_selected(self.rate_values.index(rate) if rate in self.rate_values else 0)
        self.rate_row.connect("notify::selected", self._changed)
        self.add(self.rate_row)

        self.allowed = Adw.ExpanderRow(title="Rates allowed in Auto",
                                       subtitle="Anything else is resampled to the closest fit")
        allowed = set(saved.get("allowed_rates") or [r for r in DEFAULT_AUTO_RATES if r in output["rates"]])
        self.rate_switches = {}
        for r in output["rates"]:
            sw = Adw.SwitchRow(title=fmt_rate(r), active=r in allowed)
            sw.connect("notify::active", self._changed)
            self.allowed.add_row(sw)
            self.rate_switches[r] = sw
        self.add(self.allowed)

        self.format_values = [""] + output["formats"]
        self.format_row = Adw.ComboRow(
            title="Bit depth",
            model=Gtk.StringList.new(["Auto"] + [FORMAT_LABELS[f] for f in output["formats"]]),
        )
        fmt = saved.get("format", "")
        self.format_row.set_selected(self.format_values.index(fmt) if fmt in self.format_values else 0)
        self.format_row.connect("notify::selected", self._changed)
        self.add(self.format_row)

        self._sync_sensitivity()
        self.refresh_status()

    def _sync_sensitivity(self):
        self.allowed.set_sensitive(self.rate_row.get_selected() == 0)

    def _changed(self, *_):
        self._sync_sensitivity()
        self.on_change()

    def refresh_status(self):
        self.status.set_subtitle(hw_status(self.output))

    def settings(self):
        rate = self.rate_values[self.rate_row.get_selected()]
        return {
            "rate": rate,
            "allowed_rates": [r for r, sw in self.rate_switches.items() if sw.get_active()],
            "format": self.format_values[self.format_row.get_selected()],
        }


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Audio Output Settings", default_width=560, default_height=760)
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.apply_btn = Gtk.Button(label="Apply", css_classes=["suggested-action"], sensitive=False)
        self.apply_btn.connect("clicked", self.on_apply)
        header.pack_end(self.apply_btn)

        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        reload_btn = Gtk.Button(label="Reload outputs", css_classes=["flat"])
        reload_btn.connect("clicked", lambda *_: (pop.popdown(), self.build()))
        reset_btn = Gtk.Button(label="Reset to system defaults", css_classes=["flat", "destructive-action"])
        reset_btn.connect("clicked", lambda *_: (pop.popdown(), self.on_reset()))
        box.append(reload_btn)
        box.append(reset_btn)
        pop.set_child(box)
        menu_btn.set_popover(pop)
        header.pack_start(menu_btn)

        view.add_top_bar(header)
        self.page_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        view.set_content(self.page_holder)
        self.toasts.set_child(view)
        self.set_content(self.toasts)

        self.groups = []
        self.build()
        GLib.timeout_add_seconds(1, self._tick)

    def build(self):
        child = self.page_holder.get_first_child()
        if child:
            self.page_holder.remove(child)
        state = load_state()
        page = Adw.PreferencesPage(vexpand=True)
        self.groups = []
        outputs = list_outputs()
        if not outputs:
            page.add(Adw.PreferencesGroup(title="No outputs found",
                                          description="Is PipeWire running? Try “Reload outputs”."))
        for out in outputs:
            g = OutputGroup(out, state["outputs"].get(out["name"], {}), self.mark_dirty)
            self.groups.append(g)
            page.add(g)

        glob_group = Adw.PreferencesGroup(title="All outputs")
        self.quantum_row = Adw.ComboRow(
            title="Buffer size",
            subtitle="Samples per cycle (ms at 48 kHz). Lower = less latency, higher = fewer dropouts",
            model=Gtk.StringList.new([f"{q} · {q / 48:.0f} ms" for q in QUANTA]),
        )
        self.quantum_row.set_selected(QUANTA.index(state["quantum"]) if state["quantum"] in QUANTA else 3)
        self.quantum_row.connect("notify::selected", self.mark_dirty)
        glob_group.add(self.quantum_row)
        self.rq_row = Adw.SpinRow.new_with_range(0, 14, 1)
        self.rq_row.set_title("Resampler quality")
        self.rq_row.set_subtitle("Used only when a rate must be converted. Default 4, max 14")
        self.rq_row.set_value(state["resample_quality"])
        self.rq_row.connect("notify::value", self.mark_dirty)
        glob_group.add(self.rq_row)
        page.add(glob_group)

        self.page_holder.append(page)
        self.apply_btn.set_sensitive(False)

    def mark_dirty(self, *_):
        self.apply_btn.set_sensitive(True)

    def _tick(self):
        for g in self.groups:
            g.refresh_status()
        return True

    def _restart_and_reload(self, message):
        res = restart_audio()
        if res.returncode != 0:
            self.toasts.add_toast(Adw.Toast(title=f"Restart failed: {res.stderr.strip()[:120]}"))
            return
        self.toasts.add_toast(Adw.Toast(title=message, timeout=3))
        GLib.timeout_add(2500, lambda: (self.build(), False)[1])

    def on_apply(self, *_):
        state = {
            "quantum": QUANTA[self.quantum_row.get_selected()],
            "resample_quality": int(self.rq_row.get_value()),
            "outputs": {g.output["name"]: g.settings() for g in self.groups},
        }
        for name, o in state["outputs"].items():
            if not o["rate"] and not o["allowed_rates"]:
                self.toasts.add_toast(Adw.Toast(title="Each output in Auto needs at least one allowed rate"))
                return
        write_config(state)
        self.apply_btn.set_sensitive(False)
        self._restart_and_reload("Applied — audio restarted")

    def on_reset(self):
        remove_config()
        self._restart_and_reload("Back to system defaults")


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.present()


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
