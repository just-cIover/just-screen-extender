"""Automatic quality: if the device can't keep up (high delay, lost frames), the PC lowers
the quality by one level; when it's smooth again it raises it, never above the one chosen by hand.

Levels: 0 Best, 1 Smooth, 2 Medium, 3 Light (higher number = lighter).
"""
LOWEST = 3
BAD_RTT = 250       # ms: above this, the device is struggling
GOOD_RTT = 100      # ms: below this, it's smooth
BAD_TURNS = 3       # consecutive struggling checks before lowering (one every ~2 s: 6 s in a row)
SETTLE = 10         # s after a change or a reconnection during which I don't judge (capture is restarting)
RAISE_WAIT = 60     # smooth seconds before trying to raise
RAISE_WAIT_MAX = 600


class AutoQuality:
    def __init__(self, now):
        self.last_change = now
        self.bad = 0
        self.good_since = None
        self.raise_wait = RAISE_WAIT
        self.raised_at = None

    def changed(self, now):
        """Quality just changed (by hand or from here): start from zero."""
        self.last_change = now
        self.bad = 0
        self.good_since = None

    def judge(self, now, level, best, rtt=None, shown=None, sent=None, drops=0, silent=False):
        """Returns (new level, reason) or (None, '').
        rtt in ms, shown = frames per second shown by the device, sent = sent by the PC,
        drops = frames skipped because the queue was full, silent = no statistics for too long."""
        if now - self.last_change < SETTLE:
            return None, ''
        reasons = []
        if silent:
            reasons.append('non risponde')
        if rtt is not None and rtt > BAD_RTT:
            reasons.append(f'ritardo {int(rtt)} ms')
        if sent and sent >= 10 and shown is not None and shown < 0.6 * sent:
            reasons.append(f'mostra {int(shown)} fotogrammi su {int(sent)}')
        if drops:
            reasons.append(f'{drops} fotogrammi saltati')
        if reasons:
            self.good_since = None
            self.bad += 1
            if self.bad >= BAD_TURNS and level < LOWEST:
                if self.raised_at is not None and now - self.raised_at < 60:
                    # I had just raised it and it can't keep up: next time I wait longer
                    self.raise_wait = min(self.raise_wait * 2, RAISE_WAIT_MAX)
                self.raised_at = None
                return level + 1, ', '.join(reasons)
            return None, ''
        self.bad = 0
        fluid = (rtt is None or rtt < GOOD_RTT) and (not sent or sent < 10 or (shown or 0) >= 0.85 * sent)
        if not fluid:
            self.good_since = None
            return None, ''
        if self.good_since is None:
            self.good_since = now
        if level > best and now - self.good_since >= self.raise_wait:
            self.raised_at = now
            return level - 1, 'di nuovo fluido'
        return None, ''
