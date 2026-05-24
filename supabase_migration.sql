-- ═══════════════════════════════════════════════════════════════════════
--  MjengoAI — Supabase Migration Script
--  Run this in: Supabase Dashboard → SQL Editor → New Query → Run
--  Safe to run multiple times (uses IF NOT EXISTS / ON CONFLICT DO NOTHING)
-- ═══════════════════════════════════════════════════════════════════════

-- ─────────────────────────────────────────────────────────────────────
-- 1. ARTISANS  (main members table — all categories land here first)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS artisans (
  id              bigserial PRIMARY KEY,
  full_name       text        NOT NULL,
  phone           text        NOT NULL,
  category        text        NOT NULL DEFAULT '',   -- artisans|professionals|vendors|contractors
  specialisation  text        NOT NULL DEFAULT '',
  town            text        NOT NULL DEFAULT '',
  fee             text                 DEFAULT '',
  email           text                 DEFAULT '',
  about           text                 DEFAULT '',
  reg_number      text                 DEFAULT '',
  status          text        NOT NULL DEFAULT 'pending',  -- pending|active|suspended
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz          DEFAULT now()
);

-- Prevent duplicate phone registrations
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'artisans_phone_unique'
  ) THEN
    ALTER TABLE artisans ADD CONSTRAINT artisans_phone_unique UNIQUE (phone);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS artisans_category_idx    ON artisans (category);
CREATE INDEX IF NOT EXISTS artisans_status_idx      ON artisans (status);
CREATE INDEX IF NOT EXISTS artisans_town_idx        ON artisans (town);
CREATE INDEX IF NOT EXISTS artisans_created_at_idx  ON artisans (created_at DESC);

-- Auto-update updated_at on every row change
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

DROP TRIGGER IF EXISTS artisans_updated_at ON artisans;
CREATE TRIGGER artisans_updated_at
  BEFORE UPDATE ON artisans
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────────────────────────────────
-- 2. PROFESSIONALS  (architects, engineers, QS — separate detail table)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS professionals (
  id              bigserial PRIMARY KEY,
  full_name       text        NOT NULL,
  phone           text        NOT NULL,
  specialisation  text        NOT NULL DEFAULT '',
  town            text        NOT NULL DEFAULT '',
  fee             text                 DEFAULT '',
  email           text                 DEFAULT '',
  about           text                 DEFAULT '',
  reg_number      text                 DEFAULT '',   -- EBK / AAK / BORAQS number
  status          text        NOT NULL DEFAULT 'pending',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz          DEFAULT now()
);

CREATE INDEX IF NOT EXISTS professionals_status_idx ON professionals (status);
CREATE INDEX IF NOT EXISTS professionals_town_idx   ON professionals (town);

DROP TRIGGER IF EXISTS professionals_updated_at ON professionals;
CREATE TRIGGER professionals_updated_at
  BEFORE UPDATE ON professionals
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────────────────────────────────
-- 3. VENDORS  (hardware, timber, precast, roofing, factory shops)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS vendors (
  id              bigserial PRIMARY KEY,
  full_name       text        NOT NULL,
  phone           text        NOT NULL,
  specialisation  text        NOT NULL DEFAULT '',   -- hardware_vendor, timber_vendor …
  town            text        NOT NULL DEFAULT '',
  fee             text                 DEFAULT '',
  email           text                 DEFAULT '',
  about           text                 DEFAULT '',
  reg_number      text                 DEFAULT '',
  status          text        NOT NULL DEFAULT 'pending',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz          DEFAULT now()
);

CREATE INDEX IF NOT EXISTS vendors_status_idx ON vendors (status);
CREATE INDEX IF NOT EXISTS vendors_town_idx   ON vendors (town);

