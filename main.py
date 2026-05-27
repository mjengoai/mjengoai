"""
MjengoAI FastAPI Backend
Endpoints: /search  /chat  /register  /register-profile  /ping  /health
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List
import os, traceback, json
from dotenv import load_dotenv
from search import handle_query

load_dotenv()

# ── Supabase client ────────────────────────────────────────────────────────────
from supabase import create_client, Client
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"]
)

# ── OpenAI client ──────────────────────────────────────────────────────────────
from openai import AsyncOpenAI
openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

app = FastAPI(title="MjengoAI API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════════════════

class SearchRequest(BaseModel):
    query:  str
    county: Optional[str] = None
    town:   Optional[str] = None
    session_id: Optional[str] = "default"

class ChatMessage(BaseModel):
    role:    str
    content: str

class ChatRequest(BaseModel):
    messages:   List[ChatMessage]
    max_tokens: Optional[int] = 250
    stream:     Optional[bool] = False

class RegisterRequest(BaseModel):
    full_name:      Optional[str] = ''
    phone:          Optional[str] = ''
    category:       Optional[str] = ''
    specialisation: Optional[str] = ''
    town:           Optional[str] = ''
    fee:            Optional[str] = ''
    email:          Optional[str] = ''
    about:          Optional[str] = ''
    reg_number:     Optional[str] = ''
    status:         Optional[str] = 'pending'

class RegisterProfileRequest(BaseModel):
    cat:   Optional[str] = ''
    phone: Optional[str] = ''
    data:  Optional[dict] = {}


# ══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/")
def root():
    return {"status": "MjengoAI API running", "version": "2.0.0"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/ping")
def ping():
    """Keep-alive ping from GitHub Actions and index.html"""
    return {"status": "pong", "service": "mjengoai"}


# ── AI Search ─────────────────────────────────────────────────────────────────
@app.post("/search")
async def search(req: SearchRequest):
    try:
        result = await handle_query(
            user_query=req.query,
            county=req.county,
            town=req.town,
            session_id=req.session_id or "default"
        )
        return {
            "answer":  result["answer"],
            "intent":  result["intent"],
            "sources": result["sources"],
            "query":   req.query
        }
    except Exception as e:
        tb = traceback.format_exc()
        print(f"[MjengoAI /search ERROR] {e}\n{tb}")
        return JSONResponse(status_code=500, content={
            "error": str(e),
            "answer": "I could not process that search right now. Please try again.",
            "intent": "general",
            "sources": [],
            "query": req.query
        })


# ── AI Chat  ──────────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Conversational AI chat — called by the MjengoAI Assistant widget.
    Receives full message history, returns assistant reply.
    """
    try:
        messages = [{"role": m.role, "content": m.content} for m in req.messages]

        resp = await openai_client.chat.completions.create(
            model="gpt-4o-mini",          # fast + cheap for chat
            messages=messages,
            max_tokens=req.max_tokens or 250,
            temperature=0.5,
        )
        text = resp.choices[0].message.content.strip()
        return {"content": text}

    except Exception as e:
        print(f"[MjengoAI /chat ERROR] {e}")
        return JSONResponse(status_code=500, content={
            "content": (
                "I'm having a moment — please try again! "
                "You can also search using the main bar above."
            )
        })


# ── Registration — saves to Supabase ─────────────────────────────────────────
@app.post("/register")
async def register(req: RegisterRequest):
    """
    Saves signup to the registrations table.
    Called by mjRegister() in index.html.
    """
    try:
        payload = {
            "full_name":      req.full_name,
            "phone":          req.phone,
            "category":       req.category,
            "specialisation": req.specialisation,
            "town":           req.town,
            "fee":            req.fee,
            "email":          req.email,
            "about":          req.about,
            "reg_number":     req.reg_number,
            "status":         "pending"
        }
        result = supabase.table("registrations").insert(payload).execute()
        print(f"[MjengoAI /register] Saved: {req.full_name} ({req.category})")
        return {"ok": True, "id": result.data[0]["id"] if result.data else None}

    except Exception as e:
        print(f"[MjengoAI /register ERROR] {e}")
        return JSONResponse(status_code=500, content={
            "ok": False,
            "error": str(e)
        })


