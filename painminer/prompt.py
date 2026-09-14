"""The judge prompt — the system (§7). Everything else serves it.

SYSTEM_PROMPT started as the brief's appendix prompt verbatim; the operator
has since directed specific tightening — confidence anchors; `build` requires
a stated problem or strong engagement; `arbitrage` removed; `research`/`read`
restricted to ML / systems / startups with no analogical relevance. Keep
changes to it deliberate and operator-directed, not casual "improvements".
The few-shot messages are the negatives (unremarkable text and bare launches
-> []) and one positive (multi-finding extraction). FINDINGS_SCHEMA constrains
decoding to a valid JSON array at decode time (§7.5).
"""

from __future__ import annotations

# --- verbatim system prompt (brief appendix) -------------------------------
SYSTEM_PROMPT = """\
You are reading raw text from public developer and startup communities on
behalf of one person: a machine learning engineer who is building toward
founding a company within a few years, thinks seriously about system
architecture, and follows research.

Your job is to decide whether this text contains anything genuinely useful
to that person, and if so, to extract it.

MOST TEXT CONTAINS NOTHING USEFUL. Returning an empty array is the normal,
correct answer. Do not manufacture a finding because you were asked to look
for one. A plausible-sounding finding that would waste this person's time is
worse than no finding at all.

Things that may be worth surfacing:

- pain: a specific problem, manual workaround, or unmet need someone
  describes in their actual work
- build: something being built — a repo, launch, side project, proof of
  concept. Only surface a build if EITHER the post states the problem the
  builder was solving, OR the submission shows strong community engagement
  (an engagement line is provided in brackets when it applies). A bare launch
  or Show HN that only describes what it is — no stated problem and no strong
  engagement — is NOT a finding: return nothing for it.
- research: a paper, result, or research direction in machine learning,
  systems engineering, or startups worth knowing about. Must be directly
  relevant to one of those; reject analogical or adjacent relevance.
- pattern: an architectural or engineering pattern worth internalising
- read: an article directly relevant to machine learning, systems
  engineering, or startups that is genuinely worth this person's time.
  Reject analogical relevance.
- signal: something shifting — funding, hiring, deprecation, a platform
  changing its terms

This list is not exhaustive. If something is clearly valuable but fits none
of these, use your own short kind label.

One piece of text may contain several findings of different kinds. A post
saying "I was so frustrated with X that I built Y" is both a pain and a
build. Emit both.

Rules for statements:

- Be specific. "Solo freelancers manually copy Stripe payouts into
  accounting spreadsheets every week" is useful. "Invoicing is painful" is
  not.
- Write the statement in your own words, normalized, so that two people
  describing the same underlying thing produce similar statements.
- evidence_quote must be copied verbatim from the source text. Never
  invent, paraphrase, or reconstruct a quote.
- confidence reflects how sure you are this is real and worth surfacing,
  not how confident the author sounded. Anchor it:
  - 0.9 and above only when the finding is specific, verifiable, and
    directly actionable.
  - 0.5 to 0.7 when it is speculation or opinion.

Return a JSON array. Each element:

{
  "kind": "pain",
  "statement": "...",
  "why_it_matters": "one line",
  "domain": "...",
  "evidence_quote": "verbatim from source",
  "confidence": 0.0-1.0
}

Return [] when there is nothing worth surfacing.\
"""

# --- few-shot negatives: unremarkable text that must return [] (brief) -----
# Concrete stand-ins for each category the brief lists as a correct [].
_NEGATIVES = [
    "Honestly tabs vs spaces is settled — spaces, obviously. Anyone using "
    "tabs in 2026 is just wrong and I will die on this hill.",
    "Their docs are genuinely awful. Half the examples are out of date and "
    "the search never finds the page I need. Rant over.",
    "Hiring good engineers right now is brutal, everyone knows this. The "
    "market is just rough all around.",
    "I read that in Ricky Bobby's voice and now I can't unhear it lol",
    "This. Exactly this. Couldn't agree more, well said.",
    "My advice: just keep grinding, stay curious, and network more. It "
    "worked for me and it'll work for you.",
    # Bare launches with no stated problem and no engagement line -> [].
    "Show HN: Pixelpal – a tiny menu bar app that shows a random pixel-art "
    "cat every hour. Built it over a weekend, would love feedback!",
    "Show HN: I made a URL shortener. It's fast, open source, and has a clean "
    "API. Try it out and let me know what you think.",
]

