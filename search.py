"""
MjengoAI Search Engine
Pure OpenAI + Supabase — stable on Render free tier
"""
from __future__ import annotations
import os, json, re
from typing import Optional, List, Dict
from dotenv import load_dotenv
from openai import AsyncOpenAI
from supabase import create_client, Client

load_dotenv()

# ── Clients ───────────────────────────────────────────────────────────────────
openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

# Per-session memory — stores last 10 turns
_memory: Dict[str, List[dict]] = {}

def get_history(session_id: str) -> List[dict]:
    return _memory.get(session_id, [])

def save_history(session_id: str, user_msg: str, bot_reply: str):
    h = _memory.setdefault(session_id, [])
    h.append({"role": "user",      "content": user_msg})
    h.append({"role": "assistant", "content": bot_reply})
    # Keep last 10 turns (20 messages)
    if len(h) > 20:
        _memory[session_id] = h[-20:]

INTENTS = [
    "artisan_search", "professional_search", "price_check",
    "vendor_search", "plan_browse", "cost_estimate", "general"
]

SYSTEM_PROMPT = """You are MjengoAI, Kenya's trusted construction assistant \
powered by Buildersee and Mineco House.
RULES:
1. Answer ONLY using the verified database results provided. Never invent contacts, prices, or names.
2. Always mention county/town for artisans and vendors.
3. For prices, state the date recorded.
4. Keep answers 3-6 sentences unless listing multiple results.
5. Respond in English or Swahili based on the user's language.
6. If no database results, say so clearly and suggest broadening the search.
7. Use conversation history for context (county preferences, past searches).
8. For artisans always include: name, trade, location, daily rate, phone."""


async def handle_query(
    user_query: str,
    county: Optional[str] = None,
    town: Optional[str] = None,
    session_id: str = "default"
) -> dict:

    # 1. Classify intent
    intent = await classify_intent(user_query)

    # 2. Embed query
    try:
        emb = await openai_client.embeddings.create(
            model="text-embedding-3-small", input=user_query
        )
        query_vector = emb.data[0].embedding
        use_vector = True
    except Exception:
        query_vector = []
        use_vector = False

    # 3. Retrieve from Supabase
    sources = await retrieve(intent, query_vector, county, town, user_query, use_vector)

    # 4. Generate answer with memory
    history = get_history(session_id)
    answer  = await generate_answer(user_query, sources, intent, county, history)

    # 5. Save to memory and log
    save_history(session_id, user_query, answer)
    await log_conversation(user_query, intent, sources, session_id)

    return {"answer": answer, "intent": intent, "sources": sources}


async def classify_intent(query: str) -> str:
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content":
                    f"Classify this construction query into ONE of: {', '.join(INTENTS)}. "
                    "Reply with only the intent string."},
                {"role": "user", "content": query}
            ],
            max_tokens=20, temperature=0
        )
        detected = resp.choices[0].message.content.strip().lower()
        return detected if detected in INTENTS else "general"
    except Exception:
        return "general"


