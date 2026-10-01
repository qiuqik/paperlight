"""Account-scoped reading time, independent of parsing and reading progress."""
from datetime import date, timedelta
from pydantic import BaseModel, Field


class ReadingTick(BaseModel):
    eventId: str = Field(pattern=r'^[a-f0-9-]{36}$')
    documentId: str = Field(pattern=r'^[a-f0-9]{32}$')
    day: date
    seconds: int = Field(ge=1, le=60)


def record_tick(accounts, user_id: str, tick: ReadingTick) -> None:
    if abs((tick.day - date.today()).days) > 1:
        raise ValueError('Reading date is outside the accepted window')
    with accounts.connect() as db:
        db.execute('INSERT OR IGNORE INTO reading_activity(user_id,event_id,document_id,day,seconds) VALUES(?,?,?,?,?)',
                   (user_id, tick.eventId, tick.documentId, tick.day.isoformat(), tick.seconds))


def summary(accounts, user_id: str, today: date) -> dict:
    if abs((today - date.today()).days) > 1:
        raise ValueError('Invalid current date')
    start = today - timedelta(days=83)
    with accounts.connect() as db:
        rows = [dict(row) for row in db.execute('SELECT day,SUM(seconds) AS seconds FROM reading_activity WHERE user_id=? GROUP BY day ORDER BY day', (user_id,))]
    totals = {row['day']: row['seconds'] for row in rows}
    return {'totalSeconds': sum(totals.values()), 'activeDays': len(totals),
            'days': [{'date': (start + timedelta(days=index)).isoformat(), 'seconds': totals.get((start + timedelta(days=index)).isoformat(), 0)} for index in range(84)]}
