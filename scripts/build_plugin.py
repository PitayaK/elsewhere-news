#!/usr/bin/env python3
"""Build the reviewed Skill/client bundle without host MCP registration."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import zipfile


NAME = "elsewhere-news"
MCP_URL = "https://elsewhere.news/mcp"
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
ROOT = Path(__file__).resolve().parent.parent
FILES = {
    "plugin.json": "plugin.json",
    "skills/elsewhere-news/SKILL.md": "SKILL.md",
    "skills/elsewhere-news/agents/openai.yaml": "agents/openai.yaml",
    "skills/elsewhere-news/scripts/elsewhere.py": "scripts/elsewhere.py",
}
DISPLAY_METADATA = (
    'interface:\n'
    '  display_name: "elsewhere-news"\n'
    '  short_description: "Read reporting and account-linked knowledge graphs"\n'
    '  default_prompt: "使用 $elsewhere-news 连接我的 Elsewhere 账号，并确认连接是否可用。"\n'
).encode("utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "Duplicate JSON field: " + key)
        result[key] = value
    return result


def source_bytes(relative: str) -> bytes:
    path = ROOT / relative
    require(path.is_file(), "Missing source: " + relative)
    cursor = path
    while cursor != ROOT:
        require(not cursor.is_symlink(), "Symlink source is not allowed: " + relative)
        cursor = cursor.parent
    require(path.resolve().is_relative_to(ROOT), "Source escaped the repository")
    value = path.read_bytes()
    require(0 < len(value) <= 1_048_576, "Invalid component size: " + relative)
    value.decode("utf-8", errors="strict")
    return value


def reviewed_components() -> tuple[str, dict[str, bytes]]:
    for name in ["mcp.json", ".mcp.json", ".app.json"]:
        require(not (ROOT / name).exists(), "Unexpected auto-discovered connection: " + name)
    components = {target: source_bytes(source) for target, source in FILES.items()}
    plugin = json.loads(components["plugin.json"], object_pairs_hook=unique_object)
    require(set(plugin) == {"$schema", "name", "version", "description", "author", "homepage", "extensions"},
            "Unexpected portable plugin fields")
    require(plugin["$schema"] == PLUGIN_SCHEMA and plugin["name"] == NAME, "Wrong plugin identity")
    require(isinstance(plugin["description"], str) and bool(plugin["description"].strip()),
            "Plugin description must be nonblank")
    version = plugin["version"]
    require(isinstance(version, str) and re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version) is not None,
            "Plugin version must be strict SemVer")
    require(plugin["homepage"] == "https://elsewhere.news", "Unexpected homepage")
    require(plugin["author"] == {"name": "Elsewhere"}, "Unexpected author metadata")
    require(set(plugin["extensions"]) == {"com.openai"}, "Unexpected plugin extension")
    extension = plugin["extensions"]["com.openai"]
    require(set(extension) == {"interface"}, "Unexpected app binding or extension")
    interface = extension["interface"]
    require(set(interface) == {"displayName", "shortDescription", "longDescription", "developerName",
                              "category", "capabilities", "defaultPrompt"}, "Unexpected interface fields")
    require(interface["displayName"] == NAME and interface["category"] == "Education & Research",
            "Unexpected display identity or category")
    require(interface["capabilities"] == [], "Unexpected interactive capability")
    require(isinstance(interface["shortDescription"], str) and 0 < len(interface["shortDescription"]) <= 30,
            "Listing subtitle exceeds 30 characters")
    require(all(isinstance(interface[field], str) and interface[field].strip()
                for field in ["displayName", "shortDescription", "longDescription", "developerName",
                              "category", "defaultPrompt"]), "Empty interface text")
    skill = components["skills/elsewhere-news/SKILL.md"].decode("utf-8")
    require(skill.startswith("---\n") and skill.count("\n---\n") >= 1, "Missing Skill frontmatter")
    frontmatter = skill.split("\n---\n", 1)[0]
    require(re.search(r"^name: elsewhere-news$", frontmatter, re.M) is not None, "Wrong Skill name")
    require(re.search(r'^  version: "' + re.escape(version) + r'"$', frontmatter, re.M) is not None,
            "Skill and plugin versions differ")
    require(re.search(r"^  bundle-version: 3$", frontmatter, re.M) is not None, "Unexpected Skill bundle")
    require(components["skills/elsewhere-news/agents/openai.yaml"] == DISPLAY_METADATA,
            "Unexpected display metadata or host dependency")
    artifacts = re.findall(r"^Reviewed client artifact: `([^`\n]+)`$", skill, re.M)
    require(len(artifacts) == 1, "Missing or ambiguous reviewed client artifact")
    artifact = artifacts[0]
    published_artifact = f"https://elsewhere.news/.well-known/elsewhere-skill/scripts/elsewhere-{version}.py"
    immutable_source = re.fullmatch(
        r"https://raw\.githubusercontent\.com/PitayaK/elsewhere-news/[0-9a-f]{40}/scripts/elsewhere\.py",
        artifact,
    )
    require(artifact == published_artifact or immutable_source is not None,
            "Wrong reviewed client artifact")
    expected_manifest = (
        '{"schema":2,"name":"elsewhere-news","version":"' + version
        + '","bundle-version":3,"transport":"remote-mcp","mcp-url":"' + MCP_URL + '"}'
    )
    require(expected_manifest in skill, "Unexpected version manifest contract")
    sizes = re.findall(r"^Client bytes: ([1-9][0-9]*)$", skill, re.M)
    hashes = re.findall(r"^Client SHA-256: ([0-9a-f]{64})$", skill, re.M)
    require(len(sizes) == 1 and len(hashes) == 1, "Reviewed client pin is missing or unresolved")
    client = components["skills/elsewhere-news/scripts/elsewhere.py"]
    require(len(client) == int(sizes[0]) and hashlib.sha256(client).hexdigest() == hashes[0],
            "Client differs from its reviewed byte count or SHA-256")
    require(client.startswith(b"#!/usr/bin/env python3\n"), "Unexpected client interpreter")
    return version, components


def build(output: Path) -> dict:
    require(output.is_absolute() and output.suffix.lower() == ".zip", "--output must be an absolute ZIP path")
    require(not output.is_symlink(), "Output cannot be a symlink")
    version, components = reviewed_components()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="elsewhere-plugin-package-") as staging:
        stage = Path(staging)
        require(not output.resolve().is_relative_to(stage), "Archive must be outside its staging directory")
        for target, payload in components.items():
            path = stage / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".elsewhere-plugin-", suffix=".zip", dir=output.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for target in sorted(components):
                    info = zipfile.ZipInfo(target, date_time=(1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    archive.writestr(info, (stage / target).read_bytes(), compresslevel=9)
            with zipfile.ZipFile(temporary) as archive:
                require(archive.namelist() == sorted(FILES), "Archive component list mismatch")
                require(archive.testzip() is None, "Archive CRC validation failed")
                for item in archive.infolist():
                    require(not stat.S_ISLNK(item.external_attr >> 16), "Archive contains a symlink")
                    require(archive.read(item.filename) == components[item.filename], "Archive content mismatch")
            os.replace(temporary, output)
        finally:
            if temporary.exists():
                temporary.unlink()
    archive_bytes = output.read_bytes()
    return {
        "name": NAME,
        "version": version,
        "archive": str(output),
        "bytes": len(archive_bytes),
        "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "components": [
            {"path": target, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            for target, payload in sorted(components.items())
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
