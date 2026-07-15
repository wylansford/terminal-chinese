#!/usr/bin/env python3
"""
Generate rich enrichment (mnemonic, etymology, examples, ...) for every
HSK 1-6 word that doesn't already have curated content in data/hsk*/.

Base facts (simplified/traditional/pinyin/level) come from chinese-daily's
words.sqlite -- MIT-licensed data from the complete-hsk-vocabulary project
(https://github.com/drkameleon/complete-hsk-vocabulary). This script only
asks the model to *enrich* known-correct words, never to invent them.

Usage:
    export OPENROUTER_API_KEY=sk-or-...
    python3 scripts/generate_vocab.py                 # run (resumable)
    python3 scripts/generate_vocab.py --dry-run        # show what would run
    python3 scripts/generate_vocab.py --limit 50       # smoke test

Idempotent by design:
  - data/generated/enriched.jsonl is the single source of truth for what's
    already done. Each line is one fully-formed word, written and flushed
    the moment its batch succeeds.
  - On startup, words already present in enriched.jsonl (or already curated
    in data/hsk*/) are excluded from the work list -- re-running after a
    crash, a rate limit, or a Ctrl-C only pays for what's left.
  - A batch's words are matched back by the "simplified" key the model
    echoes in each result object, not by array position -- a dropped or
    reordered item never gets silently attached to the wrong word.
  - A failed/malformed batch is simply not written -- nothing is marked
    done, so it's retried automatically next run. No separate failure
    bookkeeping needed.

At the end (and any time via --export-only), enriched.jsonl is grouped by
HSK level and written to data/hsk{N}/generated.json in the same
{"vocabulary": [...]} shape as the rest of data/, ready for `ct import` or
the auto-import drop folder.
"""

import argparse
import glob
import json
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_DIR / 'data'
GENERATED_DIR = DATA_DIR / 'generated'
CHECKPOINT_FILE = GENERATED_DIR / 'enriched.jsonl'

DEFAULT_SOURCE_DB = (
    Path.home() / 'companies_code' / 'lansford_technologies' / 'language-daily'
    / 'chinese-daily' / 'ios' / 'Shared' / 'Resources' / 'words.sqlite'
)

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'
MODEL = 'deepseek/deepseek-v3.2'
BATCH_SIZE = 25
MAX_TOKENS_PER_BATCH = 8000
CONCURRENCY = 8
MAX_RETRIES_PER_BATCH = 2

PROMPT_TEMPLATE = """You are a Chinese language expert writing vocabulary flashcards for casual, \
everyday learners. For each word below, its simplified form, traditional form, pinyin, and HSK \
level are already correct -- do not change them. Your job is only to write the enrichment fields.

Words:
{word_list}

For each word return a JSON object with these fields:
{{
  "simplified": "<echo exactly as given, used to match your answer back>",
  "word_type": "noun/verb/adjective/etc",
  "english": "a clean, single concise translation (not a dictionary dump)",
  "emoji": "one relevant emoji",
  "mnemonic": "a short, creative memory aid for learners",
  "etymology": "brief, accurate explanation of the character origins/evolution",
  "measure_word": {{"character": "...", "pinyin": "...", "example": "..."}} or null (only for countable nouns),
  "related_words": ["3-5 everyday related words in simplified Chinese"],
  "examples": [
    {{"chinese": "colloquial example sentence", "pinyin": "pinyin with tone marks", "english": "translation"}},
    {{"chinese": "a second, different example sentence", "pinyin": "...", "english": "..."}}
  ],
  "usage_notes": "brief grammar or usage note, can be empty string"
}}

Rules:
- CASUAL LANGUAGE: prefer the everyday spoken word over formal/written equivalents.
- Use proper pinyin tone marks (ā á ǎ à, etc.) everywhere pinyin appears.
- Etymology must be accurate, not invented -- if genuinely uncertain, say so briefly rather than \
fabricating a specific story.
- Examples must sound like real conversations, not textbook Chinese.
- Return ONLY a JSON array of these objects, one per word, same order not required. \
No markdown fences, no commentary, no text outside the array."""


