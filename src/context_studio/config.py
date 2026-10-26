"""Where the Studio keeps things, and the one rule about importing the engine.

The engine reads its content folder once, at import (`ICTX_CONTENT_DIR`). The Studio
serves `main`, so `bootstrap()` must run before anything imports `insurance_context`.
Proposal branches are checked in a subprocess pointed at the branch's worktree.

Layout of STUDIO_HOME (default `<repo>/.studio`, override with `CTX_STUDIO_HOME`):

    remote.git/      the content repo (bare); a GitLab project replaces it later
    main/            checkout of main: what the engine and every screen read
    work/<id>/       one worktree per proposal branch
    reviews/         change-request records for the local review backend
    warehouses/      synthetic warehouses per client (DuckDB, views installed)
    agent/           agent working files
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[2]


def _dotenv_value(raw: str) -> str:
    """A .env value: quoted ("..." or '...') up to its closing quote, else up to an inline ` #` comment."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def _load_dotenv() -> None:
    for path in (Path.cwd() / ".env", ENGINE_ROOT / ".env"):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), _dotenv_value(v))


@dataclass(frozen=True)
class Settings:
    home: Path
    git_backend: str          # "local" | "gitlab"
    gitlab_url: str
    gitlab_project: str
    default_client: str

    @property
    def remote(self) -> Path:
        return self.home / "remote.git"

    @property
    def main(self) -> Path:
        return self.home / "main"

    @property
    def work(self) -> Path:
        return self.home / "work"

    @property
    def reviews(self) -> Path:
        return self.home / "reviews"

    @property
    def warehouses(self) -> Path:
        return self.home / "warehouses"

    @property
    def agent_dir(self) -> Path:
        return self.home / "agent"


def settings() -> Settings:
    _load_dotenv()
    home = Path(os.environ.get("CTX_STUDIO_HOME") or ENGINE_ROOT / ".studio").resolve()
    return Settings(
        home=home,
        git_backend=os.environ.get("CTX_STUDIO_GIT", "local"),
        gitlab_url=os.environ.get("GIT_URL", "https://gitlab.com"),
        gitlab_project=os.environ.get("GIT_PROJECT", ""),
        default_client=os.environ.get("CTX_STUDIO_CLIENT", "summit_group"),
    )


def bootstrap() -> Settings:
    """Make sure the content repo exists and point the engine at `main`. Idempotent."""
    s = settings()
    from .gitrepo import ContentRepo
    ContentRepo(s).ensure()
    os.environ["ICTX_CONTENT_DIR"] = str(s.main)
    return s
