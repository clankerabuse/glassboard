# Glassboard

Transparent overlay whiteboard for Linux Wayland. Draw over your desktop while still seeing everything underneath.

## Requirements (Arch / KDE)

System packages:

```bash
sudo pacman -S python python-gobject python-cairo gtk3 gtk-layer-shell libayatana-appindicator
```

`libayatana-appindicator` (or `libappindicator`) enables the system tray icon.
## Run

```bash
cd /path/to/glassboard
python -m glassboard
```

Or after an editable install:

```bash
pip install -e .
glassboard
```

### App menu (desktop entry)

A `glassboard.desktop` file is included. To install it for your user:

```bash
install -Dm644 glassboard.desktop ~/.local/share/applications/glassboard.desktop
# edit Path= in that file if the repo is not at /home/kayne/Projects/glassboard
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

Then launch **Glassboard** from your app menu / launcher.

Pinning that entry to the taskbar is fine: Glassboard is a single-instance app, so another click restores the overlay (or shows it again if it was hidden to the tray) instead of spawning a second tray icon. The overlay itself is a Wayland layer-shell surface, so it will not look like a normal taskbar window button — the tray icon is the always-visible control while it is running.

## Usage

- Floating toolbar (bottom center, drag via the **drag handle**):
  - **Click** — ink stays visible; mouse passes through to apps (toolbar still works)
  - **Draw** — drag to ink on the glass
  - **Board** — pop up a solid board over the desktop (work area only — leaves the taskbar alone); toggle again to retract. Right-click cycles **White** / **Black** (icon updates; does not open the board by itself). Opens into Draw. Shortcut: `B`. Esc retracts the board first.
  - Pen / Eraser, **4 color swatches** (defaults: red, blue, green, yellow), stroke **size slider** (each tool remembers its own size; pen max 48px, eraser max 128px); in Draw mode, mouse wheel also changes size for the active tool
  - Left-click a swatch to draw with it; **right-click** opens an RGB color wheel — custom colors stick on that button across sessions
  - Choosing Pen, Eraser, a color, or changing size switches to **Draw** if you were in Click
  - Stylus **pressure** scales stroke width (slider = full pressure); mouse always uses the slider width
  - Stylus **barrel buttons** (Draw mode): button 2 toggles Pen/Eraser; hold button 3 to freeze a size ring at the press point, then drag the tip (marked with a dot) — near the center is fine control (~1–8px), farther out a power curve up to the active tool’s max. Works for both Pen and Eraser.
  - In Draw mode the pointer becomes a **crosshair** (eraser — and the button-3 size gesture — hide it and show the size ring instead)
  - Eraser also shows a size ring around the cursor while drawing
  - Eraser auto-grows while a stroke is held and moving consistently at a decent speed; pausing or slowing freezes the size (slider base is unchanged)
  - **Hide / Show** markings (eye icon) — toggles ink visibility without clearing; drawing while hidden reveals them again
  - Undo, Clear, Quit
  - **Drag handle** (vertical dots, grab-hand cursor): drag to reposition the toolbar
- **System tray** (when AppIndicator is installed): **Show** / **Hide** / **Quit**. Esc in Click mode hides to the tray instead of exiting; tray **Quit** (or toolbar Quit) exits fully.
- Shortcuts (while Draw mode has keyboard focus):
  - `D` toggle Click/Draw
  - `B` toggle solid Board
  - `P` pen, `E` eraser (stylus button 2 toggles; button 3 press-drag adjusts size)
  - Mouse wheel — stroke size up/down
  - `Ctrl+Z` undo, `Ctrl+Backspace` clear
  - `Esc` retract Board, else back to Click, else hide to tray (or quit if no tray)

Tested against KWin Wayland with `wlr-layer-shell`.
