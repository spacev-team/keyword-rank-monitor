# Dual Keyword Variant Registration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Register a compact base query for every keyword while retaining meaningful spaced queries as separate dashboard rows.

**Architecture:** Keep exact query variants in the source inventory because Naver can return different SERPs for spaced and compact forms. `all_keywords()` remains the single inventory API and validates that every spaced entry has a compact counterpart; existing SQLite history remains untouched.

**Tech Stack:** Python 3.12, built-in `unittest`, existing Naver HTTP collector

---

### Task 1: Define the dual-variant inventory contract

**Files:**
- Create: `tests/test_keywords.py`
- Test: `tests/test_keywords.py`

- [ ] **Step 1: Write the failing inventory tests**

```python
import unittest
from collections import Counter

import keywords


def compact(value: str) -> str:
    return "".join(value.split())


class KeywordInventoryTests(unittest.TestCase):
    def test_inventory_contains_compact_base_for_every_spaced_variant(self):
        pairs = keywords.all_keywords()
        names = [name for name, _ in pairs]
        spaced = [name for name in names if name != compact(name)]

        self.assertEqual(285, len(names))
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(17, len(spaced))
        self.assertFalse([name for name in spaced if compact(name) not in set(names)])
        self.assertEqual(
            Counter({
                "generic": 170,
                "competitor": 41,
                "brand_ext": 39,
                "brand": 28,
                "campaign": 7,
            }),
            Counter(group for _, group in pairs),
        )
        self.assertEqual(
            {
                "단기임대", "삼삼엠투", "잠깐살집", "잠깐 살 집",
                "모두를위한단기임대", "모두를 위한 단기임대", "33M2",
            },
            {name for name, group in pairs if group == "campaign"},
        )

    def test_spaced_keyword_without_compact_counterpart_is_rejected(self):
        keywords.CAMPAIGN_KEYWORDS.append("테스트 키워드")
        try:
            with self.assertRaisesRegex(ValueError, "붙여쓰기 기준 키워드"):
                keywords.all_keywords()
        finally:
            keywords.CAMPAIGN_KEYWORDS.pop()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and confirm the current inventory fails**

Run: `python -m unittest discover -s tests -p "test_keywords.py" -v`

Expected: both tests fail because five spaced entries lack compact counterparts and no validation exists.

### Task 2: Register compact counterparts and validate variants

**Files:**
- Modify: `keywords.py:82-102`
- Modify: `keywords.py:105-130`
- Test: `tests/test_keywords.py`

- [ ] **Step 1: Add compact counterparts beside the three spaced competitor entries**

Keep the spaced queries and add these exact compact queries in the same competitor list:

```python
"airbnb", "air bnb"
"네이버부동산", "네이버 부동산"
"피터팬의좋은방구하기", "피터팬의 좋은방 구하기"
```

- [ ] **Step 2: Add compact and spaced campaign forms as separate entries**

```python
CAMPAIGN_KEYWORDS: list[str] = [
    "단기임대", "삼삼엠투",
    "잠깐살집", "잠깐 살 집",
    "모두를위한단기임대", "모두를 위한 단기임대",
    "33M2",
]
```

- [ ] **Step 3: Validate that spaced entries have compact counterparts**

Add this helper before `all_keywords()`:

```python
def _compact(kw: str) -> str:
    return "".join(kw.split())


def _validate_variants(source: list[str]) -> None:
    names = set(source)
    missing = [kw for kw in source if kw != _compact(kw) and _compact(kw) not in names]
    if missing:
        raise ValueError(f"띄어쓰기 키워드에는 붙여쓰기 기준 키워드가 필요합니다: {missing!r}")
```

Inside `all_keywords()`, define the source tuples once, validate their flattened names, and then reuse the tuples in the existing precedence loop:

```python
    sources = ((BRAND_KEYWORDS, "brand"),
               (BRAND_EXPANDED, "brand_ext"),
               (GENERIC_KEYWORDS, "generic"),
               (COMPETITOR_KEYWORDS, "competitor"),
               (CAMPAIGN_KEYWORDS, "campaign"))
    _validate_variants([kw for kw_list, _ in sources for kw in kw_list])

    # Keep the existing campaign/competitor precedence and exact-string deduplication.
    for kw_list, group in sources:
```

- [ ] **Step 4: Run inventory tests**

Run: `python -m unittest discover -s tests -p "test_keywords.py" -v`

Expected: 2 tests pass.

- [ ] **Step 5: Verify group counts**

Run: `python keywords.py`

Expected: `Counter({'generic': 170, 'competitor': 41, 'brand_ext': 39, 'brand': 28, 'campaign': 7}) total 285`.

### Task 3: Verify separate Naver results

**Files:**
- No file changes

- [ ] **Step 1: Collect both campaign query forms**

Run: `python run.py --engines naver --keywords "모두를 위한 단기임대,모두를위한단기임대" --dry-run`

Expected: two independent `ad` and two independent `organic` records. Preserve the observed external results exactly; do not force either form to `no_section`.

- [ ] **Step 2: Verify dashboard-facing inventory**

Run: `python -c "import keywords; p=keywords.all_keywords(); n={k for k,_ in p}; assert len(p)==285; assert {'모두를 위한 단기임대','모두를위한단기임대'} <= n; print('285 keywords; both variants registered')"`

Expected: `285 keywords; both variants registered`.

- [ ] **Step 3: Inspect changed code for blockers**

Check `keywords.py` and `tests/test_keywords.py` for skipped tests, stubs, or placeholder branches. Expected: none.
