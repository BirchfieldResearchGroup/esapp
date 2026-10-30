"""esapp's pytest plugin, from ``pip install esapp[testing]``. Turn it on in your top-level
``conftest.py`` with ``pytest_plugins = ["esapp.testing"]``. It adds the ``--pw`` option, the
``powerworld`` marker and its fixtures.

Live tests take the ``pw`` fixture and say how they need to be isolated::

    from esapp.testing import powerworld

    @powerworld                                      # read-only, shared case (the default)
    def test_voltages(pw): ...

    @powerworld(isolation="state")                   # SaveState / LoadState around the test
    def test_load_growth(pw): ...

    @powerworld(case="texas2k", isolation="fresh")   # a new copy of the case
    def test_topology_edit(pw): ...

All live tests share one SimAuto process (one license), so they run in the main pytest
process, never in pytest-xdist workers.

- ``--pw=auto`` (the default) skips live tests when SimAuto or a case isn't available.
- ``--pw=require`` fails them instead.
- ``--pw=off`` always skips them.

The case comes from ``$ESAPP_TEST_CASE``, or override the ``pw_cases`` fixture for more.
Unit tests use ``fake_pw``: a session over :class:`FakeSimAuto`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from collections.abc import Callable, Generator, Iterator
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, Literal, NoReturn, overload

try:
    import pytest
except ImportError:
    raise ImportError("esapp.testing needs pytest: pip install esapp[testing]") from None

from esapp._session import PowerWorld
from esapp.errors import SimAutoUnavailable
from esapp.simauto import ComTransport, Transport
from esapp.testing._fake import FakeSimAuto

__all__ = ["FakeSimAuto", "Isolation", "powerworld"]

type Isolation = Literal["shared", "state", "fresh"]
_ISOLATIONS = ("shared", "state", "fresh")


@overload
def powerworld[F: Callable[..., object]](fn: F, /) -> F: ...
@overload
def powerworld[F: Callable[..., object]](
    *, case: str = "default", isolation: Isolation = "shared"
) -> Callable[[F], F]: ...
def powerworld(
    fn: Callable[..., object] | None = None,
    /,
    *,
    case: str = "default",
    isolation: Isolation = "shared",
) -> Any:
    """Mark a test or class as one that uses the live SimAuto session, ``pw``.

    ``isolation`` says how the test is kept from affecting the others:

    - ``"shared"``: read-only; writes raise ``ReadOnlySessionError``. The fastest.
    - ``"state"``: the case state is saved before the test and restored after. For solves and
      setpoint changes.
    - ``"fresh"``: a new copy of the case. For creating or deleting objects and other
      topology changes.
    """
    if isolation not in _ISOLATIONS:
        raise ValueError(f"isolation must be one of {_ISOLATIONS}, not {isolation!r}")
    mark = pytest.mark.powerworld(case=case, isolation=isolation)
    return mark if fn is None else mark(fn)


# ---------------------------------------------------------------------- hooks
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("esapp").addoption(
        "--pw",
        choices=("auto", "require", "off"),
        default="auto",
        help="live PowerWorld tests: auto skips them when SimAuto or a case is unavailable, "
        "require fails instead, off skips them",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "powerworld(case='default', isolation='shared'): drives the live SimAuto session",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--pw") != "off":
        return
    skip = pytest.mark.skip(reason="live PowerWorld tests are off (--pw=off)")
    for item in items:
        if item.get_closest_marker("powerworld") is not None:
            item.add_marker(skip)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Attach PowerWorld's message log to failed live tests."""
    report = yield
    session = getattr(item, "funcargs", {}).get("pw")
    if report.failed and call.when == "call" and isinstance(session, PowerWorld):
        try:
            text = session.log.text()
        except Exception as err:
            text = f"(could not read the PowerWorld log: {err})"
        report.sections.append(("PowerWorld log", text))
    return report


# ---------------------------------------------------------------------- live fixtures
@pytest.fixture(scope="session")
def pw_cases(request: pytest.FixtureRequest) -> dict[str, Path]:
    """Case files by name. Default: ``{"default": $ESAPP_TEST_CASE}``; override for more."""
    if path := os.environ.get("ESAPP_TEST_CASE"):
        return {"default": Path(path)}
    _unavailable(request.config, "no case configured: set ESAPP_TEST_CASE or override pw_cases")


@pytest.fixture(scope="session")
def pw_transport() -> Callable[[], Transport]:
    """Builds the live session's transport. Override it to show the Simulator window, etc."""
    return ComTransport


@pytest.fixture(scope="session")
def simauto(
    request: pytest.FixtureRequest, pw_transport: Callable[[], Transport]
) -> Iterator[PowerWorld]:
    """The test session's one SimAuto session: one Simulator process, one license seat."""
    if hasattr(request.config, "workerinput"):  # every xdist worker would start its own
        _unavailable(request.config, "live tests run in one process: rerun them with -n0")
    try:
        session = PowerWorld(transport=pw_transport())
    except SimAutoUnavailable as err:
        _unavailable(request.config, f"SimAuto unavailable: {err}")
    with session:
        yield session


@pytest.fixture(scope="session")
def pw_working_cases(
    pw_cases: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, Path]]:
    """Session copies of every case; the originals are checked unchanged at the end."""
    if missing := [str(p) for p in pw_cases.values() if not p.is_file()]:
        pytest.fail(f"configured case file(s) not found: {missing}", pytrace=False)
    root = tmp_path_factory.mktemp("cases")
    digests = {name: _sha256(path) for name, path in pw_cases.items()}
    yield {name: _copy(path, root / name) for name, path in pw_cases.items()}
    if changed := [str(p) for name, p in pw_cases.items() if _sha256(p) != digests[name]]:
        pytest.fail(f"original case file(s) modified during the tests: {changed}", pytrace=False)


@pytest.fixture
def pw(
    request: pytest.FixtureRequest,
    pw_working_cases: dict[str, Path],  # first: no case, no SimAuto process started
    simauto: PowerWorld,
    tmp_path: Path,
) -> Iterator[PowerWorld]:
    """The live session on the test's case, isolated as its ``@powerworld`` mark asks."""
    marker = request.node.get_closest_marker("powerworld")
    if marker is None:
        pytest.fail("tests that use the `pw` fixture must be marked @powerworld", pytrace=False)
    case = marker.kwargs.get("case", "default")
    if case not in pw_working_cases:
        pytest.fail(f"unknown case {case!r}; configured: {sorted(pw_working_cases)}", pytrace=False)
    source = pw_working_cases[case]
    scope: AbstractContextManager[object]
    match marker.kwargs.get("isolation", "shared"):
        case "fresh":
            simauto.open(_copy(source, tmp_path / "case"))
            scope = nullcontext()
        case "state":
            scope = simauto.ensure_open(source).snapshot()
        case _:
            scope = simauto.ensure_open(source).read_only()
    simauto.log.clear()
    with scope:
        yield simauto


# ---------------------------------------------------------------------- unit fixtures
@pytest.fixture
def fake_simauto() -> FakeSimAuto:
    """An empty in-memory SimAuto; ``fake_simauto.add(Bus, {...})`` gives it objects."""
    return FakeSimAuto()


@pytest.fixture
def fake_pw(fake_simauto: FakeSimAuto) -> Iterator[PowerWorld]:
    """A session over ``fake_simauto``."""
    with PowerWorld(transport=fake_simauto) as session:
        yield session


# ---------------------------------------------------------------------- helpers
def _unavailable(config: pytest.Config, reason: str) -> NoReturn:
    if config.getoption("--pw") == "require":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _copy(source: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    return Path(shutil.copy2(source, directory / source.name))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
