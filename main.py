"""
MjengoAI WhatsApp Bot — LangChain RAG Edition
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Stack:  FastAPI · LangChain · GPT-4o · Supabase pgvector
Deploy: Render (mjengoai-bot.onrender.com)
Bot:    WhatsApp Cloud API (Meta)
"""

import os
import asyncio
import httpx
from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse, JSONResponse
from dotenv import load_dotenv

# ── LangChain imports ──
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_community.vectorstores import SupabaseVectorStore
from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferWindowMemory
from langchain.prompts import PromptTemplate, SystemMessagePromptTemplate, HumanMessagePromptTemplate, ChatPromptTemplate
from langchain.schema import SystemMessage

# ── Supabase ──
from supabase import create_client, Client

load_dotenv()

app = FastAPI(title="MjengoAI WhatsApp Bot — LangChain Edition")

# ════════════════════════════════════════
# ENV VARS (set these in Render → Environment)
# ════════════════════════════════════════
WA_TOKEN        = os.environ["WA_TOKEN"]           # Meta permanent token
WA_PHONE_ID     = os.environ["WA_PHONE_ID"]        # WhatsApp phone number ID
WA_VERIFY_TOKEN = os.environ["WA_VERIFY_TOKEN"]    # e.g. mjengoai2025
OPENAI_API_KEY  = os.environ["OPENAI_API_KEY"]     # OpenAI API key
SUPABASE_URL    = os.environ["SUPABASE_URL"]       # Supabase project URL
SUPABASE_KEY    = os.environ["SUPABASE_SERVICE_KEY"] # Supabase service_role key

WA_API_URL = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"

# ════════════════════════════════════════
# LANGCHAIN — CLIENTS & CHAINS
# ════════════════════════════════════════

# Supabase client
sb: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# OpenAI LLM — GPT-4o
llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.3,
    api_key=OPENAI_API_KEY,
    max_tokens=600
)

# OpenAI Embeddings — text-embedding-3-small (cheaper, fast)
embeddings = OpenAIEmbeddings(
    model="text-embedding-3-small",
    api_key=OPENAI_API_KEY
)

# Supabase pgvector store — professionals table
vectorstore_pros = SupabaseVectorStore(
    client=sb,
    embedding=embeddings,
    table_name="professionals",
    query_name="match_professionals"
)

# Supabase pgvector store — artisans table
vectorstore_artisans = SupabaseVectorStore(
    client=sb,
    embedding=embeddings,
    table_name="artisans",
    query_name="match_artisans"
)

# ── System prompt — MjengoAI persona ──
SYSTEM_PROMPT = """You are MjengoAI Assistant — Kenya's most knowledgeable construction AI.

You help users across all 47 counties of Kenya with:
- Finding verified architects, engineers, artisans, fundis, plumbers, electricians, masons
- Material prices (cement, steel, tiles, timber, blocks, ballast, sand)
- House plans and Bills of Quantities (BOQ) from Mineco House (10,000+ plans)
- Contractor quotes and milestone payment via MJT Token (1 MJT = KES 100)
- Construction regulations (NCA, EBK, AAK, BORAQS compliance)

Rules:
- Always respond in the same language the user writes in (English or Swahili)
- Be concise — WhatsApp messages should be under 400 characters when possible
- Always include the MjengoAI website: www.mjengoai.com
- For professionals, always mention verification status (NCA/EBK/AAK)
- For prices, always specify county and date context
- Never invent registration numbers or professional credentials
- If you don't know something, say so and direct to www.mjengoai.com

Context from MjengoAI database:
{context}
"""

# ── Per-user memory store (phone → memory) ──
user_memories: dict = {}

def get_memory(phone: str) -> ConversationBufferWindowMemory:
    """Get or create conversation memory for a user."""
    if phone not in user_memories:
        user_memories[phone] = ConversationBufferWindowMemory(
            k=6,  # remember last 6 exchanges
            memory_key="chat_history",
            return_messages=True,
            output_key="answer"
        )
    return user_memories[phone]

def get_chain(phone: str, vectorstore) -> ConversationalRetrievalChain:
    """Build a RAG chain for a user with their memory."""
    # System + human prompt
    system_msg = SystemMessagePromptTemplate(
        prompt=PromptTemplate(
            input_variables=["context"],
            template=SYSTEM_PROMPT
        )
    )
    human_msg = HumanMessagePromptTemplate(
        prompt=PromptTemplate(
            input_variables=["question"],
            template="{question}"
        )
    )
    chat_prompt = ChatPromptTemplate.from_messages([system_msg, human_msg])

    return ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 4}
        ),
        memory=get_memory(phone),
        combine_docs_chain_kwargs={"prompt": chat_prompt},
        return_source_documents=False,
        verbose=False
    )

