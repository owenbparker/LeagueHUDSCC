"""HTTPS for the player cam page.

Browsers only let a page use the camera when it's secure: https, or this
PC's own http://localhost. Players on other PCs open the cam page from the
GFX PC, so the app also serves itself over https, with a certificate it
makes for itself. Browsers don't know that certificate, so the first visit
shows a warning to click through ("Advanced -> Continue").
"""

import datetime
import ipaddress
import os
import ssl

from .log import log


def ensure_cert(folder, hosts):
    """(cert_path, key_path) for this PC, made (or remade, when the PC's
    network address has changed) as needed. None if it can't be made."""
    cert, key, names = (os.path.join(folder, n) for n in ("hud-cert.pem", "hud-key.pem", "hud-cert-hosts.txt"))
    hosts = sorted({h for h in hosts if h})
    try:
        with open(names, encoding="utf-8") as f:
            if f.read().split() == hosts and os.path.isfile(cert) and os.path.isfile(key):
                return cert, key
    except OSError:
        pass
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError:
        log("Can't make an https certificate (the cryptography package is missing)", "warn")
        return None
    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SCC League HUD")])
    alt = []
    for h in hosts:
        try:
            alt.append(x509.IPAddress(ipaddress.ip_address(h)))
        except ValueError:
            alt.append(x509.DNSName(h))
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder()
                   .subject_name(name).issuer_name(name).public_key(private.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(days=1))
                   .not_valid_after(now + datetime.timedelta(days=3650))
                   .add_extension(x509.SubjectAlternativeName(alt), critical=False)
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .sign(private, hashes.SHA256()))
    os.makedirs(folder, exist_ok=True)
    with open(key, "wb") as f:
        f.write(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    with open(cert, "wb") as f:
        f.write(certificate.public_bytes(serialization.Encoding.PEM))
    with open(names, "w", encoding="utf-8") as f:
        f.write(" ".join(hosts))
    return cert, key


def server_context(folder, hosts):
    paths = ensure_cert(folder, hosts)
    if not paths:
        return None
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*paths)
    return ctx
