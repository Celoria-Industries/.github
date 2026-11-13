import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid


ENGINE = os.environ.get("CONTAINER_ENGINE", "docker")
IMAGE = "docker.io/library/alpine@sha256:bc21487101ae47172509130ee1092cc273a831968d64b617776d1f69c81e42ab"
REPOSITORY = "acceptance/alpine"
ACCEPT = ", ".join((
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
))


def run(args, *, check=True):
    return subprocess.run(args, text=True, capture_output=True, check=check)


def start_registry(name):
    run([ENGINE, "run", "--detach", "--name", name, "--publish", "127.0.0.1::5000",
         "docker.io/library/registry:2.8.3"])
    published = run([ENGINE, "port", name, "5000/tcp"]).stdout.strip().splitlines()[0]
    port = published.rsplit(":", 1)[1]
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            request = urllib.request.urlopen(f"{base}/v2/", timeout=1)
            if request.status == 200:
                return base
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    raise RuntimeError(f"Disposable registry {name} did not become ready")


def request_bytes(url):
    request = urllib.request.Request(url, headers={"Accept": ACCEPT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def verify_registry(base, expected_index_digest, expected_index_bytes):
    index = request_bytes(f"{base}/v2/{REPOSITORY}/manifests/latest")
    digest = "sha256:" + hashlib.sha256(index).hexdigest()
    if digest != expected_index_digest or index != expected_index_bytes:
        raise AssertionError(f"Index digest mismatch at {base}: {digest}")

    parsed = json.loads(index)
    platforms = set()
    descriptors = parsed.get("manifests", [])
    for descriptor in descriptors:
        platform = descriptor.get("platform") or {}
        if platform.get("os") and platform.get("architecture") and platform.get("os") != "unknown":
            platforms.add((platform["os"], platform["architecture"], platform.get("variant", "")))

        child = request_bytes(f"{base}/v2/{REPOSITORY}/manifests/{descriptor['digest']}")
        child_digest = "sha256:" + hashlib.sha256(child).hexdigest()
        if child_digest != descriptor["digest"]:
            raise AssertionError(f"Child manifest digest mismatch: {descriptor['digest']}")
        manifest = json.loads(child)
        blobs = [manifest["config"], *manifest.get("layers", [])]
        for blob in blobs:
            content = request_bytes(f"{base}/v2/{REPOSITORY}/blobs/{blob['digest']}")
            blob_digest = "sha256:" + hashlib.sha256(content).hexdigest()
            if blob_digest != blob["digest"]:
                raise AssertionError(f"Blob digest mismatch: {blob['digest']}")

    if len(platforms) < 2:
        raise AssertionError(f"Expected multiple platforms, found {sorted(platforms)}")
    return len(descriptors), sorted(platforms)


def main():
    if ENGINE not in ("docker", "podman"):
        raise ValueError("CONTAINER_ENGINE must be docker or podman")
    if not shutil.which("skopeo"):
        raise RuntimeError("skopeo is required")

    suffix = uuid.uuid4().hex[:12]
    source_name = f"staging-copy-source-{suffix}"
    destination_name = f"staging-copy-destination-{suffix}"
    names = (source_name, destination_name)
    try:
        source = start_registry(source_name)
        destination = start_registry(destination_name)
        run(["skopeo", "copy", "--all", "--preserve-digests", "--dest-tls-verify=false",
             f"docker://{IMAGE}", f"docker://{source.removeprefix('http://')}/{REPOSITORY}:latest"])
        source_index = request_bytes(f"{source}/v2/{REPOSITORY}/manifests/latest")
        expected_digest = "sha256:" + hashlib.sha256(source_index).hexdigest()
        run(["skopeo", "copy", "--all", "--preserve-digests", "--src-tls-verify=false",
             "--dest-tls-verify=false",
             f"docker://{source.removeprefix('http://')}/{REPOSITORY}:latest",
             f"docker://{destination.removeprefix('http://')}/{REPOSITORY}:latest"])

        source_descriptors, platforms = verify_registry(source, expected_digest, source_index)
        destination_descriptors, destination_platforms = verify_registry(
            destination, expected_digest, source_index
        )
        if source_descriptors != destination_descriptors or platforms != destination_platforms:
            raise AssertionError("Source and destination platform descriptors differ")
        print(f"PASS: digest {expected_digest}; {source_descriptors} descriptors; "
              f"{len(platforms)} platforms; all child manifests/configs/layers verified")
    finally:
        for name in names:
            run([ENGINE, "rm", "--force", name], check=False)


if __name__ == "__main__":
    main()
