from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import os
import json
import traceback
from dotenv import load_dotenv
from search import handle_query

load_dotenv()

# ── Supabase client (service-role key — stays on server, never sent to browser) ──
from supabase import create_client, Client
_SURL: str = os.environ.get("SUPABASE_URL", "")
_SKEY: str = os.environ.get("SUPABASE_KEY", "")   # service-role key
_sb: Client = create_client(_SURL, _SKEY) if _SURL and _SKEY else None

# ── Firebase Admin (server-side only) ──
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


app = FastAPI(
    title="MjengoAI API",
    description="Generative AI construction search — powered by OpenAI + Supabase",
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


# ════════════════════════════════════════════════════════════════
#  NEW SECURE ROUTES — all secrets stay here on the server
# ════════════════════════════════════════════════════════════════

@app.post("/register")
async def register(req: RegisterRequest):
    """
    Receives new member registration from the browser.
    Browser sends plain JSON — no API key needed from client side.
    """
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
    """
    Inserts into artisans / professionals table based on category.
    Called fire-and-forget from the browser after /register succeeds.
    """
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
    """
    Saves member to Firestore (dormant until admin approves).
    Replaces the old browser-side Firebase Firestore write.
    """
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
    """
    Returns a live Firestore profile — active members only.
    Called when opening a contact card to get live data.
    """
    if not _fdb:
        return JSONResponse(status_code=503, content={})
    try:
        doc = _fdb.collection("members").document(phone).get()
        if doc.exists:
            d = doc.to_dict()
            if d.get("active"):
                # Strip sensitive admin fields before returning
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
    """
    Returns phone + email for a listing when the user clicks 'Unlock Contact'.
    Looks up Supabase artisans → professionals → registrations tables.
    Phone numbers are NEVER sent to the browser until this endpoint is called.
    """
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
    """
    Returns material prices from Supabase for the homepage ticker.
    Replaces the old browser-side Supabase SDK call.
    """
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
