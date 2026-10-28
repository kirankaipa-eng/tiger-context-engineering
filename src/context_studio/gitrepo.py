"""The content repo: plain git, the same on every host.

Reviews (merge requests, approvals, comments) differ by host and live in `review.py`.
This module only does what `git` does: seed, branch, commit, push, diff, merge, log,
revert. Every write goes through a branch; `main` changes only by merge.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import ENGINE_ROOT, Settings

_LOCK = threading.RLock()
SEED_OWNERS = Path(__file__).parent / "seed" / "owners.yaml"
# Corpus folders that live outside clients/ in the engine repo, seeded per client.
EXTRA_CORPORA = {"summit_group": ENGINE_ROOT / "examples" / "curator" / "corpus"}


class GitError(RuntimeError):
    pass


def git(args: list[str], cwd: Path, check: bool = True, env: dict | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env)
    if check and out.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {(out.stderr or out.stdout).strip()}")
    return out.stdout.strip()


@dataclass
class Commit:
    sha: str
    author: str
    at: str
    subject: str
    body: str
    parents: list[str]


class ContentRepo:
    def __init__(self, s: Settings):
        self.s = s

    # -------------------------------------------------------------- setup

    def ensure(self) -> None:
        with _LOCK:
            if not self.s.remote.exists():
                self._seed()
            if not (self.s.main / ".git").exists():
                self.s.main.parent.mkdir(parents=True, exist_ok=True)
                git(["clone", "-q", str(self.s.remote), str(self.s.main)], self.s.home)
                self._identity(self.s.main)
            self.s.work.mkdir(parents=True, exist_ok=True)

    def _seed(self) -> None:
        """First run: copy the engine's content into a new repo (one commit on main)."""
        self.s.home.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as tmp:
            seed = Path(tmp) / "content"
            ignore = shutil.ignore_patterns("__pycache__", "*.duckdb", "proposals")
            shutil.copytree(ENGINE_ROOT / "ontology", seed / "ontology", ignore=ignore)
            shutil.copytree(ENGINE_ROOT / "clients", seed / "clients", ignore=ignore)
            for client, corpus in EXTRA_CORPORA.items():
                dest = seed / "clients" / client / "corpus"
                if corpus.exists() and (seed / "clients" / client).exists() and not dest.exists():
                    shutil.copytree(corpus, dest, ignore=ignore)
            shutil.copy(SEED_OWNERS, seed / "owners.yaml")
            (seed / "README.md").write_text(
                "# Ontology content\n\nManaged by Context Studio. Every change arrives as a proposal "
                "and merges only after its owners approve. See owners.yaml.\n", encoding="utf-8")
            git(["init", "-q", "-b", "main"], seed)
            self._identity(seed)
            git(["add", "-A"], seed)
            git(["commit", "-q", "-m", "Initial ontology content"], seed)
            git(["init", "-q", "--bare", "-b", "main", str(self.s.remote)], self.s.home)
            git(["push", "-q", str(self.s.remote), "main"], seed)

    @staticmethod
    def _identity(repo: Path) -> None:
        git(["config", "user.name", "Context Studio"], repo)
        git(["config", "user.email", "studio@localhost"], repo)

    # -------------------------------------------------------------- reading

    def sync(self) -> str:
        """Bring the main checkout up to date; returns its head."""
        with _LOCK:
            git(["fetch", "-q", "--prune", "origin"], self.s.main)
            git(["reset", "-q", "--hard", "origin/main"], self.s.main)
            return self.head()

    def head(self, ref: str = "origin/main") -> str:
        return git(["rev-parse", ref], self.s.main)

    def show(self, path: str, ref: str = "origin/main") -> str | None:
        out = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=self.s.main, capture_output=True, text=True, encoding="utf-8")
        return out.stdout if out.returncode == 0 else None

    def log(self, limit: int = 100) -> list[Commit]:
        sep, end = "<~|~>", "<~#~>"
        raw = git(["log", "--first-parent", f"-n{limit}",
                   f"--format=%H{sep}%an{sep}%aI{sep}%s{sep}%b{sep}%P{end}", "origin/main"], self.s.main)
        commits = []
        for rec in raw.split(end):
            rec = rec.strip("\n")
            if not rec:
                continue
            sha, author, at, subject, body, parents = rec.split(sep)
            commits.append(Commit(sha, author, at, subject, body.strip(), parents.split()))
        return commits

    def changed_files(self, sha: str) -> list[str]:
        c = git(["rev-list", "--parents", "-n1", sha], self.s.main).split()
        base = c[1] if len(c) > 1 else "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # empty tree
        return git(["diff", "--name-only", base, sha], self.s.main).splitlines()

    # -------------------------------------------------------------- branches

    def worktree(self, pid: str) -> Path:
        return self.s.work / pid

    def start_branch(self, pid: str, branch: str) -> Path:
        """A fresh worktree on a new branch from the latest main."""
        with _LOCK:
            self.sync()
            wt = self.worktree(pid)
            if wt.exists():
                self.drop_worktree(pid)
            git(["branch", "-q", "-D", branch], self.s.main, check=False)
            git(["worktree", "add", "-q", "-b", branch, str(wt), "origin/main"], self.s.main)
            return wt

    def open_worktree(self, pid: str, branch: str) -> Path:
        """The worktree for an existing branch (recreated from the remote if needed)."""
        with _LOCK:
            wt = self.worktree(pid)
            if wt.exists():
                return wt
            git(["fetch", "-q", "origin"], self.s.main)
            git(["branch", "-q", "-f", branch, f"origin/{branch}"], self.s.main)
            git(["worktree", "add", "-q", str(wt), branch], self.s.main)
            return wt

    def drop_worktree(self, pid: str) -> None:
        with _LOCK:
            wt = self.worktree(pid)
            git(["worktree", "remove", "--force", str(wt)], self.s.main, check=False)
            if wt.exists():
                shutil.rmtree(wt, ignore_errors=True)
            git(["worktree", "prune"], self.s.main, check=False)

    def commit(self, wt: Path, message: str, author: str, email: str) -> str | None:
        """Commit everything in the worktree; None when nothing changed."""
        with _LOCK:
            git(["add", "-A"], wt)
            if not git(["status", "--porcelain"], wt):
                return None
            git(["-c", f"user.name={author}", "-c", f"user.email={email}", "commit", "-q", "-m", message], wt)
            return git(["rev-parse", "HEAD"], wt)

    def push(self, wt: Path, branch: str) -> None:
        with _LOCK:
            git(["push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}"], wt)

    def diff(self, branch: str) -> dict:
        """What the branch changes against the main it started from."""
        with _LOCK:
            git(["fetch", "-q", "origin"], self.s.main)
            ref = f"origin/{branch}"
            files = []
            for line in git(["diff", "--name-status", f"origin/main...{ref}"], self.s.main).splitlines():
                status, _, path = line.partition("\t")
                files.append({"status": status, "path": path})
            patch = git(["diff", "--unified=3", f"origin/main...{ref}"], self.s.main)
            behind = int(git(["rev-list", "--count", f"{ref}..origin/main"], self.s.main) or 0)
            return {"files": files, "patch": patch, "behind_main": behind}

    def catch_up(self, wt: Path, branch: str) -> str | None:
        """Bring a proposal branch up to date with main, so its checks see what will land.
        Returns None on success, or why it could not (two changes edit the same lines)."""
        with _LOCK:
            git(["fetch", "-q", "origin"], wt)
            if not git(["rev-list", "--count", "HEAD..origin/main"], wt).strip("0\n"):
                return None
            out = subprocess.run(["git", "-c", "user.name=Context Studio", "-c", "user.email=studio@localhost",
                                  "merge", "--no-edit", "-q", "origin/main"], cwd=wt, capture_output=True, text=True, encoding="utf-8")
            if out.returncode != 0:
                git(["merge", "--abort"], wt, check=False)
                return "conflicts with a change that went live after this proposal was drafted"
            git(["push", "-q", "--force", "origin", f"HEAD:refs/heads/{branch}"], wt)
            return None

    def merge(self, branch: str, message: str, author: str, email: str) -> str:
        """Merge a reviewed branch into main (no fast-forward, so history shows the proposal)."""
        with _LOCK, tempfile.TemporaryDirectory(dir=self.s.home) as tmp:
            wt = Path(tmp) / "merge"
            git(["fetch", "-q", "origin"], self.s.main)
            git(["worktree", "add", "-q", "--detach", str(wt), "origin/main"], self.s.main)
            try:
                git(["-c", f"user.name={author}", "-c", f"user.email={email}",
                     "merge", "--no-ff", "-q", "-m", message, f"origin/{branch}"], wt)
                git(["push", "-q", "origin", "HEAD:refs/heads/main"], wt)
                sha = git(["rev-parse", "HEAD"], wt)
            finally:
                git(["worktree", "remove", "--force", str(wt)], self.s.main, check=False)
            git(["push", "-q", "origin", "--delete", branch], self.s.main, check=False)
            self.sync()
            return sha

    def revert_into(self, wt: Path, sha: str) -> None:
        """Apply the inverse of a merged change inside a proposal worktree."""
        parents = git(["rev-list", "--parents", "-n1", sha], wt).split()[1:]
        args = ["revert", "--no-commit"] + (["-m", "1"] if len(parents) > 1 else []) + [sha]
        git(args, wt)
