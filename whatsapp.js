// ═══════════════════════════════════════════════════════════════════════
//  MjengoAI — WhatsApp Webhook Route
//  Add this to your Render Node.js Express server (server.js / index.js)
//
//  Phone Number ID : 1129775893552932
//  Webhook URL     : https://mjengoai.onrender.com/whatsapp
//  Verify Token    : MjengoAI2026!
// ═══════════════════════════════════════════════════════════════════════

const express  = require("express");
const fetch    = require("node-fetch"); // already in your project
const { createClient } = require("@supabase/supabase-js");

// ── Env vars (set in Render Dashboard → Environment) ─────────────────
const WHATSAPP_TOKEN     = process.env.WHATSAPP_TOKEN;
const WHATSAPP_PHONE_ID  = process.env.WHATSAPP_PHONE_ID;
const VERIFY_TOKEN       = process.env.WHATSAPP_VERIFY_TOKEN;
const ANTHROPIC_KEY      = process.env.ANTHROPIC_KEY;
const SUPABASE_URL       = process.env.SUPABASE_URL;
const SUPABASE_ANON_KEY  = process.env.SUPABASE_ANON_KEY;

const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// ── MjengoAI system prompt ────────────────────────────────────────────
const SYSTEM_PROMPT = `You are MjengoAI, a smart construction assistant for Kenya built by Mineco Systems.
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
If unsure, say so and suggest visiting www.mjengoai.com`;

// ── Send WhatsApp message ─────────────────────────────────────────────
async function sendMessage(to, text) {
  await fetch(`https://graph.facebook.com/v19.0/${WHATSAPP_PHONE_ID}/messages`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${WHATSAPP_TOKEN}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      messaging_product: "whatsapp",
      to,
      type: "text",
      text: { body: text },
    }),
  });
}

// ── Get conversation history from Supabase ────────────────────────────
async function getHistory(phone) {
  const { data } = await supabase
    .from("conversations")
    .select("role, content")
    .eq("phone", phone)
    .order("created_at", { ascending: false })
    .limit(10);
  return (data || []).reverse();
}

// ── Save message to Supabase ──────────────────────────────────────────
async function saveMessage(phone, role, content) {
  await supabase.from("conversations").insert({ phone, role, content });
}

// ── Ask Claude ────────────────────────────────────────────────────────
async function askClaude(history, userMessage) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": ANTHROPIC_KEY,
      "anthropic-version": "2023-06-01",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: "claude-sonnet-4-20250514",
      max_tokens: 400,
      system: SYSTEM_PROMPT,
      messages: [...history, { role: "user", content: userMessage }],
    }),
  });
  const data = await res.json();
  return data.content?.[0]?.text || "Sorry, I couldn't process that. Try again!";
}

// ═══════════════════════════════════════════════════════════════════════
//  ROUTES — paste these into your Express app
// ═══════════════════════════════════════════════════════════════════════

const router = express.Router();

// ── GET /whatsapp — Meta webhook verification ─────────────────────────
router.get("/whatsapp", (req, res) => {
  const mode      = req.query["hub.mode"];
  const token     = req.query["hub.verify_token"];
  const challenge = req.query["hub.challenge"];

  if (mode === "subscribe" && token === VERIFY_TOKEN) {
    console.log("✅ WhatsApp webhook verified");
    return res.status(200).send(challenge);
  }
  res.sendStatus(403);
});

// ── POST /whatsapp — Incoming messages ───────────────────────────────
router.post("/whatsapp", async (req, res) => {
  // Always reply 200 immediately so Meta doesn't retry
  res.sendStatus(200);

  try {
    const message = req.body?.entry?.[0]?.changes?.[0]?.value?.messages?.[0];

    // Only handle text messages
    if (!message || message.type !== "text") return;

    const phone = message.from;   // e.g. "254712345678"
    const text  = message.text.body.trim();

    console.log(`📩 From ${phone}: ${text}`);

    // Get history, save user msg, ask Claude, save + send reply
    const history = await getHistory(phone);
    await saveMessage(phone, "user", text);
    const reply = await askClaude(history, text);
    await saveMessage(phone, "assistant", reply);
    await sendMessage(phone, reply);

    console.log(`✅ Replied to ${phone}`);
  } catch (err) {
    console.error("WhatsApp handler error:", err);
  }
});

module.exports = router;


// ═══════════════════════════════════════════════════════════════════════
//  HOW TO ADD TO YOUR server.js / index.js:
//
//  const whatsappRouter = require("./whatsapp"); // adjust path
//  app.use("/", whatsappRouter);
//
// ═══════════════════════════════════════════════════════════════════════


// ═══════════════════════════════════════════════════════════════════════
//  FINAL STEP — Set webhook in Meta Developer Console:
//
//  1. Go to developers.facebook.com → mjengoai app
//  2. WhatsApp → Configuration
//  3. Webhook URL  : https://mjengoai.onrender.com/whatsapp
//  4. Verify Token : MjengoAI2026!
//  5. Click Verify and Save
//  6. Subscribe to: messages
// ═══════════════════════════════════════════════════════════════════════
