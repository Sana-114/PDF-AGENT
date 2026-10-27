from copy import deepcopy

import pytest

from app.services.publication_metadata import publication_footer


def parsed():
    return {"pages": [{"page_number": 1, "height": 842, "blocks": [{
        "block_id": "p1-footer", "bbox": [50, 795, 550, 820],
        "text": "Proceedings of Example Conference 2020, pages 12–24. June 2020. ©2020 Publisher",
    }]}]}


def test_publication_footer_keeps_literal_positioned_evidence():
    source = parsed()
    before = deepcopy(source)
    result = publication_footer(source)
    assert result['year'] == 2020 and result['venue'] == 'Example Conference'
    assert result['quote'] == source['pages'][0]['blocks'][0]['text']
    assert result['block_id'] == 'p1-footer' and result['page_number'] == 1
    assert source == before


@pytest.mark.parametrize('change', ['body', 'reference', 'conflict', 'single_year', 'second_page', 'duplicate'])
def test_publication_footer_does_not_guess_dates(change):
    source = parsed()
    page, = source['pages']
    block, = page['blocks']
    if change == 'body':
        block['bbox'] = [50, 400, 550, 450]
    elif change == 'reference':
        block['text'] = 'Other Author. A cited title. ' + block['text']
    elif change == 'conflict':
        block['text'] += ' Copyright 2021'
    elif change == 'single_year':
        block['text'] = 'Proceedings of Example Conference 2020, pages 12–24'
    elif change == 'second_page':
        page['page_number'] = 2
    else:
        page['blocks'].append(deepcopy(block))
    assert publication_footer(source) is None
