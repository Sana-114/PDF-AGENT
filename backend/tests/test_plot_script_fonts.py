import ast
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.services.architecture_diagram import generate_architecture_diagram
from app.services.plot_script_fonts import MATPLOTLIB_FONT_SETUP


@pytest.mark.parametrize(
    "available,expected",
    [
        (["Microsoft YaHei", "DejaVu Sans"], ["Microsoft YaHei", "DejaVu Sans"]),
        (["Noto Sans CJK SC"], ["Noto Sans CJK SC"]),
        (["PingFang SC"], ["PingFang SC"]),
        (["Unrelated font"], ["DejaVu Sans"]),
    ],
)
def test_exported_font_setup_uses_installed_fonts_in_preference_order(
    monkeypatch, available, expected
):
    module = ModuleType("matplotlib")
    module.font_manager = SimpleNamespace(
        fontManager=SimpleNamespace(ttflist=[SimpleNamespace(name=name) for name in available])
    )
    monkeypatch.setitem(sys.modules, "matplotlib", module)
    plt = SimpleNamespace(rcParams={})
    exec(compile(MATPLOTLIB_FONT_SETUP, "<font-setup>", "exec"), {"plt": plt})
    assert plt.rcParams["font.sans-serif"] == expected
    assert plt.rcParams["axes.unicode_minus"] is False


def test_diagram_export_contains_shared_setup_and_remains_standalone_python():
    result = generate_architecture_diagram(
        "中文标题", "输入 -> 输出", "left-to-right", ["matplotlib"]
    )
    content = result.scripts[0].content
    ast.parse(content)
    assert MATPLOTLIB_FONT_SETUP in content
    assert "from app." not in content


@pytest.mark.parametrize("point", [(220, 0), (-220, 0), (0, 130), (0, -130), (220, 110)])
def test_exported_arrow_anchor_is_on_box_boundary_in_data_coordinates(point):
    result = generate_architecture_diagram(
        "Anchors", "Input -> Output", "left-to-right", ["matplotlib"]
    )
    tree = ast.parse(result.scripts[0].content)
    helper = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_edge_anchor"
    )
    namespace = {"node_half_width": 76, "node_half_height": 27}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), "<edge-anchor>", "exec"), namespace)
    x, y = namespace["_edge_anchor"]({"x": 0, "y": 0}, {"x": point[0], "y": point[1]})
    assert abs(x) <= 76 and abs(y) <= 27
    assert abs(x) == 76 or abs(y) == 27
    assert x * point[0] >= 0 and y * point[1] >= 0


def test_reciprocal_export_edges_are_separated_instead_of_overpainted():
    result = generate_architecture_diagram(
        "Cycle", "A -> B\nB -> A", "left-to-right", ["matplotlib"]
    )
    content = result.scripts[0].content
    assert "reciprocal = (target, source) in edges" in content
    assert '"arc3,rad=0.16" if reciprocal or backwards' in content
    assert "shrinkA=2, shrinkB=2" in content
