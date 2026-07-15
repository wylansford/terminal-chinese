"""ASCII progress graphs"""

from datetime import datetime, timedelta
from typing import Any, Dict, List

SPARK_CHARS = '▁▂▃▄▅▆▇█'


def fill_missing_dates(data: List[Dict[str, Any]], end_date: str = None) -> List[Dict[str, Any]]:
    """Zero-fill calendar gaps so sparse data doesn't misrepresent streaks/trends"""
    if not data:
        return []
    by_date = {d['date']: d for d in data}
    current = datetime.fromisoformat(data[0]['date']).date()
    end = datetime.fromisoformat(end_date).date() if end_date else datetime.now().date()

    filled = []
    while current <= end:
        key = current.isoformat()
        filled.append(by_date.get(key, {
            'date': key, 'reviews_done': 0, 'new_cards': 0,
            'knowledge_score': 0, 'accuracy': 0,
        }))
        current += timedelta(days=1)
    return filled


def sparkline(values: List[float], max_width: int = 30) -> str:
    """Compress values into a fixed-width sparkline (bucket-averaged if needed)"""
    if not values:
        return ''
    if len(values) > max_width:
        bucket = len(values) / max_width
        values = [
            sum(values[int(i * bucket):int((i + 1) * bucket)]) / max(1, int((i + 1) * bucket) - int(i * bucket))
            for i in range(max_width)
        ]
    peak = max(values)
    if peak == 0:
        return '▁' * len(values)
    return ''.join(SPARK_CHARS[min(len(SPARK_CHARS) - 1, int(v / peak * (len(SPARK_CHARS) - 1)))] for v in values)


def line_graph(data: List[Dict[str, Any]], metric: str, width: int = 60, height: int = 12) -> str:
    """Full-size ASCII line graph of one metric over time"""
    data = fill_missing_dates(data)
    values = [float(d.get(metric, 0)) for d in data]
    dates = [d['date'] for d in data]
    if not values or max(values) == 0:
        return f"No {metric} data to display."

    max_val, min_val = max(values), min(values)
    val_range = max_val - min_val or 1

    lines = []
    for row in range(height, -1, -1):
        if row == height:
            label = f"{max_val:6.1f} │"
        elif row == 0:
            label = f"{min_val:6.1f} │"
        elif row == height // 2:
            label = f"{(max_val + min_val) / 2:6.1f} │"
        else:
            label = "       │"

        chars = []
        for i, val in enumerate(values[:width]):
            normalized = (val - min_val) / val_range * height
            if abs(normalized - row) < 0.5:
                chars.append('●')
            elif i > 0:
                prev = (values[i - 1] - min_val) / val_range * height
                chars.append('│' if min(prev, normalized) < row < max(prev, normalized) else ' ')
            else:
                chars.append(' ')
        lines.append(label + ''.join(chars))

    lines.append("       └" + "─" * min(width, len(values)))

    # date axis: first and last
    first, last = _short_date(dates[0]), _short_date(dates[-1])
    gap = min(width, len(values)) - len(first) - len(last)
    lines.append("        " + first + " " * max(1, gap) + last)
    lines.append("")
    lines.append(f"{metric} · {len(dates)} days · peak {max_val:.1f} · avg {sum(values) / len(values):.1f}")
    return "\n".join(lines)


def _short_date(date_str: str) -> str:
    return datetime.fromisoformat(date_str).strftime("%m/%d")
