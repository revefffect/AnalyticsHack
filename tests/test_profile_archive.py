import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZIP_DEFLATED, ZipFile

from tools.profile_archive import main, profile_archive


class ArchiveProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "input.zip"

    def write_members(self, entries):
        with ZipFile(self.path, "w", ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)

    def run_cli(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main([str(self.path)])
        return code, json.loads(stdout.getvalue())

    def test_inventory_encodings_quoted_values_and_no_extraction(self):
        self.write_members([
            ("stations/data.CSV", 'город;значение\r\n"Верх;Низ";0\r\nГород; \r\n'.encode("cp1251")),
            ("../reference.parquet", b"\x00"),
            ("series.tsv", 'name\tvalue\n"multi\nline"\tNA\n\t\n'.encode("utf-8-sig")),
        ])
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        code, report = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(report, profile_archive(self.path))
        self.assertEqual(report["sha256"], digest)
        opaque, tsv, csv = report["members"]
        self.assertEqual(opaque, {"path": "../reference.parquet", "bytes": 1, "status": "inventory_only"})
        self.assertEqual((tsv["valid_records"], tsv["delimiter"]), (2, "\t"))
        self.assertEqual(tsv["columns"], [{"name": "name", "missing": 1}, {"name": "value", "missing": 1}])
        self.assertEqual((csv["encoding"], csv["delimiter"]), ("cp1251", ";"))
        self.assertEqual(csv["columns"], [{"name": "город", "missing": 0}, {"name": "значение", "missing": 1}])
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), digest)

    def test_missing_counts_exclude_ragged_and_blank_records(self):
        self.write_members([("bad.csv", 'a,a,\n,0,NA\n\nshort\n1,2,3,4\n')])
        code, report = self.run_cli()
        self.assertEqual(code, 1)
        table = report["members"][0]
        self.assertEqual(table["status"], "issues")
        self.assertEqual(table["records_after_header"], 4)
        self.assertEqual((table["valid_records"], table["blank_records"], table["ragged_records"]), (1, 1, 2))
        self.assertEqual(table["ragged_examples"], [{"record": 4, "fields": 1}, {"record": 5, "fields": 4}])
        self.assertEqual([column["missing"] for column in table["columns"]], [1, 0, 0])
        self.assertIn("Duplicate column name", table["issues"])
        self.assertIn("Empty column name", table["issues"])

    def test_syntax_encoding_and_empty_table_errors_leave_other_members_readable(self):
        self.write_members([
            ("a.csv", 'x,y\n"unclosed,field'),
            ("b.csv", b""),
            ("c.csv", "x,y\n1,2".encode("utf-16")),
            ("d.csv", "x,y\n1,2"),
        ])
        code, report = self.run_cli()
        self.assertEqual(code, 1)
        self.assertEqual([m["status"] for m in report["members"]], ["error", "error", "error", "profiled"])

    def test_non_zip_returns_machine_readable_failure(self):
        self.path.write_bytes(b"not a zip")
        code, report = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("error", report)


if __name__ == "__main__":
    unittest.main()
