"""Drop the environment variables herdr injects so tests behave the same inside and outside a herdr pane."""
import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_from_herdr_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith("HERDR_"):
            monkeypatch.delenv(name, raising=False)
