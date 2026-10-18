"""The container registry's platform side (VM2, A15, D50).

The registry trusts tokens this module signs. A vendor's API token with the registry scope is
exchanged for push and pull on the vendor's own namespace, `vendors/<slug>/...`, plus pull on
the platform's base image to build from. Vendor images a version names are copied into
`platform/images`, pinned by digest, and only that copy is ever run.
"""

import json
import re
import secrets
import time
from functools import cache
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app import oci
from app.config import settings

PLATFORM_IMAGES = "platform/images"
BASE_REPO = "platform/base"
TOKEN_SECONDS = 300
IMAGE_REF = re.compile(r"^(vendors/[a-z0-9-]+/[a-z0-9._/-]+)@(sha256:[0-9a-f]{64})$")


def slug(org_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", org_name.lower()).strip("-")


def namespace(org_name: str) -> str:
    return f"vendors/{slug(org_name)}"


def ensure_keys() -> None:
    """Create the signing key and the JWKS the registry reads, once, in the shared key directory."""
    keys = Path(settings.registry_key_dir)
    if (keys / "jwks.json").exists():
        return
    keys.mkdir(parents=True, exist_ok=True)
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    (keys / "signing.pem").write_bytes(pem)
    jwk = {**json.loads(RSAAlgorithm.to_jwk(private.public_key())), "kid": "platform-1", "use": "sig", "alg": "RS256"}
    tmp = keys / "jwks.json.tmp"
    tmp.write_text(json.dumps({"keys": [jwk]}))
    tmp.replace(keys / "jwks.json")  # the registry waits for this file, so it appears whole


@cache
def _signing_key() -> bytes:
    return (Path(settings.registry_key_dir) / "signing.pem").read_bytes()


def mint(subject: str, access: list[dict]) -> str:
    now = int(time.time())
    claims = {"iss": settings.registry_issuer, "sub": subject, "aud": settings.registry_service, "iat": now, "nbf": now - 5,
              "exp": now + TOKEN_SECONDS, "jti": secrets.token_hex(8), "access": access}
    return jwt.encode(claims, _signing_key(), algorithm="RS256", headers={"kid": "platform-1"})


def requested(scope: str) -> list[tuple[str, list[str]]]:
    """Parse "repository:name:pull,push repository:other:pull" into (name, actions) pairs."""
    out = []
    for item in scope.split():
        parts = item.split(":")
        if len(parts) == 3 and parts[0] == "repository":
            out.append((parts[1], parts[2].split(",")))
    return out


def vendor_grants(org_name: str, scope: str) -> list[dict]:
    """What a vendor may do: anything in its namespace, and pull the base image. Nothing else."""
    own = namespace(org_name) + "/"
    access = []
    for name, actions in requested(scope):
        allowed = {"pull", "push", "delete"} if name.startswith(own) else {"pull"} if name == BASE_REPO else set()
        if granted := [a for a in actions if a in allowed]:
            access.append({"type": "repository", "name": name, "actions": granted})
    return access


def platform_client() -> oci.Registry:
    def token_for(scope: str) -> str:
        return mint("platform", [{"type": "repository", "name": n, "actions": a} for n, a in requested(scope)])

    return oci.Registry(settings.registry_url, token_for)


def pin(vendor_ref: str) -> str:
    """Copy a vendor image into the platform's namespace and return the reference the sandbox runs."""
    repo, digest = IMAGE_REF.match(vendor_ref).groups()
    platform_client().copy(repo, digest, PLATFORM_IMAGES)
    return f"{settings.registry_host}/{PLATFORM_IMAGES}@{digest}"
