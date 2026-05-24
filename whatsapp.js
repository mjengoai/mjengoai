/**
 * ═══════════════════════════════════════════════════════════════
 *  MjengoAI — WhatsApp Meta Cloud API Webhook Handler
 *  File: whatsapp.js
 *  Mount in your main server.js:  require('./whatsapp')(app, supabase)
 * ═══════════════════════════════════════════════════════════════
 *
 *  ENV VARIABLES REQUIRED (set in Render dashboard):
 *    WHATSAPP_TOKEN          — your permanent System User access token
 *    WHATSAPP_PHONE_ID       — Phone Number ID from Meta dashboard
 *    WHATSAPP_VERIFY_TOKEN   — any secret string you choose (for webhook verification)
 *
 *  Meta Dashboard setup:
 *    1. App > WhatsApp > Configuration > Webhook URL:
 *       https://mjengoai.onrender.com/wa-webhook
 *    2. Verify Token: same value as WHATSAPP_VERIFY_TOKEN
 *    3. Subscribe to: messages
 * ═══════════════════════════════════════════════════════════════
 */

const axios = require('axios');

// ── Category keyword map — mirrors the frontend DB keys ──────────────────────
const CATEGORY_MAP = {
  mason:            'masons',
  fundi:            'masons',
  bricklayer:       'masons',
  plumber:          'plumbers',
  electrician:      'electricians',
  electrical:       'electricians',
  carpenter:        'carpenters',
  welder:           'welders',
  painter:          'painters',
  tiler:            'tiles_fixers',
  'steel fixer':    'steel_fixers',
  'steel fix':      'steel_fixers',
  glazier:          'glass_expert',
  glass:            'glass_expert',
  foreman:          'foreman',
  'site manager':   'foreman',
  landscape:        'landscapers_art',
  garden:           'landscapers_art',
  architect:        'architects',
  engineer:         'structural_eng',
  structural:       'structural_eng',
  civil:            'structural_eng',
  mep:              'mep_eng',
  mechanical:       'mep_eng',
  'quantity surveyor': 'quantity_surveyor',
  qs:               'quantity_surveyor',
  'interior design':'interior_designer',
  interior:         'interior_designer',
  'physical planner':'physical_planner',
  planner:          'physical_planner',
  'land economist': 'land_economist',
  contractor:       'builders',
  builder:          'builders',
  hardware:         'hardware_vendors',
  cement:           'hardware_vendors',
  timber:           'timber_vendors',
  wood:             'timber_vendors',
  lumber:           'timber_vendors',
  precast:          'precast_vendors',
  'hollow block':   'precast_vendors',
  block:            'precast_vendors',
  roofing:          'roofing_mat',
  mabati:           'roofing_mat',
  quarry:           'crushers',
  crusher:          'crushers',
  ballast:          'crushers',
  nema:             'nema_eia',
  eia:              'nema_eia',
};

// ── Simple session store — tracks each user's conversation state ──────────────
// In production, replace with Redis or a Supabase sessions table
const sessions = new Map();

function getSession(phone) {
  if (!sessions.has(phone)) {
    sessions.set(phone, { step: 'menu', lastQuery: null, lastResults: [] });
  }
  return sessions.get(phone);
}

// ── Detect category from free text ───────────────────────────────────────────
function detectCategory(text) {
  const t = text.toLowerCase();
  for (const [keyword, table] of Object.entries(CATEGORY_MAP)) {
    if (t.includes(keyword)) return table;
  }
  return null;
}

// ── Extract location from text ────────────────────────────────────────────────
function extractLocation(text) {
  // Common Kenya towns/counties — extend as needed
  const locations = [
    'nairobi','mombasa','kisumu','nakuru','eldoret','thika','chuka','embu',
    'meru','nyeri','garissa','isiolo','kitui','machakos','muranga','kiambu',
    'nanyuki','malindi','lamu','voi','chogoria','ruiru','juja','githurai',
    'westlands','karen','langata','kilimani','parklands','eastleigh','kangemi',
  ];
  const t = text.toLowerCase();
  return locations.find(l => t.includes(l)) || null;
}

