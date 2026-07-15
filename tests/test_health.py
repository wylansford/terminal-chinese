import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib'))

from db import Database  # noqa: E402
from fsrs_manager import next_due_human  # noqa: E402
from importer import sync_bundled  # noqa: E402


class HealthTests(unittest.TestCase):
    def make_db(self, root):
        db = Database(root / 'tutor.db')
        db.__enter__()
        db.init_schema()
        self.addCleanup(db.__exit__, None, None, None)
        return db

    @staticmethod
    def write_pack(root, english):
        pack = root / 'hsk1' / 'pack.json'
        pack.parent.mkdir(parents=True, exist_ok=True)
        pack.write_text(json.dumps({'vocabulary': [{
            'simplified': '测试', 'traditional': '測試', 'pinyin': 'cè shì',
            'english': english, 'hsk_level': 1,
        }]}, ensure_ascii=False), encoding='utf-8')

    def test_bundled_sync_refreshes_content_without_resetting_card(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / 'data'
            db = self.make_db(root)
            self.write_pack(data, 'test')
            self.assertEqual(sync_bundled(db, data), 1)
            db.conn.execute("UPDATE fsrs_cards SET state = 'Review' WHERE word_id = 1")
            db.conn.commit()

            self.write_pack(data, 'updated test')
            self.assertEqual(sync_bundled(db, data), 1)
            row = db.conn.execute("""
                SELECT v.english, c.state FROM vocabulary v
                JOIN fsrs_cards c ON c.word_id = v.word_id
            """).fetchone()
            self.assertEqual(dict(row), {'english': 'updated test', 'state': 'Review'})

    def test_bundled_sync_does_not_overwrite_user_word(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / 'data'
            db = self.make_db(root)
            db.add_vocabulary({
                'simplified': '测试', 'traditional': '測試', 'pinyin': 'cè shì',
                'english': 'my test', 'source': 'user-import.json',
            })
            self.write_pack(data, 'bundled test')
            sync_bundled(db, data)
            english = db.conn.execute("SELECT english FROM vocabulary").fetchone()[0]
            self.assertEqual(english, 'my test')

    def test_overdue_cards_display_now(self):
        overdue = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        self.assertEqual(next_due_human(overdue), 'now')

    def test_schema_version_is_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            db = self.make_db(Path(temp))
            self.assertEqual(db.conn.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_bundled_data_is_valid_and_unique(self):
        seen = set()
        for path in sorted((ROOT / 'data').glob('hsk*/*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertIsInstance(data.get('vocabulary'), list)
            level = int(path.parent.name[3:])
            for word in data['vocabulary']:
                key = (word['simplified'], word['pinyin'])
                self.assertNotIn(key, seen, f'duplicate bundled word: {key}')
                self.assertEqual(word['hsk_level'], level)
                seen.add(key)


if __name__ == '__main__':
    unittest.main()
