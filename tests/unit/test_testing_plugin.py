"""The esapp.testing plugin, exercised in inner pytest runs over FakeSimAuto."""

import pytest

from esapp.testing import powerworld

FAKE_SESSION = """
import pytest
from esapp.testing import FakeSimAuto

TRANSPORTS = []

@pytest.fixture(scope="session")
def pw_transport():
    def connect():
        TRANSPORTS.append(FakeSimAuto())
        return TRANSPORTS[-1]
    return connect

@pytest.fixture(scope="session")
def pw_cases(tmp_path_factory):
    source = tmp_path_factory.mktemp("originals") / "case.pwb"
    source.write_bytes(b"original")
    return {"default": source}
"""


@pytest.fixture
def run(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("ESAPP_TEST_CASE", raising=False)

    def run(*args: str) -> pytest.RunResult:
        # No cache: on Windows, renaming the inner run's new .pytest_cache can be denied (a
        # scanner holding it), and the warning breaks pytester's outcome parsing.
        plugins = ("-p", "esapp.testing", "-p", "no:randomly", "-p", "no:cacheprovider")
        return pytester.runpytest(*plugins, *args)

    return run


def test_isolation_levels_share_one_session(pytester: pytest.Pytester, run):
    pytester.makeconftest(FAKE_SESSION)
    pytester.makepyfile(
        """
        import pytest
        from conftest import TRANSPORTS
        from esapp import ReadOnlySessionError
        from esapp.grid import Gen
        from esapp.testing import powerworld

        @powerworld
        def test_shared_is_read_only(pw):
            with pytest.raises(ReadOnlySessionError):
                pw[Gen, Gen.GenMW] = 1.0

        @powerworld(isolation="state")
        def test_state_may_write(pw):
            pw[Gen, Gen.GenMW] = 1.0

        @powerworld(isolation="fresh")
        def test_fresh_gets_a_private_copy(pw, tmp_path):
            assert pw.case.is_relative_to(tmp_path) and pw.case.read_bytes() == b"original"

        def test_one_simauto_for_the_run():
            calls = [method for method, _ in TRANSPORTS[0].calls]
            assert len(TRANSPORTS) == 1
            assert calls.index("SaveState") < calls.index("LoadState")
        """
    )
    run().assert_outcomes(passed=4)


def test_off_skips_live_tests(pytester: pytest.Pytester, run):
    pytester.makepyfile(
        """
        from esapp.testing import powerworld

        @powerworld
        def test_live(pw): ...

        def test_unit(): ...
        """
    )
    run("--pw=off").assert_outcomes(passed=1, skipped=1)


@pytest.mark.parametrize(("mode", "outcome"), [("auto", "skipped"), ("require", "errors")])
def test_unavailable_simauto(pytester: pytest.Pytester, run, mode: str, outcome: str):
    pytester.makeconftest(
        FAKE_SESSION
        + """
from esapp.errors import SimAutoUnavailable

@pytest.fixture(scope="session")
def pw_transport():
    def connect():
        raise SimAutoUnavailable("no license seat")
    return connect
"""
    )
    pytester.makepyfile(
        "from esapp.testing import powerworld\n\n@powerworld\ndef test_live(pw): ..."
    )
    result = run(f"--pw={mode}", "-rs")
    result.assert_outcomes(**{outcome: 1})
    result.stdout.fnmatch_lines(["*no license seat*"])


FAKE_TRANSPORT_ONLY = """
import pytest
from esapp.testing import FakeSimAuto

CONNECTED = []

@pytest.fixture(scope="session")
def pw_transport():
    def connect():
        CONNECTED.append(1)
        return FakeSimAuto()
    return connect
"""


@pytest.mark.parametrize(("mode", "outcome"), [("auto", "skipped"), ("require", "errors")])
def test_no_case_configured(pytester: pytest.Pytester, run, mode: str, outcome: str):
    pytester.makeconftest(FAKE_TRANSPORT_ONLY)
    pytester.makepyfile(
        """
        from conftest import CONNECTED
        from esapp.testing import powerworld

        @powerworld
        def test_live(pw): ...

        def test_no_simauto_was_started():
            assert CONNECTED == []
        """
    )
    result = run(f"--pw={mode}", "-rs")
    result.assert_outcomes(passed=1, **{outcome: 1})
    result.stdout.fnmatch_lines(["*ESAPP_TEST_CASE*"])


def test_pw_needs_the_mark(pytester: pytest.Pytester, run):
    pytester.makeconftest(FAKE_SESSION)
    pytester.makepyfile("def test_unmarked(pw): ...")
    result = run()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*must be marked @powerworld*"])


def test_modified_original_case_fails_the_run(pytester: pytest.Pytester, run):
    pytester.makeconftest(FAKE_SESSION)
    pytester.makepyfile(
        """
        from esapp.testing import powerworld

        @powerworld
        def test_vandal(pw, pw_cases):
            pw_cases["default"].write_bytes(b"changed")
        """
    )
    result = run()
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*original case file(s) modified*"])


def test_failures_carry_the_powerworld_log(pytester: pytest.Pytester, run):
    pytester.makeconftest(FAKE_SESSION)
    pytester.makepyfile(
        """
        from esapp.testing import powerworld

        @powerworld
        def test_fails(pw):
            pw.log.add("solver gave up")
            assert False
        """
    )
    result = run()
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*PowerWorld log*", "solver gave up"])


@pytest.mark.parametrize(("mode", "outcome"), [("auto", "skipped"), ("require", "errors")])
def test_live_tests_stay_out_of_xdist_workers(
    pytester: pytest.Pytester, run, mode: str, outcome: str
):
    pytester.makeconftest(
        FAKE_SESSION
        + """
def pytest_configure(config):
    config.workerinput = {"workerid": "gw0"}  # as pytest-xdist marks its workers
"""
    )
    pytester.makepyfile(
        "from esapp.testing import powerworld\n\n@powerworld\ndef test_live(pw): ..."
    )
    result = run(f"--pw={mode}", "-rs")
    result.assert_outcomes(**{outcome: 1})
    result.stdout.fnmatch_lines(["*rerun them with -n0*"])


def test_isolation_is_validated():
    with pytest.raises(ValueError, match="isolation must be one of"):
        powerworld(isolation="bogus")  # pyright: ignore[reportArgumentType]
