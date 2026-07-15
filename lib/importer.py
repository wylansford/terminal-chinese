"""Vocabulary import/export (JSON files and the auto-import drop directory)"""

import json
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from db import Database, parse_json_field, JSON_FIELDS


def serialize_json_fields(word: Dict[str, Any]) -> Dict[str, Any]:
    """Encode list/dict fields as JSON strings for storage"""
    for field in JSON_FIELDS:
        value = word.get(field)
        word[field] = json.dumps(value, ensure_ascii=False) if value else None
    return word


def import_json(file_path: Path, db: Database) -> int:
    """Import a {"vocabulary": [...]} file; returns number of new words"""
    with open(file_path, encoding='utf-8') as f:
        data = json.load(f)

    count = 0
    for word in data.get('vocabulary', []):
        word = serialize_json_fields(word)
        word['source'] = file_path.name
        if db.add_vocabulary(word):
            count += 1

    db.conn.execute(
        "INSERT INTO import_history (filename, words_imported) VALUES (?, ?)",
        (file_path.name, count),
    )
    db.conn.commit()
    return count


def sync_bundled(db: Database, data_dir: Path) -> int:
    """Import bundled HSK packs that are new or changed since the last sync.

    The manifest avoids rechecking and reinserting every bundled word on each
    shell startup, while allowing later releases to add or update packs in an
    existing user's database.
    """
    total = 0
    for file_path in sorted(data_dir.glob('hsk*/*.json')):
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        key = str(file_path.relative_to(data_dir))
        previous = db.conn.execute(
            "SELECT sha256 FROM bundled_imports WHERE path = ?", (key,)
        ).fetchone()
        if previous and previous[0] == digest:
            continue

        total += import_json(file_path, db)
        db.conn.execute(
            "INSERT INTO bundled_imports (path, sha256) VALUES (?, ?) "
            "ON CONFLICT(path) DO UPDATE SET sha256 = excluded.sha256, "
            "imported_at = CURRENT_TIMESTAMP",
            (key, digest),
        )
        db.conn.commit()
    return total


def auto_import(db: Database, vocab_dir: Path) -> List[Tuple[str, int]]:
    """Import every JSON file in the drop directory, then archive it to .imported/"""
    imported_dir = vocab_dir / '.imported'
    imported_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for file_path in sorted(vocab_dir.glob('*.json')):
        try:
            count = import_json(file_path, db)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"warning: skipping {file_path.name}: {e}", file=sys.stderr)
            continue
        dest = imported_dir / file_path.name
        if dest.exists():
            stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            dest = imported_dir / f"{file_path.stem}_{stamp}.json"
        shutil.move(str(file_path), str(dest))
        results.append((file_path.name, count))
    return results


def export_json(file_path: Path, db: Database, hsk_level: int = None) -> int:
    """Export vocabulary (optionally one HSK level) to a re-importable JSON file"""
    query = """
        SELECT simplified, traditional, pinyin, english, hsk_level, word_type,
               examples, usage_notes, mnemonic, etymology, related_words,
               measure_word, emoji
        FROM vocabulary
    """
    params = ()
    if hsk_level:
        query += " WHERE hsk_level = ?"
        params = (hsk_level,)
    query += " ORDER BY hsk_level, simplified"

    vocabulary = []
    for row in db.conn.execute(query, params):
        word = dict(row)
        for field in JSON_FIELDS:
            word[field] = parse_json_field(word[field], field)
        vocabulary.append({k: v for k, v in word.items() if v is not None})

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump({'vocabulary': vocabulary}, f, ensure_ascii=False, indent=2)
    return len(vocabulary)
