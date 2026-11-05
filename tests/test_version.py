import tomllib
from pathlib import Path

from evalforge_agents import __version__
from evalforge_agents.api import app


def test_package_version_matches_pyproject():
    # release.yml checks the tag against pyproject.toml only; this keeps
    # evalforge_agents.__version__ (and the API's reported version) in step.
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert tomllib.loads(pyproject.read_text())["project"]["version"] == __version__
    assert app.version == __version__
