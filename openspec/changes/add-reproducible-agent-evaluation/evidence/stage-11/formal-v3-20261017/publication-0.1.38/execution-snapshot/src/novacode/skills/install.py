"""通过 GitHub Contents API 安装目录型 Skill。"""

import base64
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

import httpx

from novacode.skills.parser import parse_skill_file

MAX_FILE_SIZE = 1 * 1024 * 1024
MAX_TOTAL_SIZE = 8 * 1024 * 1024
MAX_FILE_COUNT = 64
MAX_RECURSION_DEPTH = 4


@dataclass(frozen=True)
class ParsedSkillURL:
    owner: str
    repo: str
    path: str
    ref: str | None = None
    kind: str = "github"


def parse_skill_url(url: str) -> ParsedSkillURL:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https":
        raise ValueError("skill URL must use https")
    if any(part in {".", ".."} or "\\" in part for part in parts):
        raise ValueError("skill URL contains an invalid path")
    if parsed.hostname in {"skills.sh", "www.skills.sh"} and len(parts) == 3:
        return ParsedSkillURL(parts[0], parts[1], parts[2], kind="skills_sh")
    if parsed.netloc == "github.com" and len(parts) >= 5 and parts[2] == "tree":
        return ParsedSkillURL(parts[0], parts[1], "/".join(parts[4:]), parts[3])
    if parsed.netloc == "raw.githubusercontent.com" and len(parts) >= 4:
        path = "/".join(parts[3:])
        if path == "SKILL.md":
            path = ""
        elif path.endswith("/SKILL.md"):
            path = path.removesuffix("/SKILL.md")
        return ParsedSkillURL(parts[0], parts[1], path, parts[2])
    raise ValueError("unsupported skill URL")


async def install_skill(
    src: str,
    install_root: str | Path,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    source = parse_skill_url(src)
    root = Path(install_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".skill-staging-", dir=root))
    own_client = client is None
    token = os.environ.get("GITHUB_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else None
    http = client or httpx.AsyncClient(
        timeout=60.0,
        follow_redirects=True,
        headers=headers,
    )
    try:
        api_root = f"https://api.github.com/repos/{source.owner}/{source.repo}/contents"
        if source.kind == "skills_sh":
            await _download_skills_sh(http, source, staging)
        else:
            await _download_tree(http, api_root, source.path, source.ref, staging)
        manifest = staging / "SKILL.md"
        if not manifest.is_file():
            raise ValueError("downloaded skill does not contain SKILL.md")
        skill = parse_skill_file(manifest, is_directory=True)
        destination = root / skill.name
        if destination.exists():
            raise FileExistsError(f"skill '{skill.name}' is already installed")
        staging.rename(destination)
        return skill.name
    finally:
        if own_client:
            await http.aclose()
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


async def _download_tree(
    client: httpx.AsyncClient,
    api_root: str,
    source_path: str,
    ref: str | None,
    target: Path,
) -> None:
    counters = {"files": 0, "bytes": 0}
    params = {"ref": ref} if ref else None

    async def visit(path: str, relative: PurePosixPath, depth: int) -> None:
        if depth > MAX_RECURSION_DEPTH:
            raise ValueError("skill directory exceeds maximum depth")
        response = await client.get(f"{api_root}/{path.lstrip('/')}", params=params)
        response.raise_for_status()
        data = response.json()
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("type") not in {"file", "dir"}:
                continue
            name = entry.get("name")
            if (
                not isinstance(name, str)
                or not name
                or name in {".", ".."}
                or "/" in name
                or "\\" in name
            ):
                raise ValueError("invalid path returned by GitHub")
            child = relative / name
            if entry["type"] == "dir":
                await visit(str(entry.get("path", "")), child, depth + 1)
                continue
            size = entry.get("size")
            if not isinstance(size, int) or size < 0 or size > MAX_FILE_SIZE:
                raise ValueError(f"file '{child}' exceeds size limit")
            counters["files"] += 1
            counters["bytes"] += size
            if counters["files"] > MAX_FILE_COUNT or counters["bytes"] > MAX_TOTAL_SIZE:
                raise ValueError("skill exceeds download limits")
            file_url = entry.get("url")
            if not isinstance(file_url, str) or not file_url.startswith(api_root + "/"):
                raise ValueError("invalid file API URL returned by GitHub")
            file_response = await client.get(file_url, params=params)
            file_response.raise_for_status()
            payload = file_response.json()
            if not isinstance(payload, dict) or payload.get("encoding") != "base64":
                raise ValueError(f"GitHub did not return base64 content for '{child}'")
            try:
                encoded = payload.get("content", "")
                if not isinstance(encoded, str):
                    raise ValueError
                content = base64.b64decode("".join(encoded.split()), validate=True)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid content for '{child}'") from exc
            if len(content) > MAX_FILE_SIZE:
                raise ValueError(f"file '{child}' exceeds size limit")
            counters["bytes"] += len(content) - size
            if counters["bytes"] > MAX_TOTAL_SIZE:
                raise ValueError("skill exceeds download limits")
            output = target.joinpath(*child.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)

    await visit(source_path, PurePosixPath(), 0)


async def _download_skills_sh(
    client: httpx.AsyncClient,
    source: ParsedSkillURL,
    target: Path,
) -> None:
    repo_api = f"https://api.github.com/repos/{source.owner}/{source.repo}"
    repo_response = await client.get(repo_api)
    repo_response.raise_for_status()
    repo_data = repo_response.json()
    branch = repo_data.get("default_branch") if isinstance(repo_data, dict) else None
    if not isinstance(branch, str) or not branch:
        raise ValueError("GitHub did not return the repository default branch")

    tree_response = await client.get(
        f"{repo_api}/git/trees/{branch}",
        params={"recursive": "1"},
    )
    tree_response.raise_for_status()
    tree_data = tree_response.json()
    if not isinstance(tree_data, dict) or tree_data.get("truncated") is True:
        raise ValueError("GitHub repository tree is incomplete")
    tree = tree_data.get("tree")
    if not isinstance(tree, list):
        raise ValueError("GitHub did not return a repository tree")

    manifests: list[PurePosixPath] = []
    for entry in tree:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if entry.get("type") == "blob" and isinstance(raw_path, str):
            path = PurePosixPath(raw_path)
            if path.name == "SKILL.md" and path.parent.name == source.path:
                manifests.append(path)
    if not manifests:
        all_manifests = [
            PurePosixPath(entry["path"])
            for entry in tree
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and isinstance(entry.get("path"), str)
            and PurePosixPath(entry["path"]).name == "SKILL.md"
        ]
        if len(all_manifests) == 1:
            manifests = all_manifests
    if len(manifests) != 1:
        raise ValueError(f"cannot uniquely locate skill '{source.path}' in repository")
    skill_dir = manifests[0].parent
    if skill_dir.is_absolute() or any(part in {"", ".", ".."} for part in skill_dir.parts):
        raise ValueError("GitHub returned an invalid skill path")
    directory = "" if skill_dir == PurePosixPath(".") else skill_dir.as_posix()
    await _download_tree(client, f"{repo_api}/contents", directory, branch, target)
