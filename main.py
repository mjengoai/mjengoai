from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Optional
import os
import json
import httpx
import traceback
from dotenv import load_dotenv
from search import handle_query

load_dotenv()

# ── Supabase client ───────────────────────────────────────────────────
from supabase import create_client, Client
_SURL: str = os.environ.get("SUPABASE_URL", "")
_SKEY: str = os.environ.get("SUPABASE_KEY", "")
_sb: Client = create_client(_SURL, _SKEY) if _SURL and _SKEY else None

# ── Firebase Admin ────────────────────────────────────────────────────
try:
    import firebase_admin
    from firebase_admin import credentials, firestore as fs
    if not firebase_admin._apps:
        _creds_raw = os.environ.get("FIREBASE_CREDS", "")
        if _creds_raw:
            cred = credentials.Certificate(json.loads(_creds_raw))
            firebase_admin.initialize_app(cred)
    _fdb = fs.client() if firebase_admin._apps else None
except Exception as _fe:
    print(f"[MjengoAI] Firebase init skipped: {_fe}")
    _fdb = None

# ── WhatsApp + Anthropic env vars ─────────────────────────────────────
WA_TOKEN        = os.environ.get("WHATSAPP_TOKEN", "")
WA_PHONE_ID     = os.environ.get("WHATSAPP_PHONE_ID", "")
WA_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "MjengoAI2026!")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_KEY", "")

# ── MjengoAI WhatsApp system prompt ──────────────────────────────────
WA_SYSTEM_PROMPT = """You are MjengoAI, a smart construction assistant for Kenya built by Mineco Systems.
You help users with:
- Construction material prices in KES (cement, steel, sand, ballast, blocks, timber, roofing)
- Finding artisans, contractors, professionals, and vendors across Kenya
- House planning — plot sizes, bedroom counts, build costs per sqm
- Construction phases from site prep to finishing
- Cost estimates: self-build saves ~30% vs full contract
- Precast products from Caireney/Mineco catalog

Key facts:
- Cement: ~KES 720/50kg bag (13.8 bags per m³ of Class 20 concrete)
- Mason day rate: KES 1,800 | Unskilled labour: KES 900 | Foreman: KES 2,400
- HICB blocks (200mm wall): 15.2 blocks per m²
- Substructure = 15–18% of total build cost

Keep replies SHORT and friendly — this is WhatsApp.
Use bullet points for lists. Use KES for all prices.
If unsure, say so and suggest visiting www.mjengoai.com"""


app = FastAPI(
    title="MjengoAI API",
    description="Generative AI construction search — powered by Claude + Supabase",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.mjengoai.com", "https://mjengoai.com", "*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ════════════════════════════════════════════════════════════════
#  MODELS
# ════════════════════════════════════════════════════════════════

class SearchRequest(BaseModel):
    query:  str
    county: Optional[str] = None
    town:   Optional[str] = None

class SearchResponse(BaseModel):
    answer:  str
    intent:  str
    sources: list
    query:   str

class RegisterRequest(BaseModel):
    full_name:      Optional[str] = ""
    phone:          Optional[str] = ""
    category:       Optional[str] = ""
    specialisation: Optional[str] = ""
    town:           Optional[str] = ""
    fee:            Optional[str] = ""
    email:          Optional[str] = ""
    about:          Optional[str] = ""
    reg_number:     Optional[str] = ""
    status:         Optional[str] = "pending"

class RegisterProfileRequest(BaseModel):
    cat:   str
    phone: Optional[str] = ""
    data:  dict = {}

class SaveProfileRequest(BaseModel):
    phone:       str
    name:        Optional[str] = ""
    category:    Optional[str] = ""
    subCategory: Optional[str] = ""
    location:    Optional[str] = ""
    price:       Optional[str] = ""
    email:       Optional[str] = ""
    about:       Optional[str] = ""
    nca_reg:     Optional[str] = ""


# ════════════════════════════════════════════════════════════════
#  WHATSAPP HELPER FUNCTIONS
# ════════════════════════════════════════════════════════════════

async def wa_send_message(to: str, text: str):
    """Send a WhatsApp text message via Meta Cloud API."""
    url = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text},
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers=headers, json=payload, timeout=10)
        if r.status_code != 200:
            print(f"[WhatsApp] Send error {r.status_code}: {r.text}")


