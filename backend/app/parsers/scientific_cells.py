"""Recover positive powers in a printed coefficient · 10^n from original scan pixels."""

import re

import fitz


def _ink_runs(pixels: bytes, width: int) -> list[fitz.IRect]:
    """Bounds of consecutive occupied columns. Touching groups stay unresolved."""
    runs = []
    for x in range(width):
        ys = [y for y, value in enumerate(pixels[x::width]) if value < 120]
        if not ys:
            continue
        column = fitz.IRect(x, ys[0], x + 1, ys[-1] + 1)
        if runs and runs[-1].x1 == x:
            runs[-1] |= column
        else:
            runs.append(column)
    return runs


def _parts(runs):
    if not 5 <= len(runs) <= 16:
        return None
    height = runs[0].height
    baseline = runs[0].y1
    if height < 12:
        return None
    for i in range(1, len(runs) - 3):
        dot = runs[i]
        tail = runs[i+1:]
        raised = next((j for j, b in enumerate(tail) if b.y1 < baseline - .24 * height), 0)
        if not 1 <= raised <= 5:
            continue
        base, power = tail[:raised], tail[raised:]
        if not (dot.width <= .33 * height and dot.height <= .33 * height
                and abs(dot.width - dot.height) <= .12 * height
                and .22 * height <= baseline - (dot.y0 + dot.y1) / 2 <= .67 * height
                and dot.x0 - runs[i-1].x1 >= .24 * height
                and base[0].x0 - dot.x1 >= .24 * height):
            continue
        if not all(.75 * height <= b.height <= 1.25 * height
                   and abs(b.y1 - baseline) <= .14 * height for b in base):
            continue
        if len(power) > 6 or not all(.35 * height <= b.height <= .85 * height
                                     and b.y1 < baseline - .24 * height
                                     and b.y0 < base[-1].y0 for b in power):
            continue
        if max(b.y1 for b in power) - min(b.y1 for b in power) > .14 * height:
            continue
        return runs[:i], base, power
    return None


def _read_digits(pixels, stride, boxes, language, tessdata):
    rect = fitz.IRect(boxes[0])
    for box in boxes[1:]:
        rect |= box
    # Segmentation uses dark ink. Keep the faint antialiased edge for OCR.
    rect = fitz.IRect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 2, rect.y1 + 2) & fitz.IRect(
        0, 0, stride, len(pixels) // stride
    )
    pad = 12
    width, height = rect.width + 2 * pad, rect.height + 2 * pad
    buf = bytearray([255]) * (width * height)
    for y in range(rect.height):
        source = (rect.y0 + y) * stride + rect.x0
        target = (pad + y) * width + pad
        buf[target:target + rect.width] = pixels[source:source + rect.width]
    gray = fitz.Pixmap(fitz.csGRAY, width, height, bytes(buf), False)
    rgb = fitz.Pixmap(fitz.csRGB, gray)
    rgb.set_dpi(300, 300)
    with fitz.open("pdf", rgb.pdfocr_tobytes(language=language, tessdata=tessdata)) as crop:
        return crop[0].get_text().strip()


def _read_at_scale(page, rect, prefix, dpi, language, tessdata):
    try:
        pix = page.get_pixmap(clip=rect + (-1, -1, 1, 1), dpi=dpi,
                              colorspace=fitz.csGRAY, alpha=False, annots=False)
        pixels = pix.samples
        runs = _ink_runs(pixels, pix.width)
        if any(b.x0 == 0 or b.x1 == pix.width or b.y0 == 0 or b.y1 == pix.height for b in runs):
            return None
        parts = _parts(runs)
        if parts is None:
            return None
        coefficient, base, power = [_read_digits(pixels, pix.stride, group, language, tessdata)
                                    for group in parts]
    except RuntimeError:
        return None
    if coefficient != prefix or base != "10" or not re.fullmatch(r"[0-9]{1,3}", power):
        return None
    return f"{coefficient}·10^{{{power}}}"


def refine_scientific_cells(page, tables, language, tessdata):
    edits = []
    attempts = 0
    for table in tables:
        for i, bbox in enumerate(table.cells):
            row, column = divmod(i, table.column_count)
            if not bbox or row >= len(table.rows) or column >= len(table.rows[row]):
                continue
            original = table.rows[row][column]
            prefix = re.match(r"^[0-9]+(?:\.[0-9]+)?", original)
            rect = fitz.Rect(bbox)
            if (not prefix or len(original) > 32 or "10" not in original[prefix.end():]
                    or not (6 < rect.width <= 120 and 3 < rect.height <= 25)):
                continue
            attempts += 1
            if attempts > 24:
                return edits
            reads = [_read_at_scale(page, rect, prefix.group(), dpi, language, tessdata)
                     for dpi in (432, 480, 576)]
            valid = [value for value in reads if value]
            if len(valid) < 2 or len(set(valid)) != 1:
                continue
            table.rows[row][column] = valid[0]
            edits.append({"page": page.number + 1, "bbox": bbox, "row": row, "column": column,
                          "original": original, "recovered": valid[0], "scale_reads": reads})
    return edits
