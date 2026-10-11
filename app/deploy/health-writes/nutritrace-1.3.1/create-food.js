/**
 * MCP tool: create_food (Phase 3, destructive)
 *
 * Insert a new row into the user's foods catalog. Semi-destructive
 * because the row persists across sessions and shows up in every
 * search / picker; agents that fabricate foods can pollute the
 * catalog in a way a user has to hand-clean.
 *
 * Nutrition values are per the food's portion (not per 100g). The
 * server does no unit normalization — the caller's numbers are stored
 * literally, matching how the food-editor UI writes them.
 *
 * Refuses to insert a duplicate name+brand pair; call search_foods
 * first to see if a match already exists.
 */
import { z } from 'zod';
import db from '../../../db.js';
import { toolResult, toolError } from '../_util.js';
import { resolveNewItemVisibility } from '../../default-visibility.js';
import { NUTRIMENTS } from '../../../../src/lib/nutrition.js';

// Derived from the canonical NUTRIMENTS registry so this tool can never
// drift from what the rest of the app actually sums, filters, and
// displays. Prior versions hand-maintained a copy that had subtle
// mismatches — most notably `protein` (this tool) vs `proteins` (rest
// of app), which meant every food created via MCP with `protein: X`
// silently didn't count toward daily protein totals (#103 followup
// report from @javydekoning, 2026-08-13). Also fixes vitamin-b* keys:
// canonical uses bare `b1`, `b2`, ... not `vitamin-b*`.
const NUTRIMENT_KEYS = new Set(NUTRIMENTS.map(n => n.id));

// Common bad-key rename map: keys agents might reach for that map to
// a real canonical id. Applied silently at input time so a "protein"
// arg becomes a "proteins" write with no rejection noise. Keeps the
// tool forgiving without giving up canonical storage.
const KEY_ALIASES = {
  protein:        'proteins',
  carbs:          'carbohydrates',
  carb:           'carbohydrates',
  'vitamin-b1':   'b1',
  'vitamin-b2':   'b2',
  'vitamin-b3':   'b3',
  'vitamin-b6':   'b6',
  'vitamin-b9':   'b9',
  'vitamin-b12':  'b12',
};

/**
 * Hermes patch (not upstream): the insert logic below used to live inline
 * in the create_food tool. It is shared now so POST /api/v1/foods can use
 * the same validation, aliasing and duplicate check. Behaviour of the MCP
 * tool is unchanged.
 *
 * Throws an Error on bad input. A duplicate name + brand throws an Error
 * with code 'duplicate' and existingId set.
 */
