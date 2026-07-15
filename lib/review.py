"""Interactive review session -- a single process for the whole loop"""

from pathlib import Path
from typing import Dict

import ui
from db import Database

RATING_BY_KEY = {'k': 4, 'g': 2, 'd': 1}


def run_session(db: Database, config: Dict, vocab_dir: Path):
    _auto_import(db, vocab_dir)
    if not _ensure_vocabulary(db):
        return

    leveled = db.backfill_unleveled_words()
    if leveled:
        ui.console.print(f'[dim]leveled {leveled} custom word(s) so they stay in rotation[/]\n')

    reviewed = 0
    while True:
        card = db.get_next_card(config['max_new_cards_per_day'], config.get('hsk_levels'))
        if card is None:
            ui.render_all_done(reviewed, _next_due(db))
            return

        action = _present(db, card)
        if action in ('again', 'next'):
            if action == 'again':
                reviewed += 1
            ui.console.print()  # gap before the next card
        else:  # 'done' or 'quit'
            if action == 'done':
                reviewed += 1
            ui.console.print()
            return


def _present(db: Database, card: Dict) -> str:
    """Show one card and handle its keys.

    Returns 'done' (answered, stop), 'again' (answered, next card),
    'next' (skip/delete, next card), or 'quit'.
    """
    show_pinyin = False
    due_count = db.count_due()
    while True:
        ui.render_card(card, show_pinyin, due_count)
        while True:
            key = ui.read_key()
            if key in ('p', 'P'):
                show_pinyin = not show_pinyin
                ui.console.clear()
                break  # re-render
            if key in ('k', 'g', 'd', 'K', 'G', 'D'):
                _answer(db, card, RATING_BY_KEY[key.lower()])
                return 'again' if key.isupper() else 'done'
            if key in ('x', 'X'):
                if _confirm_delete(db, card):
                    return 'next'
                ui.console.clear()
                break  # re-render
            if key in ('s', 'S'):
                return 'next'
            if key in ('q', 'Q', '\r', '\n', '\x1b'):
                return 'quit'
            # any other key: ignore and keep listening


def _answer(db: Database, card: Dict, rating: int):
    import fsrs_manager  # deferred: not needed to paint the first card

    updated = fsrs_manager.review_card(card, rating)
    db.record_review(card['card_id'], rating, updated, was_new=card['state'] == 'New')

    ui.console.clear()  # the reveal replaces the question card
    ui.render_reveal(card, ui.feedback_text(rating, fsrs_manager.next_due_human(updated['due'])))


def _confirm_delete(db: Database, card: Dict) -> bool:
    ui.console.print()
    ui.console.print(
        f"[bold red]delete {card['simplified']} ({card['english']}) and all its history?[/] [dim]y/N[/] ",
        end='')
    if ui.read_key() in ('y', 'Y'):
        db.delete_word(card['simplified'])
        db.conn.commit()
        return True
    return False


def _next_due(db: Database) -> str:
    row = db.conn.execute(
        "SELECT MIN(due) FROM fsrs_cards WHERE state != 'New' AND due IS NOT NULL"
    ).fetchone()
    if not row or not row[0]:
        return ''
    import fsrs_manager
    return fsrs_manager.next_due_human(row[0])


def _ensure_vocabulary(db: Database) -> bool:
    """Synchronize the bundled HSK packs into the local database.

    New cards default to HSK 1 only (config['hsk_levels']) -- the rest sit in
    the deck ready to go the moment the level is widened with `ct config --hsk`.
    """
    data_dir = Path(__file__).resolve().parent.parent / 'data'
    packs = sorted(data_dir.glob('hsk*/*.json'))
    if not packs:
        ui.console.print('[dim]no vocabulary yet - add words with: ct add <word>[/]')
        return False

    from importer import sync_bundled
    imported = sync_bundled(db, data_dir)
    if imported:
        ui.console.print(
            f'[bold bright_cyan]terminal-chinese[/] - synchronized {imported} bundled words.\n'
            f'[dim]Starting with HSK 1 - widen anytime: ct config --hsk 1,2,3[/]\n'
        )
    return db.conn.execute("SELECT COUNT(*) FROM vocabulary").fetchone()[0] > 0


def _auto_import(db: Database, vocab_dir: Path):
    """Import any JSON files dropped into the vocabulary directory"""
    if not vocab_dir.is_dir() or not any(vocab_dir.glob('*.json')):
        return
    from importer import auto_import
    for filename, count in auto_import(db, vocab_dir):
        ui.console.print(f'[dim]imported {filename} - {count} words[/]')
