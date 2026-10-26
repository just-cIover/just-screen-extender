"""Connessione sicura (HTTPS) tra PC e dispositivi, con certificati creati sul PC.

- Un "certificato radice" tutto tuo (certs/ca.crt), valido 10 anni: va installato una volta
  sul dispositivo (Impostazioni iPad > Generali > VPN e gestione dispositivi, poi
  Info > Impostazioni attendibilità certificati).
- Un certificato del server firmato da quello radice, valido per tutti gli indirizzi del PC
  (Wi-Fi di casa, hotspot...). Contiene già tutti gli indirizzi dell'hotspot dell'iPhone e
  ricorda quelli visti prima, così cambiando rete di solito non va rifatto; se serve si rifà da
  solo (nuovo indirizzo o scadenza vicina). Il dispositivo non deve reinstallare niente: si fida
  del certificato radice, che non cambia.
  Durata 397 giorni: oltre, iPadOS e Safari non lo accettano.
La chiave del certificato radice resta solo sul PC (certs/ca.key).
"""
import datetime
import ipaddress
import os
import socket
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), 'dati')  # dati personali (fuori da git)
DIR = os.path.join(DATA, 'certs')
CA_KEY, CA_CRT = os.path.join(DIR, 'ca.key'), os.path.join(DIR, 'ca.crt')
SRV_KEY, SRV_CRT = os.path.join(DIR, 'server.key'), os.path.join(DIR, 'server.crt')
CA_NAME = 'Schermo Esteso - certificato del PC'
LOCAL_ONLY = [x509.IPAddress(ipaddress.ip_network(n)) for n in
              ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '169.254.0.0/16',
               '100.64.0.0/10')] + \
             [x509.DNSName('localhost'), x509.DNSName('local')]  # 'local' = qualsiasi nome .local
# 100.64.0.0/10: indirizzi di Tailscale (rete privata tra i propri dispositivi), non siti di Internet
# hotspot dell'iPhone: il PC prende sempre uno di questi, quindi il certificato li ha già tutti
HOTSPOT = ['172.20.10.%d' % i for i in range(1, 16)]
MAX_OLD = 16  # indirizzi di reti viste in passato da tenere nel certificato (casa, ufficio...)


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _write_key(path, key):
    with open(path, 'wb') as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))


def _load_key(path):
    with open(path, 'rb') as f:
        return serialization.load_pem_private_key(f.read(), None)


def _load_crt(path):
    with open(path, 'rb') as f:
        return x509.load_pem_x509_certificate(f.read())


def ensure_ca():
    os.makedirs(DIR, exist_ok=True)
    if os.path.exists(CA_KEY) and os.path.exists(CA_CRT):
        return _load_key(CA_KEY), _load_crt(CA_CRT)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, socket.gethostname())])
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(_now() - datetime.timedelta(days=1))
           .not_valid_after(_now() + datetime.timedelta(days=3650))
           .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                        content_commitment=False, key_encipherment=False, data_encipherment=False,
                                        key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
           .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
           # Vale solo per la rete locale: se qualcuno rubasse ca.key non potrebbe usarlo per
           # falsificare siti veri (banca, Google...) sul dispositivo che si fida di questo certificato.
           .add_extension(x509.NameConstraints(permitted_subtrees=LOCAL_ONLY, excluded_subtrees=None), critical=True)
           .sign(key, hashes.SHA256()))
    _write_key(CA_KEY, key)
    with open(CA_CRT, 'wb') as f:
        f.write(crt.public_bytes(serialization.Encoding.PEM))
    return key, crt


def _allowed(ip):
    # solo gli indirizzi ammessi dal certificato radice (reti private e Tailscale)
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n.value for n in LOCAL_ONLY if isinstance(n, x509.IPAddress))


def _names(ips, old=()):
    host = socket.gethostname()
    dns = sorted({'localhost', host.lower() + '.local'})  # solo nomi ammessi dal certificato radice
    now = {'127.0.0.1', *HOTSPOT, *(ip for ip in ips if _allowed(ip))}
    # gli indirizzi delle reti viste prima restano (al massimo MAX_OLD), così tornando a casa non si rifà
    seen = [ip for ip in old if _allowed(ip) and ip not in now][:MAX_OLD]
    return dns, sorted(now | set(seen), key=ipaddress.ip_address)


def _san_ips(crt):
    try:
        san = crt.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    return [str(a) for a in san.get_values_for_type(x509.IPAddress)]


def _covers(crt, ips):
    return set(_names(ips)[1]) <= set(_san_ips(crt))


def ensure_server(ips):
    """Certificato del server valido per questi indirizzi. Restituisce True se è stato rifatto."""
    ca_key, ca_crt = ensure_ca()
    old = []
    if os.path.exists(SRV_KEY) and os.path.exists(SRV_CRT):
        crt = _load_crt(SRV_CRT)
        if _covers(crt, ips) and crt.not_valid_after_utc - _now() > datetime.timedelta(days=30):
            return False
        old = _san_ips(crt)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    dns, addrs = _names(ips, old)
    alt = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(a)) for a in addrs]
    crt = (x509.CertificateBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())]))
           .issuer_name(ca_crt.subject).public_key(key.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(_now() - datetime.timedelta(days=1))
           .not_valid_after(_now() + datetime.timedelta(days=397))
           .add_extension(x509.SubjectAlternativeName(alt), critical=False)
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
           .sign(ca_key, hashes.SHA256()))
    _write_key(SRV_KEY, key)
    with open(SRV_CRT, 'wb') as f:
        f.write(crt.public_bytes(serialization.Encoding.PEM))
        f.write(ca_crt.public_bytes(serialization.Encoding.PEM))  # catena completa
    return True


def ssl_context(ips, on_alert=None):
    ensure_server(ips)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    if on_alert:
        # diagnosi: se un dispositivo rifiuta la connessione cifrata (es. certificato non attendibile)
        # lo comunica con un "allarme TLS", che altrimenti andrebbe perso senza traccia
        def watch(conn, direction, version, content_type, msg_type, data):
            try:
                # un allarme ricevuto prima che la connessione cifrata sia stabilita = rifiuto del dispositivo
                # (con TLS 1.3 se ne vede solo l'intestazione: tipo di record 21)
                if direction == 'read' and int(msg_type) == 21 and int(content_type) in (21, 256)                         and conn.version() is None:
                    on_alert('allarme TLS durante la connessione')
            except Exception:
                pass
        try:
            ctx._msg_callback = watch  # interfaccia di diagnosi di Python (non pubblica)
        except Exception:
            pass
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2  # niente TLS 1.0/1.1
    ctx.load_cert_chain(SRV_CRT, SRV_KEY)
    return ctx


def reload(ctx, ips):
    """Se gli indirizzi del PC sono cambiati (es. hotspot), rifà il certificato e lo carica:
    vale per le connessioni nuove, quelle aperte continuano."""
    if ensure_server(ips):
        ctx.load_cert_chain(SRV_CRT, SRV_KEY)
        return True
    return False


def ca_der():
    ensure_ca()
    return _load_crt(CA_CRT).public_bytes(serialization.Encoding.DER)
