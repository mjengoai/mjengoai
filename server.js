// ═══════════════════════════════════════════════════════════════════════
//  MjengoAI — Render Backend Server
//  server.js — complete Express server
// ═══════════════════════════════════════════════════════════════════════

const express = require("express");
const fetch   = require("node-fetch");
const cors    = require("cors");
const { createClient } = require("@supabase/supabase-js");

const app  = express();
const PORT = process.env.PORT || 3000;

// ── Middleware ────────────────────────────────────────────────────────
app.use(cors());
app.use(express.json());

// ── Supabase ──────────────────────────────────────────────────────────
const supabase = createClient(
  process.env.SUPABASE_URL,
  process.env.SUPABASE_ANON_KEY
);

// ── Anthropic ─────────────────────────────────────────────────────────
const ANTHROPIC_KEY = process.env.ANTHROPIC_KEY;

// ── System prompt ─────────────────────────────────────────────────────
const SYSTEM_PROMPT = `You are MjengoAI, a smart construction assistant for Kenya built by Mineco Systems.
You help users with construction material prices, finding artisans/contractors/vendors,
house planning, cost estimates, and construction phases.
Key facts: Cement KES 720/bag, Mason KES 1800/day, Unskilled KES 900/day.
Keep answers concise and use KES for prices.`;


// ═══════════════════════════════════════════════════════════════════════
//  CORE ROUTES
// ═══════════════════════════════════════════════════════════════════════

// ── GET /ping — keep-alive health check ──────────────────────────────
app.get("/ping", (req, res) => {
  res.json({ status: "ok", time: new Date().toISOString() });
});

// ── POST /register — new user registration ───────────────────────────
app.post("/register", async (req, res) => {
  try {
    const { full_name, phone, category, town } = req.body;
    const { data, error } = await supabase
      .from("registrations")
      .insert({ full_name, phone, category, town })
      .select()
      .single();
    if (error) throw error;
    res.json({ success: true, data });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── POST /register-profile — artisan/professional/vendor profile ──────
app.post("/register-profile", async (req, res) => {
  try {
    const { type, ...profile } = req.body;
    const table = type === "artisan" ? "artisans"
                : type === "professional" ? "professionals"
                : type === "vendor" ? "vendors"
                : "contractors";
    const { data, error } = await supabase
      .from(table)
      .insert(profile)
      .select()
      .single();
    if (error) throw error;
    res.json({ success: true, data });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── GET /profile?phone= — fetch user profile ─────────────────────────
app.get("/profile", async (req, res) => {
  try {
    const { phone } = req.query;
    const tables = ["artisans", "professionals", "vendors", "contractors"];
    let profile = null;
    for (const table of tables) {
      const { data } = await supabase
        .from(table).select("*").eq("phone", phone).single();
      if (data) { profile = { ...data, type: table }; break; }
    }
    res.json({ success: true, profile });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── POST /save-profile — update profile ───────────────────────────────
app.post("/save-profile", async (req, res) => {
  try {
    const { type, id, ...updates } = req.body;
    const table = type === "artisan" ? "artisans"
                : type === "professional" ? "professionals"
                : type === "vendor" ? "vendors"
                : "contractors";
    const { data, error } = await supabase
      .from(table).update(updates).eq("id", id).select().single();
    if (error) throw error;
    res.json({ success: true, data });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── GET /contact?id=&name= — contact details (auth-gated) ─────────────
app.get("/contact", async (req, res) => {
  try {
    const { id, name } = req.query;
    const tables = ["artisans", "professionals", "vendors", "contractors"];
    let contact = null;
    for (const table of tables) {
      const { data } = await supabase
        .from(table).select("full_name,phone,email,town").eq("id", id).single();
      if (data) { contact = data; break; }
    }
    res.json({ success: true, contact });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── POST /chat — Claude AI proxy ──────────────────────────────────────
// Accepts two formats:
//   Frontend chat widget:  { messages: [{role,content},...], max_tokens }
//   Legacy format:         { message: "string", history: [...] }
app.post("/chat", async (req, res) => {
  try {
    const { messages: msgArr, message, history = [], max_tokens = 250 } = req.body;

    let messages;
    let systemPrompt = SYSTEM_PROMPT;

    if (msgArr && Array.isArray(msgArr)) {
      // New format — separate out system message if frontend included one
      const sysMsg = msgArr.find(m => m.role === "system");
      if (sysMsg) systemPrompt = sysMsg.content;
      messages = msgArr.filter(m => m.role !== "system");
    } else {
      // Legacy format
      messages = [...history, { role: "user", content: message }];
    }

    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": ANTHROPIC_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-20250514",
        max_tokens,
        system: systemPrompt,
        messages,
      }),
    });
    const data = await response.json();
    // Return both keys — frontend uses data.content, legacy uses data.reply
    const text = data.content?.[0]?.text || "Sorry, try again.";
    res.json({ success: true, content: text, reply: text });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── GET /search?q= — AI-powered search ───────────────────────────────
app.get("/search", async (req, res) => {
  try {
    const { q } = req.query;
    const { data: artisans } = await supabase
      .from("artisans").select("*")
      .ilike("specialisation", `%${q}%`)
      .eq("status", "active").limit(10);
    const { data: professionals } = await supabase
      .from("professionals").select("*")
      .ilike("specialisation", `%${q}%`)
      .eq("status", "active").limit(10);
    const results = [...(artisans || []), ...(professionals || [])];
    await supabase.from("search_logs").insert({ query: q, result_count: results.length });
    res.json({ success: true, results });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── GET /prices — live material prices ───────────────────────────────
app.get("/prices", async (req, res) => {
  try {
    const { data, error } = await supabase
      .from("construction_rates")
      .select("name, price_kes, unit, change_pct, up")
      .order("name");
    if (error) throw error;
    res.json({ success: true, prices: data });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});


// ═══════════════════════════════════════════════════════════════════════
//  WHATSAPP ROUTES
// ═══════════════════════════════════════════════════════════════════════

// whatsapp.js exports a function(app, supabase) — registers GET+POST /wa-webhook
require("./whatsapp")(app, supabase);


// ═══════════════════════════════════════════════════════════════════════
//  START SERVER
// ═══════════════════════════════════════════════════════════════════════

app.listen(PORT, () => {
  console.log(`✅ MjengoAI server running on port ${PORT}`);
});
