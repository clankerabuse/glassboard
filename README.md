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

## Usage

- Floating toolbar (bottom center, drag via the **drag handle** ⠿):
  - **Click** — ink stays visible; mouse passes through to apps (toolbar still works)
  - **Draw** — drag to ink on the glass
  - Pen / Eraser, color swatches, stroke **size slider** (toolbar shows a live size preview for both)
  - Choosing Pen, Eraser, a color, or changing size switches to **Draw** if you were in Click
  - Eraser also shows a size ring around the cursor while drawing
  - Undo, Clear, Quit
  - **Drag handle** (⠿): drag to reposition; click (without dragging) to collapse/expand. Collapse does not change Click/Draw mode.
- Shortcuts (while Draw mode has keyboard focus):
  - `D` toggle Click/Draw
  - `P` pen, `E` eraser
  - `Ctrl+Z` undo, `Ctrl+Backspace` clear
  - `Esc` back to Click (or quit if already Click)

Tested against KWin Wayland with `wlr-layer-shell`.
