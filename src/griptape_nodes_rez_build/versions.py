"""PEP 440 version specifiers as rez version ranges.

Each specifier converts the way rez-pip converts it (``rez_pip.utils.pythonSpecifierToRezRequirement``),
with tighter bounds. Rez versions differ from pip versions in one way that matters: ``1``,
``1.0``, and ``1.0.0`` are one version to pip, but three increasingly new versions to rez. So
rez's own ``==1`` or ``<=1`` would miss ``1.0``, and every bound is written so that all
spellings of a version land on the same side of it.

=============  ===================  ==================
PEP 440        rez (this module)    rez-pip
=============  ===================  ==================
``==1``        ``1+<1.0.0.1``       ``1+<1.1``
``==1.*``      ``1+<2``             ``1``
``>1``         ``1.0.0.1+``         ``1.1+``
``<1``         ``<1``               ``<1``
``>=1``        ``1+``               ``1+``
``<=1``        ``<1.0.0.1``         ``<1.1``
``~=1.2``      ``1.2+<2``           ``1.2+<2``
``~=1.2.3``    ``1.2.3+<1.3``       ``1.2.3+<1.3``
``!=1``        ``<1|1.0.0.1+``      ``<1|1.1+``
``!=1.2``      ``<1.2|1.2.0.1+``    ``<1.2|1.2.1+``
``!=1.*``      ``<1|2+``            ``<1|2+``
``!=1.2.*``    ``<1.2|1.3+``        ``<1.2|1.3+``
``>=1.0``      ``1+``               ``1.0+``
``<1.0.0``     ``<1``               ``<1.0.0``
=============  ===================  ==================

Where the columns differ, rez-pip's range lets in versions pip excludes (``<=1`` admits
``1.0.5``; ``<1.0.0`` admits ``1`` and ``1.0``) or leaves out ones pip allows (``>=1.0`` misses a
package versioned ``1``). ``==1.*`` is written as a range rather than rez's prefix form.

Several specifiers (``>=1,<2,!=1.5``) are intersected into one range. Anything that cannot be
converted (``===``, a malformed specifier, specifiers that exclude every version) raises
``SpecifierConversionError``: a package built from a guessed range could resolve to versions
its author ruled out.

Versions keep pip's normalised spelling (``2.0.0rc1``), because that is how the build names the
version folders it writes. One inexactness remains, shared with rez-pip: rez orders a
pre-release after its release (``2.0.0rc1`` is newer than ``2.0.0``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

from packaging.specifiers import InvalidSpecifier, Specifier, SpecifierSet
from packaging.version import InvalidVersion, Version

# Releases are padded to this many parts before the adjacent version is formed (see _just_above).
_PADDED_RELEASE_PARTS = 3


class SpecifierConversionError(ValueError):
    """A PEP 440 specifier that has no rez equivalent."""


@total_ordering
@dataclass(frozen=True)
class RezVersion:
    """A version string ordered the way rez orders versions.

    Tokens are separated by ``.`` or ``-``. Each token is split into numeric and alphabetic
    runs; alphabetic runs come before numbers, numbers compare numerically, and a version that
    is a prefix of another is the older one (``1`` < ``1.0``).
    """

    text: str

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RezVersion):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self) -> str:
        return self.text

    def _key(self) -> tuple[tuple[tuple[int, int, str], ...], ...]:
        tokens = [token for token in re.split(r"[.\-]", self.text) if token]
        return tuple(tuple(_subtoken_key(part) for part in re.findall(r"\d+|[^\d]+", token)) for token in tokens)


@dataclass(frozen=True)
class VersionInterval:
    """Versions from ``lower`` (inclusive) up to ``upper`` (exclusive). None means unbounded."""

    lower: RezVersion | None
    upper: RezVersion | None

    @property
    def is_empty(self) -> bool:
        if self.lower is None or self.upper is None:
            return False
        return not self.lower < self.upper

    def intersect(self, other: VersionInterval) -> VersionInterval:
        lower = _max_lower(self.lower, other.lower)
        upper = _min_upper(self.upper, other.upper)
        return VersionInterval(lower=lower, upper=upper)

    def to_rez(self) -> str:
        if self.lower is None and self.upper is None:
            return ""
        if self.lower is None:
            return f"<{self.upper}"
        if self.upper is None:
            return f"{self.lower}+"
        return f"{self.lower}+<{self.upper}"


def rez_requirement(family: str, specifier: str) -> str:
    """A rez request for *family* limited to the versions *specifier* (PEP 440) allows.

    ``rez_requirement("numpy", ">=1.26,<3")`` is ``numpy-1.26+<3``; a range that starts with
    ``<`` follows the family directly (``numpy<3``).

    Raises:
        SpecifierConversionError: When the specifier cannot be converted.
    """
    version_range = rez_range(specifier)
    if not version_range:
        return family
    if version_range[0].isdigit():
        return f"{family}-{version_range}"
    return f"{family}{version_range}"


def rez_range(specifier: str) -> str:
    """The rez version range for a PEP 440 specifier string; empty when it allows every version.

    Raises:
        SpecifierConversionError: When the specifier cannot be converted.
    """
    text = specifier.strip()
    if not text:
        return ""
    try:
        specifier_set = SpecifierSet(text)
    except InvalidSpecifier as exc:
        msg = f"'{text}' is not a valid version specifier"
        raise SpecifierConversionError(msg) from exc

    intervals = [VersionInterval(lower=None, upper=None)]
    for spec in sorted(specifier_set, key=str):
        intervals = _intersect_all(intervals, _spec_intervals(spec, text))
    if not intervals:
        msg = f"'{text}' excludes every version"
        raise SpecifierConversionError(msg)
    return "|".join(interval.to_rez() for interval in intervals)


def _spec_intervals(spec: Specifier, specifier_text: str) -> list[VersionInterval]:  # noqa: PLR0911
    """One specifier (``>=1.2``) as the rez intervals it allows."""
    operator = spec.operator
    wildcard = spec.version.endswith(".*")
    version = _normalise(spec.version.removesuffix(".*"), specifier_text)
    # Lower bounds and "<" use the shortest spelling, so every spelling pip treats as equal
    # (1, 1.0, 1.0.0) falls on the same side of the bound in rez.
    this = RezVersion(_shortest_spelling(version))
    adjacent = RezVersion(_just_above(version))

    if operator == "==" and wildcard:
        return [VersionInterval(lower=this, upper=RezVersion(_next_release(version)))]
    if operator == "==":
        return [VersionInterval(lower=this, upper=adjacent)]
    if operator == ">=":
        return [VersionInterval(lower=this, upper=None)]
    if operator == ">":
        return [VersionInterval(lower=adjacent, upper=None)]
    if operator == "<=":
        return [VersionInterval(lower=None, upper=adjacent)]
    if operator == "<":
        return [VersionInterval(lower=None, upper=this)]
    if operator == "~=":
        return [VersionInterval(lower=this, upper=RezVersion(_next_release(version, trim=1)))]
    if operator == "!=" and wildcard:
        return [
            VersionInterval(lower=None, upper=this),
            VersionInterval(lower=RezVersion(_next_release(version)), upper=None),
        ]
    if operator == "!=":
        return [VersionInterval(lower=None, upper=this), VersionInterval(lower=adjacent, upper=None)]
    msg = f"'{specifier_text}' uses '{operator}', which has no rez equivalent"
    raise SpecifierConversionError(msg)


def _intersect_all(left: list[VersionInterval], right: list[VersionInterval]) -> list[VersionInterval]:
    """The intersection of two unions of intervals, sorted and with overlaps merged."""
    pieces = [a.intersect(b) for a in left for b in right]
    kept = [piece for piece in pieces if not piece.is_empty]
    kept.sort(key=_interval_sort_key)
    merged: list[VersionInterval] = []
    for piece in kept:
        if merged and _touches(merged[-1], piece):
            previous = merged.pop()
            merged.append(VersionInterval(lower=previous.lower, upper=_max_upper(previous.upper, piece.upper)))
        else:
            merged.append(piece)
    return merged


def _normalise(version: str, specifier_text: str) -> str:
    """Pip's normalised spelling of a version, without a local part (``2.7.0+cu128`` -> ``2.7.0``)."""
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        msg = f"'{specifier_text}' names '{version}', which is not a valid version"
        raise SpecifierConversionError(msg) from exc
    return str(parsed).split("+", 1)[0]