def wa_get_history(phone: str, limit: int = 10) -> list:
    """Fetch last N conversation messages for this phone number."""
    if not _sb:
        return []
    try:
        r = _sb.table("conversations") \
               .select("role,content") \
               .eq("phone", phone) \
               .order("created_at", desc=True) \
               .limit(limit) \
               .execute()
        return list(reversed(r.data or []))
    except Exception as e:
        print(f"[WhatsApp] History error: {e}")
        return []


def wa_save_message(phone: str, role: str, content: str):
    """Save a message to the conversations table."""
    if not _sb:
        return
    try:
        _sb.table("conversations").insert({
            "phone": phone,
            "role": role,
            "content": content,
        }).execute()
    except Exception as e:
        print(f"[WhatsApp] Save error: {e}")


async def wa_ask_claude(history: list, user_message: str) -> str:
    """Send message to Claude and get a reply."""
    messages = history + [{"role": "user", "content": user_message}]
    headers = {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 400,
        "system": WA_SYSTEM_PROMPT,
        "messages": messages,
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=30
            )
            data = r.json()
            return data.get("content", [{}])[0].get("text", "Sorry, I couldn't process that. Try again!")
    except Exception as e:
        print(f"[WhatsApp] Claude error: {e}")
        return "Sorry, I'm having trouble right now. Please try again in a moment."


# ════════════════════════════════════════════════════════════════
#  WHATSAPP ROUTES
# ════════════════════════════════════════════════════════════════

@app.get("/whatsapp")
async def whatsapp_verify(request: Request):
    """Meta webhook verification — called once when you click Verify and Save."""
    params    = dict(request.query_params)
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    print(f"[WhatsApp] Verify request — mode={mode} token={token}")

    if mode == "subscribe" and token == WA_VERIFY_TOKEN:
        print("[WhatsApp] ✅ Webhook verified!")
        return PlainTextResponse(content=challenge, status_code=200)

    print("[WhatsApp] ❌ Verification failed")
    return PlainTextResponse(content="Forbidden", status_code=403)


@app.post("/whatsapp")
async def whatsapp_incoming(request: Request):
    """Receive incoming WhatsApp messages and reply with Claude AI."""
    # Always return 200 immediately so Meta doesn't retry
    try:
        body    = await request.json()
        message = (
            body.get("entry", [{}])[0]
                .get("changes", [{}])[0]
                .get("value", {})
                .get("messages", [{}])[0]
        )

        # Only handle text messages
        if not message or message.get("type") != "text":
            return JSONResponse(content={"ok": True}, status_code=200)

        phone = message.get("from", "")
        text  = message.get("text", {}).get("body", "").strip()

        print(f"[WhatsApp] 📩 From {phone}: {text}")

        # Get history → save user msg → ask Claude → save reply → send
        history = wa_get_history(phone)
        wa_save_message(phone, "user", text)
        reply = await wa_ask_claude(history, text)
        wa_save_message(phone, "assistant", reply)
        await wa_send_message(phone, reply)

        print(f"[WhatsApp] ✅ Replied to {phone}")

    except Exception as e:
        print(f"[WhatsApp] Handler error: {e}\n{traceback.format_exc()}")

    return JSONResponse(content={"ok": True}, status_code=200)


# ════════════════════════════════════════════════════════════════
#  EXISTING ROUTES (unchanged)
# ════════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {"status": "MjengoAI API running", "version": "1.0.0"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping():
    return {"ok": True, "service": "MjengoAI Backend"}


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
        tb = traceback.format_exc()
        print(f"[MjengoAI ERROR] {e}\n{tb}")
        return JSONResponse(
            status_code=500,
            content={
                "error":   str(e),
                "message": "Search failed — check Render logs for details",
                "query":   req.query
            }
        )


