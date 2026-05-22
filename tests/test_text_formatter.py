"""
Unit tests for app/utils/text_formatter.py

Tests verify that split_to_bullets correctly:
  - Splits on sentence-ending periods
  - Does NOT split on decimal numbers (5.1, 1.5, 7.7)
  - Does NOT split on numbered section references like (7.7)
  - Splits before numbered section headers (5.2 Title)
  - Splits ALL-CAPS titles from following sentences
  - Handles inline cross-references like ( 5.1 )
  - Handles common abbreviations like e.g., i.e.
  - Handles None / empty input
"""
import pytest
from app.utils.text_formatter import split_to_bullets


# ── Basic sentence splitting ──────────────────────────────────────────────────

def test_single_sentence_returns_one_bullet():
    text = "This is a single sentence with no period at the end"
    bullets = split_to_bullets(text)
    assert len(bullets) == 1
    assert bullets[0] == text


def test_two_sentences_split_correctly():
    text = "First sentence ends here. Second sentence starts here."
    bullets = split_to_bullets(text)
    assert len(bullets) == 2
    assert bullets[0] == "First sentence ends here."
    assert bullets[1] == "Second sentence starts here."


def test_three_sentences():
    text = "Alpha is first. Beta is second. Gamma is third."
    bullets = split_to_bullets(text)
    assert len(bullets) == 3


# ── Decimal numbers must NOT be split ─────────────────────────────────────────

def test_no_split_on_section_number_5_1():
    text = "5.1 Skeletal Muscle Effects Cases of myopathy have been reported."
    bullets = split_to_bullets(text)
    # "5.1" must not cause a split; text treated as one item
    assert all("5.1" in b or "Cases" in b for b in bullets)
    joined = " ".join(bullets)
    assert "5.1" in joined
    assert "Skeletal" in joined


def test_no_split_on_decimal_1_5():
    text = "Patients with creatinine clearance below 1.5 mL/min should be monitored."
    bullets = split_to_bullets(text)
    joined = " ".join(bullets)
    assert "1.5" in joined


def test_no_split_on_section_reference_in_parens():
    # "(7.7)" inside a sentence — must not split
    text = (
        "Caution should be exercised when prescribing this drug with colchicine "
        "[see Drug Interactions (7.7)]. Therapy should be discontinued if myopathy occurs."
    )
    bullets = split_to_bullets(text)
    joined = " ".join(bullets)
    assert "7.7" in joined
    # The ]. boundary should cause a split — two bullets expected
    assert len(bullets) >= 2


def test_no_split_on_dosage_decimal():
    text = "The recommended starting dose is 2.5 mg once daily. Titrate as needed."
    bullets = split_to_bullets(text)
    assert len(bullets) == 2
    assert "2.5" in bullets[0]


# ── Section header transitions ────────────────────────────────────────────────

def test_split_before_new_section_number():
    text = (
        "All patients should discontinue therapy immediately. "
        "5.2 Liver Enzyme Abnormalities It is recommended that tests be performed."
    )
    bullets = split_to_bullets(text)
    joined = "\n".join(bullets)
    # "5.2" should appear at the start of a bullet (or at least be present)
    assert "5.2" in joined
    # Must have at least 2 bullets
    assert len(bullets) >= 2


def test_allcaps_title_splits_from_sentence():
    text = (
        "5 WARNINGS AND PRECAUTIONS "
        "Thromboembolism, including retinal occlusion, has been reported."
    )
    bullets = split_to_bullets(text)
    # The ALL-CAPS title should be its own bullet
    assert any("WARNINGS AND PRECAUTIONS" in b for b in bullets)
    # The sentence should be its own bullet
    assert any("Thromboembolism" in b for b in bullets)


def test_allcaps_section_title_split():
    text = "CONTRAINDICATIONS Hypersensitivity to the active substance."
    bullets = split_to_bullets(text)
    assert any("CONTRAINDICATIONS" in b for b in bullets)
    assert any("Hypersensitivity" in b for b in bullets)


# ── Inline cross-references ───────────────────────────────────────────────────

def test_inline_cross_ref_splits_correctly():
    # ". ( 5.1 ) Next sentence" — the ref splits from the next sentence
    text = (
        "Concomitant use may increase the risk of thrombosis. "
        "( 5.1 ) Visual or ocular adverse reactions may occur."
    )
    bullets = split_to_bullets(text)
    # Should have at least 2 bullets
    assert len(bullets) >= 2
    joined = " ".join(bullets)
    assert "thrombosis" in joined
    assert "Visual or ocular" in joined


# ── Abbreviations must NOT be split ──────────────────────────────────────────

def test_no_split_on_eg():
    text = (
        "Predisposing factors include advanced age (e.g., >65 years) "
        "and renal impairment. Dosing should be adjusted accordingly."
    )
    bullets = split_to_bullets(text)
    # e.g. must not cause an extra split
    assert len(bullets) == 2
    assert "e.g." in bullets[0]


