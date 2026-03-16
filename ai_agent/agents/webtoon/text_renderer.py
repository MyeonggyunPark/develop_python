from __future__ import annotations

import io
import logging
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageStat

from .clients import normalize_dialogue_text, sanitize_public_text
from .config import WebtoonSettings

logger = logging.getLogger(__name__)


DEFAULT_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

FOUR_PANEL_COLUMNS = 2
FOUR_PANEL_ROWS = 2
FOUR_PANEL_PANEL_WIDTH = 768
FOUR_PANEL_PANEL_HEIGHT = 768
FOUR_PANEL_OUTER_MARGIN = 28
FOUR_PANEL_GUTTER = 24
THUMBNAIL_BADGE_TEXT = "독일생활 적응기"
DEFAULT_BUBBLE_FONT_SIZE = 34
MIN_BUBBLE_FONT_SIZE = 24
BUBBLE_LINE_GAP = 4


def resolve_font_path(settings: WebtoonSettings) -> Path | None:
    if settings.font_file and settings.font_file.exists():
        return settings.font_file

    if settings.font_assets_dir.exists():
        for pattern in ("*.ttf", "*.otf", "*.ttc"):
            candidates = sorted(settings.font_assets_dir.glob(pattern))
            if candidates:
                return candidates[0]

    for candidate in DEFAULT_FONT_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path
    return None