@app.post("/register")
async def register(req: RegisterRequest):
    if not _sb:
        return JSONResponse(status_code=503, content={"ok": False, "error": "DB not configured"})
    try:
        _sb.table("registrations").insert({
            "full_name":      req.full_name,
            "phone":          req.phone,
            "category":       req.category,
            "specialisation": req.specialisation,
            "town":           req.town,
            "fee":            req.fee,
            "email":          req.email,
            "about":          req.about,
            "reg_number":     req.reg_number,
            "status":         "pending",
        }).execute()
        return {"ok": True}
    except Exception as e:
        print(f"[register] error: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post("/register-profile")
async def register_profile(req: RegisterProfileRequest):
    if not _sb:
        return JSONResponse(status_code=503, content={"ok": False, "error": "DB not configured"})
    cat   = (req.cat or "").lower()
    data  = req.data or {}
    phone = req.phone or ""
    try:
        location = data.get("location", "")
        county   = location.split(",")[-1].strip() if "," in location else location
        town     = location.split(",")[0].strip()

        if cat == "artisans":
            _sb.table("artisans").insert({
                "name":       data.get("name", ""),
                "trade":      data.get("subCategory", "").lower().replace(" ", "_"),
                "county":     county,
                "town":       town,
                "phone":      phone,
                "daily_rate": int(data.get("price", 0) or 0),
                "verified":   False,
            }).execute()

        elif cat == "professionals":
            _sb.table("professionals").insert({
                "full_name":       data.get("name", ""),
                "profession":      data.get("subCategory", "").lower().replace(" ", "_"),
                "county":          county,
                "town":            town,
                "phone":           phone,
                "consult_fee_kes": int(data.get("price", 0) or 0),
                "bio":             data.get("about", ""),
                "reg_number":      data.get("nca_reg", ""),
                "is_verified":     False,
            }).execute()

        return {"ok": True}
    except Exception as e:
        print(f"[register-profile] error: {e}")
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


@app.post("/save-profile")
async def save_profile(req: SaveProfileRequest):
    if not _fdb:
        return JSONResponse(status_code=503, content={"ok": False, "error": "Firebase not configured"})
    try:
        from firebase_admin import firestore as fs
        _fdb.collection("members").document(req.phone).set({
            "name":        req.name,
            "category":    req.category,
            "subCategory": req.subCategory,
            "location":    req.location,
            "price":       req.price,
            "email":       req.email,
            "about":       req.about,
            "nca_reg":     req.nca_reg,
            "phone":       req.phone,
            "active":      False,
            "verified":    False,
            "rating":      0,
            "reviews":     0,
            "photo_url":   "",
            "joined":      fs.SERVER_TIMESTAMP,
        })
        return {"ok": True}
    except Exception as e:
        print(f"[save-profile] error: {e}")
        return JSONResponse(status_code=500, content={"ok": False})


@app.get("/profile")
async def get_profile(phone: str = Query(...)):
    if not _fdb:
        return JSONResponse(status_code=503, content={})
    try:
        doc = _fdb.collection("members").document(phone).get()
        if doc.exists:
            d = doc.to_dict()
            if d.get("active"):
                d.pop("joined", None)
                return d
        return JSONResponse(status_code=404, content={})
    except Exception as e:
        print(f"[profile] error: {e}")
        return JSONResponse(status_code=500, content={})


@app.get("/contact")
async def get_contact(
    id:   Optional[str] = Query(None),
    name: Optional[str] = Query(None)
):
    if not _sb:
        return JSONResponse(status_code=503, content={})

    def _find(table: str, id_field="id", name_field="name"):
        try:
            if id:
                r = _sb.table(table).select("phone,email").eq(id_field, id).limit(1).execute()
                if r.data:
                    return r.data[0]
            if name:
                r = _sb.table(table).select("phone,email").ilike(name_field, f"%{name}%").limit(1).execute()
                if r.data:
                    return r.data[0]
        except Exception as e:
            print(f"[contact] {table} lookup error: {e}")
        return None

    result = (
        _find("artisans",      id_field="id", name_field="name")      or
        _find("professionals", id_field="id", name_field="full_name") or
        _find("registrations", id_field="id", name_field="full_name")
    )

    if result and result.get("phone"):
        return {
            "phone": result.get("phone", ""),
            "email": result.get("email", ""),
        }
    return JSONResponse(status_code=404, content={})


@app.get("/prices")
async def get_prices():
    if not _sb:
        return JSONResponse(status_code=503, content=[])
    try:
        r = _sb.table("materials") \
               .select("name,price_kes,unit,county") \
               .order("price_date", desc=True) \
               .limit(10) \
               .execute()
        return r.data or []
    except Exception as e:
        print(f"[prices] error: {e}")
        return JSONResponse(status_code=500, content=[])


# ── Run locally: uvicorn main:app --host 0.0.0.0 --port $PORT ──
