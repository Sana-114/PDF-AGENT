"""Date provenance regressions runnable with Python's standard library."""
import unittest
from copy import deepcopy

from app.services.publication_metadata import resolve_publication_year


def source():
    return {"pages": [{"page_number": 1, "height": 842, "blocks": [{
        "block_id": "footer", "bbox": [50, 795, 550, 820],
        "text": "Proceedings of Example Conference 2019, pages 12–24. June 2019. ©2019 Publisher",
    }]}]}


class PublicationYearResolutionTests(unittest.TestCase):
    def test_unknown_year_retains_literal_footer(self):
        parsed = source()
        original = deepcopy(parsed)
        year, proof = resolve_publication_year(None, {}, parsed)
        self.assertEqual(year, 2019)
        self.assertEqual(proof["quote"], parsed["pages"][0]["blocks"][0]["text"])
        self.assertEqual(proof["page_number"], 1)
        self.assertEqual(parsed, original)

    def test_metadata_precedes_preprint_and_footer(self):
        self.assertEqual(resolve_publication_year("1801.12345", {"year": 2020}, source()), (2020, None))

    def test_preprint_date_is_not_replaced_by_later_conference(self):
        self.assertEqual(resolve_publication_year("1801.12345v2", {}, source()), (2018, None))

    def test_source_metadata_arxiv_id(self):
        self.assertEqual(resolve_publication_year(None, {"arxiv_id": "1801.12345"}), (2018, None))

    def test_iso_publication_date(self):
        self.assertEqual(resolve_publication_year(None, {"published_at": "2020-05-12T00:00:00Z"}), (2020, None))

    def test_bad_metadata_shapes_are_not_dates(self):
        for metadata in (None, [], "2020", 2020, {"year": True}, {"year": [2020]}, {"year": "120201"}):
            with self.subTest(metadata=metadata):
                self.assertEqual(resolve_publication_year(None, metadata), (None, None))

    def test_malformed_arxiv_does_not_establish_chronology(self):
        for identity in ("1800.12345", "1813.12345", "1801.12345junk", "1801.12345v0", True):
            with self.subTest(identity=identity):
                self.assertEqual(resolve_publication_year(identity, {}), (None, None))

    def test_bibliography_year_is_not_publication_year(self):
        parsed = source()
        parsed["pages"][0]["blocks"][0]["text"] = "Other Author. " + parsed["pages"][0]["blocks"][0]["text"]
        self.assertEqual(resolve_publication_year(None, {}, parsed), (None, None))

    def test_body_and_later_page_cannot_be_imprints(self):
        for change in ("body", "later_page"):
            parsed = source()
            if change == "body":
                parsed["pages"][0]["blocks"][0]["bbox"] = [50, 100, 550, 200]
            else:
                parsed["pages"][0]["page_number"] = 2
            self.assertEqual(resolve_publication_year(None, {}, parsed), (None, None))

    def test_conflicting_footer_years_remain_unknown(self):
        parsed = source()
        parsed["pages"][0]["blocks"][0]["text"] += " Copyright 2020"
        self.assertEqual(resolve_publication_year(None, {}, parsed), (None, None))

    def test_two_footers_are_ambiguous(self):
        parsed = source()
        parsed["pages"][0]["blocks"] *= 2
        self.assertEqual(resolve_publication_year(None, {}, parsed), (None, None))

    def test_missing_document_does_not_invent_date(self):
        self.assertEqual(resolve_publication_year(None, {}, None), (None, None))


if __name__ == "__main__":
    unittest.main()
