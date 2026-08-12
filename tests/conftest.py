"""Repo-root pytest conftest.

Runs BEFORE any test collection / import.

Sets ``LITELLM_MODE=PRODUCTION`` unless already set. Reason:
``litellm.__init__`` calls ``dotenv.load_dotenv()`` unconditionally
in DEV mode (the default), pushing every value in ``.env`` into
``os.environ``. Downstream tests that build ``Settings(_env_file=None)``
to assert on defaults then see the polluted values (e.g.
``API_OIDC_CLIENT_ID='twaky-api'`` bleeding from ``.env``). Setting
``LITELLM_MODE=PRODUCTION`` gates the ``load_dotenv()`` call —
individual tests can still set env vars via ``monkeypatch.setenv``,
but the shared ``.env`` no longer contaminates the shared process.
"""

from __future__ import annotations

import os

os.environ.setdefault("LITELLM_MODE", "PRODUCTION")
