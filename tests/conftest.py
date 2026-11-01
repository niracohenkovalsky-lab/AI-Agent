"""Isolate pytest temporary files from directories owned by other Windows accounts."""

import tempfile


def pytest_configure(config):
    if config.option.basetemp is None:
        config.option.basetemp = tempfile.mkdtemp(prefix="apple-research-tests-")
