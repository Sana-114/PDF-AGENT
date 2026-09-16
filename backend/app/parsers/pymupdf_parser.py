import json
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import fitz

from app.parsers.base import Block, Page, ParsedDocument
from app.parsers.checkpoint import (
    CHECKPOINT_SCHEMA_VERSION,
    read_manifest,
    utc_timestamp,
    write_json_atomic,
)
from app.parsers.layout import (
    TABLE_PAGE_HINT,
    RawTextBlock,
    TableSnapshot,
    appendix_nodes,
    body_font_size,
    build_outline,
    classify_block,
    extract_abstract,
    extract_first_page_people,
    extract_formulas,
    extract_references,
    extract_text_blocks,
    is_probable_section_heading,
    make_figure_nodes,
    make_table_node,
    nearest_caption,
    order_page_blocks,
    snapshot_table,
)
from app.parsers.text_table import extract_borderless_tables


class PyMuPDFParser:
    """Layout-aware local parser with page and bounding-box provenance."""

    name = "pymupdf-layout"
    version = fitz.VersionBind

    def parse(
        self,
        path: str,
        *,
        checkpoint_dir: Path | None = None,
        batch_size: int | None = None,
        source_fingerprint: str | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> ParsedDocument:
        document = fitz.open(path)
        try:
            total_pages = len(document)
            effective_batch_size = max(1, batch_size or total_pages or 1)
            source_identity = source_fingerprint or self._source_identity(Path(path))
            manifest = self._load_compatible_manifest(
                checkpoint_dir,
                source_identity=source_identity,
                page_count=total_pages,
                batch_size=effective_batch_size,
            )
            completed_pages = int(manifest.get("completed_pages", 0)) if manifest else 0
            batches = list(manifest.get("batches", [])) if manifest else []
            checkpoint_warnings = list(manifest.get("warnings", [])) if manifest else []
            if manifest:
                self._restore_checkpoint_metadata(manifest.get("processing_metadata", {}))
            elif checkpoint_dir is not None:
                manifest = self._new_manifest(
                    source_identity=source_identity,
                    page_count=total_pages,
                    batch_size=effective_batch_size,
                    completed_pages=0,
                    batches=[],
                    warnings=[],
                )
                write_json_atomic(checkpoint_dir / "manifest.json", manifest)

            memory_batches: list[dict[str, Any]] = []
            for start_index in range(completed_pages, total_pages, effective_batch_size):
                end_index = min(start_index + effective_batch_size, total_pages)
                payload = self._extract_batch(document, start_index, end_index)
                checkpoint_warnings.extend(payload["warnings"])
                if checkpoint_dir is not None:
                    filename = f"batch-{start_index + 1:06d}-{end_index:06d}.json"
                    write_json_atomic(checkpoint_dir / filename, payload)
                    batches.append(
                        {
                            "filename": filename,
                            "start_page": start_index + 1,
                            "end_page": end_index,
                        }
                    )
                    manifest = self._new_manifest(
                        source_identity=source_identity,
                        page_count=total_pages,
                        batch_size=effective_batch_size,
                        completed_pages=end_index,
                        batches=batches,
                        warnings=checkpoint_warnings,
                    )
                    write_json_atomic(checkpoint_dir / "manifest.json", manifest)
                else:
                    memory_batches.append(payload)
                if progress_callback is not None:
                    progress_callback(end_index, total_pages)

            if checkpoint_dir is not None:
                manifest = read_manifest(checkpoint_dir)
                if manifest is None:
                    raise OSError("解析检查点 manifest 丢失，无法合并页面分片。")
                batch_payloads = self._read_batches(checkpoint_dir, manifest)
                warnings = list(manifest.get("warnings", []))
            else:
                batch_payloads = memory_batches
                warnings = checkpoint_warnings

            raw_pages, page_sizes, page_images, page_tables = self._merge_batches(
                batch_payloads
            )
            first_page_title_guess = (
                self._guess_title_from_blocks(raw_pages[0]) if raw_pages else None
            )

            metadata_title = (document.metadata or {}).get("title")
            title = self._clean_metadata_title(metadata_title, path) or first_page_title_guess
            body_size = body_font_size([block for page in raw_pages for block in page])
            pages = self._build_pages(raw_pages, page_sizes, body_size=body_size, title=title)

            tables = []
            figures = []
            for page_index, page in enumerate(pages):
                for table_index, table in enumerate(page_tables[page_index], start=1):
                    caption = nearest_caption(page.blocks, list(table.bbox), "table_caption")
                    tables.append(
                        make_table_node(
                            table,
                            page_number=page.page_number,
                            table_index=table_index,
                            caption=caption,
                        )
                    )
                figures.extend(
                    make_figure_nodes(
                        page_images[page_index],
                        page_number=page.page_number,
                        page_area=page.width * page.height,
                        blocks=page.blocks,
                    )
                )

            authors, affiliations = extract_first_page_people(pages[0].blocks if pages else [])
            outline = build_outline(pages)
            text_parts = [block.text for page in pages for block in page.blocks]
            if not text_parts:
                warnings.append("未检测到文本层，需要切换到 OCR 解析器。")

            return ParsedDocument(
                schema_version="0.2.0",
                title=title,
                authors=authors,
                affiliations=affiliations,
                abstract=extract_abstract(pages),
                outline=outline,
                pages=pages,
                tables=tables,
                figures=figures,
                formulas=extract_formulas(pages),
                references=extract_references(pages),
                appendices=appendix_nodes(outline),
                full_text="\n".join(text_parts),
                warnings=warnings,
            )
        finally:
            document.close()

    def _extract_batch(
        self, document: fitz.Document, start_index: int, end_index: int
    ) -> dict[str, Any]:
        warnings: list[str] = []
        pages: list[dict[str, Any]] = []
        for page_index in range(start_index, end_index):
            pdf_page = document[page_index]
            page_number = page_index + 1
            textpage = self._get_textpage(pdf_page, page_number)
            page_dict = pdf_page.get_text("dict", sort=False, textpage=textpage)
            raw_blocks = extract_text_blocks(page_dict, page_number)
            raster_tables, raster_images = self._extract_raster_layout(
                pdf_page,
                textpage=textpage,
                raw_blocks=raw_blocks,
                warnings=warnings,
            )
            pages.append(
                {
                    "page_number": page_number,
                    "width": float(pdf_page.rect.width),
                    "height": float(pdf_page.rect.height),
                    "raw_blocks": [asdict(block) for block in raw_blocks],
                    "images": [
                        *self._extract_image_info(pdf_page, warnings),
                        *raster_images,
                    ],
                    "tables": [
                        asdict(table)
                        for table in [
                            *self._extract_native_tables(
                                pdf_page,
                                textpage=textpage,
                                raw_blocks=raw_blocks,
                                warnings=warnings,
                            ),
                            *raster_tables,
                        ]
                    ],
                }
            )
        return {
            "start_page": start_index + 1,
            "end_page": end_index,
            "pages": pages,
            "warnings": warnings,
        }

    def _new_manifest(
        self,
        *,
        source_identity: str,
        page_count: int,
        batch_size: int,
        completed_pages: int,
        batches: list[dict[str, Any]],
        warnings: list[str],
    ) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "parser": {"name": self.name, "version": str(self.version)},
            "source_fingerprint": source_identity,
            "page_count": page_count,
            "batch_size": batch_size,
            "completed_pages": completed_pages,
            "batches": batches,
            "warnings": warnings,
            "processing_metadata": self._checkpoint_metadata(),
            "updated_at": utc_timestamp(),
        }

    def _load_compatible_manifest(
        self,
        checkpoint_dir: Path | None,
        *,
        source_identity: str,
        page_count: int,
        batch_size: int,
    ) -> dict[str, Any] | None:
        if checkpoint_dir is None:
            return None
        manifest = read_manifest(checkpoint_dir)
        if not manifest or any(
            (
                manifest.get("schema_version") != CHECKPOINT_SCHEMA_VERSION,
                manifest.get("parser", {}).get("name") != self.name,
                str(manifest.get("parser", {}).get("version")) != str(self.version),
                manifest.get("source_fingerprint") != source_identity,
                manifest.get("page_count") != page_count,
                manifest.get("batch_size") != batch_size,
            )
        ):
            return None
        completed_pages = manifest.get("completed_pages")
        batches = manifest.get("batches")
        if not isinstance(completed_pages, int) or not 0 <= completed_pages <= page_count:
            return None
        if not isinstance(batches, list) or not self._batches_are_valid(
            checkpoint_dir, batches, completed_pages
        ):
            return None
        return manifest

    @staticmethod
    def _batches_are_valid(
        checkpoint_dir: Path, batches: list[dict[str, Any]], completed_pages: int
    ) -> bool:
        expected_start = 1
        for batch in batches:
            try:
                start_page = int(batch["start_page"])
                end_page = int(batch["end_page"])
                filename = str(batch["filename"])
                if Path(filename).name != filename:
                    return False
                payload = json.loads((checkpoint_dir / filename).read_text(encoding="utf-8"))
            except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
                return False
            if (
                start_page != expected_start
                or end_page < start_page
                or payload.get("start_page") != start_page
                or payload.get("end_page") != end_page
            ):
                return False
            expected_start = end_page + 1
        return expected_start - 1 == completed_pages

    @staticmethod
    def _read_batches(
        checkpoint_dir: Path, manifest: dict[str, Any]
    ) -> Iterator[dict[str, Any]]:
        for batch in manifest.get("batches", []):
            path = checkpoint_dir / str(batch["filename"])
            yield json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _merge_batches(
        batch_payloads: Iterable[dict[str, Any]],
    ) -> tuple[
        list[list[RawTextBlock]],
        list[tuple[float, float]],
        list[list[dict[str, Any]]],
        list[list[TableSnapshot]],
    ]:
        raw_pages: list[list[RawTextBlock]] = []
        page_sizes: list[tuple[float, float]] = []
        page_images: list[list[dict[str, Any]]] = []
        page_tables: list[list[TableSnapshot]] = []
        for payload in batch_payloads:
            for page in payload.get("pages", []):
                raw_pages.append(
                    [RawTextBlock(**block) for block in page.get("raw_blocks", [])]
                )
                page_sizes.append((float(page["width"]), float(page["height"])))
                page_images.append(list(page.get("images", [])))
                page_tables.append(
                    [TableSnapshot(**table) for table in page.get("tables", [])]
                )
        return raw_pages, page_sizes, page_images, page_tables

    def _checkpoint_metadata(self) -> dict[str, Any]:
        return {}

    def _restore_checkpoint_metadata(self, metadata: dict[str, Any]) -> None:
        del metadata

    @staticmethod
    def _source_identity(path: Path) -> str:
        stat = path.stat()
        return f"{stat.st_size}:{stat.st_mtime_ns}"

    def _get_textpage(self, page: fitz.Page, page_number: int) -> fitz.TextPage | None:
        del page, page_number
        return None

    @staticmethod
    def _build_pages(
        raw_pages: list[list[RawTextBlock]],
        page_sizes: list[tuple[float, float]],
        *,
        body_size: float,
        title: str | None,
    ) -> list[Page]:
        pages: list[Page] = []
        for page_index, raw_blocks in enumerate(raw_pages):
            width, height = page_sizes[page_index]
            blocks = []
            for order, (raw, column) in enumerate(order_page_blocks(raw_blocks, width)):
                block_type, level = classify_block(raw, body_size=body_size, title=title)
                blocks.append(
                    Block(
                        block_id=f"p{page_index + 1}-b{order + 1}",
                        type=block_type,
                        text=raw.text,
                        bbox=raw.bbox,
                        reading_order=order,
                        level=level,
                        font_size=round(raw.font_size, 2),
                        column=column,
                    )
                )
            pages.append(
                Page(
                    page_number=page_index + 1,
                    width=round(width, 2),
                    height=round(height, 2),
                    blocks=blocks,
                )
            )
        return pages

    @staticmethod
    def _extract_native_tables(
        page: fitz.Page,
        *,
        textpage: fitz.TextPage | None,
        raw_blocks: list[RawTextBlock],
        warnings: list[str],
    ) -> list[Any]:
        if textpage is not None or not PyMuPDFParser._should_detect_tables(raw_blocks):
            return []
        tables: list[TableSnapshot] = []
        try:
            finder = page.find_tables()
            tables.extend(snapshot_table(table) for table in finder.tables)
        except Exception as exc:
            warnings.append(f"第 {page.number + 1} 页表格检测跳过：{type(exc).__name__}")
        try:
            borderless = extract_borderless_tables(page, raw_blocks)
        except Exception as exc:
            warnings.append(
                f"第 {page.number + 1} 页无框表格检测跳过：{type(exc).__name__}"
            )
            borderless = []
        for candidate in borderless:
            if not any(
                PyMuPDFParser._table_overlap(candidate, table) >= 0.72 for table in tables
            ):
                tables.append(candidate)
        return tables

    @staticmethod
    def _should_detect_tables(raw_blocks: list[RawTextBlock]) -> bool:
        """Gate expensive native table analysis on an academic table-number hint."""

        return any(TABLE_PAGE_HINT.search(block.text) for block in raw_blocks)

    @staticmethod
    def _table_overlap(first: TableSnapshot, second: TableSnapshot) -> float:
        intersection_width = max(
            0.0, min(first.bbox[2], second.bbox[2]) - max(first.bbox[0], second.bbox[0])
        )
        intersection_height = max(
            0.0, min(first.bbox[3], second.bbox[3]) - max(first.bbox[1], second.bbox[1])
        )
        intersection = intersection_width * intersection_height
        if not intersection:
            return 0.0
        first_area = max(0.0, first.bbox[2] - first.bbox[0]) * max(
            0.0, first.bbox[3] - first.bbox[1]
        )
        second_area = max(0.0, second.bbox[2] - second.bbox[0]) * max(
            0.0, second.bbox[3] - second.bbox[1]
        )
        return intersection / max(1.0, min(first_area, second_area))

    def _extract_raster_layout(
        self,
        page: fitz.Page,
        *,
        textpage: fitz.TextPage | None,
        raw_blocks: list[RawTextBlock],
        warnings: list[str],
    ) -> tuple[list[TableSnapshot], list[dict[str, Any]]]:
        del page, textpage, raw_blocks, warnings
        return [], []

    @staticmethod
    def _extract_image_info(page: fitz.Page, warnings: list[str]) -> list[dict[str, Any]]:
        try:
            return [
                {
                    "bbox": [round(float(value), 2) for value in info.get("bbox", ())],
                    "xref": info.get("xref"),
                }
                for info in page.get_image_info(xrefs=True)
            ]
        except Exception as exc:
            warnings.append(f"第 {page.number + 1} 页图像检测跳过：{type(exc).__name__}")
            return []

    @staticmethod
    def _clean_metadata_title(title: str | None, path: str) -> str | None:
        if not title:
            return None
        cleaned = " ".join(title.split())
        if cleaned.casefold() in {"untitled", Path(path).stem.casefold()}:
            return None
        return cleaned

    @staticmethod
    def _guess_title_from_blocks(blocks: list[RawTextBlock]) -> str | None:
        candidates = []
        for block in blocks:
            width = max(0.0, block.bbox[2] - block.bbox[0])
            height = max(0.0, block.bbox[3] - block.bbox[1])
            if not 8 <= len(block.text) <= 240:
                continue
            if block.text.casefold().startswith("arxiv:"):
                continue
            if is_probable_section_heading(block.text):
                continue
            # arXiv adds a large, rotated identifier along the page margin. Its
            # font is often larger than the real title, so reject vertical bands.
            if height > 72 or height > width * 1.5:
                continue
            candidates.append(block)
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.font_size, len(item.text))).text

    @staticmethod
    def _classify_block(text: str) -> tuple[str, int | None]:
        """Backward-compatible helper retained for parser extension tests."""

        raw = RawTextBlock(1, [0, 0, 0, 0], text, 10.0, 1, len(text))
        return classify_block(raw, body_size=10.0, title=None)

    @staticmethod
    def _guess_title(
        first_page: fitz.Page, textpage: fitz.TextPage | None = None
    ) -> str | None:
        page_dict = first_page.get_text("dict", textpage=textpage)
        return PyMuPDFParser._guess_title_from_blocks(extract_text_blocks(page_dict, 1))
