"""pytest configuration for the repository root.

The repository is the plugin, so its root has the plugin's __init__.py. That
makes pytest treat the root as a package and import __init__.py before
running tests, which only works inside Picard. The root is collected as a
plain directory instead; the tests load the plugin the way Picard does.
"""

import pytest


class RootIsNotAPackage:
    @pytest.hookimpl(tryfirst=True)
    def pytest_collect_directory(self, path, parent):
        if path == parent.config.rootpath:
            return pytest.Dir.from_parent(parent, path=path)
        return None


def pytest_configure(config):
    # Registered as a plugin: this file's own hooks only apply to the
    # directories below the root, not to the root itself.
    config.pluginmanager.register(RootIsNotAPackage(), "root-is-not-a-package")
