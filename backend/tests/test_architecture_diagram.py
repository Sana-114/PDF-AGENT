from fastapi.testclient import TestClient

from app.main import app
from app.services.architecture_diagram import generate_architecture_diagram, parse_topology


def test_topology_parser_deduplicates_nodes_and_edges() -> None:
    labels, edges, warnings = parse_topology(
        "PDF 上传 -> 版式解析 -> Document AST\n"
        "用户问题 -> 混合检索 -> 证据门控 -> LLM\n"
        "Document AST -> 混合检索\n"
        "PDF 上传 -> 版式解析"
    )

    assert labels == [
        "PDF 上传",
        "版式解析",
        "Document AST",
        "用户问题",
        "混合检索",
        "证据门控",
        "LLM",
    ]
    assert edges.count((0, 1)) == 1
    assert (2, 4) in edges
    assert warnings == []


def test_generator_emits_four_formats_from_one_canonical_graph() -> None:
    result = generate_architecture_diagram(
        "证据优先科研助手",
        "PDF -> Parser -> AST\nQuestion -> Retriever -> Guard -> LLM\nAST -> Retriever",
        "left-to-right",
        ["mermaid", "graphviz", "tikz", "matplotlib"],
    )

    assert len(result.nodes) == 7
    assert len(result.edges) == 6
    assert [script.format for script in result.scripts] == [
        "mermaid",
        "graphviz",
        "tikz",
        "matplotlib",
    ]
    assert "flowchart LR" in result.scripts[0].content
    assert "rankdir=LR" in result.scripts[1].content
    assert "\\begin{tikzpicture}" in result.scripts[2].content
    assert "FancyBboxPatch" in result.scripts[3].content


def test_scripts_escape_untrusted_labels_without_changing_graph_meaning() -> None:
    result = generate_architecture_diagram(
        "R&D_架构",
        'PDF_输入 -> "Parser" & Guard -> Output#1',
        "top-down",
        ["mermaid", "graphviz", "tikz"],
    )
    scripts = {script.format: script.content for script in result.scripts}

    assert "&quot;Parser&quot; &amp; Guard" in scripts["mermaid"]
    assert '\\"Parser\\" & Guard' in scripts["graphviz"]
    assert r"PDF\_输入" in scripts["tikz"]
    assert r"\& Guard" in scripts["tikz"]
    assert r"Output\#1" in scripts["tikz"]


def test_cycle_is_preserved_and_reported() -> None:
    result = generate_architecture_diagram(
        "循环",
        "A -> B -> C\nC -> A",
        "left-to-right",
        ["mermaid"],
    )

    assert len(result.edges) == 3
    assert any("循环连接" in warning for warning in result.warnings)
    assert "n3 --> n1" in result.scripts[0].content


def test_diagram_api_returns_preview_coordinates_and_scripts() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/diagrams/generate",
            json={
                "title": "RAG Pipeline",
                "idea": "PDF -> Parser -> Index\nQuestion -> Index -> Answer",
                "layout": "top-down",
                "formats": ["mermaid", "matplotlib"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["layout"] == "top-down"
    assert payload["canvas_width"] >= 760
    assert payload["nodes"][0]["node_id"] == "n1"
    assert [script["format"] for script in payload["scripts"]] == [
        "mermaid",
        "matplotlib",
    ]


def test_diagram_api_rejects_plain_text_without_relationship() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/diagrams/generate",
            json={"title": "Incomplete", "idea": "Only one component"},
        )

    assert response.status_code == 422
    assert "组件 A -> 组件 B" in response.json()["detail"]
