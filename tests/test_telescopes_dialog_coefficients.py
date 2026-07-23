"""Guards telescopes_dialog.COEFFICIENTS against drifting out of sync with
transform_generator.TRANSFORM_DEFS.

TelescopesDialog._save() rewrites the whole telescope_coefficients table
from only the columns it displays, so any transform the generator can
compute but the dialog doesn't list would be silently deleted the next
time someone edits an unrelated cell in that dialog.
"""
from __future__ import annotations

from telescopes_dialog import COEFFICIENTS
from transform_generator import TRANSFORM_DEFS


def test_all_generator_transforms_are_displayed():
    generator_names = {name for name, *_ in TRANSFORM_DEFS}
    assert generator_names <= set(COEFFICIENTS)


def test_coefficients_list_has_no_duplicates():
    assert len(COEFFICIENTS) == len(set(COEFFICIENTS))