// ── Query Supabase for professionals ─────────────────────────────────────────
async function queryProfessionals(supabase, table, location, limit = 5) {
  try {
    let query = supabase
      .from(table)
      .select('name, phone, email, loc, sub, price, rat')
      .limit(limit);

    if (location) {
      query = query.ilike('loc', `%${location}%`);
    }

    const { data, error } = await query;
    if (error) throw error;
    return data || [];
  } catch (e) {
    console.error('[WA] Supabase query error:', e.message);
    return [];
  }
}

// ── Format results as WhatsApp message ───────────────────────────────────────
function formatResults(results, category, location) {
  if (!results.length) {
    return `😔 No results found for *${category}*${location ? ` in ${location}` : ' in Kenya'}.\n\nTry a broader search or visit mjengoai.com for the full directory.`;
  }

  const locLabel = location ? ` in ${location.charAt(0).toUpperCase() + location.slice(1)}` : '';
  let msg = `✅ *Found ${results.length} ${category}${locLabel}:*\n\n`;

  results.forEach((r, i) => {
    msg += `*${i + 1}. ${r.name || 'Unknown'}*\n`;
    if (r.sub)   msg += `   📋 ${r.sub}\n`;
    if (r.loc)   msg += `   📍 ${r.loc}\n`;
    if (r.rat && r.rat > 0) msg += `   ⭐ ${r.rat}/5\n`;
    if (r.price && r.price !== 'Unlock contact') msg += `   💰 ${r.price}\n`;
    if (r.phone) msg += `   📞 ${r.phone}\n`;
    else         msg += `   📞 Visit mjengoai.com to unlock contact\n`;
    msg += '\n';
  });

  msg += `🔍 See more at *mjengoai.com*`;
  return msg;
}

// ── Send WhatsApp message ─────────────────────────────────────────────────────
async function sendMessage(to, text) {
  const phoneId = process.env.WHATSAPP_PHONE_ID;
  const token   = process.env.WHATSAPP_TOKEN;

  try {
    await axios.post(
      `https://graph.facebook.com/v19.0/${phoneId}/messages`,
      {
        messaging_product: 'whatsapp',
        to,
        type: 'text',
        text: { body: text },
      },
      {
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
      }
    );
  } catch (e) {
    console.error('[WA] Send error:', e.response?.data || e.message);
  }
}

// ── Send interactive list menu ────────────────────────────────────────────────
async function sendMenu(to) {
  const phoneId = process.env.WHATSAPP_PHONE_ID;
  const token   = process.env.WHATSAPP_TOKEN;

  // WhatsApp interactive menus require approved templates or use simple text menu
  // Using text-based menu for reliability without template approval
  const menuText =
    `👷 *Welcome to MjengoAI!*\n` +
    `Kenya's construction directory 🏗️\n\n` +
    `What do you need?\n\n` +
    `*1* — Find an Artisan (mason, plumber, welder…)\n` +
    `*2* — Find a Professional (architect, engineer…)\n` +
    `*3* — Find a Vendor (hardware, timber, precast…)\n` +
    `*4* — Material Prices (cement, steel, mabati…)\n` +
    `*5* — House Plans & BOQ\n\n` +
    `Reply with a number *or* just type what you need\n` +
    `e.g. _"mason Nairobi"_ or _"hardware Chuka"_`;

  await sendMessage(to, menuText);
}

