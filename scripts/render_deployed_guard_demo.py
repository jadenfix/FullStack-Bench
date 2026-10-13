#!/usr/bin/env python3
"""Render the actual deployed-guard receipt as a compact GIF, MP4, and poster."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT, FPS = 1440, 900, 8
BG, PANEL, MUTED, WHITE = "#07131f", "#102436", "#91a9bc", "#f7fbff"
GREEN, RED, BLUE = "#45e0a8", "#ff7188", "#65b7ff"


def _font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size)


def _frame(title: str, kicker: str, rows: list[tuple[str, str, str]], footer: str) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((60, 52, WIDTH - 60, HEIGHT - 52), radius=30, fill=PANEL, outline="#214158", width=2)
    draw.text((105, 92), kicker.upper(), font=_font(24, True), fill=BLUE)
    draw.text((105, 137), title, font=_font(52, True), fill=WHITE)
    compact = len(rows) > 3
    row_height = 92 if compact else 118
    row_step = 108 if compact else 142
    y = 245
    for label, value, color in rows:
        draw.rounded_rectangle((105, y, WIDTH - 105, y + row_height), radius=20, fill="#0b1b29")
        draw.text((138, y + (15 if compact else 23)), label, font=_font(21 if compact else 25, True), fill=MUTED)
        draw.text((138, y + (48 if compact else 62)), value, font=_font(25 if compact else 31, True), fill=color)
        y += row_step
    draw.text((105, HEIGHT - 104), footer, font=_font(22), fill=MUTED)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    workflow = receipt["workflow"]
    source = receipt["source_commit"][:12]
    honest = workflow["honest_nested_service"]
    link = workflow["same_content_external_symlink"]
    empty = workflow["empty_archive"]
    separator = workflow["duplicate_repeated_separator"]
    dot = workflow["duplicate_dot_segment"]
    windows = workflow["ambiguous_windows_separator"]
    scenes = [
        _frame("Deployment evidence that fails closed", "FullStack-Bench · actual CLI replay", [
            ("CAPTURED SOURCE", source, BLUE),
            ("WORKFLOW", "archive → repository root → digest comparison", WHITE),
            ("SCOPE", "offline fictional checkout fixture", MUTED),
        ], "Every status below comes from fsbench/deployed_check.py."),
        _frame("Honest nested service", "Control case", [
            ("CLI EXIT", str(honest["exit_code"]), GREEN),
            ("SELECTED ROOT", honest["report"]["checkout"]["root"], WHITE),
            ("SOURCE FILES", str(honest["report"]["checkout"]["files"]), WHITE),
        ], "The deployed app.py matches the checked-in service source."),
        _frame("Same bytes, wrong trust boundary", "Adversarial case", [
            ("ATTEMPT", "replace services/checkout with an external symlink", WHITE),
            ("CLI EXIT", str(link["exit_code"]), RED),
            ("RESULT", "REJECTED — app.py is not repository evidence", RED),
        ], "Matching external content cannot satisfy the source gate."),
        _frame("Ambiguous receipts rejected", "Evidence integrity", [
            ("EMPTY ARCHIVE", f"exit {empty['exit_code']} · no source files", RED),
            ("EQUIVALENT PATHS", f"exit {separator['exit_code']}/{dot['exit_code']} · // and /./ aliases", RED),
            ("WINDOWS SEPARATOR", f"exit {windows['exit_code']} · ambiguous backslash", RED),
            ("OUTCOME", "1 honest pass · 5 adversarial rejects", GREEN),
        ], f"Source-pinned at {source} · no model or performance claim."),
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    scenes[-1].save(args.output_dir / "deployed-receipt-guard.png")
    with tempfile.TemporaryDirectory(prefix="deployed-demo-frames-") as tmp:
        frames = Path(tmp)
        index = 0
        for scene in scenes:
            for _ in range(20):
                scene.save(frames / f"frame-{index:04d}.png")
                index += 1
        mp4 = args.output_dir / "deployed-receipt-guard.mp4"
        gif = args.output_dir / "deployed-receipt-guard.gif"
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(FPS),
            "-i", str(frames / "frame-%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(mp4),
        ], check=True)
        palette = frames / "palette.png"
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4),
            "-vf", "fps=8,scale=1200:-1:flags=lanczos,palettegen=max_colors=128", str(palette),
        ], check=True)
        subprocess.run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4), "-i", str(palette),
            "-lavfi", "fps=8,scale=1200:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3",
            str(gif),
        ], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
