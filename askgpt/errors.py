"""Exception hierarchy. Callers catch AskGptError and print str(e)."""


class AskGptError(Exception):
    """Base class for every expected, user-facing failure."""


class CodexNotFound(AskGptError):
    """The Codex CLI binary could not be located."""


class CodexNotAuthenticated(AskGptError):
    """Codex is installed but not logged in to a ChatGPT account."""


class ModelUnavailable(AskGptError):
    """The pinned model was rejected. Never downgrade; fail closed."""


class SecretsDetected(AskGptError):
    """Payload contains material matching a secret pattern."""


class QuotaExhausted(AskGptError):
    """The ChatGPT plan's usage or rate limit was hit."""


class GitCommandFailed(AskGptError):
    """A git command exited non-zero. Distinct from NotAGitRepo: a bad commit
    sha or an unknown base branch is a git failure, not a missing repository,
    and conflating them tells the user to fix the wrong thing."""


class NotAGitRepo(AskGptError):
    """A review was requested outside a git repository."""


class NothingToReview(AskGptError):
    """The resolved review target contains no changes."""


class TranscriptNotFound(AskGptError):
    """No Claude session transcript could be resolved."""