// ── Handle material price queries ─────────────────────────────────────────────
async function sendPrices(supabase, to) {
  try {
    const { data } = await supabase
      .from('material_prices')
      .select('name, price_kes, unit, change_pct')
      .limit(10);

    if (data && data.length) {
      let msg = `📊 *Live Material Prices (Kenya)*\n\n`;
      data.forEach(p => {
        const change = p.change_pct
          ? (p.change_pct > 0 ? `🔺${p.change_pct}%` : `🔻${Math.abs(p.change_pct)}%`)
          : '';
        msg += `• *${p.name}*: KES ${Number(p.price_kes).toLocaleString()}/${p.unit || 'unit'} ${change}\n`;
      });
      msg += `\n_Updated daily · mjengoai.com_`;
      await sendMessage(to, msg);
    } else {
      // Fallback hardcoded prices
      await sendMessage(to,
        `📊 *Material Prices (Kenya)*\n\n` +
        `• *Cement 50kg*: KES 720 🔺2.1%\n` +
        `• *Steel rod 12mm*: KES 680/m 🔻1.5%\n` +
        `• *Roofing sheet*: KES 1,250 🔺5.0%\n` +
        `• *Hollow block*: KES 48 (Stable)\n` +
        `• *River sand/t*: KES 2,100 🔺1.8%\n` +
        `• *Murram lorry*: KES 4,200 🔻3.0%\n` +
        `• *Timber 2×4"*: KES 320/m\n` +
        `• *BRC mesh*: KES 8,500/roll\n\n` +
        `_Visit mjengoai.com for full live prices_`
      );
    }
  } catch (e) {
    console.error('[WA] Prices error:', e.message);
    await sendMessage(to, `Sorry, couldn't fetch prices right now. Visit mjengoai.com for live prices.`);
  }
}

// ── Core message router ───────────────────────────────────────────────────────
async function handleMessage(supabase, from, messageText) {
  const text    = (messageText || '').trim();
  const textLow = text.toLowerCase();
  const session = getSession(from);

  console.log(`[WA] From: ${from} | Text: "${text}" | Step: ${session.step}`);

  // ── Greetings / menu trigger ──────────────────────────────────────────────
  if (['hi','hello','hujambo','habari','menu','start','help','0'].includes(textLow) || session.step === 'menu') {
    session.step = 'waiting';
    await sendMenu(from);
    return;
  }

  // ── Numbered menu selections ──────────────────────────────────────────────
  if (text === '1') {
    session.step = 'artisan_location';
    session.pendingCategory = null;
    await sendMessage(from,
      `🔨 *Find an Artisan*\n\nWhich trade are you looking for?\n\n` +
      `Mason · Plumber · Electrician · Carpenter · Welder · Painter · Tiler · Foreman · Glazier · Steel Fixer\n\n` +
      `Type the trade and location e.g. _"mason Nairobi"_`
    );
    return;
  }

  if (text === '2') {
    session.step = 'professional_location';
    await sendMessage(from,
      `👔 *Find a Professional*\n\nWhich profession?\n\n` +
      `Architect · Civil Engineer · Quantity Surveyor · Construction Manager · Interior Designer · Physical Planner · Land Economist · MEP Engineer\n\n` +
      `Type e.g. _"architect Nairobi"_ or _"QS Mombasa"_`
    );
    return;
  }

  if (text === '3') {
    session.step = 'vendor_location';
    await sendMessage(from,
      `🏪 *Find a Vendor*\n\nWhat type?\n\n` +
      `Hardware · Timber · Precast · Crusher/Quarry · Roofing\n\n` +
      `Type e.g. _"hardware Chuka"_ or _"precast Embu"_`
    );
    return;
  }

  if (text === '4') {
    session.step = 'waiting';
    await sendPrices(supabase, from);
    return;
  }

  if (text === '5') {
    session.step = 'waiting';
    await sendMessage(from,
      `🏠 *House Plans & BOQ*\n\n` +
      `Browse our full plan repository at:\n` +
      `👉 *mjengoai.com* → House Plans\n\n` +
      `Or tell me what you need:\n` +
      `e.g. _"3 bedroom house plan under 3M"_\n` +
      `and I'll suggest options from our directory.`
    );
    return;
  }

  // ── Free-text search — detect category + location ─────────────────────────
  const category = detectCategory(text);
  const location = extractLocation(text);

  if (category) {
    session.step = 'results';
    session.lastQuery = { category, location };

    await sendMessage(from, `🔍 Searching for *${category.replace(/_/g,' ')}*${location ? ` in *${location}*` : ''}…`);

    const results = await queryProfessionals(supabase, category, location);
    session.lastResults = results;

    await sendMessage(from, formatResults(results, category.replace(/_/g,' '), location));

    // Prompt for follow-up
    await sendMessage(from,
      `💬 Need more results or a different location?\n` +
      `Reply *more* for next 5, or type a new search.\n` +
      `Reply *menu* to go back to the main menu.`
    );
    return;
  }

  // ── "More results" pagination ──────────────────────────────────────────────
  if (textLow === 'more' && session.lastQuery) {
    const { category: cat, location: loc } = session.lastQuery;
    const offset = session.lastResults.length;

    try {
      let query = supabase
        .from(cat)
        .select('name, phone, email, loc, sub, price, rat')
        .range(offset, offset + 4);
      if (loc) query = query.ilike('loc', `%${loc}%`);

      const { data } = await query;
      if (data && data.length) {
        session.lastResults.push(...data);
        await sendMessage(from, formatResults(data, cat.replace(/_/g,' '), loc));
      } else {
        await sendMessage(from, `No more results. Visit *mjengoai.com* to see the full directory.`);
      }
    } catch (e) {
      await sendMessage(from, `Couldn't load more results. Please try again.`);
    }
    return;
  }

  // ── Price-specific queries ─────────────────────────────────────────────────
  if (/price|bei|cost|how much|kes|cement|steel|mabati|timber|sand|ballast/i.test(text)) {
    await sendPrices(supabase, from);
    return;
  }

  // ── Fallback ───────────────────────────────────────────────────────────────
  await sendMessage(from,
    `🤔 I didn't quite catch that.\n\n` +
    `Try:\n` +
    `• _"mason Nairobi"_ — find a mason in Nairobi\n` +
    `• _"hardware Chuka"_ — find hardware shops in Chuka\n` +
    `• _"cement price"_ — get material prices\n` +
    `• *menu* — show the main menu\n\n` +
    `Or visit *mjengoai.com* for the full directory.`
  );
}

