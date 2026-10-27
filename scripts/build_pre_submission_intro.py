"""Render the dated pre-submission introduction using the existing report styles."""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate
from pypdf import PdfReader

from build_technical_report import register_fonts, make_styles, parse_markdown, MUTED, LINE

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / "docs/pre-submission-introduction-2026-10-27.md"
    output = ROOT / "output/pdf/PaperPilot-pre-submission-introduction-2026-10-27.pdf"
    output.parent.mkdir(parents=True, exist_ok=True)
    regular, bold, mono = register_fonts()
    styles = make_styles(regular, bold, mono)
    story = []
    sections = source.read_text(encoding="utf-8").split("\n## ")[1:]
    for index, section in enumerate(sections):
        if index:
            story.append(PageBreak())
        title, body = section.split("\n", 1)
        # The shared technical-report parser starts at its summary heading.
        flows = parse_markdown("## 摘要\n" + body, styles, regular, bold, mono, release=True)
        flows[0] = Paragraph(title, styles["h1"])
        story.extend(flows)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(22 * mm, A4[1] - 12 * mm, "PaperPilot · 科研助手 · 作品介绍")
        canvas.setStrokeColor(LINE)
        canvas.line(22 * mm, 16 * mm, A4[0] - 22 * mm, 16 * mm)
        canvas.drawString(22 * mm, 11 * mm, "预提交版  |  2026-10-27")
        canvas.drawRightString(A4[0] - 22 * mm, 11 * mm, str(doc.page))
        canvas.restoreState()

    doc = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=22 * mm,
                            rightMargin=22 * mm, topMargin=23 * mm, bottomMargin=23 * mm,
                            title="PaperPilot 科研助手 - 作品介绍", author="",
                            subject="2026-10-27 预提交材料")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    pages = len(PdfReader(output).pages)
    assert 8 <= pages <= 12, pages
    assert output.stat().st_size < 100_000_000
    print(f"Created introduction: {pages} pages, {output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
