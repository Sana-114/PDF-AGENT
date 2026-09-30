"""Build the competition technical-report draft as a polished, reviewable PDF."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    CondPageBreak,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs" / "competition-technical-report-draft.md"
DEFAULT_OUTPUT = ROOT / "output" / "pdf" / "PaperPilot-technical-report-draft.pdf"

INK = colors.HexColor("#17232B")
BLUE = colors.HexColor("#36566F")
BLUE_DARK = colors.HexColor("#293F50")
MUTED = colors.HexColor("#66727A")
LINE = colors.HexColor("#D8D6CF")
PAPER = colors.HexColor("#F7F5EF")
PALE_BLUE = colors.HexColor("#E8EEF2")
PALE_ORANGE = colors.HexColor("#F5EDE4")


def register_fonts() -> tuple[str, str, str]:
    regular_candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    bold_candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    regular = next((path for path in regular_candidates if path.exists()), None)
    bold = next((path for path in bold_candidates if path.exists()), None)
    if regular:
        pdfmetrics.registerFont(TTFont("PaperPilotCJK", str(regular)))
        regular_name = "PaperPilotCJK"
    else:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        regular_name = "STSong-Light"
    if bold:
        pdfmetrics.registerFont(TTFont("PaperPilotCJKBold", str(bold)))
        bold_name = "PaperPilotCJKBold"
    else:
        bold_name = regular_name
    mono_candidates = [
        Path("C:/Windows/Fonts/consola.ttf"),
        Path("C:/Windows/Fonts/cour.ttf"),
    ]
    mono = next((path for path in mono_candidates if path.exists()), None)
    if mono:
        pdfmetrics.registerFont(TTFont("PaperPilotMono", str(mono)))
        mono_name = "PaperPilotMono"
    else:
        mono_name = regular_name
    return regular_name, bold_name, mono_name


def make_styles(regular: str, bold: str, mono: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "BodyCJK",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.8,
            leading=14.2,
            textColor=INK,
            alignment=TA_JUSTIFY,
            wordWrap="CJK",
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "SmallCJK",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7.5,
            leading=11.2,
            textColor=MUTED,
            wordWrap="CJK",
        ),
        "h1": ParagraphStyle(
            "H1CJK",
            parent=base["Heading1"],
            fontName=bold,
            fontSize=20,
            leading=26,
            textColor=BLUE_DARK,
            spaceAfter=12,
            wordWrap="CJK",
        ),
        "h2": ParagraphStyle(
            "H2CJK",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=15,
            leading=21,
            textColor=BLUE_DARK,
            spaceAfter=9,
            wordWrap="CJK",
        ),
        "h3": ParagraphStyle(
            "H3CJK",
            parent=base["Heading3"],
            fontName=bold,
            fontSize=11.5,
            leading=17,
            textColor=BLUE,
            spaceBefore=6,
            spaceAfter=6,
            wordWrap="CJK",
        ),
        "quote": ParagraphStyle(
            "QuoteCJK",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.4,
            leading=13.5,
            leftIndent=9,
            rightIndent=5,
            borderColor=BLUE,
            borderWidth=0,
            borderPadding=(6, 8, 6, 10),
            backColor=PALE_BLUE,
            textColor=BLUE_DARK,
            wordWrap="CJK",
            spaceAfter=7,
        ),
        "code": ParagraphStyle(
            "CodeCJK",
            parent=base["Code"],
            fontName=mono,
            fontSize=6.9,
            leading=10,
            leftIndent=5,
            rightIndent=5,
            borderPadding=7,
            backColor=colors.HexColor("#EEF0EC"),
            textColor=colors.HexColor("#26343C"),
            wordWrap="CJK",
            spaceBefore=3,
            spaceAfter=7,
        ),
        "cover_title": ParagraphStyle(
            "CoverTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=29,
            leading=39,
            textColor=BLUE_DARK,
            alignment=TA_LEFT,
            wordWrap="CJK",
        ),
        "cover_subtitle": ParagraphStyle(
            "CoverSubtitle",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=12,
            leading=19,
            textColor=MUTED,
            wordWrap="CJK",
        ),
        "toc": ParagraphStyle(
            "TOC",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=9.5,
            leading=17,
            textColor=INK,
            wordWrap="CJK",
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=7.4,
            leading=11,
            textColor=MUTED,
            alignment=TA_CENTER,
            wordWrap="CJK",
        ),
    }


def inline_markup(text: str, mono: str) -> str:
    clean = html.escape(text.strip())
    clean = re.sub(r"`([^`]+)`", rf'<font name="{mono}" color="#293F50">\1</font>', clean)
    clean = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", clean)
    clean = re.sub(r"\[([^]]+)\]\([^)]+\)", r'<font color="#36566F">\1</font>', clean)
    clean = clean.replace("  ", " ")
    return clean


def box_label(drawing: Drawing, x: float, y: float, width: float, height: float, text: str,
              regular: str, fill=colors.white, stroke=LINE, size=8.2) -> None:
    drawing.add(Rect(x, y, width, height, rx=3, ry=3, fillColor=fill, strokeColor=stroke, strokeWidth=0.8))
    lines = [text] if len(text) <= 13 else [text[:13], text[13:26]]
    for index, line in enumerate(lines):
        drawing.add(String(
            x + width / 2,
            y + height / 2 + (4 if len(lines) == 1 else 7) - index * 11,
            line,
            fontName=regular,
            fontSize=size,
            fillColor=INK,
            textAnchor="middle",
        ))


def arrow(drawing: Drawing, x1: float, y1: float, x2: float, y2: float) -> None:
    drawing.add(Line(x1, y1, x2, y2, strokeColor=BLUE, strokeWidth=1.2))
    angle = 5
    drawing.add(Polygon(
        [x2, y2, x2 - angle, y2 + 2.7, x2 - angle, y2 - 2.7],
        fillColor=BLUE,
        strokeColor=BLUE,
    ))


def architecture_diagram(regular: str, bold: str) -> Drawing:
    drawing = Drawing(500, 215)
    drawing.add(Rect(0, 0, 500, 215, fillColor=PAPER, strokeColor=LINE, strokeWidth=0.8))
    drawing.add(String(18, 191, "PaperPilot 生产架构", fontName=bold, fontSize=12, fillColor=BLUE_DARK))
    box_label(drawing, 18, 145, 72, 30, "评审用户", regular, PALE_BLUE)
    box_label(drawing, 112, 145, 72, 30, "Caddy", regular)
    box_label(drawing, 206, 145, 82, 30, "Next.js Web", regular)
    box_label(drawing, 310, 145, 78, 30, "FastAPI", regular, PALE_BLUE)
    box_label(drawing, 410, 145, 72, 30, "Agent Harness", regular, PALE_ORANGE, size=7.2)
    arrow(drawing, 90, 160, 112, 160)
    arrow(drawing, 184, 160, 206, 160)
    arrow(drawing, 288, 160, 310, 160)
    arrow(drawing, 388, 160, 410, 160)
    lower = [
        (26, "PostgreSQL"),
        (120, "PDF / AST"),
        (214, "Qdrant"),
        (308, "Celery+Redis"),
        (402, "BGE+DeepSeek"),
    ]
    for x, label in lower:
        box_label(drawing, x, 61, 72, 30, label, regular, colors.white, size=7.2)
    for x in (62, 156, 250, 344, 438):
        drawing.add(Line(349, 145, x, 91, strokeColor=colors.HexColor("#8798A3"), strokeWidth=0.7))
    drawing.add(String(
        18, 22,
        "公网仅开放 HTTPS；数据库、向量库、任务队列和模型服务保留在 Docker 内部网络。",
        fontName=regular, fontSize=7.5, fillColor=MUTED,
    ))
    return drawing


def dataflow_diagram(regular: str, bold: str) -> Drawing:
    drawing = Drawing(500, 145)
    drawing.add(Rect(0, 0, 500, 145, fillColor=PAPER, strokeColor=LINE, strokeWidth=0.8))
    drawing.add(String(18, 121, "从 PDF 到可定位回答", fontName=bold, fontSize=12, fillColor=BLUE_DARK))
    labels = ["上传 PDF", "Document AST", "混合检索", "BGE 重排", "DeepSeek", "引用校验", "原页高亮"]
    x_positions = [14, 84, 158, 230, 302, 374, 446]
    widths = [58, 62, 58, 58, 58, 58, 44]
    for index, (x, width, label) in enumerate(zip(x_positions, widths, labels)):
        fill = PALE_ORANGE if label == "DeepSeek" else PALE_BLUE if index in (1, 5) else colors.white
        box_label(drawing, x, 62, width, 30, label, regular, fill, size=6.9)
        if index < len(labels) - 1:
            arrow(drawing, x + width, 77, x_positions[index + 1], 77)
    drawing.add(String(
        18, 22,
        "证据不足时拒答；任何 Claim 中的证据编号都必须来自本轮允许列表。",
        fontName=regular, fontSize=7.8, fillColor=MUTED,
    ))
    return drawing


def screenshot_plan(title: str, styles: dict[str, ParagraphStyle]):
    table = Table(
        [[
            Paragraph("正式截图规划", styles["small"]),
            Paragraph(inline_markup(title, "PaperPilotMono"), styles["body"]),
        ]],
        colWidths=[32 * mm, 126 * mm],
        rowHeights=[18 * mm],
    )
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), PALE_BLUE),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#FBFAF7")),
        ("BOX", (0, 0), (-1, -1), 0.8, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return KeepTogether([table, Spacer(1, 4), Paragraph("此处将在最终公网版本录制后替换为真实界面截图。", styles["caption"])])


def table_flow(rows: list[list[str]], styles: dict[str, ParagraphStyle], mono: str) -> Table:
    max_columns = max(len(row) for row in rows)
    normalized = [row + [""] * (max_columns - len(row)) for row in rows]
    data = [
        [Paragraph(inline_markup(cell, mono), styles["small"]) for cell in row]
        for row in normalized
    ]
    total_width = 166 * mm
    raw_weights = []
    for column in range(max_columns):
        longest = max(len(row[column]) for row in normalized)
        raw_weights.append(max(12, min(longest, 36)))
    weight_sum = sum(raw_weights)
    widths = [total_width * weight / weight_sum for weight in raw_weights]
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLUE_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), styles["h3"].fontName),
        ("GRID", (0, 0), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return table


SCREENSHOT_AFTER = {
    "3.4 语义去重与版本演进": "图 3：v7→v1 版本预警、保留建议与修订差异（隐藏本地路径）。",
    "4.2 引用白名单": "图 5：多文回答的 Claims、证据编号、页码与 PDF 原页高亮。",
    "5. 版式感知阅读与翻译": "图 6：目录、引用双向跳转、双语对照与联动滚动。",
    "6.2 局域引用图谱": "图 8：基石、桥接、衍生节点及 Future Work 双侧证据。",
    "7.3 CSV 可视化": "图 10：折线/柱状/三维雷达图、误差棒和数据约束 Caption。",
}


def parse_markdown(text: str, styles: dict[str, ParagraphStyle], regular: str, bold: str, mono: str):
    lines = text.splitlines()
    story = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code = False
    code_language = ""
    mermaid_index = 0
    started = False
    section_count = 0

    def flush_paragraph() -> None:
        if paragraph:
            combined = " ".join(item.strip() for item in paragraph if item.strip())
            if combined:
                story.append(Paragraph(inline_markup(combined, mono), styles["body"]))
            paragraph.clear()

    def flush_list() -> None:
        if not list_items:
            return
        items = [ListItem(Paragraph(inline_markup(item, mono), styles["body"]), leftIndent=8) for item in list_items]
        story.append(ListFlowable(items, bulletType="bullet", start="circle", leftIndent=18, bulletFontName=regular, bulletFontSize=6))
        story.append(Spacer(1, 3))
        list_items.clear()

    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not started:
            if stripped == "## 摘要":
                started = True
            else:
                index += 1
                continue
        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            if not in_code:
                in_code = True
                code_language = stripped[3:].strip()
                code_lines = []
            else:
                if code_language == "mermaid":
                    mermaid_index += 1
                    story.append(architecture_diagram(regular, bold) if mermaid_index == 1 else dataflow_diagram(regular, bold))
                    story.append(Paragraph(
                        f"图 {mermaid_index}：{'系统总体架构' if mermaid_index == 1 else '核心数据流'}（矢量绘制）。",
                        styles["caption"],
                    ))
                else:
                    code = "<br/>".join(html.escape(item) if item else " " for item in code_lines)
                    story.append(Paragraph(code, styles["code"]))
                in_code = False
                code_language = ""
                code_lines = []
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            flush_list()
            title = stripped[3:].strip()
            if section_count and title in {
                "4. 抗幻觉 RAG 与多文推理",
                "5. 版式感知阅读与翻译",
            }:
                story.append(CondPageBreak(105 * mm))
            elif section_count:
                story.append(PageBreak())
            section_count += 1
            story.append(Paragraph(inline_markup(title, mono), styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=0.8, color=LINE, spaceAfter=10))
            if title in SCREENSHOT_AFTER:
                story.append(screenshot_plan(SCREENSHOT_AFTER[title], styles))
                story.append(Spacer(1, 8))
            index += 1
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            flush_list()
            title = stripped[4:].strip()
            story.append(Paragraph(inline_markup(title, mono), styles["h2"]))
            if title in SCREENSHOT_AFTER:
                story.append(screenshot_plan(SCREENSHOT_AFTER[title], styles))
                story.append(Spacer(1, 8))
            index += 1
            continue
        if stripped.startswith("#### "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(stripped[5:].strip(), mono), styles["h3"]))
            index += 1
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(stripped.lstrip("> "), mono), styles["quote"]))
            index += 1
            continue
        if stripped == "---":
            flush_paragraph()
            flush_list()
            story.append(Spacer(1, 5))
            index += 1
            continue
        if stripped.startswith("|"):
            flush_paragraph()
            flush_list()
            raw_rows = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    raw_rows.append(cells)
                index += 1
            if raw_rows:
                story.append(table_flow(raw_rows, styles, mono))
                story.append(Spacer(1, 8))
            continue
        list_match = re.match(r"^(?:[-*]|\d+\.)\s+(.*)$", stripped)
        if list_match:
            flush_paragraph()
            list_items.append(list_match.group(1))
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
        else:
            paragraph.append(stripped)
        index += 1
    flush_paragraph()
    flush_list()
    return story


def cover(styles: dict[str, ParagraphStyle], regular: str, bold: str):
    meta = Table([
        ["文档状态", "比赛技术文档排版草稿"],
        ["仓库", "github.com/Sana-114/PDF-AGENT"],
        ["在线演示", "临近提交时部署并补充"],
        ["版本说明", "正式提交前记录 Git Tag 与 Commit SHA"],
    ], colWidths=[35 * mm, 115 * mm])
    meta.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), regular),
        ("FONTNAME", (0, 0), (0, -1), bold),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), BLUE_DARK),
        ("TEXTCOLOR", (1, 0), (1, -1), MUTED),
        ("LINEBELOW", (0, 0), (-1, -1), 0.45, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return [
        Spacer(1, 28 * mm),
        Paragraph("PAPERPILOT", ParagraphStyle(
            "CoverKicker", parent=styles["small"], fontName=bold, fontSize=9,
            leading=12, textColor=BLUE, spaceAfter=9, letterSpacing=1.2,
        )),
        Paragraph("原文证据驱动的<br/>科研助手 Agent 系统", styles["cover_title"]),
        Spacer(1, 8 * mm),
        Paragraph(
            "面向 PDF 细粒度解析、抗幻觉问答、多文推理、双语阅读、引用图谱与研究材料生成的可复现系统",
            styles["cover_subtitle"],
        ),
        Spacer(1, 24 * mm),
        HRFlowable(width="100%", thickness=1.2, color=BLUE_DARK, spaceAfter=7 * mm),
        meta,
        Spacer(1, 25 * mm),
        Paragraph("2026 年 9 月 · 排版初稿", styles["small"]),
        PageBreak(),
    ]


def toc(styles: dict[str, ParagraphStyle]):
    sections = [
        "摘要", "1. 项目概述", "2. 系统总体架构", "3. PDF 管理与细粒度解析",
        "4. 抗幻觉 RAG 与多文推理", "5. 版式感知阅读与翻译",
        "6. 检索推荐、引用图谱与研究综述", "7. 学术写作与数据可视化",
        "8. AI 技术选型与优化策略", "9. 测试与验证", "10. 部署、安全与可复现性",
        "11. 创新点", "12. 已知边界与后续计划", "13. 成品 PDF 编排建议",
        "14. 仓库内参考资料",
    ]
    rows = []
    for index, section in enumerate(sections):
        rows.append([Paragraph(f"{index + 1:02d}", styles["small"]), Paragraph(section, styles["toc"])])
    table = Table(rows, colWidths=[14 * mm, 145 * mm], rowHeights=[8.5 * mm] * len(rows))
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TEXTCOLOR", (0, 0), (0, -1), BLUE),
    ]))
    return [Paragraph("目录", styles["h1"]), Spacer(1, 3 * mm), table, PageBreak()]


def draw_page(canvas, doc, regular: str, bold: str) -> None:
    page = canvas.getPageNumber()
    canvas.saveState()
    canvas.setTitle("PaperPilot 比赛技术文档初稿")
    canvas.setAuthor("PaperPilot")
    if page > 1:
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(20 * mm, A4[1] - 14 * mm, A4[0] - 20 * mm, A4[1] - 14 * mm)
        canvas.setFont(regular, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, A4[1] - 10.5 * mm, "PaperPilot · 比赛技术文档排版草稿")
        canvas.drawRightString(A4[0] - 20 * mm, 11 * mm, f"{page:02d}")
        canvas.setFont(bold, 6.7)
        canvas.drawString(20 * mm, 11 * mm, "原文证据驱动的科研助手 Agent 系统")
    canvas.restoreState()


def build(source: Path, output: Path) -> None:
    regular, bold, mono = register_fonts()
    styles = make_styles(regular, bold, mono)
    output.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=20 * mm,
        bottomMargin=19 * mm,
        title="PaperPilot 比赛技术文档初稿",
        author="PaperPilot",
        subject="科研助手 Agent 系统技术方案",
    )
    markdown = source.read_text(encoding="utf-8")
    story = cover(styles, regular, bold)
    story.extend(toc(styles))
    story.extend(parse_markdown(markdown, styles, regular, bold, mono))
    doc.build(
        story,
        onFirstPage=lambda canvas, current_doc: draw_page(canvas, current_doc, regular, bold),
        onLaterPages=lambda canvas, current_doc: draw_page(canvas, current_doc, regular, bold),
    )
    reader = PdfReader(str(output))
    if not (16 <= len(reader.pages) <= 30):
        raise RuntimeError(f"Unexpected page count: {len(reader.pages)} (expected 16-30 for the draft)")
    print(f"Built {output} ({len(reader.pages)} pages, {output.stat().st_size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
