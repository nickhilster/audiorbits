#!/usr/bin/python3
"""Local GTK/WebKit host and PipeWire system-output analyser for AudiOrbits."""

import array
from dataclasses import dataclass
import json
import math
import os
import re
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
gi.require_version("Gst", "1.0")
from gi.repository import Gdk, Gio, GLib, Gst, Gtk, WebKit2


APP_ID = "org.teambotics.AudiOrbitsLinux"
TITLE = "AudiOrbits Linux"
ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "audiorbits"
FFT_N = 256
FPS = int(os.environ.get("AUDIORBITS_FPS", "30"))
if FPS not in (30, 60, 120):
    FPS = 30
SHOW_STATS = os.environ.get("AUDIORBITS_STATS", "1") != "0"
RENDER_SCALE = 1.0


@dataclass(frozen=True)
class AudioSource:
    id: str
    label: str
    target: str
    captures_output_monitor: bool


def list_audio_sources():
    """Return output monitors and capture sources exposed by PipeWire."""
    try:
        output = subprocess.check_output(["pw-cli", "ls", "Node"], text=True,
                                         stderr=subprocess.DEVNULL, timeout=3)
        blocks = re.split(r"(?=\bid \d+, type PipeWire:Interface:Node)", output)
        sources = []
        for block in blocks:
            media = re.search(r'media\.class = "([^"]+)"', block)
            name = re.search(r'node\.name = "([^"]+)"', block)
            description = re.search(r'node\.description = "([^"]+)"', block)
            nick = re.search(r'node\.nick = "([^"]+)"', block)
            priority = re.search(r'priority\.session = "(\d+)"', block)
            if not media or not name:
                continue
            class_name = media.group(1)
            pretty_name = (description or nick).group(1) if (description or nick) else name.group(1)
            if class_name == "Audio/Sink":
                source = AudioSource("monitor:" + name.group(1),
                                     "Output monitor · " + pretty_name,
                                     name.group(1), True)
                sources.append((int(priority.group(1)) if priority else 0, source))
            elif class_name.startswith("Audio/Source"):
                kind = "Microphone" if class_name == "Audio/Source" else "Virtual input"
                source = AudioSource("input:" + name.group(1),
                                     f"{kind} · {pretty_name}",
                                     name.group(1), False)
                sources.append((0, source))
        ordered = [source for _priority, source in sorted(
            sources, key=lambda item: item[1].label.lower())]
        default_monitor = max(
            (item for item in sources if item[1].captures_output_monitor),
            key=lambda item: item[0], default=(0, None))[1]
        return ordered, default_monitor
    except (OSError, subprocess.SubprocessError):
        return [], None


def fft_magnitudes(samples):
    """Return the first 64 one-sided bins as WebAudio-style 0..1 values."""
    n = len(samples)
    values = [complex(samples[i] * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))), 0.0)
              for i in range(n)]
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            values[i], values[j] = values[j], values[i]
    length = 2
    while length <= n:
        angle = -2 * math.pi / length
        wlen = complex(math.cos(angle), math.sin(angle))
        half = length // 2
        for base in range(0, n, length):
            w = 1 + 0j
            for k in range(half):
                u = values[base + k]
                v = values[base + k + half] * w
                values[base + k] = u + v
                values[base + k + half] = u - v
                w *= wlen
        length <<= 1
    result = []
    for k in range(64):
        amplitude = abs(values[k]) * (4.0 / n)
        db = 20.0 * math.log10(max(amplitude, 1e-7))
        result.append(max(0.0, min(1.0, (db + 100.0) / 70.0)))
    return result


def audio_pipeline_description(source):
    if not source:
        raise ValueError("No PipeWire audio source was selected.")
    props = ('props,stream.capture.sink=true,stream.capture.link=true'
             if source.captures_output_monitor else
             'props,stream.capture.sink=false,stream.capture.link=false')
    return (
        f'pipewiresrc target-object="{source.target}" '
        f'stream-properties="{props}" do-timestamp=true '
        '! audioconvert ! audioresample '
        '! audio/x-raw,format=F32LE,rate=48000,channels=2 '
        '! appsink name=audiosink emit-signals=true max-buffers=2 drop=true sync=false'
    )


