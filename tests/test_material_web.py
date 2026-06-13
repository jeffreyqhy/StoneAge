from __future__ import annotations

import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import urlencode

from stoneage_studio.material_db import MaterialDatabase
from stoneage_studio.material_web import start_server_in_thread


class MaterialWebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.server, self.url = start_server_in_thread(self.root, port=0)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def api(self, path: str, *, method: str = "GET", body: object | bytes | str | None = None, content_type: str = "application/json") -> dict:
        data: bytes | None = None
        headers = {}
        if body is not None:
            if isinstance(body, bytes):
                data = body
            elif isinstance(body, str):
                data = body.encode("utf-8")
            else:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.url.rstrip("/") + path, data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["ok"], payload)
        return payload["data"]

    def test_material_edit_source_query_and_recipe_cost(self) -> None:
        material = self.api(
            "/api/materials",
            method="POST",
            body={
                "name": "材料A",
                "category": "测试",
                "notes": "网页版测试",
                "price_diamonds": 120,
                "price_source": "单元测试",
            },
        )
        self.assertEqual(material["name"], "材料A")
        self.assertEqual(material["price_diamonds"], 120)

        self.api(
            "/api/source-items",
            method="POST",
            body={"item_name": "材料A", "source_name": "副本A", "parsed_quantity": 2, "raw_text": "材料A2个"},
        )
        query = self.api("/api/query?" + urlencode({"kind": "material", "q": "材料A"}))
        self.assertEqual(query["rows"][0]["name"], "材料A")
        self.assertIn("副本A", query["rows"][0]["source_names"])

        recipe = self.api(
            "/api/recipes",
            method="POST",
            body={
                "recipe": {
                    "product_name": "成品A",
                    "category": "测试",
                    "recipe_type": "打造",
                    "success_rate": 1,
                    "output_quantity": 1,
                    "diamond_cost": 10,
                },
                "materials": [{"material_name": "材料A", "quantity": 2}],
            },
        )
        self.assertGreater(recipe["id"], 0)
        cost = self.api("/api/recipe-cost", method="POST", body={"product_name": "成品A", "target_quantity": 1})
        self.assertEqual(cost["materials"][0]["material_name"], "材料A")
        self.assertEqual(cost["costs"]["standard"]["material_diamonds"], 240)

    def test_price_csv_import_and_json_export(self) -> None:
        imported = self.api(
            "/api/import/prices-csv",
            method="POST",
            body="item_name,price_diamonds,is_active,price_source,notes\n材料B,88,1,CSV,测试\n",
            content_type="text/csv",
        )
        self.assertEqual(imported["imported"], 1)
        db = MaterialDatabase(self.root)
        self.assertEqual(db.get_price("材料B")["price_diamonds"], 88)

        with urllib.request.urlopen(self.url.rstrip("/") + "/api/export/json", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn("tables", payload)
        self.assertIn("items", payload["tables"])

    def test_trade_tax_api_rounds_tax_down(self) -> None:
        data = self.api("/api/trade-tax?" + urlencode({"target_net": 9968, "gross": 10000, "tax_rate": 0.05}))
        self.assertEqual(data["required_gross"], 10492)
        self.assertEqual(data["required_tax"], 524)
        self.assertEqual(data["required_net"], 9968)
        self.assertEqual(data["gross_tax"], 500)
        self.assertEqual(data["gross_net"], 9500)


if __name__ == "__main__":
    unittest.main()
