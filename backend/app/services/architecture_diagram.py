import json
import re
from collections import defaultdict, deque

from app.schemas.diagram import (
    DiagramEdgeRead,
    DiagramFormat,
    DiagramGenerationRead,
    DiagramLayout,
    DiagramNodeRead,
    DiagramScriptRead,
)

MAX_NODES = 30
MAX_EDGES = 60
MAX_LABEL_LENGTH = 120
LINE_SPLIT_RE = re.compile(r"[\r\n;；]+")
ARROW_RE = re.compile(r"\s*(?:-->|->|=>|→|⟶|➡)\s*")
BULLET_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s*")


class ArchitectureDiagramError(ValueError):
    pass


def _clean_label(value: str) -> str:
    label = BULLET_RE.sub("", value).strip()
    if len(label) >= 2 and label[0] in "[（(" and label[-1] in "]）)":
        label = label[1:-1].strip()
    label = " ".join(label.split())
    if len(label) > MAX_LABEL_LENGTH:
        raise ArchitectureDiagramError(
            f"节点“{label[:24]}…”超过 {MAX_LABEL_LENGTH} 个字符，请缩短名称。"
        )
    return label


def parse_topology(idea: str) -> tuple[list[str], list[tuple[int, int]], list[str]]:
    labels: list[str] = []
    label_to_index: dict[str, int] = {}
    edges: list[tuple[int, int]] = []
    edge_set: set[tuple[int, int]] = set()
    warnings: list[str] = []

    def node_index(label: str) -> int:
        if label not in label_to_index:
            if len(labels) >= MAX_NODES:
                raise ArchitectureDiagramError(f"单张图最多支持 {MAX_NODES} 个节点。")
            label_to_index[label] = len(labels)
            labels.append(label)
        return label_to_index[label]

    for raw_line in LINE_SPLIT_RE.split(idea):
        line = BULLET_RE.sub("", raw_line).strip()
        if not line:
            continue
        parts = [_clean_label(part) for part in ARROW_RE.split(line)]
        parts = [part for part in parts if part]
        if not parts:
            continue
        indices = [node_index(part) for part in parts]
        if len(indices) == 1:
            warnings.append(f"“{parts[0]}”没有连接关系，已作为独立节点保留。")
        for source, target in zip(indices, indices[1:], strict=False):
            if source == target:
                warnings.append(f"忽略节点“{labels[source]}”指向自身的关系。")
                continue
            edge = (source, target)
            if edge in edge_set:
                continue
            if len(edges) >= MAX_EDGES:
                raise ArchitectureDiagramError(f"单张图最多支持 {MAX_EDGES} 条连接。")
            edge_set.add(edge)
            edges.append(edge)

    if len(labels) < 2 or not edges:
        raise ArchitectureDiagramError(
            "请至少提供一条“组件 A -> 组件 B”的连接关系；每行可以继续串联多个组件。"
        )
    return labels, edges, warnings


