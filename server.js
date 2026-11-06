import express from 'express';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;
const HOST = '0.0.0.0';

const DATA_DIR = process.env.LL_DATA_DIR || path.join(__dirname, '.data');
if (!fs.existsSync(DATA_DIR)) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

const ANNOTATIONS_FILE = path.join(DATA_DIR, 'annotations.json');
const IMPORTS_FILE = path.join(DATA_DIR, 'imports.json');
const HISTORY_FILE = path.join(DATA_DIR, 'history.json');

function loadJson(file, fallback) {
  try {
    if (fs.existsSync(file)) {
      return JSON.parse(fs.readFileSync(file, 'utf-8'));
    }
  } catch (e) {
    console.error(`Error reading ${file}:`, e);
  }
  return fallback;
}

function saveJson(file, data) {
  try {
    fs.writeFileSync(file, JSON.stringify(data, null, 2), 'utf-8');
  } catch (e) {
    console.error(`Error writing ${file}:`, e);
  }
}

// Extract seed records from index.html
let seedPosts = [];
try {
  const htmlContent = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf-8');
  const match = htmlContent.match(/<script type="application\/json" id="seed-records">([\s\S]*?)<\/script>/);
  if (match) {
    seedPosts = JSON.parse(match[1]);
  }
} catch (e) {
  console.error('Failed to load seed records from index.html:', e);
}

app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true, limit: '10mb' }));

app.get('/api/session', (req, res) => {
  res.json({ authenticated: true, offline: false });
});

app.post('/api/login', (req, res) => {
  res.json({ authenticated: true });
});

app.post('/api/logout', (req, res) => {
  res.json({ ok: true });
});

function getCombinedPosts() {
  const validations = loadJson(ANNOTATIONS_FILE, {});
  const imports = loadJson(IMPORTS_FILE, []);
  const allPosts = [...seedPosts, ...imports];
  return allPosts.map(row => {
    const item = validations[row.research_post_id];
    return {
      ...row,
      human_language: item?.human_language || null,
      human_sentiment: item?.sentiment || null,
      human_notes: item?.notes || '',
      human_status: item?.status || 'pending',
      validated_at: item?.updated_at || null,
      revision: item?.revision || 0
    };
  });
}

app.get('/api/posts', (req, res) => {
  res.json({ posts: getCombinedPosts() });
});

app.post('/api/validation', (req, res) => {
  const body = req.body || {};
  const pid = String(body.post_id || '').trim();
  const items = getCombinedPosts();
  const post = items.find(p => p.research_post_id === pid);
  if (!post) {
    return res.status(404).json({ error: 'Post does not exist.' });
  }

  let lang = body.language;
  let sentiment = body.sentiment;
  const skip = body.skip === true;
  let status;

  if (skip) {
    status = 'skipped';
    lang = null;
    sentiment = null;
  } else if (!['mansaka', 'mixed', 'not_mansaka', 'unsure'].includes(lang)) {
    return res.status(400).json({ error: 'Please select a language judgment.' });
  } else if (['mansaka', 'mixed'].includes(lang)) {
    if (!['Positive', 'Negative', 'Neutral'].includes(sentiment)) {
      return res.status(400).json({ error: 'Please choose Positive, Negative, or Neutral.' });
    }
    status = 'validated';
  } else if (lang === 'not_mansaka') {
    status = 'excluded';
    sentiment = null;
  } else {
    status = 'needs_review';
    sentiment = null;
  }

  const notes = String(body.notes || '').slice(0, 5000);
  const validations = loadJson(ANNOTATIONS_FILE, {});
  const revision = (validations[pid]?.revision || 0) + 1;
  const updated_at = new Date().toISOString();

  validations[pid] = {
    human_language: lang,
    sentiment,
    notes,
    status,
    updated_at,
    revision
  };
  saveJson(ANNOTATIONS_FILE, validations);

  const history = loadJson(HISTORY_FILE, []);
  history.push({
    research_post_id: pid,
    revision,
    human_language_label: lang || '',
    human_sentiment_label: sentiment || '',
    validation_notes: notes,
    human_review_status: status,
    changed_at: updated_at
  });
  saveJson(HISTORY_FILE, history);

  res.json({
    ok: true,
    post_id: pid,
    status,
    language: lang,
    sentiment,
    notes,
    validated_at: updated_at,
    revision
  });
});

