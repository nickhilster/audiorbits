# AudiOrbits Linux local preview

This is a local GTK/WebKit host for AudiOrbits 2.3.1. It opens a small controls
window and a separate renderer preview, loads the included renderer over a
loopback-only HTTP server, and supplies stereo audio spectra from PipeWire.

## Run

- **Start/Pause** controls animation and audio capture. Pause suspends both.
- Frame rate options: **30, 60, or 120 FPS**.
- Render scale options: **100%, 75%, or 50%**. Lower scales render fewer pixels
  and can help when GPU-bound; the image may look softer and CPU-bound work may
  not improve.
- Audio source options include output monitors, microphones, and virtual inputs
  exposed by PipeWire. Changing the selected source does not change system
  routing.
- The controls panel has a regular **Minimize** button and **Minimize to tray**.
  The Ubuntu AppIndicator menu can restore either the controls or renderer, or
  quit AudiOrbits. Install `gir1.2-ayatanaappindicator3-0.1` if tray mode is
  unavailable; ordinary minimization remains usable without it.
- **About AudiOrbits** includes a Credits tab with the Teambotics packaging
  attribution and a link to <https://teambotics.app>.
- The two application launchers start at 30 or 60 FPS; all three targets can be
  selected in the controls window. Press `Esc` or `q` in the preview to close.

The controls window reports actual AudiOrbits render-loop calls and drawing
buffer dimensions. The preview is a normal maximized window; it does not change
the current GNOME wallpaper or add anything to login startup. A GNOME 50
background extension is also staged in the disabled state and still needs live
activation testing.

## Audio and privacy

The app captures only the currently selected source while running. Audio stays
local and is not saved or transmitted. Microphone capture begins only after a
microphone is selected; Pause suspends capture. The HTTP server binds to
`127.0.0.1` on a temporary port.

## Source and modifications

Renderer: AudiOrbits 2.3.1, branch `2.3.x`, commit `1352246`, from
<https://github.com/hexxone/audiorbits>. The renderer and included `we_utils`
code are GPL-3.0; see `audiorbits/LICENSE` and `audiorbits/we_utils/LICENSE`.

Local modifications add standalone initialization, frame-rate and render-scale
controls, an FPS counter, and the Python GTK/WebKit/PipeWire host. The original
source clone in the working checkout remains unchanged.

## UI checks

```sh
cd linux
/usr/bin/python3 -m unittest test_linux_ui -v
```
