"""Context Studio: the review app over git.

Every screen reads the content repo; every save becomes a proposal on a branch;
owners approve; only approved changes reach `main`, which is what the engine serves.
The Studio keeps no data of its own beyond the git repo and review records.
"""

__version__ = "0.1.0"
