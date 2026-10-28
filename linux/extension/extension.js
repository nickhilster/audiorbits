import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

import * as Background from 'resource:///org/gnome/shell/ui/background.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';

const APP_TITLE = 'AudiOrbits Linux';
const KILL_SWITCH = GLib.build_filenamev([
    GLib.get_user_config_dir(), 'audiorbits-linux', 'disable-wallpaper',
]);

function killSwitchEngaged() {
    return GLib.file_test(KILL_SWITCH, GLib.FileTest.EXISTS);
}

export default class AudiOrbitsDesktopExtension extends Extension {
    enable() {
        this._injectionManager = null;
        this._monitorSignal = 0;
        this._pollId = 0;
        this._sourceActor = null;
        this._sourceOpacity = 255;
        this._clones = new Set();

        if (killSwitchEngaged()) {
            log('AudiOrbits desktop: kill switch found; extension is idle.');
            return;
        }

        try {
            this._injectionManager = new InjectionManager();
            this._injectionManager.overrideMethod(
                Background.BackgroundManager.prototype,
                '_createBackgroundActor',
                originalMethod => {
                    const extension = this;
                    return function (...args) {
                        const backgroundActor = originalMethod.call(this, ...args);
                        extension._attachClone(backgroundActor);
                        return backgroundActor;
                    };
                }
            );

            this._monitorSignal = Main.layoutManager.connect('monitors-changed', () => {
                this._reloadBackgrounds();
            });

            this._findRenderer();
            this._reloadBackgrounds();
            this._pollId = GLib.timeout_add_seconds(GLib.PRIORITY_LOW, 1, () => {
                if (killSwitchEngaged()) {
                    log('AudiOrbits desktop: kill switch found; disabling extension.');
                    this._pollId = 0;
                    this.disable();
                    return GLib.SOURCE_REMOVE;
                }
                this._findRenderer();
                return GLib.SOURCE_CONTINUE;
            });
        } catch (error) {
            logError(error, 'AudiOrbits desktop: enable failed; cleaning up.');
            this.disable();
        }
    }

    _findRenderer() {
        const actor = global.get_window_actors().find(windowActor => {
            const metaWindow = windowActor.get_meta_window();
            return metaWindow && metaWindow.get_title() === APP_TITLE;
        }) ?? null;

        if (actor === this._sourceActor)
            return;

        this._restoreSource();
        this._sourceActor = actor;
        if (this._sourceActor) {
            this._sourceOpacity = this._sourceActor.get_opacity();
            this._sourceActor.set_opacity(0);
            log('AudiOrbits desktop: renderer window found and mirrored.');
            this._reloadBackgrounds();
        } else {
            this._reloadBackgrounds();
        }
    }

    _attachClone(backgroundActor) {
        if (!this._sourceActor || !backgroundActor || !backgroundActor.add_child)
            return;

        const clone = new Clutter.Clone({
            source: this._sourceActor,
            reactive: false,
            name: 'audiOrbitsDesktopClone',
        });
        const monitor = Main.layoutManager.monitors[backgroundActor.monitor];
        clone.set_position(0, 0);
        clone.set_size(
            backgroundActor.width || monitor?.width || 1,
            backgroundActor.height || monitor?.height || 1
        );
        backgroundActor.add_child(clone);
        this._clones.add(clone);
        clone.connect('destroy', () => this._clones.delete(clone));
    }

    _reloadBackgrounds() {
        try {
            Main.layoutManager._updateBackgrounds();
        } catch (error) {
            logError(error, 'AudiOrbits desktop: background refresh failed.');
        }
    }

    _restoreSource() {
        if (this._sourceActor) {
            try {
                this._sourceActor.set_opacity(this._sourceOpacity);
            } catch (_error) {
                // The window may have closed while the extension was active.
            }
        }
        this._sourceActor = null;
    }

    disable() {
        if (this._pollId) {
            GLib.source_remove(this._pollId);
            this._pollId = 0;
        }
        if (this._monitorSignal) {
            Main.layoutManager.disconnect(this._monitorSignal);
            this._monitorSignal = 0;
        }

        this._clones?.forEach(clone => {
            try { clone.destroy(); } catch (_error) {}
        });
        this._clones?.clear();
        this._restoreSource();
        this._injectionManager?.clear();
        this._injectionManager = null;
        this._reloadBackgrounds();
    }
}
