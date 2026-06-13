from __future__ import annotations

import argparse
import json
import tempfile
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .material_db import MaterialCalculator, MaterialDatabase, import_excel_sources
from .material_db.price_calculator import net_after_trade_tax, required_trade_gross_for_net, trade_tax_amount


STATIC_ROOT = Path(__file__).with_name("web") / "materials"


class MaterialWebServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], workspace: str | Path) -> None:
        super().__init__(server_address, MaterialWebHandler)
        self.workspace = Path(workspace)
        self.db = MaterialDatabase(self.workspace)
        self.calculator = MaterialCalculator(self.db)


class MaterialWebHandler(BaseHTTPRequestHandler):
    server: MaterialWebServer

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_common_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api("GET", parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api("POST", parsed.path, parse_qs(parsed.query))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api("DELETE", parsed.path, parse_qs(parsed.query))
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _handle_api(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        try:
            result = self._route_api(method, path, query)
        except ApiError as exc:
            self._send_json({"ok": False, "error": exc.message}, exc.status)
            return
        except Exception as exc:  # noqa: BLE001
            self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if isinstance(result, DownloadResponse):
            self._send_download(result)
        else:
            self._send_json({"ok": True, "data": result})

    def _route_api(self, method: str, path: str, query: dict[str, list[str]]) -> Any:
        parts = [unquote(part) for part in path.strip("/").split("/")]
        if len(parts) < 2:
            raise ApiError("接口不存在", HTTPStatus.NOT_FOUND)

        resource = parts[1]
        db = self.server.db

        if method == "GET" and resource == "health":
            return self._health()

        if method == "GET" and resource == "bootstrap":
            return {
                "health": self._health(),
                "item_names": db.all_item_names(limit=5000),
                "source_names": db.all_source_names(limit=5000),
            }

        if method == "GET" and resource == "query":
            return self._query(query)

        if method == "GET" and resource == "trade-tax":
            tax_rate = _float_arg(query, "tax_rate", 0.05)
            target_net = _int_arg(query, "target_net", 0)
            gross = _int_arg(query, "gross", 0)
            required = required_trade_gross_for_net(target_net, tax_rate)
            return {
                "target_net": target_net,
                "required_gross": required,
                "required_tax": trade_tax_amount(required, tax_rate),
                "required_net": net_after_trade_tax(required, tax_rate),
                "gross": gross,
                "gross_tax": trade_tax_amount(gross, tax_rate),
                "gross_net": net_after_trade_tax(gross, tax_rate),
            }

        if resource == "materials":
            return self._materials(method, parts, query)

        if resource == "item-sources":
            return self._item_sources(method, parts, query)

        if resource == "source-items":
            return self._source_items(method, parts, query)

        if resource == "prices":
            return self._prices(method, parts, query)

        if resource == "price-history" and method == "GET":
            return db.price_history(_query_value(query, "item_name"))

        if resource == "aliases":
            return self._aliases(method, parts, query)

        if resource == "recipes":
            return self._recipes(method, parts, query)

        if resource == "recipe-cost":
            if method != "POST":
                raise ApiError("只支持 POST", HTTPStatus.METHOD_NOT_ALLOWED)
            payload = self._read_json()
            return self.server.calculator.recipe_cost(
                str(payload.get("product_name") or ""),
                target_quantity=int(payload.get("target_quantity") or 1),
                confidence=float(payload.get("confidence") or 0.95),
            )

        if resource == "upgrades":
            return self._upgrades(method, parts, query)

        if resource == "upgrade-cost":
            if method != "POST":
                raise ApiError("只支持 POST", HTTPStatus.METHOD_NOT_ALLOWED)
            payload = self._read_json()
            return self.server.calculator.upgrade_cost(
                str(payload.get("equipment_name") or ""),
                int(payload.get("from_level") or 0),
                int(payload.get("to_level") or 1),
                target_quantity=int(payload.get("target_quantity") or 1),
                confidence=float(payload.get("confidence") or 0.95),
            )

        if resource == "settings":
            if method == "POST":
                payload = self._read_json()
                db.set_diamond_per_rmb(float(payload.get("diamond_per_rmb") or 0))
                return {"diamond_per_rmb": db.diamond_per_rmb()}
            if method == "GET":
                return {"diamond_per_rmb": db.diamond_per_rmb()}
            raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)

        if resource == "import":
            return self._imports(method, parts, query)

        if resource == "export":
            return self._exports(method, parts)

        raise ApiError("接口不存在", HTTPStatus.NOT_FOUND)

    def _health(self) -> dict[str, Any]:
        db = self.server.db
        counts: dict[str, int] = {}
        with db.connect() as conn:
            for table in ("items", "source_items", "recipes", "upgrade_steps", "item_prices", "item_aliases"):
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {
            "workspace": str(self.server.workspace),
            "db_path": str(db.db_path),
            "diamond_per_rmb": db.diamond_per_rmb(),
            "counts": counts,
        }

    def _query(self, query: dict[str, list[str]]) -> dict[str, Any]:
        kind = _query_value(query, "kind", "material")
        text = _query_value(query, "q")
        if kind == "material":
            return {"kind": kind, "rows": [_with_money(self.server.db, row) for row in self.server.db.list_materials(text)]}
        if kind == "source":
            return {"kind": kind, "rows": [_with_money(self.server.db, row) for row in self.server.db.list_source_drops(text)]}
        if kind == "recipe":
            return self.server.calculator.recipe_cost(
                text,
                target_quantity=_int_arg(query, "target_quantity", 1),
                confidence=_float_arg(query, "confidence", 0.95),
            )
        if kind == "upgrade":
            return self.server.calculator.upgrade_cost(
                text,
                _int_arg(query, "from_level", 0),
                _int_arg(query, "to_level", 1),
                target_quantity=_int_arg(query, "target_quantity", 1),
                confidence=_float_arg(query, "confidence", 0.95),
            )
        raise ApiError("查询类型不正确")

    def _materials(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        db = self.server.db
        if method == "GET":
            rows = db.list_materials(_query_value(query, "q"), limit=_int_arg(query, "limit", 1000))
            return [_with_money(db, row) for row in rows]
        if method == "POST":
            payload = self._read_json()
            name = str(payload.get("name") or payload.get("item_name") or "").strip()
            if not name:
                raise ApiError("材料名不能为空")
            db.update_item_details(
                name,
                category=str(payload.get("category") or ""),
                notes=str(payload.get("notes") or ""),
                icon_path=str(payload.get("icon_path") or ""),
            )
            price = payload.get("price_diamonds")
            if price not in {None, ""}:
                db.set_price(
                    name,
                    float(price),
                    price_source=str(payload.get("price_source") or "网页版录入"),
                    notes=str(payload.get("price_notes") or ""),
                    is_active=bool(payload.get("is_active", True)),
                )
            return _first_or_none([_with_money(db, row) for row in db.list_materials(name, limit=1)])
        raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)

    def _item_sources(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        if method != "GET":
            raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)
        return self.server.db.item_sources(_query_value(query, "item_name"), limit=_int_arg(query, "limit", 500))

    def _source_items(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        db = self.server.db
        if method == "GET":
            return db.search_source_items_by_source(_query_value(query, "q"), limit=_int_arg(query, "limit", 1000))
        if method == "POST":
            payload = self._read_json()
            item_name = str(payload.get("item_name") or "").strip()
            source_name = str(payload.get("source_name") or "").strip()
            if not item_name or not source_name:
                raise ApiError("材料名和出处不能为空")
            qty = payload.get("parsed_quantity")
            inserted = db.add_source_item(
                item_name=item_name,
                raw_text=str(payload.get("raw_text") or item_name),
                source_name=source_name,
                parsed_quantity=float(qty) if qty not in {None, ""} else None,
                source_type=str(payload.get("source_type") or "manual"),
                notes=str(payload.get("notes") or "网页版新增"),
                skip_duplicate=bool(payload.get("skip_duplicate", False)),
            )
            return {"id": inserted}
        if method == "DELETE" and len(parts) == 3:
            return {"deleted": db.delete_source_items([int(parts[2])])}
        raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)

    def _prices(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        db = self.server.db
        if method == "GET":
            rows = db.list_prices(_query_value(query, "q"))
            return [_with_money(db, row) for row in rows]
        if method == "POST":
            payload = self._read_json()
            price_id = db.set_price(
                str(payload.get("item_name") or ""),
                float(payload.get("price_diamonds") or 0),
                price_source=str(payload.get("price_source") or "网页版录入"),
                notes=str(payload.get("notes") or ""),
                is_active=bool(payload.get("is_active", True)),
            )
            return {"id": price_id}
        if method == "DELETE" and len(parts) == 3:
            return {"deleted": db.delete_price(int(parts[2]))}
        raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)

    def _aliases(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        db = self.server.db
        if method == "GET":
            return db.list_aliases(_query_value(query, "q"))
        if method == "POST":
            payload = self._read_json()
            return {"id": db.add_alias(str(payload.get("item_name") or ""), str(payload.get("alias") or ""))}
        if method == "DELETE" and len(parts) == 3:
            return {"deleted": db.delete_alias(int(parts[2]))}
        raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)

    def _recipes(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        db = self.server.db
        if method == "GET" and len(parts) == 2:
            return db.list_recipes(
                _query_value(query, "q"),
                category=_query_value(query, "category", "全部"),
                recipe_type=_query_value(query, "recipe_type", "全部"),
            )
        if method == "GET" and len(parts) == 3:
            recipe = db.get_recipe(int(parts[2]))
            if not recipe:
                raise ApiError("配方不存在", HTTPStatus.NOT_FOUND)
            return recipe
        if method == "POST":
            payload = self._read_json()
            recipe = dict(payload.get("recipe") or payload)
            materials = list(payload.get("materials") or recipe.pop("materials", []) or [])
            recipe_id = recipe.get("id") or payload.get("id")
            return {"id": db.save_recipe(recipe, materials, int(recipe_id) if recipe_id else None)}
        if method == "DELETE" and len(parts) == 3:
            return {"deleted": db.delete_recipe(int(parts[2]))}
        raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)

    def _upgrades(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        db = self.server.db
        if method == "GET" and len(parts) == 2:
            return db.list_upgrade_steps(_query_value(query, "q"))
        if method == "GET" and len(parts) == 3:
            step = db.get_upgrade_step(int(parts[2]))
            if not step:
                raise ApiError("升级步骤不存在", HTTPStatus.NOT_FOUND)
            return step
        if method == "POST":
            payload = self._read_json()
            step = dict(payload.get("step") or payload)
            materials = list(payload.get("materials") or step.pop("materials", []) or [])
            step_id = step.get("id") or payload.get("id")
            return {"id": db.save_upgrade_step(step, materials, int(step_id) if step_id else None)}
        if method == "DELETE" and len(parts) == 3:
            return {"deleted": db.delete_upgrade_step(int(parts[2]))}
        raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)

    def _imports(self, method: str, parts: list[str], query: dict[str, list[str]]) -> Any:
        if method != "POST" or len(parts) != 3:
            raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)
        db = self.server.db
        kind = parts[2]
        if kind == "json":
            payload = self._read_json()
            backup = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
            path = _write_temp_text(json.dumps(backup, ensure_ascii=False), ".json")
            try:
                db.import_json(path)
            finally:
                _unlink(path)
            return {"imported": True}
        if kind == "prices-csv":
            text = self._read_body().decode("utf-8-sig")
            path = _write_temp_text(text, ".csv")
            try:
                count = db.import_prices_csv(path)
            finally:
                _unlink(path)
            return {"imported": count}
        if kind == "excel":
            mode = _query_value(query, "mode", "merge")
            filename = _query_value(query, "filename", "upload.xlsx")
            if not filename.lower().endswith(".xlsx"):
                filename = "upload.xlsx"
            path = _write_temp_bytes(self._read_body(), ".xlsx")
            try:
                summary = import_excel_sources(db, path, mode=mode, notes=f"网页版导入：{filename}")
            finally:
                _unlink(path)
            return summary.__dict__
        raise ApiError("导入类型不支持", HTTPStatus.NOT_FOUND)

    def _exports(self, method: str, parts: list[str]) -> Any:
        if method != "GET" or len(parts) != 3:
            raise ApiError("方法不支持", HTTPStatus.METHOD_NOT_ALLOWED)
        db = self.server.db
        kind = parts[2]
        if kind == "json":
            path = _temp_path(".json")
            try:
                db.export_json(path)
                data = Path(path).read_bytes()
            finally:
                _unlink(path)
            return DownloadResponse("stoneage_materials_backup.json", "application/json; charset=utf-8", data)
        callbacks = {
            "prices-csv": ("stoneage_prices.csv", db.export_prices_csv),
            "sources-csv": ("stoneage_sources.csv", db.export_sources_csv),
            "recipes-csv": ("stoneage_recipes.csv", db.export_recipes_csv),
            "upgrades-csv": ("stoneage_upgrades.csv", db.export_upgrades_csv),
        }
        if kind not in callbacks:
            raise ApiError("导出类型不支持", HTTPStatus.NOT_FOUND)
        filename, callback = callbacks[kind]
        path = _temp_path(".csv")
        try:
            callback(path)
            data = Path(path).read_bytes()
        finally:
            _unlink(path)
        return DownloadResponse(filename, "text/csv; charset=utf-8", data)

    def _serve_static(self, request_path: str) -> None:
        if request_path in {"", "/"}:
            target = STATIC_ROOT / "index.html"
        else:
            relative = request_path.removeprefix("/")
            if relative.startswith("static/materials/"):
                relative = relative.removeprefix("static/materials/")
            target = STATIC_ROOT / relative
        try:
            target = target.resolve()
            if STATIC_ROOT.resolve() not in target.parents and target != STATIC_ROOT.resolve():
                raise FileNotFoundError
            data = target.read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = _content_type(target)
        self.send_response(HTTPStatus.OK)
        self._send_common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict[str, Any]:
        raw = self._read_body()
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ApiError("JSON 必须是对象")
        return data

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length > 0 else b""

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus | int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._send_common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_download(self, response: "DownloadResponse") -> None:
        self.send_response(HTTPStatus.OK)
        self._send_common_headers()
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{response.filename}"')
        self.send_header("Content-Length", str(len(response.data)))
        self.end_headers()
        self.wfile.write(response.data)

    def _send_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")