DROP TRIGGER IF EXISTS vendors_updated_at ON vendors;
CREATE TRIGGER vendors_updated_at
  BEFORE UPDATE ON vendors
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────────────────────────────────
-- 4. CONTRACTORS  (builders, NCA-registered firms)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contractors (
  id              bigserial PRIMARY KEY,
  full_name       text        NOT NULL,
  phone           text        NOT NULL,
  specialisation  text        NOT NULL DEFAULT '',
  town            text        NOT NULL DEFAULT '',
  fee             text                 DEFAULT '',
  email           text                 DEFAULT '',
  about           text                 DEFAULT '',
  reg_number      text                 DEFAULT '',   -- NCA registration number
  status          text        NOT NULL DEFAULT 'pending',
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz          DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contractors_status_idx ON contractors (status);
CREATE INDEX IF NOT EXISTS contractors_town_idx   ON contractors (town);

DROP TRIGGER IF EXISTS contractors_updated_at ON contractors;
CREATE TRIGGER contractors_updated_at
  BEFORE UPDATE ON contractors
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ─────────────────────────────────────────────────────────────────────
-- 5. CONSTRUCTION_RATES  (material price ticker)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS construction_rates (
  id          bigserial PRIMARY KEY,
  name        text    NOT NULL,
  price_kes   numeric NOT NULL DEFAULT 0,
  unit        text             DEFAULT '',
  change_pct  text             DEFAULT 'Stable',
  up          boolean          DEFAULT NULL,   -- true=up, false=down, null=stable
  updated_at  timestamptz      DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS construction_rates_name_idx ON construction_rates (name);

-- Seed initial material prices (safe re-run with ON CONFLICT DO NOTHING)
INSERT INTO construction_rates (name, price_kes, unit, change_pct, up) VALUES
  ('Cement 50kg',     720,  'bag',   '+2.1%',  true ),
  ('Steel rod 12mm',  680,  'm',     '-1.5%',  false),
  ('Roofing sheet',   1250, 'sheet', '+5.0%',  true ),
  ('Hollow block',    48,   'pc',    'Stable',  NULL ),
  ('River sand/t',    2100, 't',     '+1.8%',  true ),
  ('Murram lorry',    4200, 'load',  '-3.0%',  false),
  ('Timber 2×4"',     320,  'm',     '+0.8%',  true ),
  ('Quarry stone/t',  3200, 't',     'Stable',  NULL ),
  ('BRC mesh',        8500, 'roll',  '+1.2%',  true ),
  ('Crown Paint 20L', 5500, 'tin',   '-0.5%',  false)
ON CONFLICT (name) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 6. CONSTRUCTION_PHASES  (project phase helper data)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS construction_phases (
  id          bigserial PRIMARY KEY,
  phase_name  text    NOT NULL,
  description text             DEFAULT '',
  sort_order  integer          DEFAULT 0
);

INSERT INTO construction_phases (phase_name, description, sort_order) VALUES
  ('Site Preparation',    'Land clearing, setting out, soil tests',          1),
  ('Foundation',          'Excavation, concrete footings, DPC',              2),
  ('Substructure',        'Ground floor slab, columns to DPC level',         3),
  ('Superstructure',      'Walling, ring beam, floor slabs, columns',        4),
  ('Roofing',             'Roof structure, mabati or tile fixing',           5),
  ('Electrical Rough-in', 'Conduits, switch boxes, cable pulls',             6),
  ('Plumbing Rough-in',   'Soil pipes, water supply lines',                  7),
  ('Plastering',          'Internal and external render',                    8),
  ('Tiling & Flooring',   'Floor and wall tile fixing, screed',              9),
  ('Joinery',             'Doors, windows, built-in furniture',             10),
  ('Finishing',           'Painting, sanitary ware, electrical fittings',   11),
  ('Landscaping',         'Fence, gate, driveway, soft landscaping',        12)
ON CONFLICT DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 7. ROW LEVEL SECURITY
-- ─────────────────────────────────────────────────────────────────────

-- ARTISANS ─ public can read active, anon can insert, service role has full access
ALTER TABLE artisans ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read active artisans"  ON artisans;
DROP POLICY IF EXISTS "anon insert artisans"         ON artisans;
DROP POLICY IF EXISTS "allow anon insert artisans"   ON artisans;
DROP POLICY IF EXISTS "service role full access"     ON artisans;

CREATE POLICY "public read active artisans"
  ON artisans FOR SELECT
  USING (status = 'active');

CREATE POLICY "anon can register"
  ON artisans FOR INSERT
  TO anon
  WITH CHECK (true);

CREATE POLICY "service role full access artisans"
  ON artisans FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

-- PROFESSIONALS ─ same pattern
ALTER TABLE professionals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read active professionals" ON professionals;
DROP POLICY IF EXISTS "anon can register professionals"  ON professionals;
DROP POLICY IF EXISTS "service role full professionals"  ON professionals;

CREATE POLICY "public read active professionals"
  ON professionals FOR SELECT
  USING (status = 'active');

CREATE POLICY "anon can register professionals"
  ON professionals FOR INSERT
  TO anon
  WITH CHECK (true);

CREATE POLICY "service role full professionals"
  ON professionals FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

-- VENDORS ─ same pattern
ALTER TABLE vendors ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read active vendors" ON vendors;
DROP POLICY IF EXISTS "anon can register vendors"  ON vendors;
DROP POLICY IF EXISTS "service role full vendors"  ON vendors;

CREATE POLICY "public read active vendors"
  ON vendors FOR SELECT
  USING (status = 'active');

CREATE POLICY "anon can register vendors"
  ON vendors FOR INSERT
  TO anon
  WITH CHECK (true);

CREATE POLICY "service role full vendors"
  ON vendors FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

-- CONTRACTORS ─ same pattern
ALTER TABLE contractors ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read active contractors" ON contractors;
DROP POLICY IF EXISTS "anon can register contractors"  ON contractors;
DROP POLICY IF EXISTS "service role full contractors"  ON contractors;

CREATE POLICY "public read active contractors"
  ON contractors FOR SELECT
  USING (status = 'active');

CREATE POLICY "anon can register contractors"
  ON contractors FOR INSERT
  TO anon
  WITH CHECK (true);

CREATE POLICY "service role full contractors"
  ON contractors FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

-- CONSTRUCTION_RATES ─ anyone can read, only service role writes
ALTER TABLE construction_rates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read rates"           ON construction_rates;
DROP POLICY IF EXISTS "service role manage rates"   ON construction_rates;

CREATE POLICY "public read rates"
  ON construction_rates FOR SELECT
  USING (true);

CREATE POLICY "service role manage rates"
  ON construction_rates FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

-- CONSTRUCTION_PHASES ─ anyone can read, only service role writes
ALTER TABLE construction_phases ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "public read phases"          ON construction_phases;
DROP POLICY IF EXISTS "service role manage phases"  ON construction_phases;

CREATE POLICY "public read phases"
  ON construction_phases FOR SELECT
  USING (true);

CREATE POLICY "service role manage phases"
  ON construction_phases FOR ALL
  TO service_role
  USING (true) WITH CHECK (true);

-- ─────────────────────────────────────────────────────────────────────
-- 8. ADMIN HELPER VIEWS  (read in Supabase Table Editor)
-- ─────────────────────────────────────────────────────────────────────

-- All pending registrations across every table
CREATE OR REPLACE VIEW pending_registrations AS
  SELECT id, full_name, phone, 'artisan'      AS source, category AS type,
         specialisation, town, created_at
  FROM artisans      WHERE status = 'pending'
UNION ALL
  SELECT id, full_name, phone, 'professional' AS source, specialisation AS type,
         specialisation, town, created_at
  FROM professionals WHERE status = 'pending'
UNION ALL
  SELECT id, full_name, phone, 'vendor'       AS source, specialisation AS type,
         specialisation, town, created_at
  FROM vendors       WHERE status = 'pending'
UNION ALL
  SELECT id, full_name, phone, 'contractor'   AS source, specialisation AS type,
         specialisation, town, created_at
  FROM contractors   WHERE status = 'pending'
ORDER BY created_at DESC;

-- ─────────────────────────────────────────────────────────────────────
-- ✅ Done — verify with:
--    SELECT table_name FROM information_schema.tables
--    WHERE table_schema = 'public' ORDER BY table_name;
-- ─────────────────────────────────────────────────────────────────────
