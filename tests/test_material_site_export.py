from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from stoneage_studio.material_db import MaterialDatabase
from stoneage_studio.material_site_export import DATA_FILE_NAME, build_public_material_payload, export_material_site, sync_material_site


class MaterialSiteExportTests(unittest.TestCase):
    def test_public_payload_contains_read_only_site_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = MaterialDatabase(root)
            db.set_diamond_per_rmb(400)
            db.add_source_item(item_name="材料A", raw_text="材料A2个", source_name="副本A", parsed_quantity=2, source_type="manual")
            db.set_price("材料A", 120, price_source="测试")
            db.save_recipe(
                {"product_name": "成品A", "success_rate": 1, "output_quantity": 1, "diamond_cost": 10},
                [{"material_name": "材料A", "quantity": 2}],
            )

            payload = build_public_material_payload(db)

            self.assertEqual(payload["diamond_per_rmb"], 400)
            self.assertEqual(payload["site"]["meta"]["title"], "石器时代-精灵召唤")
            self.assertNotIn("sponsor", payload["site"]["meta"]["meta"])
            self.assertTrue(any(row["title"] == "攻略分享" for row in payload["site"]["boards"]))
            self.assertGreaterEqual(len(payload["site"]["announcements"]), 1)
            self.assertEqual(payload["counts"]["materials"], 2)
            material_a = next(row for row in payload["materials"] if row["name"] == "材料A")
            self.assertEqual(material_a["price_rmb"], 0.3)
            self.assertEqual(payload["source_items"][0]["source_name"], "副本A")
            self.assertEqual(payload["recipes"][0]["materials"][0]["price_diamonds"], 120)

    def test_export_material_site_writes_static_files_and_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            output = Path(tmp) / "site"
            zip_path = Path(tmp) / "site.zip"
            db = MaterialDatabase(root)
            db.set_price("材料B", 88)

            export_material_site(root, output, zip_path=zip_path)

            self.assertTrue((output / "index.html").exists())
            self.assertTrue((output / "styles.css").exists())
            self.assertTrue((output / "app.js").exists())
            self.assertTrue((output / "assets" / "official-hero.jpg").exists())
            data = json.loads((output / DATA_FILE_NAME).read_text(encoding="utf-8"))
            self.assertEqual(data["materials"][0]["name"], "材料B")
            with zipfile.ZipFile(zip_path) as archive:
                self.assertIn("index.html", archive.namelist())
                self.assertIn(DATA_FILE_NAME, archive.namelist())
                self.assertIn("assets/official-hero.jpg", archive.namelist())

    def test_sync_material_site_commits_and_pushes_git_repo(self) -> None:
        if not shutil.which("git"):
            self.skipTest("git is not installed")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "workspace"
            site = Path(tmp) / "site"
            remote = Path(tmp) / "remote.git"
            db = MaterialDatabase(root)
            db.set_price("材料C", 66)
            run_git(remote.parent, "init", "--bare", str(remote))
            site.mkdir()
            run_git(site, "init")
            run_git(site, "config", "user.email", "test@example.com")
            run_git(site, "config", "user.name", "Test User")
            run_git(site, "remote", "add", "origin", str(remote))

            result = sync_material_site(root, site)

            self.assertTrue(result.git_repo)
            self.assertTrue(result.changed)
            self.assertTrue(result.committed)
            self.assertTrue(result.pushed)
            self.assertTrue((site / DATA_FILE_NAME).exists())
            log = run_git(remote.parent, "--git-dir", str(remote), "log", "--oneline", "--all")
            self.assertIn("Update material site", log.stdout)

def run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed\n{result.stdout}\n{result.stderr}")
    return result


if __name__ == "__main__":
    unittest.main()
