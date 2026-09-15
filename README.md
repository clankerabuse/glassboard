# Glassboard

Transparent overlay whiteboard for Linux Wayland. Draw over your desktop while still seeing everything underneath.

## Requirements (Arch / KDE)

System packages:

```bash
sudo pacman -S python python-gobject python-cairo gtk3 gtk-layer-shell
```

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

## Usage

- Floating toolbar (bottom center, drag via the **drag handle** ⠿):
  - **Click** — ink stays visible; mouse passes through to apps (toolbar still works)
  - **Draw** — drag to ink on the glass
  - **Board** — pop up a solid board over the desktop (work area only — leaves the taskbar alone); toggle again to retract. Right-click cycles **White** / **Black** (icon updates; does not open the board by itself). Opens into Draw. Shortcut: `B`. Esc retracts the board first.
  - Pen / Eraser, color swatches, stroke **size slider** (toolbar shows a live size preview for both); in Draw mode, mouse wheel also changes size
  - Choosing Pen, Eraser, a color, or changing size switches to **Draw** if you were in Click
  - Eraser also shows a size ring around the cursor while drawing
  - Eraser auto-grows while a stroke is held and moving consistently at a decent speed; pausing or slowing freezes the size (slider base is unchanged)
  - Undo, Clear, Quit
  - **Drag handle** (⠿): drag to reposition the toolbar
- Shortcuts (while Draw mode has keyboard focus):
  - `D` toggle Click/Draw
  - `B` toggle solid Board
  - `P` pen, `E` eraser
  - Mouse wheel — stroke size up/down
  - `Ctrl+Z` undo, `Ctrl+Backspace` clear
  - `Esc` retract Board, else back to Click (or quit if already Click)

Tested against KWin Wayland with `wlr-layer-shell`.
