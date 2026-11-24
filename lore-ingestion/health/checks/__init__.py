"""Importing this package self-registers every check. Adding a dependency = add one
check file here and one import line below. No other edits.

Intentionally NOT probed: Neo4j. It's written only by the batch builders
(builders/unified_builder.py), not by the poller process that hosts this shim — and it
isn't even a dependency of this repo. Add a check here if a builder ever gets its own shim.
"""

from health.checks import (  # noqa: F401
    github_api,
    gitlab_api,
    nvidia,
    pollers,
    store,
)
