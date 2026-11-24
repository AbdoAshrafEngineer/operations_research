"""Working-time axis (no ORM).

Scheduling with working hours is solved on a *working-minute* axis: minute ``m`` is the
m-th minute of work after the planning origin. Durations then pause automatically outside
working hours (like a work order that continues the next morning), precedence and
"one job at a time" stay exact because the mapping real time <-> working time is monotone.
"""
from datetime import timedelta


def _ceil_minute(dt):
    if dt.second or dt.microsecond:
        return dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return dt


def _floor_minute(dt):
    return dt.replace(second=0, microsecond=0)


class WorkAxis:
    """Map naive datetimes to working minutes and back.

    :param intervals: iterable of ``(start, end)`` naive datetimes (any order, may overlap);
        only the part after ``origin`` is kept.
    :param origin: naive datetime; working minute 0 is the first working minute at/after it.
    """

    def __init__(self, intervals, origin):
        self.origin = origin
        cleaned = []
        for start, end in sorted(intervals):
            start, end = _ceil_minute(max(start, origin)), _floor_minute(end)
            if end <= start:
                continue
            if cleaned and start <= cleaned[-1][1]:
                cleaned[-1] = (cleaned[-1][0], max(cleaned[-1][1], end))
            else:
                cleaned.append((start, end))
        self.intervals = cleaned
        self.cumulative = []
        total = 0
        for start, end in cleaned:
            self.cumulative.append(total)
            total += int((end - start).total_seconds() // 60)
        self.total = total

    @property
    def end(self):
        return self.intervals[-1][1] if self.intervals else self.origin

    def to_work(self, dt):
        """Working minutes between the origin and ``dt`` (0 before the first working minute)."""
        work = 0
        for (start, end), cum in zip(self.intervals, self.cumulative):
            if dt <= start:
                return work
            if dt < end:
                return cum + int((dt - start).total_seconds() // 60)
            work = cum + int((end - start).total_seconds() // 60)
        return work

    def _locate(self, minute, strict):
        if minute < 0:
            raise ValueError("negative working minute")
        for (start, end), cum in zip(self.intervals, self.cumulative):
            length = int((end - start).total_seconds() // 60)
            limit = cum + length
            if minute < limit or (not strict and minute <= limit):
                return start + timedelta(minutes=minute - cum)
        raise ValueError("working minute %s is beyond the calendar horizon" % minute)

    def to_real_start(self, minute):
        """Real time at which work starting at working ``minute`` begins (never at a closing time)."""
        return self._locate(minute, strict=True)

    def to_real_end(self, minute):
        """Real time at which work ending at working ``minute`` ends (never at an opening time)."""
        if minute == 0:
            return self.intervals[0][0] if self.intervals else self.origin
        return self._locate(minute, strict=False)
