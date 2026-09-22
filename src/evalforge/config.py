from pathlib import Path

import yaml

from .models import SuiteSpec


def load_suite(path: str | Path) -> SuiteSpec:
    path = Path(path)
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"Suite file must contain a YAML object: {path}")
    return SuiteSpec.model_validate(data)