# --- few-shot positive: multi-finding extraction (brief) -------------------
_POSITIVE_SOURCE = (
    "I was so frustrated with GitHub that I rebuilt it as a Chrome extension. "
    "You arrive on your main page, you see your PRs with different statuses, "
    "the CI, the number of comments, the diff. I reverse engineered the "
    "queries and now everything feels instant."
)
_POSITIVE_OUTPUT = [
    {
        "kind": "pain",
        "statement": (
            "GitHub's PR review UI requires separate page loads to see CI "
            "status, the diff, and comments for a pull request"
        ),
        "why_it_matters": (
            "Someone cared enough to reverse-engineer the queries and ship a "
            "faster client"
        ),
        "domain": "developer-tools",
        "evidence_quote": (
            "You arrive on your main page, you see your PRs with different "
            "statuses, the CI, the number of comments, the diff."
        ),
        "confidence": 0.8,
    },
    {
        "kind": "build",
        "statement": (
            "A Chrome extension that reimplements GitHub's PR dashboard with "
            "reverse-engineered queries for an instant-feeling UI"
        ),
        "why_it_matters": (
            "A working proof that GitHub's own client is slow enough to be "
            "worth replacing"
        ),
        "domain": "developer-tools",
        "evidence_quote": (
            "I was so frustrated with GitHub that I rebuilt it as a Chrome "
            "extension."
        ),
        "confidence": 0.8,
    },
]

# --- output schema: a JSON array of findings (open `kind`, §2, §7.2) --------
FINDINGS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string"},
            "statement": {"type": "string"},
            "why_it_matters": {"type": "string"},
            "domain": {"type": "string"},
            "evidence_quote": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": [
            "kind",
            "statement",
            "why_it_matters",
            "domain",
            "evidence_quote",
            "confidence",
        ],
        "additionalProperties": False,
    },
}

REQUIRED_KEYS = set(FINDINGS_SCHEMA["items"]["required"])


def _fewshot_messages() -> list[dict[str, str]]:
    import json

    msgs: list[dict[str, str]] = []
    for text in _NEGATIVES:
        msgs.append({"role": "user", "content": text})
        msgs.append({"role": "assistant", "content": "[]"})
    msgs.append({"role": "user", "content": _POSITIVE_SOURCE})
    msgs.append({"role": "assistant", "content": json.dumps(_POSITIVE_OUTPUT)})
    return msgs


def _engagement_line(
    engagement: dict | None, build_min_points: int, build_min_comments: int
) -> str:
    """A bracketed context line giving submission engagement, for the build
    rule. Empty when there is no engagement (e.g. a comment)."""
    if not engagement:
        return ""
    parts = []
    if engagement.get("points") is not None:
        parts.append(f"{engagement['points']} points")
    if engagement.get("num_comments") is not None:
        parts.append(f"{engagement['num_comments']} comments")
    if not parts:
        return ""
    return (
        f"[Hacker News submission engagement: {', '.join(parts)}. Treat at "
        f"least {build_min_points} points or {build_min_comments} comments as "
        "strong engagement.]\n\n"
    )


def build_messages(
    source_text: str,
    retry_error: str | None = None,
    *,
    engagement: dict | None = None,
    build_min_points: int = 50,
    build_min_comments: int = 30,
) -> list[dict[str, str]]:
    """Assemble the messages for one judge call.

    `engagement` (HN points/num_comments) is injected as a bracketed context
    line so the model can apply the build engagement rule. On a retry after a
    parse/validation failure, the error is appended so the model can correct
    itself (§7.5).
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(_fewshot_messages())
    user = _engagement_line(engagement, build_min_points, build_min_comments) + source_text
    if retry_error:
        user = (
            f"{user}\n\n---\n"
            f"Your previous response was rejected: {retry_error}\n"
            "Return only a valid JSON array matching the schema."
        )
    messages.append({"role": "user", "content": user})
    return messages
