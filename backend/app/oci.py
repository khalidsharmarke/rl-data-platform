"""A minimal client for the registry's HTTP API (the OCI distribution spec), with httpx only.

Shared by the platform, which copies vendor images into its own namespace (D50), and by the
demo's vendor CI, which builds an image by adding one layer to the platform's base image
without needing Docker. Kept free of platform imports so the simulator can load it as a file.
"""

import gzip
import hashlib
import io
import json
import tarfile
from collections.abc import Callable

import httpx

MANIFEST_TYPES = [
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
]
INDEX_TYPES = set(MANIFEST_TYPES[::2])


class Registry:
    """`token_for(scope)` returns a bearer token for a scope such as "repository:a/b:pull,push"."""

    def __init__(self, url: str, token_for: Callable[[str], str]):
        self.http = httpx.Client(base_url=url.rstrip("/"), timeout=60)
        self.token_for = token_for

    def _auth(self, *scopes: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token_for(' '.join(scopes))}"}

    def manifest(self, repo: str, ref: str) -> tuple[bytes, str] | None:
        """The manifest's exact bytes and media type, or None if there's no such manifest."""
        r = self.http.get(f"/v2/{repo}/manifests/{ref}", headers={**self._auth(f"repository:{repo}:pull"), "Accept": ", ".join(MANIFEST_TYPES)})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content, r.headers["Content-Type"].split(";")[0]

    def put_manifest(self, repo: str, ref: str, body: bytes, media_type: str) -> str:
        r = self.http.put(f"/v2/{repo}/manifests/{ref}", content=body,
                          headers={**self._auth(f"repository:{repo}:pull,push"), "Content-Type": media_type})
        r.raise_for_status()
        return r.headers["Docker-Content-Digest"]

    def delete_manifest(self, repo: str, digest: str) -> None:
        self.http.delete(f"/v2/{repo}/manifests/{digest}", headers=self._auth(f"repository:{repo}:delete")).raise_for_status()

    def blob(self, repo: str, digest: str) -> bytes:
        r = self.http.get(f"/v2/{repo}/blobs/{digest}", headers=self._auth(f"repository:{repo}:pull"), follow_redirects=True)
        r.raise_for_status()
        return r.content

    def mount(self, repo: str, digest: str, source: str) -> None:
        """Make a blob from `source` available in `repo` without moving its bytes."""
        r = self.http.post(f"/v2/{repo}/blobs/uploads/", params={"mount": digest, "from": source},
                           headers=self._auth(f"repository:{repo}:pull,push", f"repository:{source}:pull"))
        if r.status_code != 201:  # the registry fell back to an upload: there was nothing to mount
            raise RuntimeError(f"couldn't mount {digest} from {source} into {repo}: {r.status_code}")

    def upload(self, repo: str, data: bytes) -> str:
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        auth = self._auth(f"repository:{repo}:pull,push")
        start = self.http.post(f"/v2/{repo}/blobs/uploads/", headers=auth)
        start.raise_for_status()
        # The upload URL carries the registry's own state in its query; add to it, don't replace it.
        location = start.headers["Location"]
        url = f"{location}{'&' if '?' in location else '?'}digest={digest.replace(':', '%3A')}"
        self.http.put(url, content=data, headers={**auth, "Content-Type": "application/octet-stream"}).raise_for_status()
        return digest

    def copy(self, source: str, digest: str, target: str) -> None:
        """Copy a manifest (an image, or an index of images) and every blob it names, by digest."""
        found = self.manifest(source, digest)
        if found is None:
            raise LookupError(f"{source}@{digest} doesn't exist")
        body, media_type = found
        doc = json.loads(body)
        if media_type in INDEX_TYPES:
            for child in doc["manifests"]:
                self.copy(source, child["digest"], target)
        else:
            for blob in [doc["config"], *doc["layers"]]:
                self.mount(target, blob["digest"], source)
        self.put_manifest(target, digest, body, media_type)


def layer(files: dict[str, bytes]) -> tuple[bytes, str]:
    """A gzipped tar layer of the given files (paths without a leading slash) and its uncompressed digest."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tar:
        for path, data in sorted(files.items()):
            info = tarfile.TarInfo(path)
            info.size, info.mode = len(data), 0o644
            tar.addfile(info, io.BytesIO(data))
    raw_bytes = raw.getvalue()
    return gzip.compress(raw_bytes, mtime=0), "sha256:" + hashlib.sha256(raw_bytes).hexdigest()


def extend(registry: Registry, base: str, base_ref: str, files: dict[str, bytes], target: str, tag: str) -> str:
    """Push `base` plus one layer holding `files` to target:tag, as `FROM base` + `COPY` would. Returns the digest."""
    body, media_type = registry.manifest(base, base_ref)
    doc = json.loads(body)
    if media_type in INDEX_TYPES:  # pick the linux/amd64 image
        child = next(m for m in doc["manifests"] if m.get("platform", {}).get("architecture") in (None, "amd64"))
        body, media_type = registry.manifest(base, child["digest"])
        doc = json.loads(body)
    config = json.loads(registry.blob(base, doc["config"]["digest"]))
    for blob in doc["layers"]:
        registry.mount(target, blob["digest"], base)
    gz, diff_id = layer(files)
    layer_digest = registry.upload(target, gz)
    config["rootfs"]["diff_ids"].append(diff_id)
    config.setdefault("history", []).append({"created_by": f"COPY {', '.join(sorted(files))}"})
    config_bytes = json.dumps(config).encode()
    config_digest = registry.upload(target, config_bytes)
    layer_type = doc["layers"][0]["mediaType"] if doc["layers"] else "application/vnd.oci.image.layer.v1.tar+gzip"
    doc["config"] = {**doc["config"], "digest": config_digest, "size": len(config_bytes)}
    doc["layers"].append({"mediaType": layer_type, "digest": layer_digest, "size": len(gz)})
    doc.pop("annotations", None)
    return registry.put_manifest(target, tag, json.dumps(doc).encode(), media_type)