# ── Route to correct vectorstore based on intent ──
def detect_intent(text: str) -> str:
    tl = text.lower()
    if any(w in tl for w in ["fundi","mason","plumber","welder","artisan","electrician","tiler","painter"]):
        return "artisan"
    if any(w in tl for w in ["engineer","architect","quantity","surveyor","contractor","consultant","professional"]):
        return "professional"
    if any(w in tl for w in ["cement","bei","price","steel","tiles","sand","ballast","timber","block","bei ya"]):
        return "price"
    if any(w in tl for w in ["plan","nyumba","house","3br","4br","bungalow","maisonette","boq","design"]):
        return "plan"
    if any(w in tl for w in ["hi","hello","habari","hujambo","hey","start","menu","help","msaada"]):
        return "greeting"
    return "general"

# ════════════════════════════════════════
# WEBHOOK VERIFICATION
# ════════════════════════════════════════
@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == WA_VERIFY_TOKEN:
        print("✅ Webhook verified")
        return PlainTextResponse(content=hub_challenge, status_code=200)
    return PlainTextResponse(content="Forbidden", status_code=403)

# ════════════════════════════════════════
# RECEIVE MESSAGES
# ════════════════════════════════════════
@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.json()

    try:
        entry   = body["entry"][0]
        changes = entry["changes"][0]["value"]

        # Ignore status updates (delivery receipts etc.)
        if "statuses" in changes and "messages" not in changes:
            return JSONResponse({"status": "ok"})

        message  = changes["messages"][0]
        phone    = message["from"]
        msg_type = message.get("type", "")

        # Extract text from message type
        if msg_type == "text":
            text = message["text"]["body"].strip()
        elif msg_type == "interactive":
            itype = message["interactive"]["type"]
            if itype == "button_reply":
                text = message["interactive"]["button_reply"]["title"]
            elif itype == "list_reply":
                text = message["interactive"]["list_reply"]["title"]
            else:
                text = ""
        else:
            await send_text(phone,
                "Habari! 👋 Tuma ujumbe wa maandishi.\n"
                "Please send a text message to use MjengoAI.\n\n"
                "👉 www.mjengoai.com"
            )
            return JSONResponse({"status": "ok"})

        if text:
            await handle_message(phone, text)

    except (KeyError, IndexError) as e:
        print(f"⚠️ Payload parse error: {e}")

    return JSONResponse({"status": "ok"})

# ════════════════════════════════════════
# MESSAGE HANDLER — LangChain RAG
# ════════════════════════════════════════
async def handle_message(phone: str, text: str):
    intent = detect_intent(text)

    # ── Greeting → send interactive menu ──
    if intent == "greeting":
        await send_menu(phone)
        return

    # ── Quick menu button selections ──
    tl = text.lower().strip()
    if tl in ["find artisans & professionals", "find artisans"]:
        await send_text(phone,
            "🔨 *Find Artisan or Professional*\n\n"
            "Type what you need — English or Swahili:\n\n"
            "• _Find a mason in Nairobi_\n"
            "• _Plumber Mombasa bei ngapi_\n"
            "• _Structural engineer Nakuru_\n"
            "• _Fundi umeme Kisumu_"
        )
        return

    if tl in ["material price checker", "material prices"]:
        await send_text(phone,
            "💰 *Material Price Checker*\n\n"
            "Ask about any material:\n\n"
            "• _Cement prices Nairobi_\n"
            "• _Bei ya bati Nakuru_\n"
            "• _Steel rod price Mombasa_\n"
            "• _Hollow block 6 inch Kisumu_"
        )
        return

    if tl in ["house plans & boq", "house plans"]:
        await send_text(phone,
            "🏠 *House Plans — Mineco House*\n\n"
            "10,000+ pre-approved plans with BOQ:\n\n"
            "• _3 bedroom bungalow plan_\n"
            "• _2BR maisonette under 2M_\n"
            "• _4 bedroom villa BOQ_\n"
            "• _Commercial plaza Nairobi_"
        )
        return

    # ── RAG Chain — route to correct vectorstore ──
    try:
        if intent in ("artisan",):
            vs = vectorstore_artisans
        else:
            vs = vectorstore_pros

        chain = get_chain(phone, vs)

        # Run LangChain in thread to avoid blocking async
        result = await asyncio.to_thread(
            chain.invoke,
            {"question": text}
        )
        answer = result.get("answer", "").strip()

        if answer:
            # Format for WhatsApp (4096 char limit)
            reply = format_whatsapp(answer)
            await send_text(phone, reply)
        else:
            await send_fallback(phone)

    except Exception as e:
        print(f"❌ LangChain error: {e}")
        # Graceful fallback — static reply
        await send_static_reply(phone, intent, text)