app.post('/api/import', (req, res) => {
  const body = req.body || {};
  const csvRaw = body.csv;
  if (typeof csvRaw !== 'string') {
    return res.status(400).json({ error: 'Expected {csv: "..."}' });
  }

  try {
    const lines = csvRaw.replace(/^\ufeff/, '').split(/\r?\n/).filter(l => l.trim().length > 0);
    if (lines.length === 0) return res.status(400).json({ error: 'Empty CSV' });
    const parseRow = (line) => {
      const res = [];
      let cur = '', inQ = false;
      for (let i = 0; i < line.length; i++) {
        const c = line[i];
        if (inQ) {
          if (c === '"' && line[i+1] === '"') { cur += '"'; i++; }
          else if (c === '"') { inQ = false; }
          else { cur += c; }
        } else {
          if (c === '"') { inQ = true; }
          else if (c === ',') { res.push(cur); cur = ''; }
          else { cur += c; }
        }
      }
      res.push(cur);
      return res.map(s => s.trim());
    };

    const headers = parseRow(lines[0]);
    if (!headers.includes('research_post_id') || !headers.includes('cleaned_text')) {
      return res.status(400).json({ error: 'CSV needs research_post_id and cleaned_text columns.' });
    }

    const rows = lines.slice(1).map(l => {
      const vals = parseRow(l);
      const obj = {};
      headers.forEach((h, idx) => { obj[h] = vals[idx] || ''; });
      return obj;
    });

    if (rows.length > 15000) {
      return res.status(400).json({ error: 'Too many rows. Maximum 15,000 per import.' });
    }

    const imports = loadJson(IMPORTS_FILE, []);
    const existingIds = new Set([...seedPosts, ...imports].map(p => p.research_post_id));
    let inserted = 0, duplicates = 0, invalid = 0;

    for (const r of rows) {
      const pid = (r.research_post_id || '').trim();
      const txt = (r.anonymized_text || r.cleaned_text || '').trim();
      if (!pid || !txt || pid.length > 200 || txt.length > 100000) {
        invalid++;
        continue;
      }
      if (existingIds.has(pid)) {
        duplicates++;
        continue;
      }
      imports.push(r);
      existingIds.add(pid);
      inserted++;
    }

    saveJson(IMPORTS_FILE, imports);
    res.json({ ok: true, inserted, duplicates, invalid });
  } catch (e) {
    res.status(400).json({ error: 'Could not parse CSV: ' + e.message });
  }
});

app.get('/api/export', (req, res) => {
  const mode = req.query.mode || 'training';
  const items = getCombinedPosts();

  const quote = v => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
  const toCsv = (rows, cols) => '\ufeff' + cols.map(quote).join(',') + '\r\n' + rows.map(row => cols.map(c => quote(row[c])).join(',')).join('\r\n') + '\r\n';

  let csvContent = '';
  let filename = '';

  if (mode === 'history') {
    const history = loadJson(HISTORY_FILE, []);
    const cols = ['research_post_id', 'revision', 'human_language_label', 'human_sentiment_label', 'validation_notes', 'human_review_status', 'changed_at'];
    csvContent = toCsv(history, cols);
    filename = 'lumad_lingua_validation_history.csv';
  } else if (mode === 'training') {
    const cols = ['research_post_id', 'cleaned_text', 'anonymized_text', 'sentiment_label', 'language_verified', 'validated_at'];
    const validItems = items
      .filter(r => r.human_status === 'validated' && ['mansaka', 'mixed'].includes(r.human_language) && ['Positive', 'Negative', 'Neutral'].includes(r.human_sentiment))
      .map(r => ({
        research_post_id: r.research_post_id,
        cleaned_text: r.cleaned_text,
        anonymized_text: r.anonymized_text,
        sentiment_label: r.human_sentiment,
        language_verified: r.human_language,
        validated_at: r.validated_at
      }));
    csvContent = toCsv(validItems, cols);
    filename = 'lumad_lingua_validated_training.csv';
  } else {
    const originalCols = items.length > 0 ? Object.keys(items[0]).filter(k => !['human_language', 'human_sentiment', 'human_notes', 'human_status', 'validated_at', 'revision'].includes(k)) : ['research_post_id'];
    const cols = [...originalCols, 'human_language_label', 'human_sentiment_label', 'validation_notes', 'human_review_status', 'validated_at', 'review_revision'];
    const rows = items.map(r => ({
      ...r,
      human_language_label: r.human_language || '',
      human_sentiment_label: r.human_sentiment || '',
      validation_notes: r.human_notes,
      human_review_status: r.human_status,
      review_revision: r.revision
    }));
    csvContent = toCsv(rows, cols);
    filename = 'lumad_lingua_full_reviewed_dataset.csv';
  }

  res.setHeader('Content-Type', 'text/csv; charset=utf-8');
  res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
  res.send(csvContent);
});

app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, HOST, () => {
  console.log(`Lumad Lingua Validator server running at http://${HOST}:${PORT}`);
});
