"""M26 done-check, registry side: vendor images pinned by digest and checked like any version (A15, D50).

Needs the Docker daemon and the registry, so it runs in the sandbox service (`make test`, after
`make up`). A vendor image adds a package the base image lacks; a version on it passes the
checks, the same version on the base image doesn't, and the platform's pinned copy keeps
working after the vendor retags and deletes its own.
"""

import shutil
import subprocess

import pytest

from app import oci, registry, sandbox
from app.config import settings
from tests import envfixtures

pytestmark = [
    pytest.mark.sandbox,
    pytest.mark.skipif(shutil.which("docker") is None or not settings.registry_url,
                       reason="needs the Docker daemon and the registry (the sandbox service)"),
]

VENDOR = "Registry Test Vendor"
REPO = f"{registry.namespace(VENDOR)}/runtime"
KIT = {"usr/local/lib/python3.12/site-packages/vendorkit/__init__.py": b"READY = True\n"}
GRADER = ("grader/test_runtime.py", "grader", b"import vendorkit\n\ndef test_runtime_is_the_vendors():\n    assert vendorkit.READY\n")


@pytest.fixture(scope="module")
def vendor_image() -> str:
    sandbox.connect_registry()
    as_vendor = oci.Registry(settings.registry_url, lambda scope: registry.mint("test", registry.vendor_grants(VENDOR, scope)))
    digest = oci.extend(as_vendor, registry.BASE_REPO, "1", KIT, REPO, "1")
    return f"{REPO}@{digest}"


def check(image: str) -> dict[str, sandbox.CheckResult]:
    files = [*envfixtures.files("sound"), GRADER]
    manifest = {**envfixtures.manifest("sound"), "base_image": image,
                "files": [{"path": p, "part": part, "hash": p} for p, part, _ in files]}
    return {r.name: r for r in sandbox.run_checks(manifest, {p: data for p, _, data in files})}


def test_a_vendor_can_only_push_to_its_own_namespace():
    as_vendor = oci.Registry(settings.registry_url, lambda scope: registry.mint("test", registry.vendor_grants(VENDOR, scope)))
    with pytest.raises(Exception):
        oci.extend(as_vendor, registry.BASE_REPO, "1", KIT, "vendors/someone-else/runtime", "1")


def test_a_version_on_a_vendor_image_is_checked_on_it(vendor_image):
    results = check(registry.pin(vendor_image))
    assert all(r.passed for r in results.values()), {k: r.detail for k, r in results.items()}
    # The same version on the base image can't import the vendor's package.
    assert not check(settings.environment_base_image)["reference"].passed


def test_the_pinned_copy_outlives_the_vendors_image(vendor_image):
    pinned = registry.pin(vendor_image)
    digest = vendor_image.split("@")[1]
    platform = registry.platform_client()
    # The vendor points its tag at something else, then deletes the image the version names.
    oci.extend(platform, registry.BASE_REPO, "1", {"etc/other": b"x"}, REPO, "1")
    platform.delete_manifest(REPO, digest)
    assert platform.manifest(REPO, digest) is None
    subprocess.run(["docker", "rmi", "-f", pinned], capture_output=True)
    results = check(pinned)
    assert all(r.passed for r in results.values()), {k: r.detail for k, r in results.items()}
