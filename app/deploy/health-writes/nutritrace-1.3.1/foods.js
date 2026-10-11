/**
 * /api/v1/foods — federation read endpoint for foods.
 *
 * GET /          list with optional q / category / limit / offset
 * GET /:id       single food by id
 * POST /         create a food (Hermes patch, not upstream)
 *
 * Scope: read:foods or mcp:read for reads (mcp:read already covers "the
 * foods and meals catalog"). POST needs mcp:write and
 * PUBLIC_API_WRITE_ENABLED=1, like the diary write routes.
 * See docs/federation.md for the wire shape.
 */
import { Router } from 'express';
import db from '../../../db.js';
import { wrap } from '../../../logger.js';
import { requireScope } from '../../../middleware/bearer-auth.js';
import { createFoodCore } from '../../../lib/mcp/tools/create-food.js';

const router = Router();

function _envFlag(v) {
  if (v === undefined || v === null) return false;
  const s = String(v).trim().toLowerCase();
  return s === '1' || s === 'true' || s === 'yes' || s === 'on';
}

const WRITE_ENABLED = _envFlag(process.env.PUBLIC_API_WRITE_ENABLED);

function requireWriteEnabled(req, res, next) {
  if (!WRITE_ENABLED) return res.status(404).json({ error: 'Public API writes not enabled on this server' });
  next();
}

// Either scope is enough to read the catalogue.
function requireAnyScope(...scopes) {
  return (req, res, next) => {
    if (!req.apiToken) {
      return res.status(401).json({ error: 'Token required', code: 'auth_missing' });
    }
    if (!scopes.some(s => req.apiToken.scopes.includes(s))) {
      return res.status(403).json({ error: `Token lacks ${scopes.join(' or ')}`, code: 'auth_scope' });
    }
    next();
  };
}

const MAX_LIMIT = 500;
const DEFAULT_LIMIT = 100;

router.get('/', requireAnyScope('read:foods', 'mcp:read'), wrap((req, res) => {
  const userId = req.apiUser.id;
  const q = String(req.query.q || '').trim();
  const category = String(req.query.category || '').trim();
  const limit = Math.min(MAX_LIMIT, Math.max(1, Number(req.query.limit) || DEFAULT_LIMIT));
  const offset = Math.max(0, Number(req.query.offset) || 0);

  const conds = ['user_id = ?', 'deleted_at IS NULL'];
  const args = [userId];

  if (q) {
    conds.push('(name LIKE ? OR brand LIKE ?)');
    const like = `%${q}%`;
    args.push(like, like);
  }
  if (category) {
    conds.push('category = ?');
    args.push(category);
  }

  const where = conds.join(' AND ');

  const totalRow = db.prepare(`SELECT COUNT(*) AS c FROM foods WHERE ${where}`).get(...args);
  const rows = db.prepare(
    `SELECT * FROM foods WHERE ${where} ORDER BY name ASC LIMIT ? OFFSET ?`
  ).all(...args, limit, offset);

  res.json({
    items: rows.map(_toWire),
    total: totalRow.c,
    limit,
    offset,
  });
}));

router.get('/:id', requireAnyScope('read:foods', 'mcp:read'), wrap((req, res) => {
  const userId = req.apiUser.id;
  const id = Number(req.params.id);
  if (!Number.isFinite(id)) {
    return res.status(404).json({ error: 'Not found', code: 'not_found' });
  }
  const row = db.prepare(
    `SELECT * FROM foods WHERE id = ? AND user_id = ? AND deleted_at IS NULL`
  ).get(id, userId);
  if (!row) {
    return res.status(404).json({ error: 'Not found', code: 'not_found' });
  }
  res.json(_toWire(row));
}));

router.post('/', requireWriteEnabled, requireScope('mcp:write'), wrap((req, res) => {
  try {
    const result = createFoodCore(req.apiUser.id, req.body || {});
    const row = db.prepare(
      `SELECT * FROM foods WHERE id = ? AND user_id = ?`
    ).get(result.created.id, req.apiUser.id);
    res.status(201).json({
      ...(row ? _toWire(row) : result.created),
      rejected_nutriments: result.rejected_nutriments,
      aliased_nutriments: result.aliased_nutriments,
    });
  } catch (e) {
    if (e.code === 'duplicate') {
      return res.status(409).json({ error: e.message, code: 'duplicate', id: e.existingId });
    }
    res.status(400).json({ error: e.message, code: 'invalid' });
  }
}));

/**
 * Internal-row → wire-format mapper.
 *
 * IMPORTANT: this is the source of truth for what fields cross the
 * federation boundary. Adding a new internal column does NOT
 * automatically expose it — it has to be added here AND documented
 * in docs/federation.md. Same for removals: keep the wire field
 * present (with null if needed) until v2.
 */
function _toWire(row) {
  let nutrition = {};
  try { nutrition = JSON.parse(row.nutrition || '{}'); }
  catch { nutrition = {}; }
  // Strip internal-only metadata that may have leaked into the JSON
  // blob (e.g. _derived flags). External consumers don't need these.
  if (nutrition._derived !== undefined) delete nutrition._derived;

  return {
    id:         row.id,
    name:       row.name,
    brand:      row.brand || null,
    category:   row.category || null,
    barcode:    row.barcode || null,
    portion:    typeof row.portion === 'number' ? row.portion : 100,
    unit:       row.unit || 'g',
    img_url:    row.img_url || null,
    notes:      row.notes || null,
    nutrition,
    created_at: _isoUtc(row.created_at),
    updated_at: _isoUtc(row.updated_at) || _isoUtc(row.created_at),
  };
}

/** SQLite stores `datetime('now')` as `YYYY-MM-DD HH:MM:SS` UTC.
 *  Normalize to ISO-8601 UTC for the wire. */
function _isoUtc(s) {
  if (!s) return null;
  // Already ISO-ish? Pass through.
  if (s.includes('T')) return s.endsWith('Z') ? s : s + 'Z';
  // SQLite default format: YYYY-MM-DD HH:MM:SS
  return s.replace(' ', 'T') + 'Z';
}

export default router;
