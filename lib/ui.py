"""Terminal rendering and single-key input for the review session"""

import sys
import termios
import tty
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.text import Text

console = Console(highlight=False)

MAX_CARD_WIDTH = 60

STATE_STYLES = {'New': 'cyan', 'Learning': 'yellow', 'Review': 'green', 'Relearning': 'red'}
RATING_FEEDBACK = {
    4: ('✓', 'green', 'got it'),
    2: ('~', 'yellow', 'close'),
    1: ('✗', 'red', 'missed it'),
}


def read_key() -> str:
    """Block for one raw keypress; Ctrl-C raises KeyboardInterrupt"""
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if ch == '\x03':
        raise KeyboardInterrupt
    return ch


def width() -> int:
    return min(MAX_CARD_WIDTH, console.width - 2)


def rule():
    console.print('─' * width(), style='dim')


def center(*parts, style: Optional[str] = None):
    """Print one centered line; parts are (text, style) tuples or plain strings"""
    text = Text(style=style or '')
    for part in parts:
        if isinstance(part, tuple):
            text.append(part[0], style=part[1])
        else:
            text.append(part)
    console.print(text, justify='center', width=width())


def _hanzi_line(card: Dict[str, Any], reveal: bool = False) -> Text:
    text = Text()
    text.append(card['simplified'], style='bold bright_cyan')
    if card['traditional'] != card['simplified']:
        text.append(f" · {card['traditional']}", style='cyan')
    if reveal:
        text.append(f" · {card['pinyin']}", style='bold')
        if card.get('emoji'):
            text.append(f"  {card['emoji']}")  # only after reveal — it hints the meaning
    return text


def render_card(card: Dict[str, Any], show_pinyin: bool, due_count: int):
    """The question side: hanzi front and center, pinyin optional, meaning hidden"""
    state = card['state']
    seen = 'new word' if state == 'New' else f"seen {card.get('reps', 0)}×"

    console.print()
    rule()
    console.print()
    center(_hanzi_line(card))
    if show_pinyin:
        center((card['pinyin'], 'cyan'))
    console.print()
    center((seen, f"dim {STATE_STYLES.get(state, '')}"), ('  ·  ', 'dim'), (f"{due_count} due", 'dim'))

    examples = card.get('examples') or []
    if examples:
        console.print()
        for ex in examples[:3]:
            console.print(Text(f"  {ex.get('chinese', '')}"))
            if show_pinyin and ex.get('pinyin'):
                console.print(Text(f"  {ex['pinyin']}", style='dim'))

    console.print()
    rule()
    legend = Text()
    for key, label, style in (('k', 'know', 'green'), ('g', 'unsure', 'yellow'), ('d', "don't know", 'red')):
        legend.append(f' {key} ', style=f'bold {style}')
        legend.append(f'{label}   ', style='default')
    console.print(legend)
    console.print(Text(' p pinyin   s skip   x delete   q quit   ⇧ = keep going', style='dim'))


def render_reveal(card: Dict[str, Any], feedback: Text):
    """The answer side: everything, paced by a real keypress from the caller"""
    console.print()
    rule()
    console.print()
    center(_hanzi_line(card, reveal=True))
    center((card['english'], 'bold yellow'))

    measure = card.get('measure_word')
    if isinstance(measure, dict) and measure.get('character'):
        center((f"measure word: {measure['character']} {measure.get('pinyin', '')}", 'dim'))
    console.print()

    for icon, field in (('💡', 'mnemonic'), ('📜', 'etymology')):
        value = card.get(field)
        if value:
            console.print(Text(f" {icon} {value}", style='dim' if field == 'etymology' else ''),
                          width=width())
            console.print()

    for ex in (card.get('examples') or []):
        console.print(Text(f"  {ex.get('chinese', '')}"))
        detail = ' — '.join(filter(None, [ex.get('pinyin'), ex.get('english')]))
        if detail:
            console.print(Text(f"    {detail}", style='dim'))
    if card.get('examples'):
        console.print()

    related = card.get('related_words') or []
    if related:
        console.print(Text('  related  ', style='dim') + Text(' · '.join(related), style='cyan'))
        console.print()

    rule()
    line = Text(' ')
    line.append_text(feedback)
    console.print(line)


def feedback_text(rating: int, next_due: str) -> Text:
    symbol, style, word = RATING_FEEDBACK[rating]
    text = Text()
    text.append(f'{symbol} {word}', style=f'bold {style}')
    text.append(f' — next review {next_due}', style='default')
    return text


def render_all_done(reviewed: int, next_due: Optional[str]):
    console.print()
    if reviewed:
        console.print(Text(f'✓ all caught up — {reviewed} reviewed', style='bold green'))
    else:
        console.print(Text('✓ all caught up', style='bold green'))
    if next_due:
        console.print(Text(f'  next card due {next_due}', style='dim'))
    console.print(Text('  run \'ct config\' to adjust difficulty (HSK levels)', style='dim'))
    console.print()


def _bar(count: int, total: int, size: int = 20) -> Text:
    filled = round(size * count / total) if total else 0
    return Text('▓' * filled + '░' * (size - filled), style='dim')


def render_stats(stats: Dict[str, Any], spark: Optional[str] = None):
    total = stats['total']
    states = stats['states']

    console.print()
    console.print(Text('terminal-chinese', style='bold bright_cyan') + Text(f' · {total} words', style='dim'))
    console.print()

    for name, style in (('New', 'cyan'), ('Learning', 'yellow'), ('Review', 'green'), ('Relearning', 'red')):
        count = states.get(name, 0)
        if name == 'Relearning' and count == 0:
            continue
        pct = 100 * count / total if total else 0
        line = Text(f'  {name.lower():<10}', style=style)
        line.append(f'{count:>5}  ')
        line.append_text(_bar(count, total))
        line.append(f'  {pct:.0f}%', style='dim')
        console.print(line)

    console.print()
    console.print(Text(f"  due now  {stats['due_now']}", style='bold') +
                  Text(f"    mastered  {stats['mastered']}", style='default'))
    console.print(Text(f"  today  {stats['reviews_today']} reviews    this week  {stats['reviews_week']}",
                       style='dim'))

    if spark:
        console.print()
        console.print(Text(f'  last 30d  ', style='dim') + Text(spark, style='cyan'))

    if stats['hsk']:
        console.print()
        peak = max(stats['hsk'].values())
        for level in sorted(stats['hsk']):
            count = stats['hsk'][level]
            line = Text(f'  hsk {level}  ', style='dim')
            line.append('▓' * max(1, round(10 * count / peak)), style='cyan')
            line.append(f' {count}', style='dim')
            console.print(line)
    console.print()
