"""Qualità automatica: se il dispositivo non regge (ritardo alto, fotogrammi persi), il PC abbassa
la qualità di un livello; quando torna fluido la rialza, mai oltre quella scelta a mano.

Livelli: 0 Massima, 1 Fluida, 2 Media, 3 Leggera (numero più alto = più leggera).
"""
LOWEST = 3
BAD_RTT = 250       # ms: oltre, il dispositivo è in affanno
GOOD_RTT = 100      # ms: sotto, è fluido
BAD_TURNS = 3       # controlli consecutivi in affanno prima di abbassare (uno ogni ~2 s: 6 s di fila)
SETTLE = 10         # s dopo un cambio o un ricollegamento in cui non giudico (la cattura sta ripartendo)
RAISE_WAIT = 60     # s fluidi prima di provare a rialzare
RAISE_WAIT_MAX = 600


class AutoQuality:
    def __init__(self, now):
        self.last_change = now
        self.bad = 0
        self.good_since = None
        self.raise_wait = RAISE_WAIT
        self.raised_at = None

    def changed(self, now):
        """Qualità appena cambiata (a mano o da qui): riparto da zero."""
        self.last_change = now
        self.bad = 0
        self.good_since = None

    def judge(self, now, level, best, rtt=None, shown=None, sent=None, drops=0, silent=False):
        """Restituisce (nuovo livello, motivo) oppure (None, '').
        rtt in ms, shown = fotogrammi al secondo mostrati dal dispositivo, sent = inviati dal PC,
        drops = fotogrammi saltati perché la coda era piena, silent = nessuna statistica da troppo."""
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
                    # avevo appena rialzato e non regge: la prossima volta aspetto di più
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
