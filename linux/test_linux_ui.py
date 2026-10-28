#!/usr/bin/python3
"""Regression checks for AudiOrbits' local desktop UI helpers."""

import unittest

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from audiorbits_linux import build_tray_menu, create_credits_dialog


class LinuxUiTests(unittest.TestCase):
    def test_tray_menu_can_restore_both_windows_and_quit(self):
        called = []
        menu = build_tray_menu(
            lambda *_: called.append("controls"),
            lambda *_: called.append("renderer"),
            lambda *_: called.append("quit"),
        )
        labels = [item.get_label() for item in menu.get_children() if isinstance(item, Gtk.MenuItem)]
        self.assertEqual(labels, ["Show Controls", "Show Renderer", "Quit AudiOrbits"])
        for item in menu.get_children():
            if isinstance(item, Gtk.MenuItem):
                item.activate()
        self.assertEqual(called, ["controls", "renderer", "quit"])

    def test_credits_dialog_links_teambotics(self):
        dialog = create_credits_dialog(None)
        labels = []

        def visit(widget):
            if isinstance(widget, Gtk.Label):
                labels.append(widget.get_label())
            if isinstance(widget, Gtk.Container):
                for child in widget.get_children():
                    visit(child)

        visit(dialog)
        markup = "\n".join(labels)
        self.assertIn("Packaged with Love ❤️ &amp;🙏 Gratitude", markup)
        self.assertIn('href="https://teambotics.app"', markup)
        self.assertIn(">Teambotics</a>", markup)
        dialog.destroy()


if __name__ == "__main__":
    unittest.main()