# ════════════════════════════════════════
# STATIC FALLBACK (when OpenAI credits = $0 or error)
# ════════════════════════════════════════
async def send_static_reply(phone: str, intent: str, text: str):
    county = extract_county(text)
    if intent == "artisan":
        reply = (
            "🔨 *MjengoAI Artisan Finder*\n\n"
            f"{'📍 '+county+' — ' if county else ''}"
            "We have 3,200+ vetted artisans.\n"
            "👉 www.mjengoai.com\n\n"
            "Or call/WhatsApp: +254 724 744 321"
        )
    elif intent == "professional":
        reply = (
            "👷 *Find a Professional*\n\n"
            "All verified: NCA · EBK · AAK · BORAQS\n"
            "1,800+ professionals listed.\n"
            "👉 www.mjengoai.com"
        )
    elif intent == "price":
        reply = (
            "💰 *Material Prices*\n\n"
            f"{'📍 '+county+' — ' if county else ''}"
            "Compare prices across 47 counties.\n"
            "👉 www.mjengoai.com/#prices"
        )
    elif intent == "plan":
        reply = (
            "🏠 *House Plans — Mineco House*\n\n"
            "10,000+ plans with BOQ & budgets.\n"
            "👉 www.mjengoai.com/#plans"
        )
    else:
        reply = (
            "🤖 *MjengoAI — Kenya's Construction AI*\n\n"
            "I can help with:\n"
            "🔨 Artisans & professionals\n"
            "💰 Material prices (47 counties)\n"
            "🏠 House plans & BOQ\n\n"
            "👉 www.mjengoai.com\n"
            "📞 +254 724 744 321"
        )
    await send_text(phone, reply)

# ════════════════════════════════════════
# SEND FUNCTIONS
# ════════════════════════════════════════
async def send_text(phone: str, body: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": body[:4096], "preview_url": False}
    }
    await _post_wa(payload)

async def send_menu(phone: str):
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": "🏗️ MjengoAI Kenya"},
            "body": {
                "text": (
                    "Habari! 👋 I'm MjengoAI — Kenya's construction AI.\n\n"
                    "🔨 Find artisans & professionals\n"
                    "💰 Compare material prices (47 counties)\n"
                    "🏠 10,000+ house plans & BOQ\n\n"
                    "Type your question directly or choose below:"
                )
            },
            "footer": {"text": "Powered by Buildersee & Mineco House"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "artisans", "title": "Find Artisans"}},
                    {"type": "reply", "reply": {"id": "prices",   "title": "Material Prices"}},
                    {"type": "reply", "reply": {"id": "plans",    "title": "House Plans"}},
                ]
            }
        }
    }
    await _post_wa(payload)

async def send_fallback(phone: str):
    await send_text(phone,
        "Samahani! 🙏 Could not find an answer right now.\n\n"
        "Please visit 👉 *www.mjengoai.com*\n"
        "Or call/WhatsApp: +254 724 744 321"
    )

async def _post_wa(payload: dict):
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(WA_API_URL, json=payload, headers=headers)
        if res.status_code != 200:
            print(f"⚠️ WA API {res.status_code}: {res.text[:200]}")

# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════
KENYA_COUNTIES = [
    "nairobi","mombasa","kisumu","nakuru","uasin gishu","kiambu",
    "machakos","kajiado","muranga","nyeri","meru","embu","kirinyaga",
    "nyandarua","laikipia","samburu","turkana","west pokot","trans nzoia",
    "bungoma","kakamega","vihiga","busia","siaya","homa bay","migori",
    "kisii","nyamira","bomet","kericho","nandi","baringo",
    "elgeyo marakwet","narok","taita taveta","kwale","kilifi",
    "tana river","lamu","garissa","wajir","mandera","marsabit",
    "isiolo","tharaka nithi","kitui","makueni"
]

def extract_county(text: str) -> str:
    tl = text.lower()
    for county in KENYA_COUNTIES:
        if county in tl:
            return county.title()
    return ""

def format_whatsapp(text: str) -> str:
    """Clean and trim response for WhatsApp."""
    # Remove markdown that doesn't render in WA
    text = text.replace("###", "").replace("##", "").replace("# ", "")
    # Trim to WA limit
    return text[:4090].strip()

# ════════════════════════════════════════
# HEALTH CHECKS
# ════════════════════════════════════════
@app.get("/")
def root():
    return {
        "status": "MjengoAI Bot running ✓",
        "version": "LangChain RAG Edition",
        "model": "gpt-4o",
        "vectorstore": "Supabase pgvector"
    }

@app.get("/health")
def health():
    return {"status": "ok"}
