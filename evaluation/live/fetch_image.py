"""Docker 下载断流时，从官方 Registry 校验 OCI 字节后导入，不改变镜像内容。"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def authorization(repo: str) -> dict[str, str]:
    """下载令牌短期有效；沿用 Docker 登录，每段请求重新取得，不落盘。"""
    config = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "config.json"
    credentials = json.loads(config.read_text()) if config.exists() else {}
    auth = credentials.get("auths", {}).get("https://index.docker.io/v1/", {}).get("auth")
    request = urllib.request.Request(
        "https://auth.docker.io/token?service=registry.docker.io&scope=repository:"
        + repo
        + ":pull",
        headers={"Authorization": "Basic " + auth} if auth else {},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return {"Authorization": "Bearer " + str(json.load(response)["token"])}


def blob(base: str, descriptor: dict[str, Any], headers: dict[str, str], cache: Path) -> Path:
    digest = str(descriptor["digest"])
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), "Registry digest 无效"
    path = cache / digest.split(":")[1]
    if path.exists() and sha(path) == path.name:
        return path
    assert shutil.disk_usage(cache).free > descriptor["size"] + 20 * 1024**3
    partial = path.with_suffix(".part")
    for _ in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == descriptor["size"]:
            break
        request_headers = {
            **headers,
            **authorization(base.split("/v2/")[1].rstrip("/")),
            "Range": f"bytes={offset}-",
        }
        with urllib.request.urlopen(
            urllib.request.Request(base + "blobs/" + digest, headers=request_headers), timeout=60
        ) as response:
            resumed = response.status == 206
            if resumed:
                assert re.fullmatch(
                    rf"bytes {offset}-[0-9]+/{descriptor['size']}",
                    response.headers["Content-Range"],
                ), "Registry Range 响应不符"
            with partial.open("ab" if resumed else "wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
    assert partial.stat().st_size == descriptor["size"], "Registry blob 大小不符"
    assert sha(partial) == path.name, "Registry blob SHA-256 不符"
    partial.replace(path)
    return path


def fetch(image: str, cache_root: Path) -> dict[str, Any]:
    assert image.startswith("starryzhang/sweb.eval.x86_64."), "仅用于本轮官方 x86 镜像"
    repo, tag = image.rsplit(":", 1)
    cache = cache_root / "blobs"
    cache.mkdir(parents=True, exist_ok=True)
    headers = {
        **authorization(repo),
        "Accept": "application/vnd.docker.distribution.manifest.v2+json",
    }
    base = "https://registry-1.docker.io/v2/" + repo + "/"
    with urllib.request.urlopen(
        urllib.request.Request(base + "manifests/" + tag, headers=headers), timeout=60
    ) as response:
        raw = response.read()
        expected = response.headers["Docker-Content-Digest"]
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    assert actual == expected, "官方 manifest 身份不符"
    manifest = json.loads(raw)
    assert manifest["schemaVersion"] == 2 and "layers" in manifest
    manifest_blob = cache / actual.split(":")[1]
    manifest_blob.write_bytes(raw)
    descriptors = [manifest["config"], *manifest["layers"]]
    paths = []
    for descriptor in descriptors:
        print("blob", descriptor["digest"], descriptor["size"], flush=True)
        paths.append(blob(base, descriptor, headers, cache))
    config = json.loads(paths[0].read_text())
    assert config["architecture"] == "amd64" and config["os"] == "linux"
    index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [
            {
                "mediaType": manifest["mediaType"],
                "digest": actual,
                "size": len(raw),
                "annotations": {
                    "containerd.io/distribution.source.docker.io": repo,
                    "io.containerd.image.name": "docker.io/" + image,
                    "org.opencontainers.image.ref.name": tag,
                },
            }
        ],
    }
    archive = cache_root / (actual.split(":")[1] + ".tar")
    with tarfile.open(archive, "w") as package:
        for name, data in (("oci-layout", {"imageLayoutVersion": "1.0.0"}), ("index.json", index)):
            content = json.dumps(data).encode()
            info = tarfile.TarInfo(name)
            info.size = len(content)
            package.addfile(info, io.BytesIO(content))
        for path in [manifest_blob, *paths]:
            package.add(path, arcname="blobs/sha256/" + path.name)
    subprocess.run(["/snap/bin/docker", "load", "-i", str(archive)], check=True)
    proof = json.loads(
        subprocess.check_output(
            ["/snap/bin/docker", "image", "inspect", repo + "@" + actual], text=True
        )
    )[0]
    assert repo + "@" + actual in proof["RepoDigests"], "导入未保持官方 digest"
    assert proof["RootFS"]["Layers"] == config["rootfs"]["diff_ids"], "导入改变了 rootfs"
    result = {
        "schema_version": 1,
        "image": repo + "@" + actual,
        "manifest_sha256": actual,
        "blobs": descriptors,
        "image_id": proof["Id"],
        "rootfs": proof["RootFS"],
        "transport": "verified-oci-import",
    }
    (cache_root / (actual.split(":")[1] + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    fetch(args.image, args.cache)