# ── Profile — saves to artisans / professionals table ─────────────────────────
@app.post("/register-profile")
async def register_profile(req: RegisterProfileRequest):
    """
    Saves the signup into the correct category table
    (artisans, professionals, vendors) after registration.
    Non-blocking — failures are ignored by the frontend.
    """
    try:
        cat    = (req.cat or '').lower()
        data   = req.data or {}
        phone  = req.phone or ''
        name   = data.get('name', '')
        sub    = data.get('subCategory', '')
        loc    = data.get('location', '')
        price  = data.get('price', '')
        about  = data.get('about', '')
        nca    = data.get('nca_reg', '')

        county = loc.split(',')[-1].strip() if ',' in loc else loc
        town   = loc.split(',')[0].strip()  if ',' in loc else loc

        if cat == 'artisans':
            supabase.table("artisans").insert({
                "name":       name,
                "trade":      sub.lower().replace(' ', '_'),
                "county":     county,
                "town":       town,
                "phone":      phone,
                "daily_rate": int(''.join(filter(str.isdigit, price[:6])) or 0),
                "verified":   False
            }).execute()

        elif cat == 'professionals':
            supabase.table("professionals").insert({
                "full_name":       name,
                "profession":      sub.lower().replace(' ', '_'),
                "county":          county,
                "town":            town,
                "phone":           phone,
                "consult_fee_kes": int(''.join(filter(str.isdigit, price[:6])) or 0),
                "bio":             about,
                "reg_number":      nca,
                "is_verified":     False
            }).execute()

        elif cat == 'vendors':
            supabase.table("vendors").insert({
                "business_name": name,
                "vendor_type":   sub.lower().replace(' ', '_'),
                "county":        county,
                "town":          town,
                "phone":         phone,
                "description":   about,
                "is_verified":   False
            }).execute()

        return {"ok": True}

    except Exception as e:
        print(f"[MjengoAI /register-profile ERROR] {e}")
        return JSONResponse(status_code=200, content={"ok": False, "error": str(e)})


# ── WhatsApp Bot Webhook ───────────────────────────────────────────────────────

class WhatsAppMessage(BaseModel):
    Body:        Optional[str] = ''
    From:        Optional[str] = ''
    To:          Optional[str] = ''
    ProfileName: Optional[str] = ''

STATIC_PRICES = """Current Kenya construction prices:
• Cement 50kg: KES 720 (Nairobi), KES 695 (Nakuru), KES 755 (Mombasa)
• Steel rod 12mm: KES 680/m
• Roofing sheet: KES 1,250/sheet
• Hollow block: KES 48/block
• River sand: KES 2,100/tonne
• Murram: KES 4,200/lorry
• Timber 2x4\": KES 320/m treated
• BRC mesh: KES 8,500/roll"""