class ApiError(Exception):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


class DownloadResponse:
    def __init__(self, filename: str, content_type: str, data: bytes) -> None:
        self.filename = filename
        self.content_type = content_type
        self.data = data


def start_server(workspace: str | Path, host: str = "127.0.0.1", port: int = 8765) -> MaterialWebServer:
    return MaterialWebServer((host, port), workspace)


def start_server_in_thread(
    workspace: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> tuple[MaterialWebServer, str]:
    server = start_server(workspace, host, port)
    thread = threading.Thread(target=server.serve_forever, name="stoneage-material-web", daemon=True)
    thread.start()
    actual_host, actual_port = server.server_address[:2]
    return server, f"http://{actual_host}:{actual_port}/"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="启动石器时代材料库网页版")
    parser.add_argument("--workspace", default=str(Path.cwd()), help="项目根目录，默认当前目录")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--open", action="store_true", help="启动后打开浏览器")
    args = parser.parse_args(argv)

    server, url = start_server_in_thread(args.workspace, args.host, args.port)
    print(f"材料库网页版已启动：{url}")
    print(f"数据库：{server.db.db_path}")
    if args.open:
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


def _query_value(query: dict[str, list[str]], name: str, default: str = "") -> str:
    values = query.get(name)
    if not values:
        return default
    return values[0]


def _int_arg(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        return int(_query_value(query, name, str(default)))
    except ValueError:
        return default


def _float_arg(query: dict[str, list[str]], name: str, default: float) -> float:
    try:
        return float(_query_value(query, name, str(default)))
    except ValueError:
        return default


def _with_money(db: MaterialDatabase, row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    price = data.get("price_diamonds")
    if price not in {None, ""}:
        data["price_rmb"] = db.diamonds_to_rmb(float(price))
    return data


def _first_or_none(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return rows[0] if rows else None


def _content_type(path: Path) -> str:
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    if path.suffix == ".css":
        return "text/css; charset=utf-8"
    if path.suffix == ".js":
        return "text/javascript; charset=utf-8"
    return "application/octet-stream"


def _temp_path(suffix: str) -> Path:
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.close()
    return Path(handle.name)


def _write_temp_text(text: str, suffix: str) -> Path:
    path = _temp_path(suffix)
    path.write_text(text, encoding="utf-8")
    return path


def _write_temp_bytes(data: bytes, suffix: str) -> Path:
    path = _temp_path(suffix)
    path.write_bytes(data)
    return path


def _unlink(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
