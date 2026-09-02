import unittest
from collections import Counter
from unittest.mock import patch

import keywords


class KeywordInventoryTests(unittest.TestCase):
    def test_inventory_contains_285_unique_exact_queries(self):
        inventory = keywords.all_keywords()

        self.assertEqual(285, len(inventory))
        self.assertEqual(285, len({query for query, _ in inventory}))

    def test_inventory_contains_17_whitespace_bearing_queries(self):
        whitespace_queries = [
            query
            for query, _ in keywords.all_keywords()
            if any(character.isspace() for character in query)
        ]

        self.assertEqual(17, len(whitespace_queries))

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

    def test_completed_pairs_remain_separate_queries_in_their_groups(self):
        inventory = set(keywords.all_keywords())
        completed_pairs = (
            ("air bnb", "airbnb", "competitor"),
            ("네이버 부동산", "네이버부동산", "competitor"),
            ("피터팬의 좋은방 구하기", "피터팬의좋은방구하기", "competitor"),
            ("잠깐 살 집", "잠깐살집", "campaign"),
            ("모두를 위한 단기임대", "모두를위한단기임대", "campaign"),
        )

        for spaced, compact, group in completed_pairs:
            with self.subTest(spaced=spaced, compact=compact):
                self.assertIn((spaced, group), inventory)
                self.assertIn((compact, group), inventory)

    def test_campaign_keywords_take_precedence_over_other_groups(self):
        groups_by_query = dict(keywords.all_keywords())

        for query in ("33M2", "삼삼엠투", "단기임대"):
            with self.subTest(query=query):
                self.assertEqual("campaign", groups_by_query[query])

    def test_spaced_keyword_without_compact_counterpart_is_rejected(self):
        incomplete_campaign = [*keywords.CAMPAIGN_KEYWORDS, "새 키워드"]

        with patch.object(keywords, "CAMPAIGN_KEYWORDS", incomplete_campaign):
            with self.assertRaisesRegex(ValueError, "새 키워드"):
                keywords.all_keywords()

    def test_all_isspace_separators_are_removed_to_find_compact_counterparts(self):
        for separator in ("\t", "\n", "\u00a0"):
            spaced = f"새{separator}키워드"
            compact = "새키워드"
            complete_campaign = [
                *keywords.CAMPAIGN_KEYWORDS,
                spaced,
                compact,
            ]

            with self.subTest(separator=repr(separator)):
                with patch.object(
                    keywords, "CAMPAIGN_KEYWORDS", complete_campaign
                ):
                    inventory = keywords.all_keywords()

                self.assertIn((spaced, "campaign"), inventory)
                self.assertIn((compact, "campaign"), inventory)

    def test_non_ascii_whitespace_without_compact_counterpart_is_rejected(self):
        for separator in ("\t", "\n", "\u00a0"):
            malformed = f"새{separator}키워드"
            incomplete_campaign = [*keywords.CAMPAIGN_KEYWORDS, malformed]

            with self.subTest(separator=repr(separator)):
                with patch.object(
                    keywords, "CAMPAIGN_KEYWORDS", incomplete_campaign
                ):
                    with self.assertRaises(ValueError) as raised:
                        keywords.all_keywords()

                self.assertIn(repr(malformed), str(raised.exception))

    def test_shadowed_duplicate_is_validated_before_precedence_deduplication(self):
        malformed = "중복\t키워드"
        incomplete_brand = [*keywords.BRAND_KEYWORDS, malformed]
        incomplete_campaign = [*keywords.CAMPAIGN_KEYWORDS, malformed]

        with patch.object(keywords, "BRAND_KEYWORDS", incomplete_brand):
            with patch.object(
                keywords, "CAMPAIGN_KEYWORDS", incomplete_campaign
            ):
                with self.assertRaises(ValueError) as raised:
                    keywords.all_keywords()

        self.assertIn(repr(malformed), str(raised.exception))


if __name__ == "__main__":
    unittest.main()
