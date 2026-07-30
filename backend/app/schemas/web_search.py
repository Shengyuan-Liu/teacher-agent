import uuid

from pydantic import BaseModel, Field, HttpUrl


class WebSearchRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    from_question: str | None = Field(default=None, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    site_filter: list[str] | None = None


class WebSearchCandidate(BaseModel):
    url: str
    title: str
    snippet: str
    domain: str
    recommended: bool
    reason: str | None


class WebSearchResponse(BaseModel):
    queries_used: list[str]
    results: list[WebSearchCandidate]


class WebSearchIngestItem(BaseModel):
    url: HttpUrl
    title: str | None = None


class WebSearchIngestRequest(BaseModel):
    #: the query that surfaced these, stored on each source for provenance
    query: str | None = Field(default=None, max_length=500)
    results: list[WebSearchIngestItem] = Field(min_length=1, max_length=20)


class WebSearchIngestResponse(BaseModel):
    source_ids: list[uuid.UUID]
