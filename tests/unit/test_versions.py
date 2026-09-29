"""PEP 440 -> rez range conversion.

The table follows rez-pip's (``rez_pip.utils.pythonSpecifierToRezRequirement``), with the tighter
bounds described in ``versions``. An earlier
version of this code got ``<=``, ``>``, ``!=``, and unparsable specifiers wrong: ``<=2`` became
``<2``, ``>1.0`` became ``1.0+``, ``!=1.5`` was dropped, and ``garbage`` meant any version.
"""

from __future__ import annotations

import pytest

from griptape_nodes_rez_build.versions import RezVersion, SpecifierConversionError, rez_range, rez_requirement


class TestTable:
    @pytest.mark.parametrize(
        ("specifier", "expected"),
        [
            ("==1", "1+<1.0.0.1"),
            ("==1.*", "1+<2"),
            (">1", "1.0.0.1+"),
            ("<1", "<1"),
            (">=1", "1+"),
            ("<=1", "<1.0.0.1"),
            ("~=1.2", "1.2+<2"),
            ("~=1.2.3", "1.2.3+<1.3"),
            ("!=1", "<1|1.0.0.1+"),
            ("!=1.2", "<1.2|1.2.0.1+"),
            ("!=1.*", "<1|2+"),
            ("!=1.2.*", "<1.2|1.3+"),
        ],
    )
    def test_single_specifier(self, specifier: str, expected: str) -> None:
        assert rez_range(specifier) == expected


class TestReviewCases:
    def test_less_or_equal_includes_the_bound(self) -> None:
        assert rez_requirement("foo", "<=2") == "foo<2.0.0.1"

    def test_greater_excludes_the_bound(self) -> None:
        assert rez_requirement("foo", ">1.0") == "foo-1.0.0.1+"

    def test_not_equal_is_a_union(self) -> None:
        assert rez_requirement("foo", "!=1.5") == "foo<1.5|1.5.0.1+"

    @pytest.mark.parametrize("specifier", ["garbage", ">=", "==1.0,<", "~=1", "===1.0", "<1,>2"])
    def test_unconvertible_specifier_fails(self, specifier: str) -> None:
        with pytest.raises(SpecifierConversionError):
            rez_range(specifier)


class TestCombined:
    @pytest.mark.parametrize(
        ("specifier", "expected"),
        [
            ("", ""),
            ("   ", ""),
            (">=1.0,<2", "1+<2"),
            ("<2,>=1.0", "1+<2"),
            (">=1.0,!=1.5", "1+<1.5|1.5.0.1+"),
            (">=1.0,!=1.5,<2", "1+<1.5|1.5.0.1+<2"),
            (">=1.0,!=0.5", "1+"),
            (">=1.0,!=3.*,<5", "1+<3|4+<5"),
            ("==2.0+cu128", "2+<2.0.0.1"),
            ("==1.2.*,!=1.2.3", "1.2+<1.2.3|1.2.3.1+<1.3"),
            (">=0.9,<1.0.0", "0.9+<1"),
            (">=2.0.0rc1", "2.0.0rc1+"),
            ("~=1.4.2", "1.4.2+<1.5"),
        ],
    )
    def test_ranges_are_intersected(self, specifier: str, expected: str) -> None:
        assert rez_range(specifier) == expected


class TestTrailingZeros:
    """Pip treats 1, 1.0, and 1.0.0 as one version; rez orders them 1 < 1.0 < 1.0.0."""

    @pytest.mark.parametrize(
        ("specifier", "expected"),
        [(">=1.0", "1+"), ("<1.0.0", "<1"), ("==1.0.0", "1+<1.0.0.1"), ("<=1.0", "<1.0.0.1"), ("!=1.0", "<1|1.0.0.1+")],
    )
    def test_every_spelling_of_the_bound_lands_on_the_same_side(self, specifier: str, expected: str) -> None:
        assert rez_range(specifier) == expected


class TestRequirement:
    def test_range_starting_with_a_version_uses_a_hyphen(self) -> None:
        assert rez_requirement("numpy", ">=1.26,<3") == "numpy-1.26+<3"

    def test_range_starting_with_less_than_follows_the_family(self) -> None:
        assert rez_requirement("numpy", "<3") == "numpy<3"

    def test_union_starting_with_less_than(self) -> None:
        assert rez_requirement("numpy", "!=2.0") == "numpy<2|2.0.0.1+"

    def test_no_specifier_is_the_bare_family(self) -> None:
        assert rez_requirement("numpy", "") == "numpy"


class TestRezVersionOrdering:
    @pytest.mark.parametrize(
        ("older", "newer"),
        [("1", "1.0"), ("1.9", "1.10"), ("1.0.a2", "1.0.0"), ("beta", "1"), ("01", "1"), ("2.0.0", "2.0.0rc1")],
    )
    def test_matches_rez(self, older: str, newer: str) -> None:
        assert RezVersion(older) < RezVersion(newer)
        assert not RezVersion(newer) < RezVersion(older)
