/**
 * Red-team viewer: a read-mostly web app bound to the sheets-llm Google Sheet.
 *
 * Row r of the "Prompts" tab is answered in row r of every model tab
 * (header: ID | Prompt | Response | Status | ...), which is how sheets-llm writes.
 * Viewers look up a row by number; nothing they do changes shared state,
 * except submitting a new prompt (appends a row to Prompts).
 *
 * Deploy: Extensions > Apps Script in the sheet, paste Code.gs + Index.html,
 * Deploy > New deployment > Web app, Execute as: Me, Who has access: Anyone.
 */

const PROMPTS_TAB = 'Prompts';
const PROMPT_COL = 3;            // Prompts: ID | Category | Prompt | Notes
const MODEL_HEADER = ['ID', 'Prompt', 'Response', 'Status'];
const MAX_PROMPT_CHARS = 4000;

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Red-team viewer')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/** Link to the sheet itself, for people who want to see the source. */
function getSheetUrl() {
  return SpreadsheetApp.getActiveSpreadsheet().getUrl();
}

/** Tabs whose header matches what sheets-llm writes, in tab order. Cached 2 min. */
function modelSheets_(ss) {
  const cache = CacheService.getScriptCache();
  const hit = cache.get('model-tabs');
  if (hit) {
    const sheets = JSON.parse(hit).map(n => ss.getSheetByName(n));
    if (sheets.every(Boolean)) return sheets;
  }
  const sheets = ss.getSheets().filter(sh => {
    if (sh.getName() === PROMPTS_TAB || sh.getLastColumn() < 4) return false;
    const head = sh.getRange(1, 1, 1, 4).getValues()[0].map(String);
    return MODEL_HEADER.every((h, i) => head[i] === h);
  });
  if (sheets.length) cache.put('model-tabs', JSON.stringify(sheets.map(s => s.getName())), 120);
  return sheets;
}

function norm_(s) {
  return String(s).replace(/\s+/g, ' ').trim();
}

/** Bulk-read Prompts and every model tab: one getValues per tab. */
function readAll_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const prompts = ss.getSheetByName(PROMPTS_TAB);
  const last = prompts.getLastRow();
  const P = last >= 2 ? prompts.getRange(2, 1, last - 1, 4).getValues() : [];
  const tabs = modelSheets_(ss);
  const M = tabs.map(sh => {
    const n = Math.min(sh.getLastRow(), last);
    return n >= 2 ? sh.getRange(2, 1, n - 1, 7).getValues() : [];
  });
  return { last: last, P: P, names: tabs.map(sh => sh.getName()), M: M };
}

/** Row r in the shape the page draws, from a readAll_() snapshot. */
function rowFrom_(all, r) {
  if (!(r >= 2 && r <= all.last)) return { row: r, last: all.last, missing: true };
  const p = all.P[r - 2];
  const promptText = String(p[PROMPT_COL - 1]);
  if (!promptText.trim()) return { row: r, last: all.last, blank: true };
  const models = all.names.map((name, i) => {
    const v = all.M[i][r - 2] || [];
    const seen = String(v[1] || '');
    return {
      name: name,
      response: String(v[2] || ''),
      status: String(v[3] || 'WAITING'),
      error: String(v[6] || ''),
      // rows shifted after the model ran (someone inserted/deleted rows)
      stale: seen !== '' && norm_(seen) !== norm_(promptText),
    };
  });
  return { row: r, last: all.last, prompt: promptText, category: String(p[1]), models: models };
}

/** One row (0 = latest). */
function getRow(r) {
  const all = readAll_();
  r = Math.floor(Number(r));
  return rowFrom_(all, r > 0 ? r : all.last);
}

/**
 * Every row except those the page already has complete (`have`), so the
 * page can browse from memory and only sync what changed.
 */
function getAll(have) {
  const all = readAll_();
  const skip = {};
  (have || []).forEach(r => { skip[r] = true; });
  const rows = {};
  for (let r = 2; r <= all.last; r++) {
    if (!skip[r]) rows[r] = rowFrom_(all, r);
  }
  return { last: all.last, rows: rows };
}

/** Append a prompt; returns its row, ready to draw (saves a second round trip). */
function submitPrompt(text, category) {
  text = String(text || '').trim();
  if (!text) throw new Error('Empty prompt');
  if (text.length > MAX_PROMPT_CHARS) throw new Error('Prompt too long');
  const lock = LockService.getScriptLock();
  let row;
  lock.waitLock(10000);
  try {
    const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(PROMPTS_TAB);
    // A leading apostrophe stops "=…", "+…", "-…", "@…" being parsed as a formula
    const safe = s => (/^[=+\-@]/.test(s) ? "'" + s : s);
    sh.appendRow(['', safe(String(category || '').slice(0, 100)), safe(text), '']);
    SpreadsheetApp.flush();
    row = sh.getLastRow();
  } finally {
    lock.releaseLock();
  }
  return getRow(row);
}
