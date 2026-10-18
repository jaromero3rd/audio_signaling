#!/usr/bin/env python3
"""Manually draw wheat-poster boxes (tkinter UI).

Run:
  python -m wheat_detection.manual_label_10
  python -m wheat_detection.manual_label_10 --src data/poster_yolo/to_label_25

Keys / buttons:
  drag mouse  = draw box
  Save / Enter = save box, next image
  Redraw       = clear current box
  Copy prev    = reuse last saved box
  Skip         = next without saving
  Quit         = exit (keeps saved)

Writes labels next to each image and copies into:
  data/poster_yolo/reference/{images,labels,preview}/
"""

from __future__ import annotations

import argparse
import shutil
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from PIL import Image, ImageDraw, ImageTk

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SRC = REPO / "data" / "poster_yolo" / "to_label_25"
REF = REPO / "data" / "poster_yolo" / "reference"
CLASS_ID = 0
# display scale (image → canvas)
MAX_W, MAX_H = 960, 720


def to_yolo(x1: float, y1: float, x2: float, y2: float, w: int, h: int) -> str:
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    cx = ((x1 + x2) / 2) / w
    cy = ((y1 + y2) / 2) / h
    bw = (x2 - x1) / w
    bh = (y2 - y1) / h
    return f"{CLASS_ID} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n"


class LabelApp:
    def __init__(self, images: list[Path]) -> None:
        self.images = images
        self.idx = 0
        self.saved = 0
        self.prev_box: tuple[float, float, float, float] | None = None  # image coords
        self.box: tuple[float, float, float, float] | None = None
        self.drag_start: tuple[float, float] | None = None
        self.scale = 1.0
        self.img_w = self.img_h = 0
        self.pil: Image.Image | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.rect_id: int | None = None

        (REF / "images").mkdir(parents=True, exist_ok=True)
        (REF / "labels").mkdir(parents=True, exist_ok=True)
        (REF / "preview").mkdir(parents=True, exist_ok=True)

        self.root = tk.Tk()
        self.root.title("Label wheat poster — drag box around the poster")
        self.root.bind("<Return>", lambda _e: self.save())
        self.root.bind("<Escape>", lambda _e: self.quit())
        self.root.bind("r", lambda _e: self.redraw())
        self.root.bind("c", lambda _e: self.copy_prev())
        self.root.bind("s", lambda _e: self.skip())
        self.root.bind("q", lambda _e: self.quit())

        top = tk.Frame(self.root)
        top.pack(fill=tk.X, padx=8, pady=6)
        self.status = tk.Label(top, text="", font=("DejaVu Sans", 11))
        self.status.pack(side=tk.LEFT)
        for text, cmd in (
            ("Save (Enter)", self.save),
            ("Redraw (r)", self.redraw),
            ("Copy prev (c)", self.copy_prev),
            ("Skip (s)", self.skip),
            ("Quit (q)", self.quit),
        ):
            tk.Button(top, text=text, command=cmd).pack(side=tk.RIGHT, padx=3)

        self.canvas = tk.Canvas(self.root, bg="#222", cursor="cross")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        self.load_current()

    def load_current(self) -> None:
        path = self.images[self.idx]
        self.pil = Image.open(path).convert("RGB")
        self.img_w, self.img_h = self.pil.size
        scale = min(MAX_W / self.img_w, MAX_H / self.img_h, 1.0)
        self.scale = scale
        disp_w, disp_h = int(self.img_w * scale), int(self.img_h * scale)
        shown = self.pil.resize((disp_w, disp_h), Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(shown)
        self.canvas.config(width=disp_w, height=disp_h)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
        self.rect_id = None
        self.box = None
        self.drag_start = None
        self.status.config(
            text=f"[{self.idx + 1}/{len(self.images)}] {path.name}  — drag box around WHEAT POSTER"
        )

    def canvas_to_img(self, x: float, y: float) -> tuple[float, float]:
        return x / self.scale, y / self.scale

    def img_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale, y * self.scale

    def draw_box(self, x1: float, y1: float, x2: float, y2: float) -> None:
        cx1, cy1 = self.img_to_canvas(x1, y1)
        cx2, cy2 = self.img_to_canvas(x2, y2)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="#00ff00", width=2)
        self.box = (x1, y1, x2, y2)

    def on_press(self, event: tk.Event) -> None:
        self.drag_start = self.canvas_to_img(event.x, event.y)

    def on_drag(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        x1, y1 = self.canvas_to_img(event.x, event.y)
        self.draw_box(x0, y0, x1, y1)

    def on_release(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        x0, y0 = self.drag_start
        x1, y1 = self.canvas_to_img(event.x, event.y)
        self.drag_start = None
        if abs(x1 - x0) < 4 or abs(y1 - y0) < 4:
            return
        self.draw_box(x0, y0, x1, y1)

    def redraw(self) -> None:
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = None
        self.box = None

    def copy_prev(self) -> None:
        if self.prev_box is None:
            messagebox.showinfo("Copy", "No previous box yet.")
            return
        self.draw_box(*self.prev_box)

    def save(self) -> None:
        if self.box is None:
            messagebox.showinfo("Save", "Draw a box first.")
            return
        path = self.images[self.idx]
        x1, y1, x2, y2 = self.box
        x1, x2 = sorted((max(0, x1), min(self.img_w, x2)))
        y1, y2 = sorted((max(0, y1), min(self.img_h, y2)))
        label = to_yolo(x1, y1, x2, y2, self.img_w, self.img_h)
        label_path = path.with_suffix(".txt")
        label_path.write_text(label)

        shutil.copy2(path, REF / "images" / path.name)
        shutil.copy2(label_path, REF / "labels" / f"{path.stem}.txt")

        assert self.pil is not None
        preview = self.pil.copy()
        draw = ImageDraw.Draw(preview)
        draw.rectangle([x1, y1, x2, y2], outline=(0, 255, 0), width=3)
        preview.save(REF / "preview" / path.name, quality=92)

        self.prev_box = (x1, y1, x2, y2)
        self.saved += 1
        print(f"saved {label_path.name}")
        self.next_image()

    def skip(self) -> None:
        print(f"skipped {self.images[self.idx].name}")
        self.next_image()

    def next_image(self) -> None:
        self.idx += 1
        if self.idx >= len(self.images):
            messagebox.showinfo("Done", f"Finished. saved={self.saved}\n{REF}")
            self.quit()
            return
        self.load_current()

    def quit(self) -> None:
        print(f"quit — saved={self.saved} → {REF}")
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    try:
        from PIL import Image as _  # noqa: F401
    except ImportError as e:
        raise SystemExit("Need Pillow: pip install pillow") from e

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--src",
        type=Path,
        default=DEFAULT_SRC,
        help="folder of jpgs to label (default: to_label_25)",
    )
    args = ap.parse_args()
    src = args.src if args.src.is_absolute() else REPO / args.src

    images = sorted(src.glob("*.jpg"))
    # skip BADBOX previews if any slipped in
    images = [p for p in images if "_BADBOX" not in p.name]
    if not images:
        raise SystemExit(f"no images in {src}")
    print(f"{len(images)} images in {src}")
    LabelApp(images).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
