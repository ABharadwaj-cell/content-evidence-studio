"""Editorial tools that preserve provenance and expose unsupported claims."""

import difflib
import re
from collections import Counter
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Input):
    id: str = Field(pattern=r"^S[1-9][0-9]{0,3}$")
    url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(min_length=1, max_length=10000)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        p = urlsplit(value)
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
            raise ValueError("Absolute HTTP(S) URL without credentials required")
        return value


class BriefInput(Input):
    topic: str = Field(min_length=3, max_length=300)
    audience: str = Field(min_length=3, max_length=1000)
    goal: str = Field(min_length=3, max_length=1000)
    product_facts: list[str] = Field(default_factory=list, max_length=30)
    sources: list[Source] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def unique(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique")
        if sum(map(len, self.product_facts)) > 20000:
            raise ValueError("Product facts exceed 20000 characters")
        return self


class EditorialInput(Input):
    draft: str = Field(min_length=1, max_length=100000)
    sources: list[Source] = Field(default_factory=list, max_length=40)
    prohibited_terms: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Source IDs must be unique")
        if any(not t.strip() or len(t) > 200 for t in self.prohibited_terms):
            raise ValueError("Prohibited terms must contain 1 to 200 characters")
        return self


class RevisionInput(Input):
    before: str = Field(max_length=100000)
    after: str = Field(max_length=100000)


class LinkPage(Input):
    url: str = Field(max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(max_length=5000)
    _url = field_validator("url")(Source.valid_url.__func__)


class LinksInput(Input):
    draft: str = Field(min_length=1, max_length=50000)
    pages: list[LinkPage] = Field(min_length=1, max_length=100)
    limit: int = Field(default=5, ge=1, le=20)


class RefreshRow(Input):
    url: str = Field(max_length=2048)
    previous_clicks: int = Field(ge=0, strict=True)
    current_clicks: int = Field(ge=0, strict=True)
    previous_impressions: int = Field(ge=0, strict=True)
    current_impressions: int = Field(ge=0, strict=True)
    business_priority: Literal["high", "normal", "low"] = "normal"
    _url = field_validator("url")(Source.valid_url.__func__)

    @model_validator(mode="after")
    def consistent(self):
        if (
            self.previous_clicks > self.previous_impressions
            or self.current_clicks > self.current_impressions
        ):
            raise ValueError("Clicks cannot exceed impressions")
        return self


class RefreshInput(Input):
    rows: list[RefreshRow] = Field(min_length=1, max_length=500)
    previous_days: int = Field(ge=1, le=366)
    current_days: int = Field(ge=1, le=366)
    comparable_scope_confirmed: bool

    @model_validator(mode="after")
    def unique(self):
        if len({r.url for r in self.rows}) != len(self.rows):
            raise ValueError("Duplicate page rows")
        return self


def create_brief(p: BriefInput) -> dict:
    return {
        "kind": "editorial_brief",
        "topic": p.topic,
        "audience": p.audience,
        "goal": p.goal,
        "product_facts_supplied_unverified": p.product_facts,
        "outline": [
            {
                "section": "Reader's problem",
                "instruction": "State the audience's decision and constraints; avoid invented prevalence.",
            },
            {
                "section": "Evidence and options",
                "instruction": "Compare alternatives using supplied excerpts; cite source IDs after claims.",
            },
            {
                "section": "Practical steps",
                "instruction": "Explain prerequisites, steps, tradeoffs and verification.",
            },
            {
                "section": "Limitations and next action",
                "instruction": "List unknowns and an action aligned with the goal.",
            },
        ],
        "source_ledger": [s.model_dump() for s in p.sources],
        "acceptance_checks": [
            "Every material factual claim reviewed against source text",
            "No invented customer proof or metrics",
            "Citations use [S1] style IDs",
            "Clear next action appropriate to the audience",
        ],
        "limitations": [
            "A planning deliverable, not a generated finished article.",
            "Supplied excerpts and product facts are not independently verified.",
            "Use the OpenAI agent runner to draft from this brief; review its claims manually.",
        ],
    }


def review_draft(p: EditorialInput) -> dict:
    known = {s.id for s in p.sources}
    cited = set(re.findall(r"\[(S\d+)\]", p.draft))
    findings = []
    # Match editorial terms as complete words or phrases, not substrings of unrelated words.
    prohibited_patterns = [
        (term, re.compile(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", re.I))
        for term in p.prohibited_terms
    ]
    for source in sorted(cited - known):
        findings.append(
            {
                "code": "unknown_citation",
                "severity": "high",
                "evidence": source,
                "action": "Supply the source or remove the unsupported citation.",
            }
        )
    for number, line in enumerate(p.draft.splitlines(), 1):
        plain = re.sub(r"\[S\d+\]", "", line)
        if re.search(
            r"\b\d+(?:\.\d+)?\s*(?:%|percent|million|billion|times)\b|\d+%", plain, re.I
        ) and not re.search(r"\[S\d+\]", line):
            findings.append(
                {
                    "code": "numeric_claim_review",
                    "severity": "medium",
                    "line": number,
                    "evidence": line[:500],
                    "action": "Verify this quantitative claim and attach supporting evidence.",
                }
            )
        for term, pattern in prohibited_patterns:
            if pattern.search(line):
                findings.append(
                    {
                        "code": "prohibited_term",
                        "severity": "medium",
                        "line": number,
                        "evidence": term,
                        "action": "Review against editorial policy.",
                    }
                )
    headings = re.findall(r"^#{1,6}\s+(.+)$", p.draft, re.M)
    duplicates = [
        h for h, count in Counter(h.casefold().strip() for h in headings).items() if count > 1
    ]
    for h in duplicates:
        findings.append(
            {
                "code": "duplicate_heading",
                "severity": "low",
                "evidence": h,
                "action": "Differentiate sections if they serve distinct purposes.",
            }
        )
    return {
        "kind": "editorial_review",
        "findings": findings,
        "word_count": len(p.draft.split()),
        "known_citations": sorted(cited & known),
        "unused_sources": sorted(known - cited),
        "publication_gate": "human_review_required",
        "limitations": [
            "Citation existence does not prove entailment or source quality.",
            "Pattern checks can miss unsupported claims and produce false positives.",
            "No AI-detection, humanity score, keyword-density target or ranking prediction.",
        ],
    }


def compare_revision(p: RevisionInput) -> dict:
    before_cites = set(re.findall(r"\[S\d+\]", p.before))
    after_cites = set(re.findall(r"\[S\d+\]", p.after))
    return {
        "kind": "revision_diff",
        "diff": "\n".join(
            difflib.unified_diff(
                p.before.splitlines(),
                p.after.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        ),
        "removed_citations": sorted(before_cites - after_cites),
        "added_citations": sorted(after_cites - before_cites),
        "word_change": len(p.after.split()) - len(p.before.split()),
        "limitations": [
            "Text differences do not establish factual correctness; review changed claims and removed citations."
        ],
    }


STOP = {"the", "and", "with", "from", "that", "this", "your", "for", "are", "how"}


def tokens(text):
    return set(re.findall(r"[a-z]{3,}", text.casefold())) - STOP


def suggest_links(p: LinksInput) -> dict:
    source = tokens(p.draft)
    candidates = []
    for page in p.pages:
        target = tokens(page.title + " " + page.summary)
        overlap = source & target
        if overlap:
            candidates.append(
                {
                    "url": page.url,
                    "title": page.title,
                    "shared_terms": sorted(overlap),
                    "lexical_overlap": len(overlap) / len(source | target),
                }
            )
    candidates.sort(key=lambda r: (-r["lexical_overlap"], r["url"]))
    return {
        "kind": "internal_link_candidates",
        "candidates": candidates[: p.limit],
        "limitations": [
            "English lexical overlap is a heuristic, not semantic relevance or SEO value.",
            "Only supplied URLs are suggested. Destination status and existing links are not checked.",
        ],
    }


def plan_refresh(p: RefreshInput) -> dict:
    if not p.comparable_scope_confirmed:
        raise ValueError("Confirm matching filters, source, timezone and comparable periods first")
    metrics = []
    for r in p.rows:
        previous = r.previous_clicks / p.previous_days
        current = r.current_clicks / p.current_days
        change = (current - previous) / previous if previous else None
        action = "investigate_decline" if current < previous else "monitor"
        metrics.append(
            {
                "url": r.url,
                "previous_clicks_per_day": previous,
                "current_clicks_per_day": current,
                "relative_change": change,
                "clicks_per_day_lost": max(0, previous - current),
                "business_priority": r.business_priority,
                "action": action,
            }
        )
    priority = {"high": 0, "normal": 1, "low": 2}
    metrics.sort(
        key=lambda r: (priority[r["business_priority"]], -r["clicks_per_day_lost"], r["url"])
    )
    return {
        "kind": "content_refresh_queue",
        "metrics": metrics,
        "limitations": [
            "Daily normalization does not remove seasonality, weekday or algorithm effects.",
            "Decline is not proof of content decay; inspect indexing, demand, SERP and conversion changes.",
            "Do not automatically delete or merge pages from this queue.",
        ],
    }


REGISTRY = {
    "create_brief": (
        BriefInput,
        create_brief,
        "Create an evidence ledger and actionable editorial brief from supplied sources.",
    ),
    "review_draft": (
        EditorialInput,
        review_draft,
        "Flag unknown citations, quantitative claims and editorial policy issues.",
    ),
    "compare_revision": (
        RevisionInput,
        compare_revision,
        "Produce an inspectable revision diff and citation-change ledger.",
    ),
    "suggest_internal_links": (
        LinksInput,
        suggest_links,
        "Rank supplied internal pages by explicit lexical overlap.",
    ),
    "plan_content_refresh": (
        RefreshInput,
        plan_refresh,
        "Prioritize comparable page performance changes for investigation.",
    ),
}