def flatten_panel_dialogues(panels: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for panel in panels:
        for line in panel.get("dialogue_lines", []):
            stripped = str(line).strip()
            if stripped:
                lines.append(stripped)
    return lines


def _load_font(path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path is None:
        logger.warning("사용 가능한 폰트가 없어 기본 비트맵 폰트를 사용합니다. 텍스트 렌더링 품질이 저하됩니다.")
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError:
        logger.warning("폰트 로딩 실패 (%s), 기본 비트맵 폰트로 대체합니다.", path)
        return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [text]

    # Step 1: greedy wrap to determine the number of lines needed.
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)

    num_lines = len(lines)
    if num_lines <= 1:
        return lines

    # Step 2: binary-search for the minimum line width that still fits in
    # the same number of lines.  This distributes text evenly across lines
    # and avoids short orphan lines like a single word on its own row.
    lo = max(
        draw.textbbox((0, 0), w, font=font)[2] - draw.textbbox((0, 0), w, font=font)[0]
        for w in words
    )
    hi = max_width
    best = lines

    while lo <= hi:
        mid = (lo + hi) // 2
        trial: list[str] = []
        cur = words[0]
        for word in words[1:]:
            cand = f"{cur} {word}"
            bbox = draw.textbbox((0, 0), cand, font=font)
            if bbox[2] - bbox[0] <= mid:
                cur = cand
            else:
                trial.append(cur)
                cur = word
        trial.append(cur)

        if len(trial) <= num_lines:
            best = trial
            hi = mid - 1
        else:
            lo = mid + 1

    return best


def _compute_text_lines(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if " " in text:
        return _wrap_text(draw, text, font, max_width)

    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _fit_and_crop(image: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    target_width, target_height = target_size
    scale = max(target_width / image.width, target_height / image.height)
    resized = image.resize((math.ceil(image.width * scale), math.ceil(image.height * scale)), Image.Resampling.LANCZOS)
    offset_x = max(0, (resized.width - target_width) // 2)
    offset_y = max(0, (resized.height - target_height) // 2)
    return resized.crop((offset_x, offset_y, offset_x + target_width, offset_y + target_height))


def get_four_panel_boxes(
    canvas_width: int,
    canvas_height: int,
    *,
    outer_margin: int = FOUR_PANEL_OUTER_MARGIN,
    gutter: int = FOUR_PANEL_GUTTER,
) -> list[tuple[int, int, int, int]]:
    panel_width = (canvas_width - outer_margin * 2 - gutter) // FOUR_PANEL_COLUMNS
    panel_height = (canvas_height - outer_margin * 2 - gutter) // FOUR_PANEL_ROWS
    boxes: list[tuple[int, int, int, int]] = []
    for row in range(FOUR_PANEL_ROWS):
        for column in range(FOUR_PANEL_COLUMNS):
            x1 = outer_margin + column * (panel_width + gutter)
            y1 = outer_margin + row * (panel_height + gutter)
            x2 = x1 + panel_width
            y2 = y1 + panel_height
            boxes.append((x1, y1, x2, y2))
    return boxes


def compose_four_panel_canvas(panel_image_bytes_list: list[bytes]) -> dict[str, Any]:
    if len(panel_image_bytes_list) != FOUR_PANEL_COLUMNS * FOUR_PANEL_ROWS:
        raise ValueError("compose_four_panel_canvas requires exactly four panel images")

    canvas_width = FOUR_PANEL_OUTER_MARGIN * 2 + FOUR_PANEL_PANEL_WIDTH * FOUR_PANEL_COLUMNS + FOUR_PANEL_GUTTER
    canvas_height = FOUR_PANEL_OUTER_MARGIN * 2 + FOUR_PANEL_PANEL_HEIGHT * FOUR_PANEL_ROWS + FOUR_PANEL_GUTTER
    panel_boxes = get_four_panel_boxes(canvas_width, canvas_height)
    canvas = Image.new("RGB", (canvas_width, canvas_height), color=(247, 242, 234))
    draw = ImageDraw.Draw(canvas)

    for index, (panel_bytes, panel_box) in enumerate(zip(panel_image_bytes_list, panel_boxes, strict=True), start=1):
        with Image.open(io.BytesIO(panel_bytes)) as raw_image:
            panel_image = _fit_and_crop(raw_image.convert("RGB"), (panel_box[2] - panel_box[0], panel_box[3] - panel_box[1]))
        canvas.paste(panel_image, (panel_box[0], panel_box[1]))
        draw.rounded_rectangle(panel_box, radius=20, outline=(30, 30, 30), width=5)
        label_box = (panel_box[0] + 16, panel_box[1] + 14, panel_box[0] + 56, panel_box[1] + 48)
        draw.rounded_rectangle(label_box, radius=10, fill=(24, 24, 24), outline=None)
        draw.text((label_box[0] + 11, label_box[1] + 4), str(index), fill=(255, 255, 255))

    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return {
        "image_bytes": output.getvalue(),
        "panel_boxes": panel_boxes,
        "canvas_size": [canvas_width, canvas_height],
    }


def get_single_panel_box(
    image_width: int,
    image_height: int,
    *,
    outer_margin_x: int = 28,
    outer_margin_y: int = 24,
) -> tuple[int, int, int, int]:
    return (
        outer_margin_x,
        outer_margin_y,
        image_width - outer_margin_x,
        image_height - outer_margin_y,
    )


def split_dialogue_groups(dialogue_lines: list[str]) -> list[list[str]]:
    lines = [str(line).strip() for line in dialogue_lines if str(line).strip()]
    if len(lines) <= 1:
        return [lines] if lines else []
    if len(lines) == 2:
        return [[lines[0]], [lines[1]]]
    if len(lines) == 3:
        return [lines[:2], [lines[2]]]
    return [lines[:2], lines[2:4]]


def _scale_polygon(points: list[tuple[int, int]], scale: float) -> list[tuple[int, int]]:
    cx = sum(x for x, _ in points) / len(points)
    cy = sum(y for _, y in points) / len(points)
    return [(int(cx + (x - cx) * scale), int(cy + (y - cy) * scale)) for x, y in points]


def _estimate_bubble_size(
    panel_box: tuple[int, int, int, int],
    *,
    num_groups: int,
    render_version: int,
) -> tuple[int, int]:
    panel_width = panel_box[2] - panel_box[0]
    panel_height = panel_box[3] - panel_box[1]
    if num_groups <= 1:
        width_ratio = min(0.60, 0.46 + 0.03 * (render_version - 1))
        height_ratio = min(0.32, 0.22 + 0.02 * render_version)
    else:
        width_ratio = min(0.42, 0.31 + 0.02 * render_version)
        height_ratio = min(0.24, 0.16 + 0.02 * render_version)
    return int(panel_width * width_ratio), int(panel_height * height_ratio)


def _candidate_boxes(
    panel_box: tuple[int, int, int, int],
    *,
    box_width: int,
    box_height: int,
    side: str,
) -> list[dict[str, Any]]:
    panel_width = panel_box[2] - panel_box[0]
    panel_height = panel_box[3] - panel_box[1]
    margin_x = max(18, int(panel_width * 0.04))
    margin_y = max(18, int(panel_height * 0.035))

    if side == "left":
        x1 = panel_box[0] + margin_x
    elif side == "right":
        x1 = panel_box[2] - margin_x - box_width
    else:
        x1 = panel_box[0] + (panel_width - box_width) // 2

    positions = [
        ("top", panel_box[1] + margin_y),
        ("middle", panel_box[1] + (panel_height - box_height) // 2),
        ("bottom", panel_box[3] - margin_y - box_height),
    ]
    return [
        {
            "box": (x1, y1, x1 + box_width, y1 + box_height),
            "v_pos": "bottom" if label == "bottom" else "top",
            "label": label,
            "side": side,
        }
        for label, y1 in positions
    ]


def _visual_density_score(image: Image.Image, box: tuple[int, int, int, int]) -> float:
    crop = image.crop(box).convert("L").resize((72, 72), Image.Resampling.BILINEAR)
    edge_map = crop.filter(ImageFilter.FIND_EDGES)
    crop_stat = ImageStat.Stat(crop)
    edge_stat = ImageStat.Stat(edge_map)
    variance = crop_stat.var[0] if crop_stat.var else 0.0
    edge_mean = edge_stat.mean[0] if edge_stat.mean else 0.0
    return edge_mean * 1.4 + variance * 0.08


def _boxes_overlap(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> bool:
    return not (box_a[2] <= box_b[0] or box_b[2] <= box_a[0] or box_a[3] <= box_b[1] or box_b[3] <= box_a[1])


def _multi_bubble_geometries(
    image: Image.Image,
    panel_box: tuple[int, int, int, int],
    panel_index: int,
    num_groups: int,
    render_version: int,
) -> list[dict[str, Any]]:
    del panel_index
    box_width, box_height = _estimate_bubble_size(panel_box, num_groups=num_groups, render_version=render_version)

    if num_groups <= 1:
        candidates = []
        for side in ("left", "center", "right"):
            candidates.extend(_candidate_boxes(panel_box, box_width=box_width, box_height=box_height, side=side))
        best = min(candidates, key=lambda item: _visual_density_score(image, item["box"]))
        return [{"box": best["box"], "v_pos": best["v_pos"], "side": best["side"]}]

    chosen: list[dict[str, Any]] = []
    for side in ("left", "right"):
        side_candidates = _candidate_boxes(panel_box, box_width=box_width, box_height=box_height, side=side)
        best = min(
            side_candidates,
            key=lambda item: _visual_density_score(image, item["box"])
            + sum(400.0 for previous in chosen if _boxes_overlap(item["box"], previous["box"])),
        )
        chosen.append({"box": best["box"], "v_pos": best["v_pos"], "side": best["side"]})
    return chosen[:num_groups]


def _draw_speech_bubble(
    draw: ImageDraw.ImageDraw,
    bubble_box: tuple[int, int, int, int],
    panel_index: int,
    *,
    vertical_position: str | None = None,
    tail_side: str | None = None,
) -> list[tuple[int, int]]:
    x1, y1, x2, y2 = bubble_box
    bw = x2 - x1
    bh = y2 - y1
    radius = min(bw, bh) // 3

    wipe_pad = max(10, min(bw, bh) // 12)
    wipe_box = (x1 - wipe_pad, y1 - wipe_pad, x2 + wipe_pad, y2 + wipe_pad)
    draw.rounded_rectangle(wipe_box, radius=radius + wipe_pad // 2, fill=(255, 255, 255, 255))

    # outer stroke (bold, webtoon-like)
    stroke_box = (x1 - 2, y1 - 2, x2 + 2, y2 + 2)
    draw.rounded_rectangle(stroke_box, radius=radius + 2, fill=(30, 30, 30, 255))
    # white fill
    draw.rounded_rectangle(bubble_box, radius=radius, fill=(255, 255, 255, 248))

    # tail: triangular pointer
    if vertical_position is None:
        vertical_position = ["top", "bottom", "top", "bottom"][panel_index % 4]
    if tail_side is None:
        tail_side = ["left", "right", "right", "left"][panel_index % 4]

    tail_w = min(28, bw // 6)
    if tail_side == "left":
        tail_cx = x1 + bw // 4
    else:
        tail_cx = x2 - bw // 4

    if vertical_position == "top":
        tip_x = tail_cx + (12 if tail_side == "left" else -12)
        tip_y = y2 + min(30, bh // 3)
        tri = [(tail_cx - tail_w // 2, y2 - 2), (tail_cx + tail_w // 2, y2 - 2), (tip_x, tip_y)]
    else:
        tip_x = tail_cx + (12 if tail_side == "left" else -12)
        tip_y = y1 - min(30, bh // 3)
        tri = [(tail_cx - tail_w // 2, y1 + 2), (tail_cx + tail_w // 2, y1 + 2), (tip_x, tip_y)]

    draw.polygon(_scale_polygon(tri, 1.28), fill=(255, 255, 255, 255))
    draw.polygon(tri, fill=(30, 30, 30, 255))
    cx_avg = sum(p[0] for p in tri) / 3
    cy_avg = sum(p[1] for p in tri) / 3
    shrink = 0.82
    inner_tri = [(int(cx_avg + (px - cx_avg) * shrink), int(cy_avg + (py - cy_avg) * shrink)) for px, py in tri]
    draw.polygon(inner_tri, fill=(255, 255, 255, 248))

    return [(tip_x, tip_y)]


def _render_bubble_with_text(
    draw: ImageDraw.ImageDraw,
    font_path: Path | None,
    lines: list[str],
    bubble_box: tuple[int, int, int, int],
    panel_index: int,
    *,
    vertical_position: str | None = None,
    tail_side: str | None = None,
    render_version: int = 1,
) -> dict[str, Any] | None:
    """Render one speech bubble with dialogue lines. Returns layout info or None."""
    x1, y1, x2, y2 = bubble_box
    padding_x = 28
    padding_y = 20
    max_text_width = max(60, (x2 - x1) - padding_x * 2)
    del render_version
    base_font_size = DEFAULT_BUBBLE_FONT_SIZE
    font = _load_font(font_path, base_font_size)

    wrapped_lines: list[str] = []
    for line in lines:
        wrapped_lines.extend(_compute_text_lines(draw, normalize_dialogue_text(str(line)), font, max_text_width))
    if not wrapped_lines:
        return None

    while True:
        line_height = draw.textbbox((0, 0), "한Ag", font=font)[3]
        total_text_height = len(wrapped_lines) * line_height + max(0, len(wrapped_lines) - 1) * BUBBLE_LINE_GAP
        if total_text_height <= (y2 - y1) - padding_y * 2 or base_font_size <= MIN_BUBBLE_FONT_SIZE:
            break
        base_font_size -= 2
        font = _load_font(font_path, base_font_size)
        wrapped_lines = []
        for line in lines:
            wrapped_lines.extend(_compute_text_lines(draw, normalize_dialogue_text(str(line)), font, max_text_width))

    tail_circles = _draw_speech_bubble(
        draw, bubble_box, panel_index,
        vertical_position=vertical_position, tail_side=tail_side,
    )

    line_height = draw.textbbox((0, 0), "한Ag", font=font)[3]
    total_text_h = len(wrapped_lines) * line_height + max(0, len(wrapped_lines) - 1) * BUBBLE_LINE_GAP
    cursor_y = y1 + max(padding_y, ((y2 - y1) - total_text_h) // 2)
    for line in wrapped_lines:
        line_bbox = draw.textbbox((0, 0), line, font=font)
        line_w = line_bbox[2] - line_bbox[0]
        text_x = x1 + max(padding_x, ((x2 - x1) - line_w) // 2)
        draw.text((text_x, cursor_y), line, font=font, fill=(20, 20, 20, 255))
        cursor_y += (line_bbox[3] - line_bbox[1]) + BUBBLE_LINE_GAP

    return {
        "bubble_box": [x1, y1, x2, y2],
        "dialogue_lines": wrapped_lines,
        "font_size": base_font_size,
        "tail_circles": tail_circles,
    }


def render_text_boxes(
    base_image_bytes: bytes,
    panels: list[dict[str, Any]],
    settings: WebtoonSettings,
    *,
    render_version: int = 1,
    panel_boxes: list[tuple[int, int, int, int]] | None = None,
) -> dict[str, Any]:
    font_path = resolve_font_path(settings)
    with Image.open(io.BytesIO(base_image_bytes)) as image:
        canvas = image.convert("RGBA")

    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size
    boxes = panel_boxes or (
        [get_single_panel_box(width, height)] if len(panels) == 1 else get_four_panel_boxes(width, height)
    )
    layout: list[dict[str, Any]] = []

    for index, panel in enumerate(panels):
        if index >= len(boxes):
            break
        dialogue_lines = panel.get("dialogue_lines", [])
        if not dialogue_lines:
            continue

        groups = split_dialogue_groups(dialogue_lines)
        geoms = _multi_bubble_geometries(canvas, boxes[index], index, len(groups), render_version)
        bubble_items: list[dict[str, Any]] = []
        for group_lines, geom in zip(groups, geoms, strict=False):
            result = _render_bubble_with_text(
                draw, font_path, group_lines, geom["box"], index,
                vertical_position=geom["v_pos"], tail_side=geom["side"],
                render_version=render_version,
            )
            if result:
                bubble_items.append(result)

        if not bubble_items:
            continue

        all_wrapped: list[str] = []
        all_tails: list[tuple[int, int]] = []
        all_boxes: list[list[int]] = []
        for item in bubble_items:
            all_wrapped.extend(item["dialogue_lines"])
            all_tails.extend(item["tail_circles"])
            all_boxes.append(item["bubble_box"])

        layout.append({
            "panel_no": panel.get("panel_no", index + 1),
            "panel_box": list(boxes[index]),
            "bubble_box": all_boxes[0],
            "bubble_boxes": all_boxes,
            "dialogue_lines": all_wrapped,
            "font_size": bubble_items[0]["font_size"],
            "tail_circles": all_tails,
        })

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG")
    return {
        "image_bytes": output.getvalue(),
        "font_path": str(font_path) if font_path else None,
        "layout": layout,
        "render_version": render_version,
    }


def render_thumbnail_card(
    base_image_bytes: bytes,
    *,
    title: str,
    topic: str,
    settings: WebtoonSettings,
) -> dict[str, Any]:
    font_path = resolve_font_path(settings)
    with Image.open(io.BytesIO(base_image_bytes)) as image:
        canvas = image.convert("RGBA")

    draw = ImageDraw.Draw(canvas)
    width, height = canvas.size

    # --- top overlay: gradient-like dark plate covering upper ~45% for title/subtitle ---
    plate_height = int(height * 0.45)
    overlay = Image.new("RGBA", (width, plate_height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    # gradient from opaque top to transparent bottom
    band_count = 20
    band_h = plate_height // band_count
    for i in range(band_count):
        alpha = int(185 * (1.0 - i / band_count) ** 0.6)
        y_start = i * band_h
        y_end = y_start + band_h if i < band_count - 1 else plate_height
        overlay_draw.rectangle((0, y_start, width, y_end), fill=(18, 18, 18, alpha))
    canvas.alpha_composite(overlay, (0, 0))
    draw = ImageDraw.Draw(canvas)

    # --- badge ---
    badge_font = _load_font(font_path, max(22, width // 30))
    badge_text = THUMBNAIL_BADGE_TEXT
    badge_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_w = badge_bbox[2] - badge_bbox[0] + 32
    badge_h = badge_bbox[3] - badge_bbox[1] + 18
    badge_x = 36
    badge_y = 36
    draw.rounded_rectangle(
        (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
        radius=16,
        fill=(255, 255, 255, 230),
    )
    draw.text((badge_x + 16, badge_y + 9), badge_text, font=badge_font, fill=(20, 20, 20, 255))

    # --- main title (large, bold) ---
    title_font_size = max(38, width // 14)
    title_font = _load_font(font_path, title_font_size)
    title_max_width = width - 72
    title_text = sanitize_public_text(title.strip(), fallback=sanitize_public_text(topic.strip(), fallback=THUMBNAIL_BADGE_TEXT))
    title_lines = _compute_text_lines(draw, title_text, title_font, title_max_width)
    title_lines = title_lines[:2]
    title_y = badge_y + badge_h + 28
    for line in title_lines:
        # text shadow
        draw.text((38, title_y + 2), line, font=title_font, fill=(0, 0, 0, 120))
        draw.text((36, title_y), line, font=title_font, fill=(255, 255, 255, 255))
        bbox = draw.textbbox((0, 0), line, font=title_font)
        title_y += (bbox[3] - bbox[1]) + 10

    # --- subtitle (topic) ---
    subtitle_font_size = max(24, width // 26)
    subtitle_font = _load_font(font_path, subtitle_font_size)
    subtitle_text = sanitize_public_text(topic.strip(), fallback="")
    if subtitle_text and subtitle_text != title_text:
        subtitle_lines = _compute_text_lines(draw, subtitle_text, subtitle_font, title_max_width)
        subtitle_lines = subtitle_lines[:2]
        subtitle_y = title_y + 12
        for line in subtitle_lines:
            draw.text((38, subtitle_y + 1), line, font=subtitle_font, fill=(0, 0, 0, 90))
            draw.text((36, subtitle_y), line, font=subtitle_font, fill=(230, 220, 200, 255))
            bbox = draw.textbbox((0, 0), line, font=subtitle_font)
            subtitle_y += (bbox[3] - bbox[1]) + 6
    else:
        subtitle_lines = []

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG")
    return {
        "image_bytes": output.getvalue(),
        "font_path": str(font_path) if font_path else None,
        "layout": {
            "title_lines": title_lines,
            "subtitle_lines": subtitle_lines,
            "topic": subtitle_text,
        },
    }
