from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os
from dotenv import load_dotenv

from search import handle_query

load_dotenv()

app = FastAPI(
    title="MjengoAI API",
    description="Generative AI construction search — powered by OpenAI + Supabase",
    version="1.0.0"
)

# Allow your frontend (MjengoAI website) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten to your domain in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str                        # e.g. "find a mason in Westlands"
    county: Optional[str] = None      # e.g. "Nairobi"
    town:   Optional[str] = None      # e.g. "Westlands"

class SearchResponse(BaseModel):
    answer:  str                      # AI-generated grounded answer
    intent:  str                      # what the AI detected: artisan_search, price_check …
    sources: list                     # raw DB rows that backed the answer
    query:   str                      # echoed back for the frontend


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"status": "MjengoAI API running", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    """
    Main search endpoint.
    Accepts a natural-language query + optional location filters.
    Returns an AI-grounded answer plus the raw DB sources.
    """
    result = await handle_query(
        user_query=req.query,
        county=req.county,
        town=req.town
    )
    return SearchResponse(
        answer=result["answer"],
        intent=result["intent"],
        sources=result["sources"],
        query=req.query
    )


# ── Run locally ───────────────────────────────────────────────────────────────
# uvicorn main:app --reload --port 8000
