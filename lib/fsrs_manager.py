"""FSRS scheduling wrapper (fsrs v6+)"""

import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fsrs import Scheduler, Card, Rating, State

_scheduler = Scheduler()

RATINGS = {1: Rating.Again, 2: Rating.Hard, 3: Rating.Good, 4: Rating.Easy}
# fsrs v6+ has no separate New state; unreviewed cards enter as Learning
STATES = {'New': State.Learning, 'Learning': State.Learning,
          'Review': State.Review, 'Relearning': State.Relearning}
STATE_NAMES = {State.Learning: 'Learning', State.Review: 'Review', State.Relearning: 'Relearning'}


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"warning: malformed timestamp in card data: {value!r}", file=sys.stderr)
        return None


def review_card(card_data: Dict[str, Any], rating: int) -> Dict[str, Any]:
    """Apply a rating to a DB card dict; returns updated card fields to persist"""
    now = datetime.now(timezone.utc)
    card = Card(
        card_id=card_data['card_id'],
        state=STATES.get(card_data.get('state', 'New'), State.Learning),
        step=card_data.get('step'),
        stability=card_data.get('stability'),
        difficulty=card_data.get('difficulty'),
        due=_parse_dt(card_data.get('due')),
        last_review=_parse_dt(card_data.get('last_review')),
    )
    scheduled, _ = _scheduler.review_card(card, RATINGS[rating], review_datetime=now)
    return {
        'due': scheduled.due.isoformat() if scheduled.due else None,
        'stability': scheduled.stability,
        'difficulty': scheduled.difficulty,
        'step': scheduled.step,
        'state': STATE_NAMES.get(scheduled.state, 'Learning'),
        'last_review': now.isoformat(),
        'reps': card_data.get('reps', 0) + 1,
    }


def next_due_human(due_iso: Optional[str]) -> str:
    """'in 3 days', 'in 2 hours', 'now'"""
    due = _parse_dt(due_iso)
    if due is None:
        return "later"
    diff = due - datetime.now(timezone.utc)
    if diff.total_seconds() <= 0:
        return "now"
    days, hours, minutes = diff.days, diff.seconds // 3600, (diff.seconds % 3600) // 60
    if days > 30:
        months = days // 30
        return f"in {months} month{'s' if months > 1 else ''}"
    if days > 0:
        return f"in {days} day{'s' if days > 1 else ''}"
    if hours > 0:
        return f"in {hours} hour{'s' if hours > 1 else ''}"
    if minutes > 0:
        return f"in {minutes} minute{'s' if minutes > 1 else ''}"
    return "now"
