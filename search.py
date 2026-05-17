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
    "artisan_search", "professional_search", "price_check",
    "vendor_search", "plan_browse", "cost_estimate", "general",
]

SYSTEM_PROMPT = """You are MjengoAI, Kenya's trusted construction assistant.
Answer ONLY using the verified data provided. Do not invent prices or contacts.
Always mention county/town for artisans. Keep answers to 3-6 sentences.
Respond in English or Swahili based on the user's language."""


async def handle_query(
    user_query: str,
    county: Optional[str] = None,
    town: Optional[str] = None
) -> dict:
    try:
        intent = await classify_intent(user_query)
    except Exception:
        intent = "general"

    try:
        query_vector = await embed_text(user_query)
        use_vector = True
    except Exception:
        query_vector = []
        use_vector = False

    sources = await retrieve(intent, query_vector, county, town, user_query, use_vector)
    answer  = await generate_answer(user_query, sources, intent)
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
                    "Reply with the intent string only — no punctuation, no explanation."
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
    use_vector: bool,
) -> List[dict]:
    """
    Tries vector search first (if embeddings available).
    Falls back to simple keyword/filter queries on the raw tables.
    All exceptions are caught so a DB error never causes a 500.
    """
    rows: List[dict] = []

    # ── Artisan search ────────────────────────────────────────────────────────
    if intent in ("artisan_search", "general", "cost_estimate"):
        trade = extract_trade(raw_query)
        # Try vector search function first
        if use_vector and query_vector:
            try:
                res = supabase.rpc("search_artisans", {
                    "query_embedding": query_vector,
                    "county_filter":   county,
                    "trade_filter":    trade,
                    "match_count":     5,
                }).execute()
                rows = res.data or []
            except Exception:
                pass

        # Fallback: direct table query (no pgvector needed)
        if not rows:
            try:
                q = supabase.table("artisans") \
                    .select("id,name,trade,county,town,nca_grade,phone,daily_rate,rating") \
                    .eq("verified", True)
                if county:
                    q = q.eq("county", county)
                if trade:
                    q = q.eq("trade", trade)
                res2 = q.limit(6).execute()
                rows = res2.data or []
            except Exception:
                rows = []

    # ── Professional search ───────────────────────────────────────────────────
    elif intent == "professional_search":
        if use_vector and query_vector:
            try:
                res = supabase.rpc("search_professionals", {
                    "query_embedding":   query_vector,
                    "county_filter":     county,
                    "profession_filter": None,
                    "match_count":       5,
                }).execute()
                rows = res.data or []
            except Exception:
                pass

        if not rows:
            try:
                q = supabase.table("professionals") \
                    .select("id,full_name,profession,county,reg_body,consult_fee_kes,rating") \
                    .eq("is_verified", True)
                if county:
                    q = q.eq("county", county)
                res2 = q.limit(6).execute()
                rows = res2.data or []
            except Exception:
                rows = []

    # ── Price check ───────────────────────────────────────────────────────────
    elif intent == "price_check":
        keyword = extract_material_keyword(raw_query)
        try:
            res = supabase.rpc("compare_material_prices", {
                "material_keyword": keyword,
                "county_filter":    county,
            }).execute()
            rows = res.data or []
        except Exception:
            pass

        if not rows:
            try:
                q = supabase.table("materials") \
                    .select("name,price_kes,unit,county,price_date") \
                    .ilike("name", f"%{keyword}%") \
                    .order("price_date", desc=True) \
                    .limit(8)
                res2 = q.execute()
                rows = res2.data or []
            except Exception:
                rows = []

    # ── Vendor search ─────────────────────────────────────────────────────────
    elif intent == "vendor_search":
        if use_vector and query_vector:
            try:
                res = supabase.rpc("search_vendors", {
                    "query_embedding": query_vector,
                    "county_filter":   county,
                    "type_filter":     None,
                    "match_count":     5,
                }).execute()
                rows = res.data or []
            except Exception:
                pass

        if not rows:
            try:
                q = supabase.table("vendors") \
                    .select("id,business_name,vendor_type,county,town,phone,rating") \
                    .eq("is_verified", True)
                if county:
                    q = q.eq("county", county)
                res2 = q.limit(6).execute()
                rows = res2.data or []
            except Exception:
                rows = []

    # ── Plan browse ───────────────────────────────────────────────────────────
    elif intent == "plan_browse":
        bedrooms = extract_bedroom_count(raw_query)
        if use_vector and query_vector:
            try:
                res = supabase.rpc("search_house_plans", {
                    "query_embedding":  query_vector,
                    "category_filter":  "residential",
                    "bedrooms_filter":  bedrooms,
                    "max_price_kes":    None,
                    "match_count":      5,
                }).execute()
                rows = res.data or []
            except Exception:
                pass

        if not rows:
            try:
                q = supabase.table("house_plans") \
                    .select("id,plan_code,title,category,bedrooms,area_sqm,price_kes") \
                    .eq("is_approved", True)
                if bedrooms:
                    q = q.eq("bedrooms", bedrooms)
                res2 = q.limit(6).execute()
                rows = res2.data or []
            except Exception:
                rows = []

    return rows


async def generate_answer(query: str, sources: List[dict], intent: str) -> str:
    if sources:
        data_block = json.dumps(sources, indent=2, default=str)
    else:
        data_block = (
            "No matching records found in the MjengoAI database yet. "
            "The database is growing — please try a broader search or check back soon."
        )

    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"User query: {query}\n"
                f"Intent: {intent}\n"
                f"Database results:\n{data_block}"
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
        pass  # logging never breaks the response


# ── Keyword extractors ────────────────────────────────────────────────────────

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
