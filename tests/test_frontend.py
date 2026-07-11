from pathlib import Path

import quickjs


def test_app_javascript_parses():
    source = Path("static/app.js").read_text(encoding="utf-8")
    # Compiles the complete browser script without executing DOM-dependent code.
    quickjs.Context().eval(f"new Function({source!r})")


def test_theme_picker_exposes_all_themes_and_removes_quick_toggle():
    html = Path("static/index.html").read_text(encoding="utf-8")
    javascript = Path("static/app.js").read_text(encoding="utf-8")
    css = Path("static/app.css").read_text(encoding="utf-8")

    themes = ("dark", "light", "fresh", "ocean", "midnight")
    assert 'id="theme-btn"' not in html
    assert 'data-settings-panel="theme"' in html
    for theme in themes:
        assert f'data-theme-choice="{theme}"' in html
        assert f"{theme}:{{background:" in javascript
    for theme in themes[1:]:
        assert f':root[data-theme="{theme}"]' in css
