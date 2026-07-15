"""SQLite storage, card selection, and statistics"""

import json
import sys
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Fields stored as JSON strings in the vocabulary table
JSON_FIELDS = ('examples', 'related_words', 'measure_word')

CARD_QUERY = """
    SELECT c.card_id, c.due, c.stability, c.difficulty, c.step,
           c.reps, c.lapses, c.state, c.last_review, v.*
    FROM fsrs_cards c
    JOIN vocabulary v ON c.word_id = v.word_id
"""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_json_field(value: Optional[str], field: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        print(f"warning: malformed JSON in vocabulary field '{field}'", file=sys.stderr)
        return None


class Database:
    """SQLite database manager for vocabulary and FSRS cards"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: Optional[sqlite3.Connection] = None

    def __enter__(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()

    def init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS vocabulary (
                word_id INTEGER PRIMARY KEY AUTOINCREMENT,
                simplified TEXT NOT NULL,
                traditional TEXT,
                pinyin TEXT NOT NULL,
                english TEXT NOT NULL,
                hsk_level INTEGER,
                word_type TEXT,
                examples TEXT,
                usage_notes TEXT,
                mnemonic TEXT,
                etymology TEXT,
                related_words TEXT,
                measure_word TEXT,
                emoji TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                source TEXT,
                UNIQUE(simplified, pinyin)
            );

            CREATE TABLE IF NOT EXISTS fsrs_cards (
                card_id INTEGER PRIMARY KEY AUTOINCREMENT,
                word_id INTEGER NOT NULL,
                due TIMESTAMP,
                stability REAL,
                difficulty REAL,
                step INTEGER,
                elapsed_days INTEGER DEFAULT 0,
                scheduled_days INTEGER DEFAULT 0,
                reps INTEGER DEFAULT 0,
                lapses INTEGER DEFAULT 0,
                state TEXT DEFAULT 'New',
                last_review TIMESTAMP,
                FOREIGN KEY (word_id) REFERENCES vocabulary(word_id) ON DELETE CASCADE,
                UNIQUE(word_id)
            );

            CREATE TABLE IF NOT EXISTS reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                state TEXT NOT NULL,
                due TIMESTAMP,
                stability REAL,
                difficulty REAL,
                elapsed_days INTEGER,
                scheduled_days INTEGER,
                reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (card_id) REFERENCES fsrs_cards(card_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS import_history (
                import_id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                words_imported INTEGER,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bundled_imports (
                path TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                reviews_done INTEGER DEFAULT 0,
                new_cards INTEGER DEFAULT 0,
                knowledge_score REAL DEFAULT 0,
                accuracy REAL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_fsrs_due ON fsrs_cards(due, state);
            CREATE INDEX IF NOT EXISTS idx_vocab_hsk ON vocabulary(hsk_level);
            CREATE INDEX IF NOT EXISTS idx_vocab_source ON vocabulary(source);
        """)
        self.conn.commit()

    # ── vocabulary ────────────────────────────────────────────────

    def add_vocabulary(self, word: Dict[str, Any]) -> Optional[int]:
        """Insert a word (JSON fields already serialized) and its FSRS card.

        Words with no hsk_level (custom/AI-added, or a dropped-in JSON file that
        omits it) get one assigned via frontier_level() instead of staying NULL --
        NULL sorts behind every leveled word forever, so an unleveled word could
        never surface as long as any leveled backlog remained.

        Returns the word_id, or None if the word already existed.
        """
        hsk_level = word.get('hsk_level')
        if hsk_level is None:
            hsk_level = self.frontier_level()

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO vocabulary
            (simplified, traditional, pinyin, english, hsk_level, word_type, examples,
             usage_notes, mnemonic, etymology, related_words, measure_word, emoji, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            word['simplified'], word.get('traditional'), word['pinyin'], word['english'],
            hsk_level, word.get('word_type'), word.get('examples'),
            word.get('usage_notes'), word.get('mnemonic'), word.get('etymology'),
            word.get('related_words'), word.get('measure_word'), word.get('emoji'),
            word.get('source'),
        ))
        if cursor.rowcount == 0:
            return None
        word_id = cursor.lastrowid
        cursor.execute("INSERT OR IGNORE INTO fsrs_cards (word_id, state) VALUES (?, 'New')", (word_id,))
        return word_id

    def frontier_level(self, max_level: int = 6) -> int:
        """The HSK level to file an unleveled (custom/AI-added) word under.

        Walks the ladder from HSK 1: each level where you've started (state !=
        'New') at least half its words promotes the frontier to the next level.
        Stops at the first level under 50%, so custom words track roughly where
        you actually are -- not stuck at 1 forever, not dumped at 6 on day one.

        Clamped to the highest HSK level enabled in config, so a word is never
        assigned a level get_next_card would then refuse to ever show it.
        """
        level = 1
        for candidate in range(1, max_level + 1):
            total, started = self.conn.execute("""
                SELECT COUNT(*), SUM(CASE WHEN c.state != 'New' THEN 1 ELSE 0 END)
                FROM vocabulary v JOIN fsrs_cards c ON c.word_id = v.word_id
                WHERE v.hsk_level = ?
            """, (candidate,)).fetchone()
            if not total or (started or 0) / total < 0.5:
                break
            level = candidate + 1
        return min(level, max_level, max(self._allowed_hsk_levels()))

    def _allowed_hsk_levels(self) -> List[int]:
        config_path = self.db_path.parent / 'config.json'
        try:
            with open(config_path) as f:
                levels = json.load(f).get('hsk_levels')
            if levels:
                return levels
        except (OSError, json.JSONDecodeError):
            pass
        return [1]

    def backfill_unleveled_words(self) -> int:
        """One-off fix for words inserted before this fallback existed"""
        total = self.conn.execute("SELECT COUNT(*) FROM vocabulary WHERE hsk_level IS NULL").fetchone()[0]
        if not total:
            return 0
        level = self.frontier_level()
        self.conn.execute("UPDATE vocabulary SET hsk_level = ? WHERE hsk_level IS NULL", (level,))
        self.conn.commit()
        return total

    def delete_word(self, word: str) -> int:
        """Delete by simplified form (cascades to card and reviews)"""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM vocabulary WHERE simplified = ?", (word,))
        return cursor.rowcount

    def existing_words(self) -> set:
        rows = self.conn.execute("SELECT simplified FROM vocabulary").fetchall()
        return {row[0] for row in rows}

    # ── card selection ────────────────────────────────────────────

    def _row_to_card(self, row: sqlite3.Row) -> Dict[str, Any]:
        card = dict(row)
        for field in JSON_FIELDS:
            card[field] = parse_json_field(card.get(field), field)
        if not card.get('traditional'):
            card['traditional'] = card['simplified']
        return card

    def get_next_card(self, max_new_per_day: int = 10, hsk_levels: Optional[List[int]] = None) -> Optional[Dict[str, Any]]:
        """Next card to review: overdue reviews, then learning, then new (daily-capped).

        hsk_levels restricts which *new* cards are introduced -- words already in
        Learning/Review/Relearning keep being reviewed regardless, and words with
        no HSK level (custom/AI-added) are always eligible.
        """
        now = utcnow_iso()
        for condition, order in (
            ("c.state IN ('Review', 'Relearning') AND c.due <= ?", "c.due ASC, c.stability ASC"),
            ("c.state = 'Learning' AND c.due <= ?", "c.due ASC"),
        ):
            row = self.conn.execute(
                f"{CARD_QUERY} WHERE {condition} ORDER BY {order} LIMIT 1", (now,)
            ).fetchone()
            if row:
                return self._row_to_card(row)

        if self.new_cards_today() < max_new_per_day:
            level_filter, params = "", ()
            if hsk_levels:
                placeholders = ', '.join('?' * len(hsk_levels))
                level_filter = f"AND (v.hsk_level IN ({placeholders}) OR v.hsk_level IS NULL)"
                params = tuple(hsk_levels)
            row = self.conn.execute(f"""
                {CARD_QUERY}
                WHERE c.state = 'New' {level_filter}
                ORDER BY CASE WHEN v.hsk_level IS NULL THEN 999 ELSE v.hsk_level END, RANDOM()
                LIMIT 1
            """, params).fetchone()
            if row:
                return self._row_to_card(row)
        return None

    def new_cards_today(self) -> int:
        """Cards whose first-ever review happened today (local time)"""
        row = self.conn.execute("""
            SELECT COUNT(*) FROM (
                SELECT card_id, MIN(reviewed_at) AS first_review
                FROM reviews GROUP BY card_id
            ) WHERE date(first_review, 'localtime') = date('now', 'localtime')
        """).fetchone()
        return row[0]

    def count_due(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM fsrs_cards WHERE state != 'New' AND due <= ?",
            (utcnow_iso(),),
        ).fetchone()
        return row[0]

    # ── reviews ───────────────────────────────────────────────────

    def record_review(self, card_id: int, rating: int, updated: Dict[str, Any], was_new: bool):
        """Persist an answered card: card state, review log, and daily stats"""
        fields = ', '.join(f"{k} = ?" for k in updated)
        self.conn.execute(
            f"UPDATE fsrs_cards SET {fields} WHERE card_id = ?",
            (*updated.values(), card_id),
        )
        self.conn.execute("""
            INSERT INTO reviews (card_id, rating, state, due, stability, difficulty)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (card_id, rating, updated['state'], updated['due'],
              updated['stability'], updated['difficulty']))
        self._update_daily_stats(rating, was_new)
        self.conn.commit()

    def _update_daily_stats(self, rating: int, was_new: bool):
        today = datetime.now().date().isoformat()
        row = self.conn.execute(
            "SELECT reviews_done, new_cards, knowledge_score, accuracy FROM daily_stats WHERE date = ?",
            (today,),
        ).fetchone()
        prev = dict(row) if row else {'reviews_done': 0, 'new_cards': 0, 'knowledge_score': 0, 'accuracy': 0}

        reviews_done = prev['reviews_done'] + 1
        correct = 1 if rating >= 3 else 0
        accuracy = ((prev['accuracy'] * prev['reviews_done']) + correct) / reviews_done
        self.conn.execute("""
            INSERT INTO daily_stats (date, reviews_done, new_cards, knowledge_score, accuracy)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                reviews_done = excluded.reviews_done,
                new_cards = excluded.new_cards,
                knowledge_score = excluded.knowledge_score,
                accuracy = excluded.accuracy,
                updated_at = CURRENT_TIMESTAMP
        """, (today, reviews_done, prev['new_cards'] + (1 if was_new else 0),
              prev['knowledge_score'] + rating, accuracy))

    # ── stats & graphing ──────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        cur = self.conn.cursor()
        total = cur.execute("SELECT COUNT(*) FROM vocabulary").fetchone()[0]
        states = {row['state']: row['count'] for row in cur.execute(
            "SELECT state, COUNT(*) as count FROM fsrs_cards GROUP BY state")}
        hsk = {row['hsk_level']: row['count'] for row in cur.execute(
            "SELECT hsk_level, COUNT(*) as count FROM vocabulary "
            "WHERE hsk_level IS NOT NULL GROUP BY hsk_level ORDER BY hsk_level")}
        reviews_today = cur.execute(
            "SELECT COUNT(*) FROM reviews WHERE date(reviewed_at, 'localtime') = date('now', 'localtime')"
        ).fetchone()[0]
        reviews_week = cur.execute(
            "SELECT COUNT(*) FROM reviews WHERE date(reviewed_at, 'localtime') >= date('now', 'localtime', '-6 days')"
        ).fetchone()[0]
        mastered = cur.execute("SELECT COUNT(*) FROM fsrs_cards WHERE stability > 100").fetchone()[0]
        last_review = cur.execute("SELECT MAX(reviewed_at) FROM reviews").fetchone()[0]

        return {
            'total': total,
            'states': states,
            'due_now': self.count_due(),
            'hsk': hsk,
            'reviews_today': reviews_today,
            'reviews_week': reviews_week,
            'mastered': mastered,
            'last_review': last_review,
        }

    def get_graph_data(self, days: int = 30) -> List[Dict[str, Any]]:
        rows = self.conn.execute("""
            SELECT date, reviews_done, new_cards, knowledge_score, accuracy
            FROM daily_stats
            WHERE date >= date('now', 'localtime', '-' || ? || ' days')
            ORDER BY date ASC
        """, (days,)).fetchall()
        return [dict(row) for row in rows]