async def generate_answer(
    query: str,
    sources: List[dict],
    intent: str,
    county: Optional[str],
    history: List[dict]
) -> str:
    db_results = json.dumps(sources, indent=2, default=str) if sources else (
        "No matching records found in the MjengoAI database yet. "
        "The database is growing — try a broader search or different location."
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Add last 6 history messages for context
    messages.extend(history[-6:])
    messages.append({"role": "user", "content": (
        f"Query: {query}\n"
        f"Intent: {intent}\n"
        f"County preference: {county or 'not specified'}\n\n"
        f"Database results:\n{db_results}\n\n"
        "Answer using only the above data."
    )})
    try:
        resp = await openai_client.chat.completions.create(
            model="gpt-4o", messages=messages,
            max_tokens=600, temperature=0.3
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Search completed. Found {len(sources)} results. Please check the listings below."


async def retrieve(
    intent: str,
    query_vector: list,
    county: Optional[str],
    town: Optional[str],
    raw_query: str,
    use_vector: bool
) -> List[dict]:
    rows: List[dict] = []

    if intent in ("artisan_search", "general", "cost_estimate"):
        trade = extract_trade(raw_query)
        if use_vector:
            try:
                res = supabase.rpc("search_artisans", {
                    "query_embedding": query_vector,
                    "county_filter": county,
                    "trade_filter": trade,
                    "match_count": 5
                }).execute()
                rows = res.data or []
            except Exception:
                pass
        if not rows:
            try:
                q = supabase.table("artisans").select(
                    "id,name,trade,county,town,nca_grade,phone,daily_rate,rating"
                ).eq("verified", True)
                if county: q = q.eq("county", county)
                if trade:  q = q.eq("trade", trade)
                rows = q.limit(6).execute().data or []
            except Exception:
                rows = []

    elif intent == "professional_search":
        if use_vector:
            try:
                res = supabase.rpc("search_professionals", {
                    "query_embedding": query_vector,
                    "county_filter": county,
                    "profession_filter": None,
                    "match_count": 5
                }).execute()
                rows = res.data or []
            except Exception:
                pass
        if not rows:
            try:
                q = supabase.table("professionals").select(
                    "id,full_name,profession,county,reg_body,consult_fee_kes,rating,phone"
                ).eq("is_verified", True)
                if county: q = q.eq("county", county)
                rows = q.limit(5).execute().data or []
            except Exception:
                rows = []

    elif intent == "price_check":
        keyword = extract_material_keyword(raw_query)
        try:
            res = supabase.rpc("compare_material_prices", {
                "material_keyword": keyword,
                "county_filter": county
            }).execute()
            rows = res.data or []
        except Exception:
            pass
        if not rows:
            try:
                rows = supabase.table("materials").select(
                    "name,price_kes,unit,county,price_date"
                ).ilike("name", f"%{keyword}%").order(
                    "price_date", desc=True
                ).limit(8).execute().data or []
            except Exception:
                rows = []

    elif intent == "vendor_search":
        if use_vector:
            try:
                res = supabase.rpc("search_vendors", {
                    "query_embedding": query_vector,
                    "county_filter": county,
                    "type_filter": None,
                    "match_count": 5
                }).execute()
                rows = res.data or []
            except Exception:
                pass
        if not rows:
            try:
                q = supabase.table("vendors").select(
                    "id,business_name,vendor_type,county,town,phone,rating"
                ).eq("is_verified", True)
                if county: q = q.eq("county", county)
                rows = q.limit(5).execute().data or []
            except Exception:
                rows = []

    elif intent == "plan_browse":
        bedrooms = extract_bedroom_count(raw_query)
        if use_vector:
            try:
                res = supabase.rpc("search_house_plans", {
                    "query_embedding": query_vector,
                    "category_filter": "residential",
                    "bedrooms_filter": bedrooms,
                    "max_price_kes": None,
                    "match_count": 5
                }).execute()
                rows = res.data or []
            except Exception:
                pass
        if not rows:
            try:
                q = supabase.table("house_plans").select(
                    "id,plan_code,title,category,bedrooms,area_sqm,price_kes"
                ).eq("is_approved", True)
                if bedrooms: q = q.eq("bedrooms", bedrooms)
                rows = q.limit(5).execute().data or []
            except Exception:
                rows = []

    return rows


async def log_conversation(
    query: str, intent: str,
    sources: List[dict], session_id: str
) -> None:
    try:
        supabase.table("conversations").insert({
            "query":         query,
            "intent":        intent,
            "retrieved_ids": [str(r.get("id")) for r in sources if r.get("id")],
            "session_id":    session_id,
        }).execute()
    except Exception:
        pass


# ── Keyword extractors ────────────────────────────────────────────────────────
TRADES = [
    "mason","plumber","electrician","carpenter","welder","painter",
    "tiles_fixer","tiler","steel_fixer","landscaper","roofer","fundi",
    "foreman","glass"
]
MATERIALS = [
    "cement","steel","iron sheet","roofing","timber","block","sand",
    "ballast","murram","hardcore","tile","paint","pipe","wire","nail",
    "glass","brick","aggregate"
]

def extract_trade(query: str) -> Optional[str]:
    q = query.lower()
    for t in TRADES:
        if t.replace("_"," ") in q or t in q:
            return t
    return None

def extract_material_keyword(query: str) -> str:
    q = query.lower()
    for m in MATERIALS:
        if m in q: return m
    words = [w for w in q.split() if len(w) > 4]
    return words[0] if words else "cement"

def extract_bedroom_count(query: str) -> Optional[int]:
    match = re.search(r'(\d)\s*(?:br|bed|bedroom)', query.lower())
    if match: return int(match.group(1))
    for word, num in [("one",1),("two",2),("three",3),("four",4),("five",5)]:
        if word in query.lower(): return num
    return None
