import unittest
from collections import Counter
from unittest.mock import patch

import keywords


class KeywordInventoryTests(unittest.TestCase):
    def test_inventory_contains_285_unique_exact_queries(self):
        inventory = keywords.all_keywords()

        self.assertEqual(285, len(inventory))
        self.assertEqual(285, len({query for query, _ in inventory}))

    def test_inventory_contains_17_spaced_queries(self):
        spaced_queries = [
            query for query, _ in keywords.all_keywords() if " " in query
        ]

        self.assertEqual(17, len(spaced_queries))

    def test_inventory_has_expected_group_counts(self):
        group_counts = Counter(group for _, group in keywords.all_keywords())

        self.assertEqual(
            {
                "brand": 28,
                "brand_ext": 39,
                "generic": 170,
                "competitor": 41,
                "campaign": 7,
            },
            dict(group_counts),
        )

    def test_campaign_contains_spaced_and_compact_variants(self):
        campaign_queries = {
            query
            for query, group in keywords.all_keywords()
            if group == "campaign"
        }

        self.assertTrue(
            {
                "잠깐살집",
                "잠깐 살 집",
                "모두를위한단기임대",
                "모두를 위한 단기임대",
            }.issubset(campaign_queries)
        )

    def test_spaced_keyword_without_compact_counterpart_is_rejected(self):
        incomplete_campaign = [*keywords.CAMPAIGN_KEYWORDS, "새 키워드"]

        with patch.object(keywords, "CAMPAIGN_KEYWORDS", incomplete_campaign):
            with self.assertRaisesRegex(ValueError, "새 키워드"):
                keywords.all_keywords()


if __name__ == "__main__":
    unittest.main()