def _shortest_spelling(version: str) -> str:
    """A plain release without trailing zeros: ``1.0.0`` -> ``1``. Other versions are kept as they are."""
    parsed = Version(version)
    if parsed.pre is not None or parsed.post is not None or parsed.dev is not None or parsed.epoch:
        return version
    release = list(parsed.release)
    while len(release) > 1 and release[-1] == 0:
        release.pop()
    return ".".join(str(part) for part in release)


def _just_above(version: str) -> str:
    """The smallest rez version above every spelling of *version*, and below its next release.

    ``1.2`` -> ``1.2.0.1``: above ``1.2`` and ``1.2.0`` (the same version to pip), below
    ``1.2.0.5`` and ``1.2.1`` (newer to pip). rez-pip uses ``1.2.1``, which lets ``1.2.0.5`` through
    ``<=1.2``.
    """
    parsed = Version(version)
    if parsed.pre is not None or parsed.post is not None or parsed.dev is not None or parsed.epoch:
        return f"{version}.1"
    release = list(parsed.release)
    while len(release) < _PADDED_RELEASE_PARTS:
        release.append(0)
    return ".".join(str(part) for part in release) + ".1"


def _next_release(version: str, *, trim: int = 0) -> str:
    """The next release after *version*, after dropping *trim* trailing parts.

    ``_next_release("1.2")`` is ``1.3``; ``_next_release("1.2.3", trim=1)`` is ``1.3``, the
    upper bound of ``~=1.2.3``.
    """
    release = list(Version(version).release)
    if trim and len(release) > trim:
        release = release[:-trim]
    release[-1] += 1
    return ".".join(str(part) for part in release)


def _subtoken_key(part: str) -> tuple[int, int, str]:
    if part.isdigit():
        return (1, int(part), part)
    return (0, 0, part)


def _interval_sort_key(interval: VersionInterval) -> tuple[int, tuple[tuple[tuple[int, int, str], ...], ...]]:
    if interval.lower is None:
        return (0, ())
    return (1, interval.lower._key())


def _touches(first: VersionInterval, second: VersionInterval) -> bool:
    """Whether *second* (sorted after *first*) overlaps or directly continues *first*."""
    if first.upper is None:
        return True
    if second.lower is None:
        return True
    return not first.upper < second.lower


def _max_lower(first: RezVersion | None, second: RezVersion | None) -> RezVersion | None:
    if first is None:
        return second
    if second is None:
        return first
    return max(first, second)


def _min_upper(first: RezVersion | None, second: RezVersion | None) -> RezVersion | None:
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)


def _max_upper(first: RezVersion | None, second: RezVersion | None) -> RezVersion | None:
    if first is None or second is None:
        return None
    return max(first, second)