def _node_levels(node_count: int, edges: list[tuple[int, int]]) -> tuple[list[int], bool]:
    adjacency: dict[int, list[int]] = defaultdict(list)
    indegree = [0] * node_count
    for source, target in edges:
        adjacency[source].append(target)
        indegree[target] += 1
    queue = deque(index for index, degree in enumerate(indegree) if degree == 0)
    levels = [0] * node_count
    visited: list[int] = []
    while queue:
        source = queue.popleft()
        visited.append(source)
        for target in adjacency[source]:
            levels[target] = max(levels[target], levels[source] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    has_cycle = len(visited) != node_count
    if has_cycle:
        assigned = set(visited)
        next_level = max((levels[index] for index in visited), default=-1) + 1
        for index in range(node_count):
            if index not in assigned:
                levels[index] = next_level
                next_level += 1
    return levels, has_cycle


def _layout_nodes(
    labels: list[str],
    edges: list[tuple[int, int]],
    layout: DiagramLayout,
) -> tuple[list[DiagramNodeRead], int, int, bool]:
    levels, has_cycle = _node_levels(len(labels), edges)
    grouped: dict[int, list[int]] = defaultdict(list)
    for index, level in enumerate(levels):
        grouped[level].append(index)
    layer_count = max(grouped) + 1
    widest_layer = max(len(indices) for indices in grouped.values())
    if layout == "left-to-right":
        canvas_width = max(760, 220 + (layer_count - 1) * 220)
        canvas_height = max(360, 130 + (widest_layer - 1) * 110)
    else:
        canvas_width = max(760, 260 + (widest_layer - 1) * 220)
        canvas_height = max(400, 150 + (layer_count - 1) * 130)
    nodes: list[DiagramNodeRead | None] = [None] * len(labels)
    for level in sorted(grouped):
        indices = grouped[level]
        if layout == "left-to-right":
            x = 110 + level * 220
            span = (len(indices) - 1) * 110
            start_y = canvas_height / 2 - span / 2
            for position, index in enumerate(indices):
                nodes[index] = DiagramNodeRead(
                    node_id=f"n{index + 1}",
                    label=labels[index],
                    x=x,
                    y=start_y + position * 110,
                )
        else:
            y = 75 + level * 130
            span = (len(indices) - 1) * 220
            start_x = canvas_width / 2 - span / 2
            for position, index in enumerate(indices):
                nodes[index] = DiagramNodeRead(
                    node_id=f"n{index + 1}",
                    label=labels[index],
                    x=start_x + position * 220,
                    y=y,
                )
    return [node for node in nodes if node is not None], canvas_width, canvas_height, has_cycle


def _mermaid_escape(label: str) -> str:
    return (
        label.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _latex_escape(label: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in label)


def _mermaid_script(
    title: str,
    layout: DiagramLayout,
    nodes: list[DiagramNodeRead],
    edges: list[DiagramEdgeRead],
) -> DiagramScriptRead:
    direction = "LR" if layout == "left-to-right" else "TD"
    yaml_title = json.dumps(title, ensure_ascii=False)
    lines = [f"---\ntitle: {yaml_title}\n---", f"flowchart {direction}"]
    lines.extend(f'    {node.node_id}["{_mermaid_escape(node.label)}"]' for node in nodes)
    lines.extend(f"    {edge.source} --> {edge.target}" for edge in edges)
    lines.extend(
        [
            "    classDef component fill:#edf4ef,stroke:#25734f,color:#172a22,stroke-width:1.5px",
            f"    class {','.join(node.node_id for node in nodes)} component",
        ]
    )
    return DiagramScriptRead(
        format="mermaid",
        language="mermaid",
        filename="research-architecture.mmd",
        content="\n".join(lines) + "\n",
    )


def _graphviz_script(
    title: str,
    layout: DiagramLayout,
    nodes: list[DiagramNodeRead],
    edges: list[DiagramEdgeRead],
) -> DiagramScriptRead:
    rankdir = "LR" if layout == "left-to-right" else "TB"
    lines = [
        "digraph ResearchArchitecture {",
        f"  graph [rankdir={rankdir}, bgcolor=\"transparent\", "
        f"label={json.dumps(title, ensure_ascii=False)}, labelloc=t];",
        '  node [shape=box, style="rounded,filled", fillcolor="#edf4ef", '
        'color="#25734f", fontname="Arial"];',
        '  edge [color="#66756d", arrowsize=0.8];',
    ]
    lines.extend(
        f"  {node.node_id} [label={json.dumps(node.label, ensure_ascii=False)}];" for node in nodes
    )
    lines.extend(f"  {edge.source} -> {edge.target};" for edge in edges)
    lines.append("}")
    return DiagramScriptRead(
        format="graphviz",
        language="dot",
        filename="research-architecture.dot",
        content="\n".join(lines) + "\n",
    )


def _tikz_script(
    title: str,
    nodes: list[DiagramNodeRead],
    edges: list[DiagramEdgeRead],
) -> DiagramScriptRead:
    lines = [
        r"\documentclass[tikz,border=8pt]{standalone}",
        r"\usepackage[UTF8]{ctex}",
        r"\usetikzlibrary{arrows.meta}",
        r"\begin{document}",
        rf"% {_latex_escape(title)} — compile with XeLaTeX for Chinese labels",
        r"\begin{tikzpicture}[component/.style={draw=#1, fill=#1!8, "
        r"rounded corners=2pt, minimum width=3.0cm, minimum height=0.9cm, "
        r"align=center}, flow/.style={-{Latex[length=2mm]}, draw=gray!70, thick}]",
    ]
    for node in nodes:
        x = node.x / 110
        y = -node.y / 85
        lines.append(
            rf"  \node[component=green!50!black] ({node.node_id}) "
            rf"at ({x:.2f},{y:.2f}) {{{_latex_escape(node.label)}}};"
        )
    lines.extend(rf"  \draw[flow] ({edge.source}) -- ({edge.target});" for edge in edges)
    lines.extend([r"\end{tikzpicture}", r"\end{document}"])
    return DiagramScriptRead(
        format="tikz",
        language="latex",
        filename="research-architecture.tex",
        content="\n".join(lines) + "\n",
    )


def _matplotlib_script(
    title: str,
    nodes: list[DiagramNodeRead],
    edges: list[DiagramEdgeRead],
    canvas_width: int,
    canvas_height: int,
) -> DiagramScriptRead:
    node_data = {
        node.node_id: {"label": node.label, "x": node.x, "y": node.y} for node in nodes
    }
    edge_data = [(edge.source, edge.target) for edge in edges]
    content = f'''import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

title = {title!r}
nodes = {node_data!r}
edges = {edge_data!r}
canvas_width = {canvas_width}
canvas_height = {canvas_height}

fig, ax = plt.subplots(figsize=(12, 7), constrained_layout=True)
for source, target in edges:
    start, end = nodes[source], nodes[target]
    ax.annotate(
        "", xy=(end["x"], end["y"]), xytext=(start["x"], start["y"]),
        arrowprops=dict(arrowstyle="-|>", color="#66756d", lw=1.4, shrinkA=48, shrinkB=48),
        zorder=1,
    )
for node in nodes.values():
    box = FancyBboxPatch(
        (node["x"] - 76, node["y"] - 27), 152, 54,
        boxstyle="round,pad=0.03,rounding_size=8",
        facecolor="#edf4ef", edgecolor="#25734f", linewidth=1.5, zorder=2,
    )
    ax.add_patch(box)
    ax.text(node["x"], node["y"], node["label"], ha="center", va="center", fontsize=9, zorder=3)
ax.set_xlim(0, canvas_width)
ax.set_ylim(canvas_height, 0)
ax.set_title(title, fontsize=14, loc="left")
ax.set_axis_off()
plt.savefig("research-architecture.png", dpi=300, bbox_inches="tight", transparent=True)
plt.show()
'''
    return DiagramScriptRead(
        format="matplotlib",
        language="python",
        filename="research_architecture.py",
        content=content,
    )


def generate_architecture_diagram(
    title: str,
    idea: str,
    layout: DiagramLayout,
    formats: list[DiagramFormat],
) -> DiagramGenerationRead:
    labels, indexed_edges, warnings = parse_topology(idea)
    nodes, canvas_width, canvas_height, has_cycle = _layout_nodes(
        labels, indexed_edges, layout
    )
    edges = [
        DiagramEdgeRead(source=f"n{source + 1}", target=f"n{target + 1}")
        for source, target in indexed_edges
    ]
    if has_cycle:
        warnings.append("检测到循环连接；预览已展开循环节点，脚本仍保留原始方向。")
    generators = {
        "mermaid": lambda: _mermaid_script(title, layout, nodes, edges),
        "graphviz": lambda: _graphviz_script(title, layout, nodes, edges),
        "tikz": lambda: _tikz_script(title, nodes, edges),
        "matplotlib": lambda: _matplotlib_script(
            title, nodes, edges, canvas_width, canvas_height
        ),
    }
    scripts = [generators[format_name]() for format_name in formats]
    return DiagramGenerationRead(
        title=title,
        layout=layout,
        nodes=nodes,
        edges=edges,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        scripts=scripts,
        warnings=warnings,
    )