def load_source_words(db_path: Path) -> list:
    if not db_path.exists():
        sys.exit(f"Source database not found: {db_path}\n"
                  f"Pass --source-db to point at chinese-daily's words.sqlite.")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT simplified, traditional, pinyin, hsk_level
        FROM words WHERE hsk_level BETWEEN 1 AND 6
        ORDER BY hsk_level, simplified
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def already_curated() -> set:
    """Words with existing hand/AI-curated content in data/hsk*/ -- never touch these."""
    seen = set()
    for path in glob.glob(str(DATA_DIR / 'hsk*' / '*.json')):
        with open(path, encoding='utf-8') as f:
            for word in json.load(f).get('vocabulary', []):
                seen.add(word['simplified'])
    return seen


def already_generated() -> set:
    """Words already written to the checkpoint file by a prior run."""
    if not CHECKPOINT_FILE.exists():
        return set()
    seen = set()
    with open(CHECKPOINT_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                seen.add(json.loads(line)['simplified'])
    return seen


def call_openrouter(prompt: str, api_key: str) -> str:
    body = json.dumps({
        'model': MODEL,
        'max_tokens': MAX_TOKENS_PER_BATCH,
        'messages': [{'role': 'user', 'content': prompt}],
    }).encode('utf-8')
    req = urllib.request.Request(
        OPENROUTER_URL, data=body, method='POST',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
            'HTTP-Referer': 'https://terminal-chinese.lansford.dev',
            'X-Title': 'terminal-chinese vocab generation',
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    return data['choices'][0]['message']['content']


def parse_batch_response(text: str) -> list:
    text = text.strip()
    if text.startswith('```'):
        text = '\n'.join(line for line in text.splitlines() if not line.strip().startswith('```'))
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        text = match.group(0)
    return json.loads(text)


REQUIRED_FIELDS = ('word_type', 'english', 'mnemonic', 'etymology', 'related_words', 'examples')


def validate_entry(entry: dict) -> bool:
    if not all(entry.get(f) for f in REQUIRED_FIELDS):
        return False
    if len(entry.get('related_words') or []) < 2:
        return False
    if len(entry.get('examples') or []) < 2:
        return False
    return True


def run_batch(batch_no: int, total_batches: int, group: list, api_key: str, write_lock: threading.Lock) -> int:
    """Generate enrichment for one group of source words. Returns count written."""
    tag = f"[{batch_no}/{total_batches}]"
    sample = ', '.join(w['simplified'] for w in group[:4])
    print(f"  {tag} → sending {len(group)} words ({sample}, ...)", flush=True)

    word_list = '\n'.join(
        f"- {w['simplified']} ({w['traditional']}) [{w['pinyin']}] HSK{w['hsk_level']}"
        for w in group
    )
    prompt = PROMPT_TEMPLATE.format(word_list=word_list)
    by_simplified = {w['simplified']: w for w in group}

    start = time.monotonic()
    for attempt in range(MAX_RETRIES_PER_BATCH + 1):
        try:
            raw = call_openrouter(prompt, api_key)
            entries = parse_batch_response(raw)
            print(f"  {tag} ← response received ({time.monotonic() - start:.1f}s)", flush=True)
            break
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError) as e:
            elapsed = time.monotonic() - start
            if attempt == MAX_RETRIES_PER_BATCH:
                print(f"  {tag} ✗ failed after {attempt + 1} attempts ({elapsed:.1f}s): {e}", file=sys.stderr, flush=True)
                return 0
            print(f"  {tag} ⚠ attempt {attempt + 1} failed ({elapsed:.1f}s): {e} — retrying", file=sys.stderr, flush=True)
            time.sleep(2 ** attempt)
    else:
        return 0

    written = 0
    lines = []
    for entry in entries:
        simplified = entry.get('simplified')
        source = by_simplified.get(simplified)
        if source is None or not validate_entry(entry):
            continue
        word = {
            'simplified': simplified,
            'traditional': source['traditional'],
            'pinyin': source['pinyin'],
            'english': entry['english'],
            'hsk_level': source['hsk_level'],
            'word_type': entry['word_type'],
            'emoji': entry.get('emoji'),
            'mnemonic': entry['mnemonic'],
            'etymology': entry['etymology'],
            'measure_word': entry.get('measure_word'),
            'related_words': entry['related_words'],
            'examples': entry['examples'],
            'usage_notes': entry.get('usage_notes', ''),
        }
        lines.append(json.dumps(word, ensure_ascii=False))
        written += 1

    if lines:
        with write_lock:
            with open(CHECKPOINT_FILE, 'a', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')
    return written


def export_by_level():
    """Rebuild data/hsk{N}/generated.json from the checkpoint file. Safe to re-run anytime."""
    by_level = defaultdict(list)
    if not CHECKPOINT_FILE.exists():
        return
    with open(CHECKPOINT_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            word = json.loads(line)
            by_level[word['hsk_level']].append(word)

    for level, words in sorted(by_level.items()):
        out_dir = DATA_DIR / f'hsk{level}'
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / 'generated.json'
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'vocabulary': words}, f, ensure_ascii=False, indent=2)
        print(f"  hsk{level}/generated.json: {len(words)} words")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--source-db', type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument('--limit', type=int, help='only process the first N remaining words (smoke test)')
    parser.add_argument('--dry-run', action='store_true', help='show what would run, make no API calls')
    parser.add_argument('--export-only', action='store_true', help='just rebuild data/hsk*/generated.json from the checkpoint')
    args = parser.parse_args()

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    if args.export_only:
        print("Exporting checkpoint to data/hsk*/generated.json ...")
        export_by_level()
        return

    api_key = os.environ.get('OPENROUTER_API_KEY')
    if not api_key and not args.dry_run:
        sys.exit("OPENROUTER_API_KEY is not set. Export it and re-run.")

    source = load_source_words(args.source_db)
    curated = already_curated()
    done = already_generated()
    remaining = [w for w in source if w['simplified'] not in curated and w['simplified'] not in done]

    print(f"source words (HSK1-6):     {len(source)}")
    print(f"already curated (skip):    {len(curated & {w['simplified'] for w in source})}")
    print(f"already generated (skip):  {len(done)}")
    print(f"remaining to generate:     {len(remaining)}")

    if args.limit:
        remaining = remaining[:args.limit]
        print(f"--limit applied:           {len(remaining)}")

    if not remaining:
        print("Nothing to do.")
        export_by_level()
        return

    groups = [remaining[i:i + BATCH_SIZE] for i in range(0, len(remaining), BATCH_SIZE)]
    print(f"batches of {BATCH_SIZE}:            {len(groups)}  (concurrency={CONCURRENCY})")

    if args.dry_run:
        print("\n--dry-run: no API calls made.")
        return

    write_lock = threading.Lock()
    total_written = 0
    completed = 0
    print(f"submitting {len(groups)} batches to {CONCURRENCY} workers ...\n", flush=True)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = {
            pool.submit(run_batch, i, len(groups), g, api_key, write_lock): g
            for i, g in enumerate(groups, 1)
        }
        for future in as_completed(futures):
            written = future.result()
            total_written += written
            completed += 1
            print(f"  ✓ {completed}/{len(groups)} batches done — "
                  f"{total_written}/{len(remaining)} words written so far\n", flush=True)

    print(f"\nDone. {total_written}/{len(remaining)} words generated this run.")
    still_missing = len(remaining) - total_written
    if still_missing:
        print(f"{still_missing} words failed and will retry automatically on the next run.")

    print("\nExporting to data/hsk*/generated.json ...")
    export_by_level()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted -- already-written words are safe in the checkpoint. Re-run to resume.")
        sys.exit(1)
