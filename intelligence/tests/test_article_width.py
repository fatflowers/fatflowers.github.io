from pathlib import Path


def test_article_content_uses_eighty_percent_without_widening_source_cards():
    root = Path(__file__).resolve().parents[2]
    css = (root / 'assets/css/extended/post-layout.css').read_text()
    assert '.main:has(.post-single:not(.sources-page))' in css
    # PaperMod main has border-box sizing and horizontal --gap padding.
    assert 'width: calc(80vw + var(--gap) * 2)' in css
    assert 'max-width: 100%' in css
    assert '@media (max-width: 768px)' in css and 'width: 100%' in css
