"""Inventory a ZIP and stream its headered CSV/TSV files into a JSON report.

Run: python3 tools/profile_archive.py data.zip > profile.json
Exit codes: 0 clean, 1 member issues, 2 unreadable archive.
Only empty or whitespace-only cells count as missing. Counts by column cover
complete-width records only. CSV row numbers are logical records, including
the header and blank records. Non-CSV/TSV members are inventoried only.
"""

import argparse
import csv
import hashlib
import io
import json
from itertools import chain
from pathlib import Path, PurePosixPath
import sys
from zipfile import BadZipFile, ZipFile
import zlib


def choose_delimiter(header_line, suffix):
    """Use the first physical header line. The filename resolves ties."""
    preferred = "\t" if suffix == ".tsv" else ","
    candidates = list(dict.fromkeys([preferred, ",", ";", "\t"]))
    return max(candidates, key=lambda d: len(next(csv.reader([header_line], delimiter=d))))


def read_table(archive, info, suffix, encoding):
    with archive.open(info) as raw, io.TextIOWrapper(raw, encoding=encoding, newline="") as source:
        first_line = source.readline()
        if not first_line:
            return {"status": "error", "error": "Missing header"}
        if "\x00" in first_line:
            return {"status": "error", "error": "NUL in header: unsupported encoding or binary file"}
        delimiter = choose_delimiter(first_line, suffix)
        # Reuse the first line even when the header contains a quoted newline.
        reader = csv.reader(chain([first_line], source), delimiter=delimiter, strict=True)
        header = next(reader)
        if not header:
            return {"status": "error", "error": "Empty header row"}

        columns = [{"name": name, "missing": 0} for name in header]
        issues = []
        if any(not name.strip() for name in header):
            issues.append("Empty column name")
        if len(set(header)) != len(header):
            issues.append("Duplicate column name")

        valid = blank = ragged = 0
        examples = []
        for index, row in enumerate(reader, start=2):
            if not row:
                blank += 1
            elif len(row) != len(header):
                ragged += 1
                if len(examples) < 5:
                    examples.append({"record": index, "fields": len(row)})
            else:
                valid += 1
                for column, value in zip(columns, row):
                    if not value.strip():
                        column["missing"] += 1
        if ragged:
            issues.append("Records with a different number of fields than the header")
        return {
            "status": "issues" if issues else "profiled",
            "encoding": encoding,
            "delimiter": delimiter,
            "records_after_header": valid + blank + ragged,
            "valid_records": valid,
            "blank_records": blank,
            "ragged_records": ragged,
            "ragged_examples": examples,
            "columns": columns,
            "issues": issues,
        }


def profile_member(archive, info):
    entry = {"path": info.filename, "bytes": info.file_size}
    suffix = PurePosixPath(info.filename).suffix.lower()
    if info.is_dir() or suffix not in {".csv", ".tsv"}:
        return {**entry, "status": "inventory_only"}
    try:
        try:
            table = read_table(archive, info, suffix, "utf-8-sig")
        except UnicodeDecodeError:
            table = read_table(archive, info, suffix, "cp1251")
    except (BadZipFile, OSError, RuntimeError, UnicodeError, csv.Error, EOFError, zlib.error) as exc:
        table = {"status": "error", "error": str(exc)}
    return {**entry, **table}


def profile_archive(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
        source.seek(0)
        with ZipFile(source) as archive:
            members = sorted(archive.infolist(), key=lambda entry: (entry.filename, entry.header_offset))
            return {"sha256": digest.hexdigest(), "members": [profile_member(archive, m) for m in members]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)
    try:
        report = profile_archive(args.archive)
        status = int(any(m["status"] in {"issues", "error"} for m in report["members"]))
    except (OSError, BadZipFile, EOFError) as exc:
        report = {"error": str(exc)}
        status = 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    sys.exit(main())
