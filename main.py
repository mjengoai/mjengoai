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

