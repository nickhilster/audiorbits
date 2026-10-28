# GNOME background integration (installed disabled)

This GNOME Shell 50 extension mirrors the local AudiOrbits GTK/WebKit window
into each GNOME background actor. It is intentionally installed but disabled
until its activation is approved and can be tested in the live desktop.
When installed during an existing Shell session, GNOME may not discover it
until the next login; this install did not restart or log out of the session.

It matches a window titled `AudiOrbits Linux`, so start one of the preview
launchers before enabling it. Disable it with:

```sh
gnome-extensions disable audiorbits-linux@teambotics.local
```

For a startup guard, create
`~/.config/audiorbits-linux/disable-wallpaper` before enabling. If that file is
created while active, the extension notices it within about one second and
tears down its clones. It restores the source window opacity during teardown.

The extension uses GNOME Shell's internal background actor hook and must be
tested on this exact GNOME Shell version. The original renderer window is made
transparent while its Clutter actor is cloned; desktop input behavior needs
live verification.
