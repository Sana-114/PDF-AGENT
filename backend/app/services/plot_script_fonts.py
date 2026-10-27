"""Shared setup emitted into standalone scripts; no server plotting dependency."""

MATPLOTLIB_FONT_SETUP = """from matplotlib import font_manager as _font_manager

# For Chinese labels install a CJK font, e.g. Noto Sans CJK SC, if unavailable.
_preferred_fonts = ['Microsoft YaHei', 'Noto Sans CJK SC', 'Source Han Sans SC',
                    'SimHei', 'PingFang SC', 'Heiti SC', 'DejaVu Sans']
_available_fonts = {font.name for font in _font_manager.fontManager.ttflist}
plt.rcParams['font.sans-serif'] = [name for name in _preferred_fonts
                                 if name in _available_fonts] or ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False
"""
