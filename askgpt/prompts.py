"""Assemble the payloads sent to Codex."""

from pathlib import Path

from .errors import AskGptError

NO_TASK_NOTICE = (
    "No authoritative task statement is available for this change.\n"
    "Do NOT guess what was intended. Review the code on its own terms:\n"
    "correctness, regressions, security, and maintainability."
)


def load_persona(repo_root):
    return (Path(repo_root) / "prompts" / "adversarial-review.md").read_text(
        encoding="utf-8"
    )


PREFIXES = ("feature/", "feat/", "fix/", "build-", "feature-", "feat-", "fix-", "wip-")


def _branch_candidates(branch):
    """The branch name, then it with conventional prefixes stripped."""
    seen = [branch]
    lowered = branch.lower()
    for prefix in PREFIXES:
        if lowered.startswith(prefix) and len(branch) > len(prefix):
            trimmed = branch[len(prefix):]
            if trimmed not in seen:
                seen.append(trimmed)
    return seen


def resolve_task(explicit_text, task_file, spec_dir, branch):
    """Return (task_text_or_None, source_label).

    There is deliberately no "first user message" fallback: in a long session
    covering several tasks it supplies the wrong assignment, and a reviewer
    working from a wrong requirement produces confident, misdirected findings.
    """
    if explicit_text:
        return explicit_text, "--task"

    if task_file:
        try:
            body = Path(task_file).read_text(encoding="utf-8")
        except OSError as error:
            raise AskGptError("Could not read --task-file: " + str(error)) from None
        return body, "--task-file " + str(task_file)

    spec_dir = Path(spec_dir)
    if branch and spec_dir.is_dir():
        # Real branch names rarely appear verbatim in a dated spec filename:
        # "build-ask-gpt" is not a substring of "2026-08-23-ask-gpt-design.md",
        # so exact matching meant this fallback essentially never fired. Try the
        # branch, then progressively strip conventional prefixes. Still requires
        # exactly one match -- ambiguity is declined, never guessed.
        for candidate in _branch_candidates(branch):
            matches = sorted(p for p in spec_dir.glob("*.md") if candidate in p.name)
            if len(matches) == 1:
                return (
                    matches[0].read_text(encoding="utf-8"),
                    "spec " + matches[0].name,
                )

    return None, "none"


def build_review_payload(persona, task, target, accepted_block=""):
    sections = [persona, "", "---", ""]

    if accepted_block:
        # User-authored disposition data from the state directory -- never from
        # the repository, which the persona rightly treats as untrusted.
        sections += [accepted_block, ""]

    if task:
        sections += ["<TASK>", task.strip(), "</TASK>", ""]
    else:
        sections += [NO_TASK_NOTICE, ""]

    sections += [
        "<SCOPE>",
        target.instruction,
        "",
        "Changed files (" + str(len(target.files)) + "):",
    ]
    sections += ["  " + f for f in target.files]
    sections += ["</SCOPE>"]
    return "\n".join(sections)


def build_ask_payload(question, transcript):
    return "\n".join(
        [
            "A Claude Code session is asking for your independent opinion.",
            "",
            "<QUESTION>",
            question.strip(),
            "</QUESTION>",
            "",
            "<CONVERSATION>",
            "Dialogue is verbatim. Tool payloads are omitted, except failed ones.",
            "",
            transcript,
            "</CONVERSATION>",
            "",
            "You may read the repository to check anything asserted above.",
        ]
    )