export function createFoodCore(userId, { name, brand, portion, unit, nutrition, category, barcode, notes } = {}) {
  const cleanName = typeof name === 'string' ? name.trim() : '';
  const cleanBrand = typeof brand === 'string' ? (brand.trim() || null) : null;
  if (!cleanName) throw new Error('name is required and cannot be blank.');
  if (cleanName.length > 200) throw new Error('name is longer than 200 characters.');
  if (cleanBrand && cleanBrand.length > 100) throw new Error('brand is longer than 100 characters.');
  if (!Number.isFinite(portion) || portion <= 0 || portion > 10000) {
    throw new Error('portion must be a number above 0 and at most 10000.');
  }
  if (typeof unit !== 'string' || !unit.trim() || unit.length > 20) {
    throw new Error('unit is required (1 to 20 characters).');
  }
  if (category != null && (typeof category !== 'string' || category.length > 50)) {
    throw new Error('category must be text of at most 50 characters.');
  }
  if (barcode != null && (typeof barcode !== 'string' || barcode.length > 30)) {
    throw new Error('barcode must be text of at most 30 characters.');
  }
  if (notes != null && (typeof notes !== 'string' || notes.length > 1000)) {
    throw new Error('notes must be text of at most 1000 characters.');
  }
  if (!nutrition || typeof nutrition !== 'object' || Array.isArray(nutrition)) {
    throw new Error('nutrition must be an object of nutriment: number.');
  }

  // Filter nutrition to known keys with finite values. Silent drop
  // rather than reject-the-whole-call so partial data still lands,
  // but report what was dropped so the agent can retry. Per-value
  // sanity cap catches hallucinated 1e12 values before they poison
  // the catalog (real per-portion nutriments are all under 10000).
  const MAX_NUT_VALUE = 100000;
  const clean = {};
  const rejected = [];
  const aliased = [];
  for (const [rawKey, v] of Object.entries(nutrition)) {
    // Map common aliases to canonical ids before validating so the
    // caller's `protein` / `carbs` / `vitamin-b12` land under the
    // key the rest of the app sums (`proteins` / `carbohydrates` /
    // `b12`). Note the alias mapping so the response is honest.
    const k = KEY_ALIASES[rawKey] || rawKey;
    if (k !== rawKey) aliased.push(`${rawKey} → ${k}`);
    if (!NUTRIMENT_KEYS.has(k))  { rejected.push(`${rawKey} (unknown key)`); continue; }
    if (!Number.isFinite(v))     { rejected.push(`${rawKey} (not a number)`); continue; }
    if (v < 0)                   { rejected.push(`${rawKey} (negative)`); continue; }
    if (v > MAX_NUT_VALUE)       { rejected.push(`${rawKey} (${v} exceeds ${MAX_NUT_VALUE} cap)`); continue; }
    clean[k] = Math.round(v * 100) / 100;
  }
  if (Object.keys(clean).length === 0) {
    throw new Error(
      `nutrition must include at least one valid nutriment. Rejected: ${rejected.join('; ') || '(none)'}. ` +
      `Canonical keys: calories, proteins, carbohydrates, fat, ...`
    );
  }

  // Dedup on (user_id, name, brand). Case-insensitive comparison
  // matches how the client-side Foods picker groups results.
  const existing = db.prepare(
    `SELECT id FROM foods
      WHERE user_id = ?
        AND deleted_at IS NULL
        AND LOWER(name) = LOWER(?)
        AND ( (brand IS NULL AND ? IS NULL) OR LOWER(brand) = LOWER(?) )
      LIMIT 1`
  ).get(userId, cleanName, cleanBrand, cleanBrand);
  if (existing) {
    const err = new Error(
      `A food with this name${cleanBrand ? ` and brand '${cleanBrand}'` : ''} ` +
      `already exists (id=${existing.id}). Use that id with log_food, or edit ` +
      'the existing food in the app if the nutrition needs updating.'
    );
    err.code = 'duplicate';
    err.existingId = existing.id;
    throw err;
  }

  // #183 — MCP-created foods honor the caller's defaultShareVisibility
  // too. Requester explicitly called out "created via the app or MCP".
  const vis = resolveNewItemVisibility(userId);
  const result = db.prepare(
    `INSERT INTO foods (user_id, name, brand, portion, unit, nutrition, category, barcode, notes, visibility, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))`
  ).run(
    userId,
    cleanName,
    cleanBrand,
    portion,
    unit,
    JSON.stringify(clean),
    category || null,
    barcode || null,
    notes || null,
    vis,
  );

  return {
    ok: true,
    created: {
      id: result.lastInsertRowid,
      name: cleanName,
      brand: cleanBrand,
      portion,
      unit,
      nutrition: clean,
      category: category || null,
      barcode: barcode || null,
    },
    rejected_nutriments: rejected.length ? rejected : undefined,
    aliased_nutriments: aliased.length ? aliased : undefined,
  };
}

export function registerCreateFood(server, { userId }) {
  server.registerTool(
    'create_food',
    {
      title: 'Create Food',
      description:
        "Add a new food to the user's catalog. Nutrition values are per the " +
        "food's stated portion (not per 100 g by convention). Requires confirm=true. " +
        'Refuses to insert if a food with the same name + brand already exists — ' +
        "call search_foods first if you're not sure. Rejects unknown nutriment keys " +
        'to keep the catalog clean; use canonical ids like "calories", "proteins" ' +
        '(plural), "carbohydrates", "fat", "fiber", "sugars", "vitamin-d", "b12" ' +
        '(bare, not "vitamin-b12"). Common aliases (protein, carb, carbs, vitamin-b*) ' +
        'are silently mapped to the canonical id.',
      inputSchema: {
        confirm:   z.boolean(),
        name:      z.string().min(1).max(200),
        brand:     z.string().max(100).optional(),
        portion:   z.number().positive().max(10000),
        unit:      z.string().min(1).max(20),
        nutrition: z.record(z.string(), z.number()),
        category:  z.string().max(50).optional(),
        barcode:   z.string().max(30).optional(),
        notes:     z.string().max(1000).optional(),
      },
    },
    async ({ confirm, name, brand, portion, unit, nutrition, category, barcode, notes }) => {
      if (confirm !== true) {
        return toolError(
          'create_food requires confirm=true. This safeguards against accidental ' +
          "catalog pollution. Set the confirm argument to true and re-invoke if you're sure."
        );
      }
      try {
        return toolResult(createFoodCore(userId, { name, brand, portion, unit, nutrition, category, barcode, notes }));
      } catch (e) {
        return toolError(e.message);
      }
    }
  );
}
