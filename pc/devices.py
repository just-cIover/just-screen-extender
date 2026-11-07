"""Dispositivi conosciuti e abbinamento con PIN.

- Un dispositivo nuovo deve inserire un PIN di 6 cifre mostrato sul PC. Il PIN è diverso
  ogni volta, scade dopo 5 minuti e dopo 5 tentativi sbagliati ne viene generato un altro.
- Abbinato il dispositivo, il PC gli consegna una chiave segreta (token) che lui conserva:
  dalle volte successive si collega senza PIN.
- Sul PC resta solo l'impronta (SHA-256) della chiave, non la chiave stessa.
- Ogni dispositivo ha il suo schermo virtuale ("slot"), sempre lo stesso, così Windows
  ricorda per lui posizione, risoluzione e ridimensionamento.
"""
import hashlib
import hmac
import json
import os
import secrets
import time

PIN_TTL = 300        # secondi di validità di un PIN
PIN_TRIES = 5        # tentativi sbagliati prima di cambiare PIN
MAX_DEVICES = 4      # schermi virtuali al massimo
MAX_PENDING = 3      # richieste di abbinamento aperte insieme


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                self.data = json.load(f)
        self.pins = {}  # id dispositivo -> {'pin', 'scade', 'errori'}

    def save(self):
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)

    # ---- riconoscimento ----
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

    # ---- abbinamento ----
    def pin_for(self, dev_id, name=''):
        """PIN valido per questo dispositivo (ne crea uno nuovo se manca o è scaduto).
        Restituisce (pin, nuovo?)."""
        p = self.pins.get(dev_id)
        if p and p['scade'] > time.time():
            if name:
                p['nome'] = name
            return p['pin'], False
        self.pins = {k: v for k, v in self.pins.items() if v['scade'] > time.time()}
        if len(self.pins) >= MAX_PENDING:
            return None  # troppe richieste aperte insieme: chi prova a indovinare non ne apre altre
        pin = '%06d' % secrets.randbelow(1_000_000)
        self.pins[dev_id] = {'pin': pin, 'scade': time.time() + PIN_TTL, 'errori': 0, 'nome': name}
        return pin, True

    def try_pin(self, dev_id, pin):
        """'ok', 'errato' oppure 'nuovo' (troppi errori o scaduto: c'è un PIN nuovo sul PC)."""
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
        """Registra un dispositivo nuovo. Restituisce (token da consegnargli, voci sostituite),
        oppure (None, []) se sono già abbinati troppi dispositivi.

        Su iPad e iPhone l'app aggiunta alla schermata Home ha una memoria separata da Safari:
        per il PC sembra un dispositivo nuovo. Se nome e indirizzo coincidono con un dispositivo
        già noto è lo stesso apparecchio: sostituisco le vecchie voci e riuso il suo schermo
        (con posizione e impostazioni già ricordate) invece di crearne un altro."""
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
        if old:  # stesso apparecchio: tengo posizione e risoluzione che Windows ricorda
            entry.update({k: old[k] for k in ('posizione', 'configurato', 'risoluzione') if k in old})
        else:
            entry['scala_da_impostare'] = True  # dispositivo davvero nuovo: scelgo io il ridimensionamento
        self.data[dev_id] = entry
        self.save()
        return token, same
