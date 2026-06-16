"""
Digital certificate management (A1 - PKCS#12 / PFX format).

Flow:
  1. User uploads PFX file + password
  2. We extract cert.pem and key.pem using the `cryptography` library
  3. Files are stored in data/certs/{cnpj}/
  4. Certificate metadata (CN, validity) is stored in the DB

Security note: private key is stored unencrypted in PEM format.
For a production environment exposed to the internet, encrypt the key at rest.
For office/local use this is acceptable — restrict OS-level access to data/certs/.
"""
from pathlib import Path
from datetime import datetime, timezone

from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption
from cryptography.hazmat.primitives import serialization
from cryptography import x509

_CERTS_DIR = Path(__file__).parent.parent / "data" / "certs"


def _cert_dir(cnpj: str) -> Path:
    d = _CERTS_DIR / cnpj
    d.mkdir(parents=True, exist_ok=True)
    return d


def process_pfx(cnpj: str, pfx_bytes: bytes, password: str) -> dict:
    """
    Load a PFX/P12 file, extract cert and key, save as PEM.
    Returns metadata dict: cn, validade, emitente, cert_path, key_path.
    Raises ValueError on wrong password or invalid file.
    """
    pwd = password.encode("utf-8") if password else None
    try:
        private_key, cert, _chain = pkcs12.load_key_and_certificates(pfx_bytes, pwd)
    except Exception as exc:
        raise ValueError(f"Não foi possível abrir o certificado. Verifique a senha. Detalhe: {exc}") from exc

    if cert is None:
        raise ValueError("PFX não contém um certificado válido.")
    if private_key is None:
        raise ValueError("PFX não contém a chave privada.")

    cert_dir = _cert_dir(cnpj)

    # Save certificate
    cert_pem = cert.public_bytes(Encoding.PEM)
    (cert_dir / "cert.pem").write_bytes(cert_pem)

    # Save private key (unencrypted PEM)
    key_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    (cert_dir / "key.pem").write_bytes(key_pem)

    # Keep original PFX as backup
    (cert_dir / "cert.pfx").write_bytes(pfx_bytes)

    # Extract metadata
    try:
        cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    except IndexError:
        cn = "Desconhecido"

    try:
        emitente = cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)[0].value
    except IndexError:
        emitente = "Desconhecido"

    # Validity — use timezone-aware datetime
    try:
        validade_dt = cert.not_valid_after_utc
    except AttributeError:
        # cryptography < 42 compatibility
        validade_dt = cert.not_valid_after.replace(tzinfo=timezone.utc)

    validade_str = validade_dt.strftime("%d/%m/%Y")

    # Check if already expired
    now = datetime.now(timezone.utc)
    if validade_dt < now:
        days_expired = (now - validade_dt).days
        raise ValueError(f"Certificado expirado há {days_expired} dias (válido até {validade_str}).")

    return {
        "cn": cn,
        "validade": validade_str,
        "emitente": emitente,
        "cert_path": str(cert_dir / "cert.pem"),
        "key_path": str(cert_dir / "key.pem"),
        "dias_restantes": (validade_dt - now).days,
    }


def get_cert_paths(cnpj: str) -> tuple[str, str] | None:
    """Returns (cert_path, key_path) if certificate files exist, else None."""
    cert_dir = _CERTS_DIR / cnpj
    cert = cert_dir / "cert.pem"
    key = cert_dir / "key.pem"
    if cert.exists() and key.exists():
        return str(cert), str(key)
    return None


def remove_cert(cnpj: str) -> None:
    """Delete all certificate files for a company."""
    cert_dir = _CERTS_DIR / cnpj
    for f in ["cert.pem", "key.pem", "cert.pfx"]:
        p = cert_dir / f
        if p.exists():
            p.unlink()


def cert_status(cnpj: str, validade_str: str | None) -> dict:
    """Returns a status summary for display in the frontend."""
    paths = get_cert_paths(cnpj)
    if not paths or not validade_str:
        return {"tem_certificado": False, "status": "sem_certificado", "cor": "gray"}

    try:
        validade_dt = datetime.strptime(validade_str, "%d/%m/%Y").replace(tzinfo=timezone.utc)
        dias = (validade_dt - datetime.now(timezone.utc)).days
        if dias < 0:
            return {"tem_certificado": True, "status": "expirado", "cor": "red", "dias_restantes": dias}
        if dias <= 30:
            return {"tem_certificado": True, "status": "expirando", "cor": "yellow", "dias_restantes": dias}
        return {"tem_certificado": True, "status": "valido", "cor": "green", "dias_restantes": dias}
    except Exception:
        return {"tem_certificado": True, "status": "valido", "cor": "green"}
