from __future__ import annotations
import os
import json
from typing import Optional, List
from openai import AsyncOpenAI
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ── Clients ───────────────────────────────────────────────────────────────────

openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

INTENTS = [
    "artisan_search",
    "professional_search",
    "price_check",
    "vendor_search",
    "plan_browse",
    "cost_estimate",
    "general",
]

SYSTEM_PROMPT = """You are MjengoAI, Kenya's trusted construction assistant.
Your job is to give clients, artisans, and suppliers accurate, helpful answers
about construction in Kenya.

STRICT RULES:
1. Answer ONLY using the verified data provided below. Do not invent prices,
   contacts, or names that are not in the data.
2. If the data does not contain enough information, say so clearly and suggest
   what the user should do next.
3. Always mention the county/town when discussing prices or artisans.
4. For prices, always state the date the price was recorded.
5. Keep answers concise — 3 to 6 sentences maximum unless a list is needed.
6. You may respond in English or Swahili depending on what language the user used.
7. Never recommend a professional or artisan who is not in the verified data.
"""


async def handle_query(
    user_query: str,
    county: Optional[str] = None,
    town: Optional[str] = None
) -> dict:
    intent       = await classify_intent(user_query)
    query_vector = await embed_text(user_query)
    sources      = await retrieve(intent, query_vector, county, town, user_query)
    answer       = await generate_answer(user_query, sources, intent)
    await log_conversation(user_query, intent, sources)
    return {"answer": answer, "intent": intent, "sources": sources}


async def classify_intent(query: str) -> str:
    resp = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    f"Classify this construction query into one of: {', '.join(INTENTS)}. "
                    "Reply with the intent string only."
                )
            },
            {"role": "user", "content": query}
        ],
        max_tokens=20,
        temperature=0,
    )
    detected = resp.choices[0].message.content.strip().lower()
    return detected if detected in INTENTS else "general"


async def embed_text(text: str) -> List[float]:
    resp = await openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return resp.data[0].embedding


async def retrieve(
    intent: str,
    query_vector: List[float],
    county: Optional[str],
    town: Optional[str],
    raw_query: str,
) -> List[dict]:
    rows: List[dict] = []

    if intent == "artisan_search":
        trade = extract_trade(raw_query)
        try:
            result = supabase.rpc("search_artisans", {
                "query_embedding": query_vector,
                "county_filter":   county,
                "trade_filter":    trade,
                "match_count":     5,
            }).execute()
            rows = result.data or []
        except Exception:
            rows = []

    elif intent == "professional_search":
        try:
            result = supabase.rpc("search_professionals", {
                "query_embedding":   query_vector,
                "county_filter":     county,
                "profession_filter": None,
                "match_count":       5,
            }).execute()
            rows = result.data or []
        except Exception:
            rows = []

    elif intent == "price_check":
        keyword = extract_material_keyword(raw_query)
        try:
            result = supabase.rpc("compare_material_prices", {
                "material_keyword": keyword,
                "county_filter":    county,
            }).execute()
            rows = result.data or []
        except Exception:
            rows = []
        if not rows:
            try:
                result2 = supabase.table("materials") \
                    .select("name, price_kes, unit, county, price_date") \
                    .ilike("name", f"%{keyword}%") \
                    .order("price_date", desc=True) \
                    .limit(8).execute()
                rows = result2.data or []
            except Exception:
                rows = []

    elif intent == "vendor_search":
        try:
            result = supabase.rpc("search_vendors", {
                "query_embedding": query_vector,
                "county_filter":   county,
                "type_filter":     None,
                "match_count":     5,
            }).execute()
            rows = result.data or []
        except Exception:
            rows = []

    elif intent == "plan_browse":
        bedrooms = extract_bedroom_count(raw_query)
        try:
            result = supabase.rpc("search_house_plans", {
                "query_embedding":  query_vector,
                "category_filter":  "residential",
                "bedrooms_filter":  bedrooms,
                "max_price_kes":    None,
                "match_count":      5,
            }).execute()
            rows = result.data or []
        except Exception:
            rows = []

    elif intent == "cost_estimate":
        try:
            r1 = supabase.rpc("search_artisans", {
                "query_embedding": query_vector,
                "county_filter":   county,
                "trade_filter":    None,
                "match_count":     3,
            }).execute()
            r2 = supabase.table("materials") \
                .select("name, price_kes, unit, county") \
                .order("price_date", desc=True) \
                .limit(6).execute()
            rows = (r1.data or []) + (r2.data or [])
        except Exception:
            rows = []

    else:
        try:
            result = supabase.rpc("search_artisans", {
                "query_embedding": query_vector,
                "county_filter":   county,
                "trade_filter":    None,
                "match_count":     3,
            }).execute()
            rows = result.data or []
        except Exception:
            rows = []

    return rows


async def generate_answer(query: str, sources: List[dict], intent: str) -> str:
    if sources:
        data_block = json.dumps(sources, indent=2, default=str)
    else:
        data_block = "No matching records found in the MjengoAI database for this query."

    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"User query: {query}\n\n"
                f"Detected intent: {intent}\n\n"
                f"Verified MjengoAI database results:\n{data_block}"
            )}
        ],
        max_tokens=600,
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


async def log_conversation(query: str, intent: str, sources: List[dict]) -> None:
    try:
        ids = [str(r.get("id")) for r in sources if r.get("id")]
        supabase.table("conversations").insert({
            "query":         query,
            "intent":        intent,
            "retrieved_ids": ids,
        }).execute()
    except Exception:
        pass


TRADES = [
    "mason", "plumber", "electrician", "carpenter", "welder",
    "painter", "tiles fixer", "tiler", "steel fixer", "landscaper",
    "roofer", "fundi"
]

MATERIALS = [
    "cement", "steel", "iron sheet", "roofing", "timber", "block",
    "sand", "ballast", "murram", "hardcore", "tile", "paint",
    "pipe", "wire", "nail", "glass", "brick"
]


def extract_trade(query: str) -> Optional[str]:
    q = query.lower()
    for t in TRADES:
        if t in q:
            return t.replace(" ", "_")
    return None


def extract_material_keyword(query: str) -> str:
    q = query.lower()
    for m in MATERIALS:
        if m in q:
            return m
    words = [w for w in q.split() if len(w) > 4]
    return words[0] if words else "cement"


def extract_bedroom_count(query: str) -> Optional[int]:
    import re
    match = re.search(r'(\d)\s*(?:br|bed|bedroom)', query.lower())
    if match:
        return int(match.group(1))
    for word, num in [("one", 1), ("two", 2), ("three", 3), ("four", 4), ("five", 5)]:
        if word in query.lower():
            return num
    return None