// ── Express route registration ────────────────────────────────────────────────
module.exports = function registerWhatsAppRoutes(app, supabase) {

  // ── Webhook verification (Meta calls this once during setup) ──────────────
  app.get('/wa-webhook', (req, res) => {
    const mode      = req.query['hub.mode'];
    const token     = req.query['hub.verify_token'];
    const challenge = req.query['hub.challenge'];

    if (mode === 'subscribe' && token === process.env.WHATSAPP_VERIFY_TOKEN) {
      console.log('[WA] Webhook verified ✓');
      res.status(200).send(challenge);
    } else {
      console.warn('[WA] Webhook verification failed');
      res.sendStatus(403);
    }
  });

  // ── Incoming messages ─────────────────────────────────────────────────────
  app.post('/wa-webhook', async (req, res) => {
    // Always respond 200 immediately — Meta will retry if you're slow
    res.sendStatus(200);

    try {
      const body = req.body;
      if (body.object !== 'whatsapp_business_account') return;

      for (const entry of (body.entry || [])) {
        for (const change of (entry.changes || [])) {
          const value = change.value;
          if (!value || !value.messages) continue;

          for (const msg of value.messages) {
            if (msg.type !== 'text') continue; // ignore images/audio for now

            const from = msg.from;          // sender's phone number
            const text = msg.text?.body;    // message content

            if (!from || !text) continue;

            // Process async — don't block the 200 response
            handleMessage(supabase, from, text).catch(e =>
              console.error('[WA] handleMessage error:', e.message)
            );
          }
        }
      }
    } catch (e) {
      console.error('[WA] Webhook parse error:', e.message);
    }
  });

  console.log('[MjengoAI] WhatsApp routes registered: GET/POST /wa-webhook');
};
