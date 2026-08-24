import io
import os
import unittest
import uuid
from unittest.mock import patch

from PIL import Image

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["APP_SECRET"] = "test-secret-only"
os.environ["APP_ENV"] = "development"

from app import (Inventory, InventoryTransaction, RATE_BUCKETS, User, VisitDaily, app, db,
                 parse_legend_ocr, prepare_owner_activity)  # noqa: E402


class ProductionFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)

    def setUp(self):
        RATE_BUCKETS.clear()
        self.client = app.test_client()
        self.email = f"test-{uuid.uuid4().hex[:8]}@example.com"
        response = self.client.post("/api/auth/register", json={
            "email": self.email,
            "username": "测试豆友",
            "password": "safe-password-123",
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        self.csrf = response.get_json()["csrfToken"]
        self.headers = {"X-CSRF-Token": self.csrf}

    def test_account_isolation_and_inventory_transaction(self):
        inventory = self.client.get("/api/inventory").get_json()["items"]
        self.assertEqual(len(inventory), 221)
        response = self.client.post("/api/inventory/transactions", headers=self.headers, json={
            "operation": "checkin",
            "items": [{"id": "A1", "quantity": 1000}, {"id": "H2", "quantity": 500}],
            "remark": "测试入库",
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        batch_id = response.get_json()["batchId"]
        quantities = {row["id"]: row["quantity"] for row in self.client.get("/api/inventory").get_json()["items"]}
        self.assertEqual(quantities["A1"], 1000)
        self.assertEqual(quantities["H2"], 500)
        undo = self.client.post(f"/api/inventory/undo/{batch_id}", headers=self.headers)
        self.assertEqual(undo.status_code, 200, undo.get_json())
        quantities = {row["id"]: row["quantity"] for row in self.client.get("/api/inventory").get_json()["items"]}
        self.assertEqual(quantities["A1"], 0)

    def test_221_color_starter_kit_adds_1000_to_every_color(self):
        before = {row["id"]: row["quantity"] for row in self.client.get("/api/inventory").get_json()["items"]}
        response = self.client.post("/api/inventory/starter-kit", headers=self.headers,
                                    json={"confirm": "ADD_221_KIT"})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["colors"], 221)
        self.assertEqual(response.get_json()["totalAdded"], 221000)
        after = {row["id"]: row["quantity"] for row in self.client.get("/api/inventory").get_json()["items"]}
        self.assertTrue(all(after[code] == quantity + 1000 for code, quantity in before.items()))
        history = self.client.get("/api/inventory/history?limit=500").get_json()["items"]
        kit_rows = [row for row in history if row["remark"] == "221 色新手套装"]
        self.assertEqual(len(kit_rows), 221)
        self.assertEqual(len({row["batchId"] for row in kit_rows}), 1)

    def test_csrf_and_cross_user_protection(self):
        rejected = self.client.post("/api/inventory/clear", json={"confirm": "CLEAR"})
        self.assertEqual(rejected.status_code, 403)
        create = self.client.post("/api/blueprints", headers=self.headers, json={
            "name": "隔离测试图纸", "items": [{"id": "A1", "quantity": 12}], "pattern": {}
        })
        blueprint_id = create.get_json()["item"]["id"]
        other = app.test_client()
        session = other.get("/api/session").get_json()
        register = other.post("/api/auth/register", json={
            "email": f"other-{uuid.uuid4().hex[:8]}@example.com", "username": "其他用户", "password": "safe-password-456"
        })
        self.assertEqual(register.status_code, 201)
        self.assertEqual(other.get(f"/api/blueprints/{blueprint_id}").status_code, 404)
        self.assertIsNotNone(session["csrfToken"])

    def test_blueprint_calculation_and_consumption(self):
        self.client.post("/api/inventory/transactions", headers=self.headers, json={
            "operation": "checkin", "items": [{"id": "A1", "quantity": 100}], "remark": "准备库存"
        })
        create = self.client.post("/api/blueprints", headers=self.headers, json={
            "name": "小花", "tag": "植物", "items": [{"id": "A1", "quantity": 30}], "pattern": {}
        })
        self.assertEqual(create.status_code, 201, create.get_json())
        blueprint_id = create.get_json()["item"]["id"]
        calculation = self.client.post("/api/blueprints/calculate", headers=self.headers,
                                       json={"selections": [{"id": blueprint_id, "count": 2}]})
        self.assertEqual(calculation.get_json()["items"][0]["remain"], 40)
        consumed = self.client.post(f"/api/blueprints/{blueprint_id}/consume", headers=self.headers, json={"count": 1})
        self.assertEqual(consumed.status_code, 200)
        quantities = {row["id"]: row["quantity"] for row in self.client.get("/api/inventory").get_json()["items"]}
        self.assertEqual(quantities["A1"], 70)

        progress = self.client.put(f"/api/blueprints/{blueprint_id}/progress", headers=self.headers,
                                   json={"progress": {"byColor": {"A1": 10}, "doneCells": [0, 1]}})
        self.assertEqual(progress.status_code, 200)
        detail = self.client.get(f"/api/blueprints/{blueprint_id}").get_json()["item"]
        self.assertEqual(detail["progress"]["byColor"]["A1"], 10)

    def test_real_image_quantization_and_export(self):
        image = Image.new("RGB", (16, 16), "#faf5cd")
        image_bytes = io.BytesIO()
        image.save(image_bytes, "PNG")
        image_bytes.seek(0)
        analyzed = self.client.post("/api/analyze", headers=self.headers, data={
            "columns": "8", "rows": "8", "maxColors": "8", "dither": "false",
            "image": (image_bytes, "sample.png"),
        }, content_type="multipart/form-data")
        self.assertEqual(analyzed.status_code, 200, analyzed.get_json())
        result = analyzed.get_json()["result"]
        self.assertEqual(len(result["cells"]), 64)
        self.assertEqual(sum(row["quantity"] for row in result["items"]), 64)
        exported = self.client.post("/api/pattern/export.png", headers=self.headers, json=result)
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.mimetype, "image/png")

    def test_subject_first_quantization_removes_edge_background(self):
        image = Image.new("RGB", (120, 80), "white")
        for x in range(0, 24):
            for y in range(80):
                image.putpixel((x, y), (10, 60, 58))  # screenshot-like sidebar touching the edge
        for x in range(42, 78):
            for y in range(22, 58):
                image.putpixel((x, y), (215, 50, 55))
        image_bytes = io.BytesIO()
        image.save(image_bytes, "PNG")
        image_bytes.seek(0)
        analyzed = self.client.post("/api/analyze", headers=self.headers, data={
            "columns": "16", "rows": "0", "cropMode": "subject", "cropMargin": "8", "dither": "false",
            "image": (image_bytes, "subject.png"),
        }, content_type="multipart/form-data")
        self.assertEqual(analyzed.status_code, 200, analyzed.get_json())
        result = analyzed.get_json()["result"]
        self.assertTrue(result["crop"]["applied"])
        self.assertGreater(result["crop"]["box"][0], 24)
        self.assertGreater(result["rows"], 11)  # full image would be 16x11; square subject keeps its own ratio
        self.assertTrue(any(cell is None for cell in result["cells"]))

    def test_legend_ocr_mode_returns_direct_color_counts(self):
        image = Image.new("RGB", (320, 180), "white")
        image_bytes = io.BytesIO()
        image.save(image_bytes, "PNG")
        image_bytes.seek(0)
        recognized = [{"id": "A5", "quantity": 98}, {"id": "D11", "quantity": 199}]
        with patch("app.extract_legend_items", return_value=recognized):
            response = self.client.post("/api/analyze", headers=self.headers, data={
                "recognitionMode": "legend", "image": (image_bytes, "legend.png"),
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()["result"]
        self.assertEqual(result["recognitionMode"], "legend")
        self.assertEqual(result["cells"], [])
        self.assertEqual([(item["id"], item["quantity"]) for item in result["items"]],
                         [("A5", 98), ("D11", 199)])

    def test_legend_parser_selects_legend_column_instead_of_grid_text(self):
        def box(x, y, width=30, height=18):
            return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]

        texts = ["H2", "H7", "A5", "98", "D11", "199", "E23", "147"]
        boxes = [box(100, 40), box(150, 40), box(800, 80), box(940, 80),
                 box(800, 130), box(940, 130), box(800, 180), box(940, 180)]
        items = parse_legend_ocr(texts, boxes, {"H2", "H7", "A5", "D11", "E23"})
        self.assertEqual(items, [{"id": "A5", "quantity": 98}, {"id": "D11", "quantity": 199},
                                 {"id": "E23", "quantity": 147}])

    def test_legend_parser_reads_horizontal_cards_with_counts_below(self):
        def box(x, y, width=36, height=19):
            return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]

        texts = ["A16", "A21", "A24", "A26", "A5", "G10", "G13", "G21", "G6", "H7",
                 "x3", "x18", "x8", "x5", "x243", "x85", "x6", "x4", "x155", "x277"]
        boxes = [box(20 + index * 53, 22) for index in range(10)]
        boxes += [box(27 + index * 53, 61, 30, 17) for index in range(10)]
        expected = [("A16", 3), ("A21", 18), ("A24", 8), ("A26", 5), ("A5", 243),
                    ("G10", 85), ("G13", 6), ("G21", 4), ("G6", 155), ("H7", 277)]
        items = parse_legend_ocr(texts, boxes, {code for code, _quantity in expected})
        self.assertEqual([(item["id"], item["quantity"]) for item in items], expected)

    def test_legend_parser_accepts_inline_and_multiplication_variants(self):
        box = [[[10, 10], [80, 10], [80, 30], [10, 30]],
               [[10, 45], [50, 45], [50, 65], [10, 65]]]
        items = parse_legend_ocr(["A16×3", "H7*277"], box, {"A16", "H7"})
        self.assertEqual(items, [{"id": "A16", "quantity": 3}, {"id": "H7", "quantity": 277}])

    def test_blueprint_create_request_key_prevents_duplicate_saves(self):
        request_key = f"save-{uuid.uuid4()}"
        payload = {"name": "防重复图纸", "items": '[{"id":"A1","quantity":12}]',
                   "pattern": "{}", "requestKey": request_key}
        first = self.client.post("/api/blueprints", headers=self.headers, data=payload)
        second = self.client.post("/api/blueprints", headers=self.headers, data=payload)
        self.assertEqual(first.status_code, 201, first.get_json())
        self.assertEqual(second.status_code, 200, second.get_json())
        self.assertTrue(second.get_json()["duplicate"])
        self.assertEqual(first.get_json()["item"]["id"], second.get_json()["item"]["id"])
        matching = [item for item in self.client.get("/api/blueprints").get_json()["items"]
                    if item["name"] == "防重复图纸"]
        self.assertEqual(len(matching), 1)

    def test_one_click_guest_clones_and_isolates_admin_data(self):
        previous_owner = os.environ.get("OWNER_EMAIL")
        os.environ["OWNER_EMAIL"] = self.email
        with app.app_context():
            source = User.query.filter_by(email=self.email).one()
            source.is_admin = True
            db.session.commit()
        self.client.post("/api/inventory/transactions", headers=self.headers, json={
            "operation": "checkin", "items": [{"id": "A1", "quantity": 321}], "remark": "游客模板"
        })
        self.client.post("/api/blueprints", headers=self.headers, json={
            "name": "游客示例图纸", "items": [{"id": "A1", "quantity": 12}], "pattern": {}
        })
        guest = app.test_client()
        created = guest.post("/api/auth/guest")
        self.assertEqual(created.status_code, 201, created.get_json())
        self.assertTrue(created.get_json()["user"]["isGuest"])
        guest_headers = {"X-CSRF-Token": created.get_json()["csrfToken"]}
        guest_inventory = {row["id"]: row["quantity"] for row in guest.get("/api/inventory").get_json()["items"]}
        self.assertEqual(guest_inventory["A1"], 321)
        self.assertEqual(len(guest.get("/api/blueprints").get_json()["items"]), 1)
        guest.post("/api/inventory/transactions", headers=guest_headers, json={
            "operation": "checkout", "items": [{"id": "A1", "quantity": 21}], "remark": "游客操作"
        })
        owner_inventory = {row["id"]: row["quantity"] for row in self.client.get("/api/inventory").get_json()["items"]}
        self.assertEqual(owner_inventory["A1"], 321)
        self.assertEqual(guest.post("/api/auth/logout", headers=guest_headers).status_code, 200)
        self.assertEqual(guest.get("/api/inventory").status_code, 401)
        if previous_owner is None:
            os.environ.pop("OWNER_EMAIL", None)
        else:
            os.environ["OWNER_EMAIL"] = previous_owner

    def test_owner_activity_seed_is_once_only_and_hides_migration_note(self):
        with app.app_context():
            user = User.query.filter_by(email=self.email).one()
            user.is_admin = True
            inventory = Inventory.query.filter_by(user_id=user.id, color_code="A1").one()
            db.session.add(InventoryTransaction(
                user_id=user.id, color_code="A1", operation="set", delta=0,
                balance_after=inventory.quantity, remark="旧版数据迁移", source="migration",
                batch_id=str(uuid.uuid4()),
            ))
            db.session.commit()
            prepare_owner_activity(user)
            db.session.commit()
            first_count = InventoryTransaction.query.filter_by(user_id=user.id, source="activity").count()
            prepare_owner_activity(user)
            db.session.commit()
            second_count = InventoryTransaction.query.filter_by(user_id=user.id, source="activity").count()
            cleaned = InventoryTransaction.query.filter_by(user_id=user.id, color_code="A1").order_by(
                InventoryTransaction.id.asc()).first()
            self.assertEqual(first_count, 14)
            self.assertEqual(second_count, first_count)
            self.assertEqual(cleaned.remark, "初始库存")

    def test_password_reset_debug_flow(self):
        forgot = self.client.post("/api/auth/forgot-password", json={"email": self.email})
        self.assertEqual(forgot.status_code, 200)
        token = forgot.get_json()["debugToken"]
        self.assertTrue(token)
        reset = self.client.post("/api/auth/reset-password", json={"token": token, "password": "new-password-987"})
        self.assertEqual(reset.status_code, 200, reset.get_json())

    def test_first_visit_tracking_initializes_daily_counters(self):
        response = self.client.post("/api/visits", headers=self.headers, json={
            "visitorId": f"browser-{uuid.uuid4()}", "durationSeconds": 17,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        with app.app_context():
            row = VisitDaily.query.order_by(VisitDaily.id.desc()).first()
            self.assertGreaterEqual(row.total_visits, 1)
            self.assertGreaterEqual(row.total_duration_seconds, 17)

    def test_account_export_and_delete(self):
        exported = self.client.get("/api/account/export")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(len(exported.get_json()["inventory"]), 221)
        wrong = self.client.delete("/api/account", headers=self.headers,
                                   json={"password": "wrong-password", "confirm": "DELETE"})
        self.assertEqual(wrong.status_code, 400)
        deleted = self.client.delete("/api/account", headers=self.headers,
                                     json={"password": "safe-password-123", "confirm": "DELETE"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/inventory").status_code, 401)

    def test_admin_legacy_import_sanitizes_scope(self):
        with app.app_context():
            user = User.query.filter_by(email=self.email).one()
            user.is_admin = True
            db.session.commit()
        migrated = self.client.post("/api/admin/import-legacy", headers=self.headers, json={
            "confirm": "IMPORT",
            "data": {
                "inventory": [{"id": "A1", "quantity": 888}],
                "blueprints": [{"id": str(uuid.uuid4()), "name": "旧版图纸", "tag": "迁移", "items": [{"id": "A1", "quantity": 20}]}],
                "visit_records": [{"ip": "192.0.2.1"}],
            },
        })
        self.assertEqual(migrated.status_code, 200, migrated.get_json())
        self.assertEqual(migrated.get_json()["inventoryCount"], 1)
        self.assertEqual(migrated.get_json()["blueprintCount"], 1)
        quantities = {row["id"]: row["quantity"] for row in self.client.get("/api/inventory").get_json()["items"]}
        self.assertEqual(quantities["A1"], 888)


if __name__ == "__main__":
    unittest.main(verbosity=2)
