#!/usr/bin/env python3
import bz2
import glob
import gzip
import hashlib
import io
import lzma
import os
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEBS_DIR = os.path.join(REPO_DIR, "debs")

REPO_ORIGIN = "Fatality's Repo"
REPO_LABEL = "Fatality's Repo"
REPO_DESCRIPTION = "RootHide (iphoneos-arm64e) & Procursus Tweaks Repository"
REPO_ARCHITECTURES = "iphoneos-arm64e iphoneos-arm64"


def extract_control_from_deb(deb_path: str) -> str:
    """Extract the DEBIAN/control text from a .deb archive using ar + tar."""
    members = subprocess.check_output(["ar", "t", deb_path], text=True).splitlines()
    control_member = next((m for m in members if m.startswith("control.tar")), None)
    if not control_member:
        raise RuntimeError(f"No control.tar.* found in {deb_path}")

    raw_tar = subprocess.check_output(["ar", "p", deb_path, control_member])

    if control_member.endswith(".gz"):
        tar_bytes = gzip.decompress(raw_tar)
    elif control_member.endswith(".xz"):
        tar_bytes = lzma.decompress(raw_tar)
    elif control_member.endswith(".bz2"):
        tar_bytes = bz2.decompress(raw_tar)
    elif control_member.endswith(".zst"):
        proc = subprocess.run(["zstd", "-d", "-c"], input=raw_tar, capture_output=True, check=True)
        tar_bytes = proc.stdout
    else:
        tar_bytes = raw_tar

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:") as tf:
        for member in tf.getmembers():
            if member.name in ("control", "./control", "DEBIAN/control"):
                f = tf.extractfile(member)
                if f:
                    return f.read().decode("utf-8", errors="replace").strip()
    raise RuntimeError(f"Could not find control file inside {control_member} of {deb_path}")


def compute_hashes(data: bytes) -> dict:
    return {
        "md5": hashlib.md5(data).hexdigest(),
        "sha1": hashlib.sha1(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha512": hashlib.sha512(data).hexdigest(),
        "size": len(data),
    }


def main():
    os.makedirs(DEBS_DIR, exist_ok=True)

    # Copy any newly built .deb files from ../FiveBars/packages/
    fivebars_debs = glob.glob(os.path.join(REPO_DIR, "..", "FiveBars", "packages", "*.deb"))
    for src in fivebars_debs:
        dst = os.path.join(DEBS_DIR, os.path.basename(src))
        shutil.copy2(src, dst)

    deb_files = sorted(glob.glob(os.path.join(DEBS_DIR, "*.deb")))
    entries = []

    for deb_path in deb_files:
        control_text = extract_control_from_deb(deb_path)
        with open(deb_path, "rb") as f:
            deb_bytes = f.read()
        h = compute_hashes(deb_bytes)
        rel_path = f"debs/{os.path.basename(deb_path)}"

        stanza_lines = [control_text]
        pkg_id = None
        for line in control_text.splitlines():
            if line.lower().startswith("package:"):
                pkg_id = line.split(":", 1)[1].strip()
                break
        if pkg_id and os.path.exists(os.path.join(REPO_DIR, "depictions", f"{pkg_id}.json")):
            stanza_lines.append(f"SileoDepiction: https://repo.bigoilyguys.org/depictions/{pkg_id}.json")
        stanza_lines.append(f"Filename: {rel_path}")
        stanza_lines.append(f"Size: {h['size']}")
        stanza_lines.append(f"MD5sum: {h['md5']}")
        stanza_lines.append(f"SHA1: {h['sha1']}")
        stanza_lines.append(f"SHA256: {h['sha256']}")
        stanza_lines.append(f"SHA512: {h['sha512']}")
        entries.append("\n".join(stanza_lines) + "\n")

    packages_content = "\n".join(entries)
    packages_bytes = packages_content.encode("utf-8")

    # Write uncompressed and compressed Packages indices
    packages_path = os.path.join(REPO_DIR, "Packages")
    with open(packages_path, "wb") as f:
        f.write(packages_bytes)

    with gzip.open(os.path.join(REPO_DIR, "Packages.gz"), "wb", compresslevel=9) as f:
        f.write(packages_bytes)

    with bz2.open(os.path.join(REPO_DIR, "Packages.bz2"), "wb", compresslevel=9) as f:
        f.write(packages_bytes)

    with lzma.open(os.path.join(REPO_DIR, "Packages.xz"), "wb", preset=9) as f:
        f.write(packages_bytes)

    subprocess.run(
        ["zstd", "-q", "-f", "-19", packages_path, "-o", os.path.join(REPO_DIR, "Packages.zst")],
        check=True,
    )

    index_files = ["Packages", "Packages.gz", "Packages.bz2", "Packages.xz", "Packages.zst"]
    index_stats = {}
    for fname in index_files:
        with open(os.path.join(REPO_DIR, fname), "rb") as f:
            index_stats[fname] = compute_hashes(f.read())

    now_str = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC")
    release_lines = [
        f"Origin: {REPO_ORIGIN}",
        f"Label: {REPO_LABEL}",
        "Suite: stable",
        "Version: 1.0",
        "Codename: ios",
        f"Date: {now_str}",
        f"Architectures: {REPO_ARCHITECTURES}",
        "Components: main",
        f"Description: {REPO_DESCRIPTION}",
        "MD5Sum:",
    ]
    for fname in index_files:
        s = index_stats[fname]
        release_lines.append(f" {s['md5']} {s['size']:16d} {fname}")

    release_lines.append("SHA1:")
    for fname in index_files:
        s = index_stats[fname]
        release_lines.append(f" {s['sha1']} {s['size']:16d} {fname}")

    release_lines.append("SHA256:")
    for fname in index_files:
        s = index_stats[fname]
        release_lines.append(f" {s['sha256']} {s['size']:16d} {fname}")

    release_lines.append("SHA512:")
    for fname in index_files:
        s = index_stats[fname]
        release_lines.append(f" {s['sha512']} {s['size']:16d} {fname}")

    release_path = os.path.join(REPO_DIR, "Release")
    with open(release_path, "w", encoding="utf-8") as f:
        f.write("\n".join(release_lines) + "\n")

    print(f"Successfully generated Sileo repo with {len(deb_files)} package(s) in {REPO_DIR}")


if __name__ == "__main__":
    main()
