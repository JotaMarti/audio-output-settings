# Audio Output Settings

Small GTK4/libadwaita app to set sample rate and bit depth **per audio output** on PipeWire (Ubuntu GNOME has no UI for this).

- **Sample rate**: `Auto` lets the output switch to the source's rate (from the list of allowed rates), so there is no resampling. Or pick a fixed rate.
- **Bit depth**: Auto, or force 16 / 24 / 32-bit.
- **Buffer size** and **resampler quality** apply to all outputs.
- The "Now" row shows what the hardware is actually running at (`/proc/asound/.../hw_params`).

Apply writes these files and restarts PipeWire (audio cuts out for about a second):

```
~/.config/wireplumber/wireplumber.conf.d/60-pw-output-settings.conf
~/.config/pipewire/pipewire.conf.d/60-pw-output-settings.conf
~/.config/pipewire/client.conf.d/60-pw-output-settings.conf
~/.config/pipewire/pipewire-pulse.conf.d/60-pw-output-settings.conf
```

"Reset to system defaults" (in the menu) deletes them.

Note: the rate only switches while the output is idle. If something keeps it open, such as the GNOME Sound settings panel and its level meter, it stays at its current rate.

## Requirements

PipeWire + WirePlumber 0.5 (Ubuntu 24.04+), Python 3 with GTK4 and libadwaita (`python3-gi`, `gir1.2-adw-1`, preinstalled on Ubuntu GNOME).

## Install

```bash
git clone <this repo>
cd audio-output-settings
./install.sh
```
