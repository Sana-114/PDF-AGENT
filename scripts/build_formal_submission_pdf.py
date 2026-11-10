"""Build the combined introduction/technical PDF with a fixed chapter layout."""
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

from build_technical_report import MUTED, make_styles, parse_markdown, register_fonts

ROOT = Path(__file__).resolve().parents[1]


def main():
    regular, bold, mono = register_fonts()
    styles = make_styles(regular, bold, mono)
    styles["body"].fontSize = 10
    styles["body"].leading = 16
    source = ROOT / "docs/formal-submission-2026-11-10.md"
    target = ROOT / "output/pdf/PaperPilot-formal-introduction-technical-2026-11-10.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    story = []
    for index, section in enumerate(source.read_text(encoding="utf-8").split("\n## ")[1:]):
        if index:
            story.append(PageBreak())
        title, body = section.split("\n", 1)
        items = parse_markdown("## 摘要\n" + body, styles, regular, bold, mono, release=True)
        items[0] = Paragraph(title, styles["h1"])
        story.extend(items)

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(22 * mm, A4[1] - 12 * mm, "PaperPilot · 作品介绍与技术文档")
        canvas.drawRightString(A4[0] - 22 * mm, 12 * mm, str(doc.page))
        canvas.restoreState()

    SimpleDocTemplate(str(target), pagesize=A4, leftMargin=22*mm, rightMargin=22*mm,
                      topMargin=24*mm, bottomMargin=22*mm).build(
                          story, onFirstPage=footer, onLaterPages=footer)
    pages = len(PdfReader(target).pages)
    if pages > 30:
        raise ValueError("Technical document exceeds 30 pages")
    print(f"PDF: {target}; pages={pages}; bytes={target.stat().st_size}")


if __name__ == "__main__":
    main()
