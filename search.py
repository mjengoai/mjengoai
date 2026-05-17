import os
import json
from openai import AsyncOpenAI
from supabase import create_client, Client

# ── Clients ───────────────────────────────────────────────────────────────────

openai  = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

# ── Intent list ───────────────────────────────────────────────────────────────

INTENTS = [
    "artisan_search",       # find a mason, plumber, electrician…
    "professional_search",  # find an architect, engineer, QS…
    "price_check",          # how much is cement in Nakuru?
    "vendor_search",        # find a hardware shop near me
    "plan_browse",          # show me 3-bedroom bungalow plans
    "cost_estimate",        # how much to build a 3BR house?
    "general",              # anything else
]

# ── System prompt for final answer generation ─────────────────────────────────

SYSTEM_PROMPT = """You are MjengoAI, Kenya's trusted construction assistant.
Your job is to give clients, artisans, and suppliers accurate, helpful answers
about construction in Kenya.

STRICT RULES:
1. Answer ONLY using the verified data provided below. Do not invent prices,
   contacts, or names that are not in the data.
2. If the data does not contain enough information, say so clearly and suggest
   what the user should do next (e.g. "call the vendor directly").
3. Always mention the county/town when discussing prices or artisans.
4. For prices, always state the date the price was recorded.
5. Keep answers concise — 3 to 6 sentences maximum unless a list is needed.
6. You may respond in English or Swahili depending on what language the user used.
7. Never recommend a professional or artisan who is not in the verified data.
"""


# ── Main RAG function ─────────────────────────────────────────────────────────

async def handle_query(user_query: str, county: str = None, town: str = None) -> dict:
    """
    Full RAG pipeline:
      1. Classify intent (gpt-4o-mini — fast + cheap)
      2. Embed the query (text-embedding-3-small)
      3. Retrieve relevant rows from Supabase
      4. Generate grounded answer (gpt-4o)
      5. Log conversation (grows your truth layer)
    """

    # ── 1. Classify intent ────────────────────────────────────────────────────
    intent = await classify_intent(user_query)

    # ── 2. Embed the query ────────────────────────────────────────────────────
    query_vector = await embed_text(user_query)

    # ── 3. Retrieve from Supabase based on intent ─────────────────────────────
    sources = await retrieve(intent, query_vector, county, town, user_query)

    # ── 4. Generate grounded answer ───────────────────────────────────────────
    answer = await generate_answer(user_query, sources, intent)

    # ── 5. Log to conversations table (truth layer grows) ─────────────────────
    await log_conversation(user_query, intent, sources)

    return {
        "answer":  answer,
        "intent":  intent,
        "sources": sources,
    }


# ── Step 1: Intent classification ─────────────────────────────────────────────

async def classify_intent(query: str) -> str:
    resp = await openai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    f"Classify the following construction query into exactly one of these intents: "
                    f"{', '.join(INTENTS)}. "
                    "Reply with the intent string only — no explanation."
                )
            },
            {"role": "user", "content": query}
        ],
        max_tokens=20,
        temperature=0,
    )
    detected = resp.choices[0].message.content.strip().lower()
    # fall back to general if model returns something unexpected
    return detected if detected in INTENTS else "general"


# ── Step 2: Embedding ─────────────────────────────────────────────────────────

async def embed_text(text: str) -> list[float]:
    resp = await openai.embeddings.create(
        model="text-embedding-3-small",
        input=text,
    )
    return resp.data[0].embedding


# ── Step 3: Supabase retrieval ────────────────────────────────────────────────

async def retrieve(
    intent: str,
    query_vector: list[float],
    county: str,
    town: str,
    raw_query: str,
) -> list[dict]:

    rows = []

    # ── Artisan search ────────────────────────────────────────────────────────
    if intent == "artisan_search":
        trade = extract_trade(raw_query)
        result = supabase.rpc("search_artisans", {
            "query_embedding": query_vector,
            "county_filter":   county,
            "trade_filter":    trade,
            "match_count":     5,
        }).execute()
        rows = result.data or []

    # ── Professional search ───────────────────────────────────────────────────
    elif intent == "professional_search":
        result = supabase.rpc("search_professionals", {
            "query_embedding":   query_vector,
            "county_filter":     county,
            "profession_filter": None,
            "match_count":       5,
        }).execute()
        rows = result.data or []

    # ── Price check ───────────────────────────────────────────────────────────
    elif intent == "price_check":
        keyword = extract_material_keyword(raw_query)
        result = supabase.rpc("compare_material_prices", {
            "material_keyword": keyword,
            "county_filter":    county,
        }).execute()
        rows = result.data or []

        # fallback: also search materials table directly
        if not rows:
            result2 = supabase.table("materials") \
                .select("name, price_kes, unit, county, price_date") \
                .ilike("name", f"%{keyword}%") \
                .order("price_date", desc=True) \
                .limit(8).execute()
            rows = result2.data or []

    # ── Vendor search ─────────────────────────────────────────────────────────
    elif intent == "vendor_search":
        result = supabase.rpc("search_vendors", {
            "query_embedding": query_vector,
            "county_filter":   county,
            "type_filter":     None,
            "match_count":     5,
        }).execute()
        rows = result.data or []

    # ── Plan browse ───────────────────────────────────────────────────────────
    elif intent == "plan_browse":
        bedrooms = extract_bedroom_count(raw_query)
        result = supabase.rpc("search_house_plans", {
            "query_embedding":  query_vector,
            "category_filter":  "residential",
            "bedrooms_filter":  bedrooms,
            "max_price_kes":    None,
            "match_count":      5,
        }).execute()
        rows = result.data or []

    # ── Cost estimate — combine materials + artisans ──────────────────────────
    elif intent == "cost_estimate":
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

    # ── General fallback — broad artisan search ───────────────────────────────
    else:
        result = supabase.rpc("search_artisans", {
            "query_embedding": query_vector,
            "county_filter":   county,
            "trade_filter":    None,
            "match_count":     3,
        }).execute()
        rows = result.data or []

    return rows


# ── Step 4: Generate grounded answer ──────────────────────────────────────────

async def generate_answer(query: str, sources: list, intent: str) -> str:
    if sources:
        data_block = json.dumps(sources, indent=2, default=str)
    else:
        data_block = "No matching records found in the MjengoAI database for this query."

    resp = await openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": (
                f"User query: {query}\n\n"
                f"Detected intent: {intent}\n\n"
                f"Verified MjengoAI database results:\n{data_block}"
            )}
        ],
        max_tokens=600,
        temperature=0.3,
    )
    return resp.choices[0].message.content.strip()


# ── Step 5: Log conversation ───────────────────────────────────────────────────

async def log_conversation(query: str, intent: str, sources: list):
    try:
        ids = [str(r.get("id")) for r in sources if r.get("id")]
        supabase.table("conversations").insert({
            "query":          query,
            "intent":         intent,
            "retrieved_ids":  ids,
        }).execute()
    except Exception:
        pass   # logging must never break the main response


# ── Keyword extractors ────────────────────────────────────────────────────────
# Simple keyword matching — replace with NER later as your data grows

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

def extract_trade(query: str) -> str | None:
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
    # return the longest word as a last resort
    words = [w for w in q.split() if len(w) > 4]
    return words[0] if words else "cement"

def extract_bedroom_count(query: str) -> int | None:
    import re
    match = re.search(r'(\d)\s*(?:br|bed|bedroom)', query.lower())
    if match:
        return int(match.group(1))
    for word, num in [("one",1),("two",2),("three",3),("four",4),("five",5)]:
        if word in query.lower():
            return num
    return None