@app.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    WhatsApp Bot webhook — handles incoming messages from Twilio or Meta.
    Accepts both JSON and form data.
    """
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body = await request.json()
            msg_body = body.get("Body", body.get("message", body.get("text", "")))
            from_num  = body.get("From", body.get("from", ""))
        else:
            # Twilio sends form-encoded data
            form = await request.form()
            msg_body = form.get("Body", "")
            from_num  = form.get("From", "")

        msg = (msg_body or "").strip().lower()
        print(f"[MjengoAI WhatsApp] From: {from_num} | Msg: {msg_body}")

        # ── Route message ─────────────────────────────────────────────────────
        if any(w in msg for w in ["price","bei","cement","steel","timber","roofing","block","sand","ngapi"]):
            reply = STATIC_PRICES
        elif any(w in msg for w in ["mason","fundi","plumber","electr","carpenter","welder","artisan"]):
            reply = (
                "🔨 Find vetted artisans at www.mjengoai.com\n\n"
                "Top picks:\n"
                "• Joseph Kamau — Mason, Nairobi, KES 1,600/day ⭐4.8\n"
                "• Peter Ngugi — Plumber, Nairobi, KES 1,800/day ⭐4.8\n"
                "• Grace Njeri — Electrician (ERC), Nairobi, KES 2,200/day ⭐4.9\n"
                "• Samuel Gitau — Carpenter, Nairobi, KES 1,800/day ⭐4.9\n\n"
                "Reply with your county for local results."
            )
        elif any(w in msg for w in ["plan","bedroom","bungalow","house","design","nyumba"]):
            reply = (
                "🏠 MjengoAI House Plans (Mineco House Repository)\n\n"
                "• 3BR Bungalow Type B — KES 15,000 (full BOQ)\n"
                "• 2BR Maisonette — KES 9,500\n"
                "• 4BR Executive Villa — KES 35,000\n\n"
                "All include working drawings + material schedule.\n"
                "Browse all plans: www.mjengoai.com"
            )
        elif any(w in msg for w in ["architect","engineer","qs","professional","quantity"]):
            reply = (
                "📐 Top Professionals on MjengoAI:\n\n"
                "• Sarah Kamau — Architect (BORAQS), Nairobi, KES 12,000/consult\n"
                "• Mate Njiru — Structural Engineer (EBK), Nairobi, KES 15,000/consult\n"
                "• James Odhiambo — QS (IQSK), Nairobi, KES 8,000/BOQ\n\n"
                "Find 1,800+ professionals: www.mjengoai.com"
            )
        elif any(w in msg for w in ["hardware","vendor","supplier","shop","duka"]):
            reply = (
                "🏪 Top Hardware Vendors:\n\n"
                "• Tuff Foam Hardware — Nairobi Industrial Area\n"
                "• Coast Hardware — Mombasa\n"
                "• BuildMart Nakuru — Nakuru Town\n\n"
                "Compare 850+ suppliers: www.mjengoai.com"
            )
        elif any(w in msg for w in ["register","join","jiandikishe","sign up","list"]):
            reply = (
                "✅ Join MjengoAI in 2 minutes!\n\n"
                "1. Visit www.mjengoai.com\n"
                "2. Click 'For Pros' in the top menu\n"
                "3. Enter your phone number\n"
                "4. Verify OTP + complete your profile\n\n"
                "Your listing goes live within 24 hours after review."
            )
        elif any(w in msg for w in ["hi","hello","habari","hujambo","hey","start","menu","help","msaada"]):
            reply = (
                "👋 Habari! Welcome to MjengoAI Kenya's #1 construction directory!\n\n"
                "I can help you with:\n"
                "💰 *Material prices* — type 'prices'\n"
                "🔨 *Find artisans* — type 'mason' or trade\n"
                "🏠 *House plans* — type 'plans'\n"
                "📐 *Professionals* — type 'architect'\n"
                "🏪 *Vendors* — type 'hardware'\n"
                "✅ *Join directory* — type 'register'\n\n"
                "Or visit: www.mjengoai.com"
            )
        else:
            # Use AI for anything else
            try:
                ai_resp = await handle_query(user_query=msg_body, session_id=from_num)
                reply = ai_resp.get("answer", "")
                if not reply:
                    raise ValueError("empty")
            except Exception:
                reply = (
                    f"🤔 I didn't quite get that. Try:\n\n"
                    f"• Type 'prices' for material prices\n"
                    f"• Type 'mason' to find an artisan\n"
                    f"• Type 'plans' for house plans\n"
                    f"• Visit www.mjengoai.com for full search\n\n"
                    f"Need help? Call: +254 143 422 201"
                )

        # Log to Supabase
        try:
            supabase.table("conversations").insert({
                "query":      msg_body,
                "intent":     "whatsapp",
                "session_id": from_num,
            }).execute()
        except Exception:
            pass

        # Return TwiML for Twilio, or JSON for Meta/others
        if "whatsapp:" in from_num or "application/x-www-form-urlencoded" in content_type:
            twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{reply}</Message></Response>'
            from fastapi.responses import Response as FastResponse
            return FastResponse(content=twiml, media_type="application/xml")

        return {"reply": reply, "status": "ok"}

    except Exception as e:
        print(f"[MjengoAI /whatsapp ERROR] {e}")
        return JSONResponse(status_code=200, content={"reply": "Service temporarily unavailable.", "status": "error"})


# ── Live Prices Endpoint ───────────────────────────────────────────────────────

@app.get("/prices")
async def get_prices(county: Optional[str] = None):
    """Returns latest material prices from Supabase materials table."""
    try:
        q = supabase.table("materials").select(
            "name,price_kes,unit,county,price_date"
        ).order("price_date", desc=True)
        if county:
            q = q.eq("county", county)
        result = q.limit(20).execute()
        return {"prices": result.data or [], "count": len(result.data or [])}
    except Exception as e:
        print(f"[MjengoAI /prices ERROR] {e}")
        # Return static fallback
        return {"prices": [
            {"name": "Cement 50kg",    "price_kes": 720,  "unit": "bag",   "county": "Nairobi"},
            {"name": "Steel rod 12mm", "price_kes": 680,  "unit": "metre", "county": "Nairobi"},
            {"name": "Roofing sheet",  "price_kes": 1250, "unit": "sheet", "county": "Nairobi"},
            {"name": "Hollow block",   "price_kes": 48,   "unit": "block", "county": "Nairobi"},
            {"name": "River sand",     "price_kes": 2100, "unit": "tonne", "county": "Nairobi"},
        ], "count": 5, "source": "static_fallback"}

