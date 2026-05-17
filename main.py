from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os
import traceback
from dotenv import load_dotenv

from search import handle_query

load_dotenv()

app = FastAPI(
    title="MjengoAI API",
    description="Generative AI construction search — powered by OpenAI + Supabase",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class SearchRequest(BaseModel):
    query: str
    county: Optional[str] = None
    town:   Optional[str] = None


class SearchResponse(BaseModel):
    answer:  str
    intent:  str
    sources: list
    query:   str


@app.get("/")
def root():
    return {"status": "MjengoAI API running", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search")
async def search(req: SearchRequest, request: Request):
    try:
        result = await handle_query(
            user_query=req.query,
            county=req.county,
            town=req.town
        )
        return {
            "answer":  result["answer"],
            "intent":  result["intent"],
            "sources": result["sources"],
            "query":   req.query
        }
    except Exception as e:
        # Log full traceback to Render logs
        tb = traceback.format_exc()
        print(f"[MjengoAI ERROR] {e}\n{tb}")
        # Return helpful error instead of plain 500
        return JSONResponse(
            status_code=500,
            content={
                "error":   str(e),
                "message": "Search failed — check Render logs for details",
                "query":   req.query
            }
        )


# uvicorn main:app --host 0.0.0.0 --port $PORT
