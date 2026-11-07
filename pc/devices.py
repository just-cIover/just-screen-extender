"""Known devices and pairing with a PIN.

- A new device must enter a 6-digit PIN shown on the PC. The PIN is different
  every time, expires after 5 minutes and after 5 wrong attempts a new one is generated.
- Once paired, the PC hands the device a secret key (token) that it keeps:
  from then on it connects without a PIN.
- The PC keeps only the fingerprint (SHA-256) of the key, not the key itself.
- Every device has its own virtual screen ("slot"), always the same, so Windows
  remembers its position, resolution and scaling.
"""
import hashlib
import hmac
import json
import os
import secrets
import time

PIN_TTL = 300        # seconds a PIN is valid
PIN_TRIES = 5        # wrong attempts before changing the PIN
MAX_DEVICES = 4      # maximum number of virtual screens
MAX_PENDING = 3      # pairing requests open at the same time


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                self.data = json.load(f)
        self.pins = {}  # device id -> {'pin', 'scade', 'errori'}

    def save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ---- recognition ----
    def check(self, dev_id, token):
        d = self.data.get(dev_id)
        return bool(d and token and hmac.compare_digest(d['token'], _hash(token)))

    def get(self, dev_id):
        return self.data.get(dev_id)

    def update(self, dev_id, **kv):
        self.data[dev_id].update(kv)
        self.save()

    def remove(self, dev_id):
        self.data.pop(dev_id, None)
        self.save()

    # ---- pairing ----
    def pin_for(self, dev_id, name=''):
        """Valid PIN for this device (creates a new one if missing or expired).
        Returns (pin, new?)."""
        p = self.pins.get(dev_id)
        if p and p['scade'] > time.time():
            if name:
                p['nome'] = name
            return p['pin'], False
        self.pins = {k: v for k, v in self.pins.items() if v['scade'] > time.time()}
        if len(self.pins) >= MAX_PENDING:
            return None  # too many requests open at once: someone guessing can't open more
        pin = '%06d' % secrets.randbelow(1_000_000)
        self.pins[dev_id] = {'pin': pin, 'scade': time.time() + PIN_TTL, 'errori': 0, 'nome': name}
        return pin, True

    def try_pin(self, dev_id, pin):
        """'ok', 'errato' or 'nuovo' (too many errors or expired: there's a new PIN on the PC)."""
        p = self.pins.get(dev_id)
        if not p or p['scade'] <= time.time():
            self.pins.pop(dev_id, None)
            return 'nuovo'
        if hmac.compare_digest(p['pin'], str(pin)):
            self.pins.pop(dev_id, None)
            return 'ok'
        p['errori'] += 1
        if p['errori'] >= PIN_TRIES:
            self.pins.pop(dev_id, None)
            return 'nuovo'
        return 'errato'

    def free_slot(self):
        used = {d['slot'] for d in self.data.values()}
        return next((s for s in range(MAX_DEVICES) if s not in used), None)

    def pair(self, dev_id, name, size, dpr, ip=None):
        """Registers a new device. Returns (token to hand over, replaced entries),
        or (None, []) if too many devices are already paired.

        On iPad and iPhone the app added to the Home Screen has storage separate from Safari:
        to the PC it looks like a new device. If name and address match an already known
        device it's the same hardware: I replace the old entries and reuse its screen
        (with position and settings already remembered) instead of creating another one."""
        same = [k for k, v in self.data.items()
                if k != dev_id and ip and v.get('ip') == ip and v.get('nome') == name]
        old = self.data.get(dev_id) or (min((self.data[k] for k in same), key=lambda v: v['slot']) if same else None)
        for k in same:
            self.data.pop(k)
        slot = old['slot'] if old else self.free_slot()
        if slot is None:
            return None, []
        token = secrets.token_urlsafe(32)
        entry = {
            'nome': name, 'token': _hash(token), 'slot': slot,
            'risoluzione': list(size), 'dpr': dpr, 'qualita': (old or {}).get('qualita', 1),
            'abbinato': time.strftime('%Y-%m-%d %H:%M'), 'configurato': False, 'ip': ip,
        }
        if old:  # same hardware: keep the position and resolution Windows remembers
            entry.update({k: old[k] for k in ('posizione', 'configurato', 'risoluzione') if k in old})
        else:
            entry['scala_da_impostare'] = True  # really new device: I choose the scaling
        self.data[dev_id] = entry
        self.save()
        return token, same
