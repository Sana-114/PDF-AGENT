"""Render completed, aligned document translations as portable text artifacts."""

import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Literal

TranslationExportFormat = Literal["markdown", "html"]
TranslationExportMode = Literal["translation", "bilingual"]


@dataclass(slots=True, frozen=True)
class DocumentTranslationExport:
    content: str
    filename: str
    media_type: str


def _clean_title(title: str | None, original_filename: str) -> str:
    value = (title or Path(original_filename).stem or "Document translation").strip()
    return " ".join(value.split()) or "Document translation"


def _export_stem(original_filename: str) -> str:
    stem = Path(original_filename).stem or "document"
    cleaned = re.sub(r"[^\w.-]+", "-", stem, flags=re.UNICODE).strip("-._")
    return cleaned[:120] or "document"


def _segments(page: dict[str, Any]) -> list[dict[str, str]]:
    values = page.get("segments", [])
    if not isinstance(values, list):
        return []
    segments: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        translation = str(value.get("translation", "")).strip()
        source = str(value.get("source_text", "")).strip()
        if not translation and not source:
            continue
        segments.append(
            {
                "block_id": str(value.get("block_id", "")).strip(),
                "source": source,
                "translation": translation,
            }
        )
    return segments


def _markdown(
    *,
    title: str,
    original_filename: str,
    manifest: dict[str, Any],
    pages: list[dict[str, Any]],
    mode: TranslationExportMode,
) -> str:
    target = str(manifest.get("target_language", ""))
    provider = str(manifest.get("provider", "unknown"))
    model = str(manifest.get("model") or "default")
    lines = [
        f"# {title}",
        "",
        f"> Source: `{original_filename}`  ",
        f"> Target language: `{target}`  ",
        f"> Translation provider: `{provider}` / `{model}`  ",
        f"> Export mode: `{mode}`",
        "",
    ]
    for page in pages:
        page_number = int(page.get("page_number", 0))
        lines.extend([f'<a id="page-{page_number}"></a>', "", f"## Page {page_number}", ""])
        page_segments = _segments(page)
        if not page_segments:
            lines.extend(["_No translatable text on this page._", ""])
            continue
        for index, segment in enumerate(page_segments, start=1):
            block_label = segment["block_id"] or f"segment-{index}"
            if mode == "bilingual":
                lines.extend(
                    [
                        f"### {index}. `{block_label}`",
                        "",
                        "**Original**",
                        "",
                        segment["source"],
                        "",
                        "**Translation**",
                        "",
                        segment["translation"],
                        "",
                    ]
                )
            else:
                lines.extend([segment["translation"], ""])
    return "\n".join(lines).rstrip() + "\n"


def _html(
    *,
    title: str,
    original_filename: str,
    manifest: dict[str, Any],
    pages: list[dict[str, Any]],
    mode: TranslationExportMode,
) -> str:
    target = str(manifest.get("target_language", ""))
    provider = str(manifest.get("provider", "unknown"))
    model = str(manifest.get("model") or "default")
    page_markup: list[str] = []
    for page in pages:
        page_number = int(page.get("page_number", 0))
        segment_markup: list[str] = []
        for index, segment in enumerate(_segments(page), start=1):
            block_label = segment["block_id"] or f"segment-{index}"
            if mode == "bilingual":
                body = (
                    '<div class="source"><h3>Original</h3><p>'
                    f'{escape(segment["source"])}</p></div>'
                    '<div class="translation"><h3>Translation</h3><p>'
                    f'{escape(segment["translation"])}</p></div>'
                )
            else:
                body = f'<div class="translation"><p>{escape(segment["translation"])}</p></div>'
            segment_markup.append(
                f'<article data-block-id="{escape(block_label, quote=True)}">'
                f'<small>{index:02d} · {escape(block_label)}</small>{body}</article>'
            )
        if not segment_markup:
            segment_markup.append("<p><em>No translatable text on this page.</em></p>")
        page_markup.append(
            f'<section class="page" id="page-{page_number}"><h2>Page {page_number}</h2>'
            f'{"".join(segment_markup)}</section>'
        )

    language = "zh-CN" if target == "zh" else "en"
    columns = "1fr 1fr" if mode == "bilingual" else "1fr"
    return f"""<!doctype html>
<html lang="{language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
  <style>
    :root {{ color-scheme: light; font-family: Georgia, "Noto Serif SC", serif; }}
    body {{
      max-width: 1100px; margin: 0 auto; padding: 32px 24px 72px;
      color: #1d3029; background: #f5f5ef;
    }}
    header {{ padding: 24px; background: #1d3b30; color: white; border-radius: 12px; }}
    header h1 {{ margin: 0 0 12px; }}
    header p {{ margin: 4px 0; color: #dfe9df; }}
    .page {{ margin-top: 28px; }}
    article {{
      display: grid; grid-template-columns: {columns}; gap: 18px;
      margin: 12px 0; padding: 18px; background: white;
      border: 1px solid #d9dfd9; border-radius: 10px; break-inside: avoid;
    }}
    article > small {{
      grid-column: 1 / -1; color: #527566;
      font: 700 11px/1.4 system-ui, sans-serif;
    }}
    h2 {{ border-bottom: 2px solid #9fc1aa; padding-bottom: 6px; }}
    h3 {{
      margin: 0 0 8px; color: #527566; font: 700 12px/1.4 system-ui, sans-serif;
      text-transform: uppercase; letter-spacing: .06em;
    }}
    p {{ margin: 0; white-space: pre-wrap; line-height: 1.75; overflow-wrap: anywhere; }}
    @media (max-width: 720px) {{ article {{ grid-template-columns: 1fr; }} }}
    @media print {{
      body {{ max-width: none; padding: 0; background: white; }}
      header {{ border-radius: 0; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(title)}</h1>
    <p>Source: {escape(original_filename)}</p>
    <p>
      Target language: {escape(target)} · Provider: {escape(provider)} /
      {escape(model)} · Mode: {escape(mode)}
    </p>
  </header>
  {''.join(page_markup)}
</body>
</html>
"""


def build_document_translation_export(
    *,
    title: str | None,
    original_filename: str,
    manifest: dict[str, Any],
    pages: list[dict[str, Any]],
    output_format: TranslationExportFormat,
    mode: TranslationExportMode,
) -> DocumentTranslationExport:
    """Build a deterministic export without altering translated text or protected tokens."""
    document_title = _clean_title(title, original_filename)
    stem = _export_stem(original_filename)
    if output_format == "markdown":
        return DocumentTranslationExport(
            content=_markdown(
                title=document_title,
                original_filename=original_filename,
                manifest=manifest,
                pages=pages,
                mode=mode,
            ),
            filename=f"{stem}-{manifest.get('target_language', 'translated')}.md",
            media_type="text/markdown",
        )
    if output_format == "html":
        return DocumentTranslationExport(
            content=_html(
                title=document_title,
                original_filename=original_filename,
                manifest=manifest,
                pages=pages,
                mode=mode,
            ),
            filename=f"{stem}-{manifest.get('target_language', 'translated')}.html",
            media_type="text/html",
        )
    raise ValueError(f"Unsupported translation export format: {output_format}")