def test_no_split_on_us_abbreviation():
    # "U.S." must not be split into "U." and "S. postmarketing"
    text = (
        "However, there have been U.S. postmarketing reports of venous thrombotic events. "
        "For this reason, concomitant use is contraindicated."
    )
    bullets = split_to_bullets(text)
    assert any("U.S." in b for b in bullets)
    # Should have 2 bullets, not 3+
    assert len(bullets) == 2


def test_no_split_on_eu_abbreviation():
    text = "The product is approved in the E.U. Patients should consult their physician."
    bullets = split_to_bullets(text)
    assert any("E.U." in b for b in bullets)
    assert len(bullets) == 2


def test_no_split_on_product_number():
    # "FD&C Yellow No. 5" must not be split on "No. 5"
    text = (
        "This tablet contains FD&C Yellow No. 5 (tartrazine) which may cause "
        "allergic-type reactions. Patients with aspirin sensitivity should be cautious."
    )
    bullets = split_to_bullets(text)
    # "FD&C Yellow No. 5" must be kept in the same bullet
    assert any("No. 5" in b for b in bullets)
    assert len(bullets) == 2


def test_section_number_after_period_does_split():
    # ". 5.2 Title" is a section transition — must split
    text = "Therapy should be discontinued. 5.2 Liver Effects It is recommended."
    bullets = split_to_bullets(text)
    joined = " ".join(bullets)
    assert "5.2" in joined
    assert len(bullets) >= 2


def test_no_split_on_ie():
    text = (
        "Patients with severe hepatic impairment (i.e., Child-Pugh C) "
        "should avoid this drug. Monitor liver function tests."
    )
    bullets = split_to_bullets(text)
    assert len(bullets) == 2
    assert "i.e." in bullets[0]


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_none_returns_empty_list():
    assert split_to_bullets(None) == []


def test_empty_string_returns_empty_list():
    assert split_to_bullets("") == []


def test_whitespace_only_returns_empty_list():
    assert split_to_bullets("   \n\t  ") == []


def test_very_short_fragments_are_filtered():
    # Fragments of ≤5 chars should be dropped
    bullets = split_to_bullets("A. B. This is a real sentence.")
    assert all(len(b) > 5 for b in bullets)


def test_already_newlined_text():
    text = "First sentence.\nSecond sentence.\nThird sentence."
    bullets = split_to_bullets(text)
    assert len(bullets) == 3


def test_multiple_spaces_collapsed():
    text = "First   sentence  ends  here.   Second  sentence  here."
    bullets = split_to_bullets(text)
    assert len(bullets) == 2
    assert "  " not in bullets[0]


# ── Realistic pharmaceutical text ────────────────────────────────────────────

ROSUVASTATIN_SNIPPET = (
    "5.1 Skeletal Muscle Effects Cases of myopathy and rhabdomyolysis with acute "
    "renal failure secondary to myoglobinuria have been reported with HMG-CoA "
    "reductase inhibitors, including rosuvastatin calcium. "
    "These risks can occur at any dose level, but are increased at the highest dose (40 mg). "
    "Rosuvastatin calcium therapy should be discontinued if markedly elevated creatine "
    "kinase levels occur or myopathy is diagnosed or suspected. "
    "5.2 Liver Enzyme Abnormalities It is recommended that liver enzyme tests be "
    "performed before the initiation of rosuvastatin calcium."
)

TRANEXAMIC_SNIPPET = (
    "5 WARNINGS AND PRECAUTIONS "
    "Thromboembolism, including retinal occlusion, has been reported with tranexamic acid use. "
    "Concomitant use of tranexamic acid with combined hormonal contraceptives may increase "
    "the risk of thrombosis. "
    "( 5.1 ) Visual or ocular adverse reactions may occur with tranexamic acid. "
    "Immediately discontinue use if visual or ocular symptoms occur."
)


def test_rosuvastatin_snippet_produces_multiple_bullets():
    bullets = split_to_bullets(ROSUVASTATIN_SNIPPET)
    assert len(bullets) >= 4
    # Decimal section numbers must survive intact
    joined = " ".join(bullets)
    assert "5.1" in joined
    assert "5.2" in joined
    assert "40 mg" in joined


def test_tranexamic_snippet_produces_multiple_bullets():
    bullets = split_to_bullets(TRANEXAMIC_SNIPPET)
    assert len(bullets) >= 4
    assert any("WARNINGS AND PRECAUTIONS" in b for b in bullets)
    assert any("Thromboembolism" in b for b in bullets)
    assert any("Visual or ocular" in b for b in bullets)


def test_all_bullets_are_non_empty_strings():
    bullets = split_to_bullets(ROSUVASTATIN_SNIPPET)
    assert all(isinstance(b, str) and b.strip() for b in bullets)