class AudioMonitor:
    def __init__(self, on_spectrum, source):
        self.on_spectrum = on_spectrum
        self.source = source
        self.pipeline = None
        self.sink = None
        self.samples = [[], []]
        self.lock = threading.Lock()
        self.latest_spectrum = None
        self.last_error = None
        self.timer_id = 0

    def start(self):
        Gst.init(None)
        if not self.source:
            raise RuntimeError("No PipeWire audio source was selected.")
        # Output sinks use their monitor; input sources connect directly.
        self.pipeline = Gst.parse_launch(audio_pipeline_description(self.source))
        self.sink = self.pipeline.get_by_name("audiosink")
        self.sink.connect("new-sample", self._new_sample)
        result = self.pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("PipeWire audio monitor could not enter PLAYING state.")
        # Audio response does not benefit from more than 60 updates per second.
        self.timer_id = GLib.timeout_add(17, self._push_latest)
        print(f"Audio monitor attached read-only to {self.source.label}", flush=True)

    def _new_sample(self, sink):
        try:
            sample = sink.emit("pull-sample")
            if sample is None:
                return Gst.FlowReturn.EOS
            buf = sample.get_buffer()
            ok, mapped = buf.map(Gst.MapFlags.READ)
            if not ok:
                return Gst.FlowReturn.ERROR
            try:
                data = array.array("f")
                data.frombytes(mapped.data)
                frames = len(data) // 2
                with self.lock:
                    for i in range(frames):
                        self.samples[0].append(data[2 * i])
                        self.samples[1].append(data[2 * i + 1])
                    for channel in self.samples:
                        if len(channel) > FFT_N * 3:
                            del channel[:-FFT_N * 2]
                    if len(self.samples[0]) >= FFT_N:
                        left = fft_magnitudes(self.samples[0][-FFT_N:])
                        right = fft_magnitudes(self.samples[1][-FFT_N:])
                        self.latest_spectrum = left + right
            finally:
                buf.unmap(mapped)
        except Exception as exc:  # keep a transient audio error from taking down the host
            if str(exc) != self.last_error:
                print(f"Audio monitor warning: {exc}", file=sys.stderr, flush=True)
                self.last_error = str(exc)
            return Gst.FlowReturn.ERROR
        return Gst.FlowReturn.OK

    def _push_latest(self):
        with self.lock:
            spectrum = self.latest_spectrum
        if spectrum is not None:
            self.on_spectrum(spectrum)
        return GLib.SOURCE_CONTINUE

    def stop(self):
        if self.timer_id:
            GLib.source_remove(self.timer_id)
            self.timer_id = 0
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_state(2 * Gst.SECOND)
            self.pipeline = None
            self.sink = None


class QuietHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, app=None, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if urlsplit(self.path).path == "/__metrics":
            query = parse_qs(urlsplit(self.path).query)
            try:
                measured = int(query.get("fps", ["0"])[0])
                target = int(query.get("target", ["0"])[0])
                width = int(query.get("width", ["0"])[0])
                height = int(query.get("height", ["0"])[0])
                print(f"Render rate: {measured} FPS (target {target}, {width}x{height})", flush=True)
                if self.app:
                    GLib.idle_add(self.app._on_render_rate, measured, target, width, height)
            except ValueError:
                pass
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()


class AudiOrbitsApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.window = None
        self.control_window = None
        self.webview = None
        self.audio = None
        self.server = None
        self.server_thread = None
        self.port = None
        self.fps = FPS
        self.render_scale = RENDER_SCALE
        self.paused = False
        self.page_loaded = False
        self.scene_ready = False
        self.measured_fps = 0
        self.render_size = (0, 0)
        self.audio_sources, self.default_audio_source = list_audio_sources()
        self.audio_source_by_id = {source.id: source for source in self.audio_sources}
        self.selected_audio_source = self.default_audio_source
        self.connect("shutdown", self._shutdown)

    def do_activate(self):
        if self.window:
            self.window.present()
            if self.control_window:
                self.control_window.present()
            return
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         lambda *a, **kw: QuietHandler(
                                             *a, app=self, directory=str(WEB_ROOT), **kw))
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

        self.window = Gtk.ApplicationWindow(application=self)
        self.window.set_title(TITLE)
        try:
            self.window.set_wmclass("AudiOrbitsLinux", "AudiOrbitsLinux")
        except AttributeError:
            pass
        self.window.set_decorated(False)
        self.window.set_default_size(1280, 720)
        self.webview = WebKit2.WebView()
        settings = self.webview.get_settings()
        settings.set_enable_accelerated_2d_canvas(True)
        settings.set_enable_webgl(True)
        settings.set_enable_page_cache(False)
        settings.set_enable_mediasource(False)
        self.window.add(self.webview)
        self.webview.connect("load-changed", self._loaded)
        self.window.connect("destroy", self._preview_destroyed)
        self.window.show_all()
        self.window.maximize()
        self.webview.load_uri(
            f"http://127.0.0.1:{self.port}/?fps={self.fps}"
            f"&scale={self.render_scale}&stats={int(SHOW_STATS)}"
        )
        self.window.connect("key-press-event", self._key_press)

        self._build_controls()
        self.control_window.set_transient_for(self.window)
        self.control_window.show_all()
        self._update_status()
        self._start_audio()

    def _build_controls(self):
        self.control_window = Gtk.ApplicationWindow(application=self)
        self.control_window.set_title("AudiOrbits Controls")
        self.control_window.set_default_size(440, 390)
        self.control_window.set_resizable(False)
        self.control_window.connect("delete-event", self._controls_close)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_border_width(18)
        self.control_window.add(root)

        title = Gtk.Label(label="AudiOrbits Controls")
        title.set_xalign(0)
        title.get_style_context().add_class("title-2")
        root.pack_start(title, False, False, 0)

        self.toggle_button = Gtk.Button(label="Pause")
        self.toggle_button.set_size_request(-1, 42)
        self.toggle_button.set_sensitive(False)
        self.toggle_button.connect("clicked", self._toggle_playback)
        root.pack_start(self.toggle_button, False, False, 0)

        grid = Gtk.Grid(column_spacing=14, row_spacing=10)
        root.pack_start(grid, False, False, 0)

        fps_label = Gtk.Label(label="Frame rate")
        fps_label.set_xalign(0)
        grid.attach(fps_label, 0, 0, 1, 1)
        self.fps_combo = Gtk.ComboBoxText()
        for value in (30, 60, 120):
            self.fps_combo.append(str(value), f"{value} FPS")
        self.fps_combo.set_active_id(str(self.fps))
        self.fps_combo.connect("changed", self._fps_changed)
        grid.attach(self.fps_combo, 1, 0, 1, 1)

        scale_label = Gtk.Label(label="Render scale")
        scale_label.set_xalign(0)
        grid.attach(scale_label, 0, 1, 1, 1)
        self.scale_combo = Gtk.ComboBoxText()
        for value, label in ((1.0, "100% · full"), (0.75, "75% · reduced"),
                             (0.5, "50% · low")):
            self.scale_combo.append(str(value), label)
        self.scale_combo.set_active_id(str(self.render_scale))
        self.scale_combo.connect("changed", self._scale_changed)
        grid.attach(self.scale_combo, 1, 1, 1, 1)

        audio_label = Gtk.Label(label="Audio source")
        audio_label.set_xalign(0)
        grid.attach(audio_label, 0, 2, 1, 1)
        self.audio_combo = Gtk.ComboBoxText()
        if not self.audio_sources:
            self.audio_combo.append("none", "No sources found")
        for source in self.audio_sources:
            self.audio_combo.append(source.id, source.label)
        self.audio_combo.set_active_id(
            self.selected_audio_source.id if self.selected_audio_source else "none")
        self.audio_combo.connect("changed", self._audio_source_changed)
        grid.attach(self.audio_combo, 1, 2, 1, 1)

        scale_hint = Gtk.Label(label="Lower render scale draws fewer pixels; sharpness may decrease.")
        scale_hint.set_xalign(0)
        scale_hint.set_line_wrap(True)
        scale_hint.get_style_context().add_class("dim-label")
        root.pack_start(scale_hint, False, False, 0)

        self.status_label = Gtk.Label(label="Starting renderer…")
        self.status_label.set_xalign(0)
        self.status_label.set_line_wrap(True)
        root.pack_start(self.status_label, False, False, 0)

        capture_note = Gtk.Label(
            label="Audio stays on this device. Microphone capture is active only while running and selected.")
        capture_note.set_xalign(0)
        capture_note.set_line_wrap(True)
        capture_note.get_style_context().add_class("dim-label")
        root.pack_start(capture_note, False, False, 0)

        about_button = Gtk.Button(label="About AudiOrbits")
        about_button.set_halign(Gtk.Align.START)
        about_button.connect("clicked", self._show_about)
        root.pack_start(about_button, False, False, 0)

    def _show_about(self, _button):
        dialog = Gtk.AboutDialog(transient_for=self.control_window, modal=True)
        dialog.set_program_name("AudiOrbits")
        dialog.set_version("2.3.1")
        dialog.set_comments(
            "A beautiful piece of software art. Thank you, hexxone, for creating it."
        )
        dialog.set_website("https://hexx.one/")
        dialog.set_website_label("hexx.one")
        dialog.set_authors(["hexxone"])
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.run()
        dialog.destroy()

    def _loaded(self, _webview, event):
        if event == WebKit2.LoadEvent.FINISHED:
            self.page_loaded = True
            print(f"AudiOrbits page loaded (WebKit {WebKit2.get_major_version()}.{WebKit2.get_minor_version()})", flush=True)
            self._apply_render_settings()

    def _apply_render_settings(self):
        self._run_js(
            f"window.audiOrbitsLinuxSetFps({self.fps});"
            f"window.audiOrbitsLinuxSetRenderScale({self.render_scale});"
        )

    def _run_js(self, script):
        if self.webview and self.page_loaded:
            self.webview.run_javascript(script, None, None, None)

    def _fps_changed(self, combo):
        value = combo.get_active_id()
        if not value:
            return
        self.fps = int(value)
        self._run_js(f"window.audiOrbitsLinuxSetFps({self.fps});")
        self._update_status()

    def _scale_changed(self, combo):
        value = combo.get_active_id()
        if not value:
            return
        self.render_scale = float(value)
        self._run_js(f"window.audiOrbitsLinuxSetRenderScale({self.render_scale});")
        self._update_status()

    def _audio_source_changed(self, combo):
        source_id = combo.get_active_id()
        if source_id == "none":
            self.selected_audio_source = None
        elif source_id:
            self.selected_audio_source = self.audio_source_by_id.get(source_id)
        if not self.paused:
            self._start_audio()
        self._update_status()

    def _toggle_playback(self, _button):
        if not self.scene_ready:
            return
        self.paused = not self.paused
        self._run_js(f"window.audiOrbitsLinuxSetPaused({str(self.paused).lower()});")
        if self.paused:
            self._stop_audio()
        else:
            self._start_audio()
        self.toggle_button.set_label("Start" if self.paused else "Pause")
        self._update_status()

    def _start_audio(self):
        self._stop_audio()
        self.audio_error = None
        if self.paused or not self.selected_audio_source:
            return
        self.audio = AudioMonitor(self._send_spectrum, self.selected_audio_source)
        try:
            self.audio.start()
        except Exception as exc:
            print(f"Audio monitor unavailable: {exc}", file=sys.stderr, flush=True)
            self.audio.stop()
            self.audio = None
            self.audio_error = str(exc)
        else:
            self.audio_error = None

    def _stop_audio(self):
        if self.audio:
            self.audio.stop()
            self.audio = None

    def _on_render_rate(self, measured, _target, width, height):
        self.measured_fps = measured
        self.render_size = (width, height)
        if measured > 0:
            self.scene_ready = True
            if getattr(self, "toggle_button", None):
                self.toggle_button.set_sensitive(True)
        self._update_status()
        return GLib.SOURCE_REMOVE

    def _update_status(self):
        if not getattr(self, "status_label", None):
            return
        if self.paused:
            state = "Paused · renderer and audio capture paused"
        elif self.measured_fps:
            width, height = self.render_size
            state = f"Running · {self.measured_fps}/{self.fps} FPS · {width}×{height} render"
        else:
            state = "Starting renderer…"
        if getattr(self, "audio_error", None):
            audio = "Audio unavailable: " + self.audio_error
        elif self.paused:
            audio = "Audio capture paused"
        elif self.selected_audio_source:
            audio = "Audio: " + self.selected_audio_source.label
        else:
            audio = "No audio source selected"
        self.status_label.set_text(state + "\n" + audio)

    def _controls_close(self, *_args):
        self.quit()
        return False

    def _preview_destroyed(self, *_args):
        self.window = None
        self.quit()

    def _send_spectrum(self, values):
        if not self.webview or not self.page_loaded or self.paused:
            return
        payload = json.dumps(values, separators=(",", ":"))
        script = f"window.audiOrbitsLinuxAudio && window.audiOrbitsLinuxAudio({payload});"
        self.webview.run_javascript(script, None, None, None)

    def _key_press(self, _window, event):
        if event.keyval in (Gdk.KEY_Escape, Gdk.KEY_q):
            self.quit()
            return True
        return False

    def _shutdown(self, *_args):
        self._stop_audio()
        self.webview = None
        self.window = None
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


if __name__ == "__main__":
    app = AudiOrbitsApp()
    raise SystemExit(app.run(sys.argv))
