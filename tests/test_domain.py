import pytest
from pydantic import ValidationError
from content_evidence.tools import (
    BriefInput,
    EditorialInput,
    RevisionInput,
    LinksInput,
    RefreshInput,
    create_brief,
    review_draft,
    compare_revision,
    suggest_links,
    plan_refresh,
)

SOURCE = {
    "id": "S1",
    "url": "https://example.com/research",
    "title": "Synthetic source",
    "excerpt": "Illustrative findings, not real research.",
}


def test_brief_preserves_evidence():
    r = create_brief(
        BriefInput(
            topic="Pricing workflow",
            audience="Small teams",
            goal="Choose a process",
            sources=[SOURCE],
        )
    )
    assert r["source_ledger"][0] == SOURCE
    assert len(r["outline"]) == 4


def test_citation_and_numeric_review():
    r = review_draft(
        EditorialInput(
            draft="Conversion rose 25%.\nA claim [S99].\nAnother [S1].", sources=[SOURCE]
        )
    )
    assert {x["code"] for x in r["findings"]} == {"unknown_citation", "numeric_claim_review"}
    assert r["publication_gate"] == "human_review_required"


def test_duplicate_source_rejected():
    with pytest.raises(ValidationError):
        EditorialInput(draft="x", sources=[SOURCE, SOURCE])


def test_revision_tracks_removed_citations():
    r = compare_revision(RevisionInput(before="Claim [S1]", after="Changed claim"))
    assert r["removed_citations"] == ["[S1]"]
    assert "-Claim [S1]" in r["diff"]


def test_links_only_use_supplied_targets():
    r = suggest_links(
        LinksInput(
            draft="Pricing research and customer interviews",
            pages=[
                {
                    "url": "https://example.com/pricing",
                    "title": "Pricing research",
                    "summary": "Customer interviews",
                },
                {"url": "https://example.com/cats", "title": "Cats", "summary": "Kittens"},
            ],
        )
    )
    assert len(r["candidates"]) == 1
    assert r["candidates"][0]["url"].endswith("/pricing")


def test_refresh_normalizes_days_and_requires_comparability():
    data = {
        "rows": [
            {
                "url": "https://example.com",
                "previous_clicks": 100,
                "current_clicks": 50,
                "previous_impressions": 1000,
                "current_impressions": 500,
            }
        ],
        "previous_days": 20,
        "current_days": 10,
        "comparable_scope_confirmed": True,
    }
    r = plan_refresh(RefreshInput(**data))
    assert r["metrics"][0]["relative_change"] == 0
    data["comparable_scope_confirmed"] = False
    with pytest.raises(ValueError):
        plan_refresh(RefreshInput(**data))
