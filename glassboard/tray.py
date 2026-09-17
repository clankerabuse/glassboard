"""System tray (StatusNotifier) control: Show / Hide / Quit."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from glassboard import _gi  # noqa: F401
from gi.repository import GLib, Gtk

if TYPE_CHECKING:
    from glassboard.overlay import OverlayWindow


def _load_indicator_module() -> Any | None:
    import gi

    for name in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(name, "0.1")
            module = __import__("gi.repository", fromlist=[name])
            return getattr(module, name)
        except (ValueError, AttributeError, ImportError):
            continue
    return None


class TrayIcon:
    """AppIndicator tray entry for a running Glassboard overlay."""

    def __init__(
        self,
        *,
        on_show: Callable[[], None],
        on_hide: Callable[[], None],
        on_quit: Callable[[], None],
        is_visible: Callable[[], bool],
    ) -> None:
        self._on_show = on_show
        self._on_hide = on_hide
        self._on_quit = on_quit
        self._is_visible = is_visible
        self._indicator = None
        self._item_show: Gtk.MenuItem | None = None
        self._item_hide: Gtk.MenuItem | None = None

        AppIndicator = _load_indicator_module()
        if AppIndicator is None:
            return

        self._indicator = AppIndicator.Indicator.new(
            "glassboard",
            "applications-graphics",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_title("Glassboard")
        self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        self._item_show = Gtk.MenuItem(label="Show")
        self._item_show.connect("activate", lambda *_: self._on_show())
        menu.append(self._item_show)

        self._item_hide = Gtk.MenuItem(label="Hide")
        self._item_hide.connect("activate", lambda *_: self._on_hide())
        menu.append(self._item_hide)

        menu.append(Gtk.SeparatorMenuItem())

        item_quit = Gtk.MenuItem(label="Quit")
        item_quit.connect("activate", lambda *_: self._on_quit())
        menu.append(item_quit)

        menu.show_all()
        self._indicator.set_menu(menu)
        self._indicator.connect("activate", self._on_activate)
        self.sync_menu()

    def _on_activate(self, *_args) -> None:
        """Tray icon click — toggle Show / Hide."""
        if self._is_visible():
            self._on_hide()
        else:
            self._on_show()

    @property
    def available(self) -> bool:
        return self._indicator is not None

    def sync_menu(self) -> None:
        if not self.available:
            return
        visible = self._is_visible()
        if self._item_show is not None:
            self._item_show.set_sensitive(not visible)
        if self._item_hide is not None:
            self._item_hide.set_sensitive(visible)


def attach_tray(window: OverlayWindow) -> TrayIcon | None:
    """Bind a tray icon to an overlay window. Returns None if unsupported."""

    def is_visible() -> bool:
        return bool(window.get_mapped() and window.get_visible())

    def show() -> None:
        window.show_from_tray()

    def hide() -> None:
        window.hide_to_tray()

    def quit_() -> None:
        window.quit_app()

    tray = TrayIcon(
        on_show=show,
        on_hide=hide,
        on_quit=quit_,
        is_visible=is_visible,
    )
    if not tray.available:
        return None

    window.set_tray(tray)
    return tray
