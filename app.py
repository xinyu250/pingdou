from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
import math
import os
import re
import secrets
import smtplib
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Flask, Response, has_request_context, jsonify, request, send_file, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from PIL import Image, ImageChops, ImageDraw, ImageOps
from sqlalchemy import UniqueConstraint, func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "instance"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

database_url = os.getenv("DATABASE_URL", "").strip()
if database_url.startswith("postgres://"):
    database_url = "postgresql://" + database_url.removeprefix("postgres://")
if database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
if not database_url:
    database_url = f"sqlite:///{(DATA_DIR / 'pingdou.db').as_posix()}"

app = Flask(__name__, static_folder="static")
app.config.update(
    SECRET_KEY=os.getenv("APP_SECRET") or secrets.token_hex(32),
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={
        "pool_pre_ping": True,
        **({"connect_args": {"check_same_thread": False, "timeout": 30}} if database_url.startswith("sqlite") else {}),
    },
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "8")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("APP_ENV", "development") == "production",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
db = SQLAlchemy(app)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    username = db.Column(db.String(40), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    settings_json = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    @property
    def settings(self) -> dict[str, Any]:
        defaults = {"lowThreshold": 300, "allowNegative": True, "palette": "MARD-221"}
        try:
            defaults.update(json.loads(self.settings_json or "{}"))
        except json.JSONDecodeError:
            pass
        return defaults


class PaletteColor(db.Model):
    code = db.Column(db.String(12), primary_key=True)
    series = db.Column(db.String(4), nullable=False, index=True)
    hex_value = db.Column(db.String(7), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False)


class Inventory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    color_code = db.Column(db.String(12), db.ForeignKey("palette_color.code"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    low_threshold = db.Column(db.Integer, nullable=True)
    __table_args__ = (UniqueConstraint("user_id", "color_code", name="uq_inventory_user_color"),)


class InventoryTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    color_code = db.Column(db.String(12), nullable=False, index=True)
    operation = db.Column(db.String(20), nullable=False)
    delta = db.Column(db.Integer, nullable=False)
    balance_after = db.Column(db.Integer, nullable=False)
    remark = db.Column(db.String(160), nullable=False, default="")
    source = db.Column(db.String(30), nullable=False, default="manual")
    batch_id = db.Column(db.String(36), nullable=False, index=True)
    undone = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class Blueprint(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    tag = db.Column(db.String(40), nullable=False, default="默认")
    folder = db.Column(db.String(40), nullable=False, default="未分类")
    source_url = db.Column(db.String(500), nullable=False, default="")
    status = db.Column(db.String(20), nullable=False, default="待拼")
    image_data = db.Column(db.LargeBinary, nullable=True)
    image_mime = db.Column(db.String(40), nullable=True)
    grid_columns = db.Column(db.Integer, nullable=True)
    grid_rows = db.Column(db.Integer, nullable=True)
    pattern_json = db.Column(db.Text, nullable=False, default="{}")
    progress_json = db.Column(db.Text, nullable=False, default="{}")
    craft_minutes = db.Column(db.Integer, nullable=False, default=0)
    share_token = db.Column(db.String(40), unique=True, nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class BlueprintItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    blueprint_id = db.Column(db.String(36), db.ForeignKey("blueprint.id", ondelete="CASCADE"), nullable=False, index=True)
    color_code = db.Column(db.String(12), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    __table_args__ = (UniqueConstraint("blueprint_id", "color_code", name="uq_blueprint_color"),)


class PasswordReset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    action = db.Column(db.String(60), nullable=False)
    detail_json = db.Column(db.Text, nullable=False, default="{}")
    request_id = db.Column(db.String(36), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class VisitDaily(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, unique=True, nullable=False, index=True)
    total_visits = db.Column(db.Integer, nullable=False, default=0)
    total_duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    visitor_hashes_json = db.Column(db.Text, nullable=False, default="[]")


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SAFE_STATUS = {"待拼", "拼制中", "已拼", "已发布"}
RATE_BUCKETS: dict[str, list[datetime]] = {}


def json_body() -> dict[str, Any]:
    return request.get_json(silent=True) or {}


def audit(action: str, detail: dict[str, Any] | None = None, user_id: int | None = None) -> None:
    request_id = getattr(request, "request_id", str(uuid.uuid4())) if has_request_context() else str(uuid.uuid4())
    db.session.add(AuditLog(
        user_id=user_id if user_id is not None else (session.get("user_id") if has_request_context() else None),
        action=action,
        detail_json=json.dumps(detail or {}, ensure_ascii=False),
        request_id=request_id,
    ))


def current_user() -> User | None:
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return jsonify({"error": "请先登录", "code": "AUTH_REQUIRED"}), 401
        return fn(user, *args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or not user.is_admin:
            return jsonify({"error": "无权访问", "code": "FORBIDDEN"}), 403
        return fn(user, *args, **kwargs)
    return wrapper


def rate_limited(scope: str, limit: int, window_seconds: int) -> bool:
    key = f"{scope}:{request.headers.get('X-Forwarded-For', request.remote_addr or '')}"
    now = utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    bucket = [item for item in RATE_BUCKETS.get(key, []) if item > cutoff]
    if len(bucket) >= limit:
        RATE_BUCKETS[key] = bucket
        return True
    bucket.append(now)
    RATE_BUCKETS[key] = bucket
    return False


@app.before_request
def prepare_request():
    request.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(24)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.path.startswith("/api/"):
        exempt = {"/api/auth/login", "/api/auth/guest", "/api/auth/register", "/api/auth/forgot-password", "/api/auth/reset-password"}
        if request.path not in exempt and not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.get("csrf_token", "")):
            return jsonify({"error": "页面令牌已失效，请刷新后重试", "code": "CSRF_FAILED"}), 403


@app.after_request
def secure_response(response: Response):
    response.headers["X-Request-ID"] = getattr(request, "request_id", "")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; font-src 'self'; base-uri 'self'; frame-ancestors 'none'"
    )
    return response


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "未找到对应内容"}), 404
    return send_from_directory(BASE_DIR, "index.html")


@app.errorhandler(413)
def file_too_large(_error):
    return jsonify({"error": "图片过大，请压缩后重试", "code": "FILE_TOO_LARGE"}), 413


@app.errorhandler(500)
def server_error(error):
    db.session.rollback()
    app.logger.exception("request failed: %s", error)
    return jsonify({"error": "服务暂时不可用", "requestId": getattr(request, "request_id", "")}), 500


def load_palette_seed() -> list[dict[str, Any]]:
    for path in [BASE_DIR / "legacy_seed.json", BASE_DIR / "seed_data.json"]:
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                colors = []
                for index, item in enumerate(raw.get("inventory", [])):
                    code = str(item.get("id", "")).strip().upper()
                    hex_value = str(item.get("hex", "")).strip().lower()
                    if code and re.fullmatch(r"#[0-9a-f]{6}", hex_value):
                        colors.append({"code": code, "series": re.sub(r"\d", "", code), "hex": hex_value, "sort": index})
                if colors:
                    return colors
            except (OSError, json.JSONDecodeError):
                continue
    raise RuntimeError("No valid palette found in legacy_seed.json or seed_data.json")


def seed_inventory(user: User, quantities: dict[str, int] | None = None) -> None:
    quantities = quantities or {}
    existing = {row.color_code for row in Inventory.query.filter_by(user_id=user.id).all()}
    for color in PaletteColor.query.order_by(PaletteColor.sort_order).all():
        if color.code not in existing:
            db.session.add(Inventory(user_id=user.id, color_code=color.code, quantity=int(quantities.get(color.code, 0))))


def decode_data_image(value: str) -> tuple[bytes | None, str | None]:
    if not value.startswith("data:image/") or "," not in value:
        return None, None
    header, payload = value.split(",", 1)
    mime = header.split(";")[0].removeprefix("data:")
    try:
        return base64.b64decode(payload, validate=True), mime
    except (ValueError, binascii.Error):
        return None, None


def compress_image(raw: bytes) -> tuple[bytes, str]:
    with Image.open(io.BytesIO(raw)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")
        image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", image.size, "white")
        canvas.paste(image, mask=image.getchannel("A"))
        output = io.BytesIO()
        canvas.save(output, format="WEBP", quality=86, method=6)
        return output.getvalue(), "image/webp"


def import_legacy(user: User) -> None:
    path = BASE_DIR / "legacy_seed.json"
    if not path.exists():
        seed_inventory(user)
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    quantities = {str(item.get("id", "")).upper(): int(item.get("quantity", 0) or 0) for item in raw.get("inventory", [])}
    seed_inventory(user, quantities)
    for item in raw.get("blueprints", []):
        image_data, image_mime = decode_data_image(str(item.get("image", "")))
        if image_data:
            try:
                image_data, image_mime = compress_image(image_data)
            except Exception:
                image_data, image_mime = None, None
        bp = Blueprint(
            id=str(item.get("id") or uuid.uuid4()), user_id=user.id,
            name=str(item.get("name") or "未命名图纸")[:80], tag=str(item.get("tag") or "默认")[:40],
            source_url=str(item.get("source") or "")[:500], status=str(item.get("status") or "待拼"),
            image_data=image_data, image_mime=image_mime,
        )
        if bp.status not in SAFE_STATUS:
            bp.status = "待拼"
        db.session.add(bp)
        for entry in item.get("items", []):
            code = str(entry.get("id", "")).upper()
            quantity = int(entry.get("quantity", 0) or 0)
            if code and quantity > 0:
                db.session.add(BlueprintItem(blueprint_id=bp.id, color_code=code, quantity=quantity))


def initialize_database() -> None:
    db.create_all()
    if PaletteColor.query.count() == 0:
        for color in load_palette_seed():
            db.session.add(PaletteColor(code=color["code"], series=color["series"], hex_value=color["hex"], sort_order=color["sort"]))
        db.session.commit()
    owner_email = os.getenv("OWNER_EMAIL", "").strip().lower()
    owner_password = os.getenv("OWNER_PASSWORD", "")
    if owner_email and owner_password and not User.query.filter_by(email=owner_email).first():
        owner = User(email=owner_email, username=os.getenv("OWNER_NAME", "豆仓主人")[:40],
                     password_hash=generate_password_hash(owner_password), is_admin=True)
        db.session.add(owner)
        db.session.flush()
        import_legacy(owner)
        audit("owner.bootstrap", {"email": owner_email}, owner.id)
        db.session.commit()


with app.app_context():
    initialize_database()


def serialize_user(user: User) -> dict[str, Any]:
    return {"id": user.id, "email": user.email, "username": user.username, "isAdmin": user.is_admin,
            "isGuest": bool(user.settings.get("demoGuest")), "settings": user.settings}


def serialize_blueprint(bp: Blueprint, include_detail: bool = False) -> dict[str, Any]:
    items = BlueprintItem.query.filter_by(blueprint_id=bp.id).order_by(BlueprintItem.color_code).all()
    payload = {
        "id": bp.id, "name": bp.name, "tag": bp.tag, "folder": bp.folder, "source": bp.source_url,
        "status": bp.status, "imageUrl": f"/api/blueprints/{bp.id}/image" if bp.image_data else "",
        "gridColumns": bp.grid_columns, "gridRows": bp.grid_rows, "craftMinutes": bp.craft_minutes,
        "totalBeads": sum(item.quantity for item in items), "colorCount": len(items),
        "items": [{"id": item.color_code, "quantity": item.quantity} for item in items],
        "shareToken": bp.share_token, "createdAt": bp.created_at.isoformat(), "updatedAt": bp.updated_at.isoformat(),
    }
    if include_detail:
        try:
            payload["pattern"] = json.loads(bp.pattern_json or "{}")
        except json.JSONDecodeError:
            payload["pattern"] = {}
        try:
            payload["progress"] = json.loads(bp.progress_json or "{}")
        except json.JSONDecodeError:
            payload["progress"] = {}
    return payload


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/privacy")
def privacy_page():
    return send_from_directory(BASE_DIR / "static", "privacy.html")


@app.get("/terms")
def terms_page():
    return send_from_directory(BASE_DIR / "static", "terms.html")


@app.get("/favicon.ico")
def favicon():
    return Response(status=204)


@app.get("/api/health")
def health():
    db.session.execute(db.select(func.count(User.id))).scalar()
    durable = bool(os.getenv("DATABASE_URL"))
    return jsonify({"status": "ok", "database": "postgresql" if durable else "sqlite", "durable": durable,
                    "version": os.getenv("APP_VERSION", "2.1.0")})


@app.get("/api/session")
def session_info():
    user = current_user()
    return jsonify({"authenticated": bool(user), "user": serialize_user(user) if user else None,
                    "csrfToken": session["csrf_token"], "maxUploadMb": app.config["MAX_CONTENT_LENGTH"] // 1024 // 1024})


@app.post("/api/auth/register")
def register():
    if rate_limited("register", 5, 3600):
        return jsonify({"error": "注册请求过多，请稍后再试"}), 429
    body = json_body()
    email = str(body.get("email", "")).strip().lower()
    username = str(body.get("username", "")).strip()[:40]
    password = str(body.get("password", ""))
    if not EMAIL_RE.match(email) or not username or len(password) < 10:
        return jsonify({"error": "请填写有效邮箱、用户名和至少10位密码"}), 400
    user = User(email=email, username=username, password_hash=generate_password_hash(password))
    db.session.add(user)
    try:
        db.session.flush()
        seed_inventory(user)
        audit("auth.register", {"email": email}, user.id)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "该邮箱已注册"}), 409
    session.clear()
    session["user_id"] = user.id
    session["csrf_token"] = secrets.token_urlsafe(24)
    session.permanent = True
    return jsonify({"user": serialize_user(user), "csrfToken": session["csrf_token"]}), 201


@app.post("/api/auth/login")
def login():
    if rate_limited("login", 10, 900):
        return jsonify({"error": "登录失败次数过多，请15分钟后再试"}), 429
    body = json_body()
    email = str(body.get("email", "")).strip().lower()
    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, str(body.get("password", ""))):
        return jsonify({"error": "邮箱或密码错误"}), 401
    session.clear()
    session["user_id"] = user.id
    session["csrf_token"] = secrets.token_urlsafe(24)
    session.permanent = True
    audit("auth.login", user_id=user.id)
    db.session.commit()
    return jsonify({"user": serialize_user(user), "csrfToken": session["csrf_token"]})


def remove_user_data(user: User) -> None:
    blueprint_ids = [bp.id for bp in Blueprint.query.filter_by(user_id=user.id).all()]
    if blueprint_ids:
        BlueprintItem.query.filter(BlueprintItem.blueprint_id.in_(blueprint_ids)).delete(synchronize_session=False)
    Blueprint.query.filter_by(user_id=user.id).delete()
    InventoryTransaction.query.filter_by(user_id=user.id).delete()
    Inventory.query.filter_by(user_id=user.id).delete()
    PasswordReset.query.filter_by(user_id=user.id).delete()
    AuditLog.query.filter_by(user_id=user.id).delete()
    db.session.delete(user)


def clone_demo_data(source: User, guest: User) -> None:
    for row in Inventory.query.filter_by(user_id=source.id).all():
        db.session.add(Inventory(user_id=guest.id, color_code=row.color_code, quantity=row.quantity,
                                 low_threshold=row.low_threshold))
    for tx in InventoryTransaction.query.filter_by(user_id=source.id).order_by(InventoryTransaction.id).all():
        db.session.add(InventoryTransaction(
            user_id=guest.id, color_code=tx.color_code, operation=tx.operation, delta=tx.delta,
            balance_after=tx.balance_after, remark=tx.remark, source=tx.source,
            batch_id=str(uuid.uuid4()), undone=tx.undone, created_at=tx.created_at,
        ))
    for source_bp in Blueprint.query.filter_by(user_id=source.id).all():
        guest_bp = Blueprint(
            user_id=guest.id, name=source_bp.name, tag=source_bp.tag, folder=source_bp.folder,
            source_url=source_bp.source_url, status=source_bp.status, image_data=source_bp.image_data,
            image_mime=source_bp.image_mime, grid_columns=source_bp.grid_columns, grid_rows=source_bp.grid_rows,
            pattern_json=source_bp.pattern_json, progress_json=source_bp.progress_json,
            craft_minutes=source_bp.craft_minutes, created_at=source_bp.created_at,
        )
        db.session.add(guest_bp)
        db.session.flush()
        for item in BlueprintItem.query.filter_by(blueprint_id=source_bp.id).all():
            db.session.add(BlueprintItem(blueprint_id=guest_bp.id, color_code=item.color_code, quantity=item.quantity))


@app.post("/api/auth/guest")
def guest_login():
    if rate_limited("guest", 12, 3600):
        return jsonify({"error": "游客空间创建过于频繁，请稍后再试"}), 429
    cutoff = utcnow() - timedelta(hours=24)
    expired = User.query.filter(User.email.like("guest+%@demo.local"), User.created_at < cutoff).limit(30).all()
    for old_guest in expired:
        remove_user_data(old_guest)
    owner_email = os.getenv("OWNER_EMAIL", "").strip().lower()
    source = User.query.filter_by(email=owner_email).first() if owner_email else None
    source = source or User.query.filter_by(is_admin=True).order_by(User.id).first()
    if not source:
        return jsonify({"error": "游客演示数据暂未准备好"}), 503
    guest_id = uuid.uuid4().hex
    settings = dict(source.settings)
    settings.update({"demoGuest": True, "expiresAt": (utcnow() + timedelta(hours=24)).isoformat()})
    guest = User(email=f"guest+{guest_id}@demo.local", username="游客体验账号",
                 password_hash=generate_password_hash(secrets.token_urlsafe(32)), settings_json=json.dumps(settings))
    db.session.add(guest)
    db.session.flush()
    clone_demo_data(source, guest)
    audit("auth.guest", {"templateUserId": source.id}, guest.id)
    db.session.commit()
    session.clear()
    session["user_id"] = guest.id
    session["csrf_token"] = secrets.token_urlsafe(24)
    return jsonify({"user": serialize_user(guest), "csrfToken": session["csrf_token"]}), 201


@app.post("/api/auth/logout")
@login_required
def logout(user: User):
    is_guest = bool(user.settings.get("demoGuest"))
    if is_guest:
        remove_user_data(user)
        db.session.commit()
        session.clear()
        return jsonify({"status": "ok"})
    audit("auth.logout", user_id=user.id)
    db.session.commit()
    session.clear()
    return jsonify({"status": "ok"})


def send_reset_email(email: str, link: str) -> bool:
    host = os.getenv("SMTP_HOST", "")
    if not host:
        return False
    message = EmailMessage()
    message["Subject"] = "重置你的豆仓密码"
    message["From"] = os.getenv("SMTP_FROM", "noreply@example.com")
    message["To"] = email
    message.set_content(f"请在30分钟内打开以下链接重置密码：\n\n{link}\n")
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=15) as client:
        client.starttls()
        if os.getenv("SMTP_USER"):
            client.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
        client.send_message(message)
    return True


@app.post("/api/auth/forgot-password")
def forgot_password():
    if rate_limited("forgot", 5, 3600):
        return jsonify({"status": "ok"})
    email = str(json_body().get("email", "")).strip().lower()
    user = User.query.filter_by(email=email).first()
    debug_token = None
    if user:
        token = secrets.token_urlsafe(32)
        db.session.add(PasswordReset(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                     expires_at=utcnow() + timedelta(minutes=30)))
        db.session.commit()
        link = f"{os.getenv('APP_URL', request.host_url.rstrip('/'))}/?reset={token}"
        try:
            delivered = send_reset_email(email, link)
        except Exception:
            app.logger.exception("password reset email failed")
            delivered = False
        if not delivered and os.getenv("APP_ENV", "development") != "production":
            debug_token = token
    return jsonify({"status": "ok", "message": "如果账号存在，重置邮件会发送到该邮箱", "debugToken": debug_token})


@app.post("/api/auth/reset-password")
def reset_password():
    body = json_body()
    token = str(body.get("token", ""))
    password = str(body.get("password", ""))
    if len(password) < 10:
        return jsonify({"error": "密码至少需要10位"}), 400
    row = PasswordReset.query.filter_by(token_hash=hashlib.sha256(token.encode()).hexdigest(), used_at=None).first()
    if not row or row.expires_at.replace(tzinfo=timezone.utc) < utcnow():
        return jsonify({"error": "重置链接无效或已过期"}), 400
    user = db.session.get(User, row.user_id)
    user.password_hash = generate_password_hash(password)
    row.used_at = utcnow()
    audit("auth.password_reset", user_id=user.id)
    db.session.commit()
    return jsonify({"status": "ok"})


@app.get("/api/dashboard")
@login_required
def dashboard(user: User):
    inventory = Inventory.query.filter_by(user_id=user.id).all()
    total = sum(row.quantity for row in inventory)
    low = sum(1 for row in inventory if row.quantity < (row.low_threshold if row.low_threshold is not None else user.settings["lowThreshold"]))
    bps = Blueprint.query.filter_by(user_id=user.id).all()
    recent = InventoryTransaction.query.filter_by(user_id=user.id).order_by(InventoryTransaction.created_at.desc()).limit(8).all()
    return jsonify({
        "totalBeads": total, "trackedColors": sum(1 for row in inventory if row.quantity != 0), "lowColors": low,
        "blueprintCount": len(bps), "todoBlueprints": sum(1 for bp in bps if bp.status in {"待拼", "拼制中"}),
        "recentTransactions": [{"id": row.id, "code": row.color_code, "delta": row.delta, "operation": row.operation,
                                "remark": row.remark, "createdAt": row.created_at.isoformat()} for row in recent],
        "recentBlueprints": [serialize_blueprint(bp) for bp in sorted(bps, key=lambda x: x.updated_at, reverse=True)[:4]],
    })


@app.get("/api/inventory")
@login_required
def inventory_list(user: User):
    rows = (db.session.query(Inventory, PaletteColor).join(PaletteColor, Inventory.color_code == PaletteColor.code)
            .filter(Inventory.user_id == user.id).order_by(PaletteColor.sort_order).all())
    default_threshold = int(user.settings["lowThreshold"])
    return jsonify({"items": [{"id": color.code, "series": color.series, "hex": color.hex_value,
                                "quantity": inv.quantity, "threshold": inv.low_threshold if inv.low_threshold is not None else default_threshold,
                                "status": "欠库存" if inv.quantity < 0 else ("库存不足" if inv.quantity < (inv.low_threshold if inv.low_threshold is not None else default_threshold) else "库存充足")}
                               for inv, color in rows]})


def normalize_transaction_items(body: dict[str, Any], user: User):
    operation = str(body.get("operation", ""))
    if operation not in {"checkin", "checkout", "set"}:
        return None, None, "无效操作"
    raw_items = body.get("items") or []
    if not isinstance(raw_items, list) or not 0 < len(raw_items) <= 500:
        return None, None, "请填写1至500个色号"
    normalized, seen = [], set()
    for entry in raw_items:
        code = str(entry.get("id", "")).strip().upper()
        if code in seen:
            return None, None, f"色号 {code} 重复"
        seen.add(code)
        try:
            quantity = int(entry.get("quantity", 0))
        except (ValueError, TypeError):
            return None, None, f"{code} 数量无效"
        if quantity < 0 or (operation != "set" and quantity == 0):
            return None, None, f"{code} 数量必须为正数"
        inventory = Inventory.query.filter_by(user_id=user.id, color_code=code).with_for_update().first()
        if not inventory:
            return None, None, f"不存在色号 {code}"
        delta = quantity - inventory.quantity if operation == "set" else (quantity if operation == "checkin" else -quantity)
        normalized.append((inventory, delta))
    return operation, normalized, ""


@app.post("/api/inventory/transactions")
@login_required
def inventory_transaction(user: User):
    body = json_body()
    operation, items, error = normalize_transaction_items(body, user)
    if error:
        return jsonify({"error": error}), 400
    batch_id, remark, source = str(uuid.uuid4()), str(body.get("remark", ""))[:160], str(body.get("source", "manual"))[:30]
    for inventory, delta in items:
        inventory.quantity += delta
        db.session.add(InventoryTransaction(user_id=user.id, color_code=inventory.color_code, operation=operation,
                                             delta=delta, balance_after=inventory.quantity, remark=remark, source=source, batch_id=batch_id))
    audit("inventory.transaction", {"batchId": batch_id, "operation": operation, "count": len(items)})
    db.session.commit()
    return jsonify({"status": "ok", "batchId": batch_id})


@app.get("/api/inventory/history")
@login_required
def inventory_history(user: User):
    limit = min(max(int(request.args.get("limit", 100)), 1), 500)
    operation = request.args.get("operation")
    query = InventoryTransaction.query.filter_by(user_id=user.id)
    if operation in {"checkin", "checkout", "set", "undo", "initialize", "clear"}:
        query = query.filter_by(operation=operation)
    rows = query.order_by(InventoryTransaction.created_at.desc()).limit(limit).all()
    return jsonify({"items": [{"id": row.id, "code": row.color_code, "operation": row.operation, "delta": row.delta,
                                "balanceAfter": row.balance_after, "remark": row.remark, "source": row.source,
                                "batchId": row.batch_id, "undone": row.undone, "createdAt": row.created_at.isoformat()} for row in rows]})


@app.post("/api/inventory/undo/<batch_id>")
@login_required
def undo_transaction(user: User, batch_id: str):
    rows = InventoryTransaction.query.filter_by(user_id=user.id, batch_id=batch_id, undone=False).all()
    if not rows:
        return jsonify({"error": "该批次不存在或已撤销"}), 404
    undo_batch = str(uuid.uuid4())
    for row in rows:
        inventory = Inventory.query.filter_by(user_id=user.id, color_code=row.color_code).with_for_update().one()
        inverse = -row.delta
        inventory.quantity += inverse
        row.undone = True
        db.session.add(InventoryTransaction(user_id=user.id, color_code=row.color_code, operation="undo", delta=inverse,
                                             balance_after=inventory.quantity, remark=f"撤销 {batch_id[:8]}", source="undo", batch_id=undo_batch))
    audit("inventory.undo", {"batchId": batch_id, "undoBatch": undo_batch})
    db.session.commit()
    return jsonify({"status": "ok", "batchId": undo_batch})


@app.post("/api/inventory/initialize")
@login_required
def initialize_inventory(user: User):
    body = json_body()
    if body.get("confirm") != "INIT":
        return jsonify({"error": "确认文本不正确"}), 400
    quantity = min(max(int(body.get("quantity", 1000)), 0), 1_000_000)
    batch_id = str(uuid.uuid4())
    for inventory in Inventory.query.filter_by(user_id=user.id).all():
        delta = quantity - inventory.quantity
        if delta:
            inventory.quantity = quantity
            db.session.add(InventoryTransaction(user_id=user.id, color_code=inventory.color_code, operation="initialize",
                                                 delta=delta, balance_after=quantity, remark="快速初始化库存", source="settings", batch_id=batch_id))
    audit("inventory.initialize", {"quantity": quantity, "batchId": batch_id})
    db.session.commit()
    return jsonify({"status": "ok", "batchId": batch_id})


@app.post("/api/inventory/clear")
@login_required
def clear_inventory(user: User):
    if json_body().get("confirm") != "CLEAR":
        return jsonify({"error": "请输入 CLEAR 确认"}), 400
    batch_id = str(uuid.uuid4())
    for inventory in Inventory.query.filter_by(user_id=user.id).all():
        if inventory.quantity:
            delta = -inventory.quantity
            inventory.quantity = 0
            db.session.add(InventoryTransaction(user_id=user.id, color_code=inventory.color_code, operation="clear",
                                                 delta=delta, balance_after=0, remark="清空库存", source="settings", batch_id=batch_id))
    audit("inventory.clear", {"batchId": batch_id})
    db.session.commit()
    return jsonify({"status": "ok"})


@app.get("/api/inventory/restock")
@login_required
def restock_list(user: User):
    target = min(max(int(request.args.get("target", 1000)), 0), 1_000_000)
    colors = {row.code: row for row in PaletteColor.query.all()}
    items = []
    for row in Inventory.query.filter_by(user_id=user.id).all():
        threshold = row.low_threshold if row.low_threshold is not None else int(user.settings["lowThreshold"])
        if row.quantity < threshold:
            needed = max(0, target - row.quantity)
            items.append({"id": row.color_code, "hex": colors[row.color_code].hex_value, "current": row.quantity,
                          "target": target, "needed": needed, "grams": math.ceil(needed / 100)})
    items.sort(key=lambda item: item["needed"], reverse=True)
    return jsonify({"items": items, "command": " ".join(f"{item['id']}:{item['grams']}g" for item in items),
                    "totalColors": len(items), "totalGrams": sum(item["grams"] for item in items)})


@app.get("/api/inventory/export.csv")
@login_required
def export_inventory(user: User):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["色号", "库存数量", "预警阈值"])
    for row in Inventory.query.filter_by(user_id=user.id).order_by(Inventory.color_code).all():
        writer.writerow([row.color_code, row.quantity, row.low_threshold if row.low_threshold is not None else user.settings["lowThreshold"]])
    return Response("\ufeff" + output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=pingdou-inventory.csv"})


def parse_blueprint_payload():
    payload = request.form if request.form else json_body()
    items_value, pattern_value = payload.get("items", "[]"), payload.get("pattern", "{}")
    items = json.loads(items_value) if isinstance(items_value, str) else items_value
    pattern = json.loads(pattern_value) if isinstance(pattern_value, str) else pattern_value
    image_data = image_mime = None
    upload = request.files.get("image")
    if upload and upload.filename:
        if upload.mimetype not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            raise ValueError("仅支持 JPG、PNG、WebP 或 GIF 图片")
        image_data, image_mime = compress_image(upload.read())
    return payload, items, pattern, image_data, image_mime


def replace_blueprint_items(bp: Blueprint, items: list[dict[str, Any]]) -> None:
    BlueprintItem.query.filter_by(blueprint_id=bp.id).delete()
    seen = set()
    for entry in items:
        code, quantity = str(entry.get("id", "")).strip().upper(), int(entry.get("quantity", 0) or 0)
        if code in seen or quantity <= 0 or not db.session.get(PaletteColor, code):
            continue
        seen.add(code)
        db.session.add(BlueprintItem(blueprint_id=bp.id, color_code=code, quantity=quantity))


@app.get("/api/blueprints")
@login_required
def blueprint_list(user: User):
    query = Blueprint.query.filter_by(user_id=user.id)
    if request.args.get("status") in SAFE_STATUS:
        query = query.filter_by(status=request.args["status"])
    return jsonify({"items": [serialize_blueprint(bp) for bp in query.order_by(Blueprint.updated_at.desc()).all()]})


@app.post("/api/blueprints")
@login_required
def create_blueprint(user: User):
    try:
        payload, items, pattern, image_data, image_mime = parse_blueprint_payload()
    except (ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": str(error)}), 400
    name = str(payload.get("name", "")).strip()[:80]
    if not name:
        return jsonify({"error": "请填写图纸名称"}), 400
    status = str(payload.get("status", "待拼"))
    bp = Blueprint(user_id=user.id, name=name, tag=str(payload.get("tag", "默认"))[:40],
                   folder=str(payload.get("folder", "未分类"))[:40], source_url=str(payload.get("source", ""))[:500],
                   status=status if status in SAFE_STATUS else "待拼", image_data=image_data, image_mime=image_mime,
                   grid_columns=int(pattern.get("columns")) if pattern.get("columns") else None,
                   grid_rows=int(pattern.get("rows")) if pattern.get("rows") else None,
                   pattern_json=json.dumps(pattern, ensure_ascii=False), craft_minutes=int(payload.get("craftMinutes", 0) or 0))
    db.session.add(bp)
    db.session.flush()
    replace_blueprint_items(bp, items)
    audit("blueprint.create", {"id": bp.id, "name": bp.name})
    db.session.commit()
    return jsonify({"item": serialize_blueprint(bp, True)}), 201


@app.get("/api/blueprints/<blueprint_id>")
@login_required
def blueprint_detail(user: User, blueprint_id: str):
    bp = Blueprint.query.filter_by(id=blueprint_id, user_id=user.id).first_or_404()
    return jsonify({"item": serialize_blueprint(bp, True)})


@app.put("/api/blueprints/<blueprint_id>")
@login_required
def update_blueprint(user: User, blueprint_id: str):
    bp = Blueprint.query.filter_by(id=blueprint_id, user_id=user.id).first_or_404()
    try:
        payload, items, pattern, image_data, image_mime = parse_blueprint_payload()
    except (ValueError, json.JSONDecodeError) as error:
        return jsonify({"error": str(error)}), 400
    bp.name = str(payload.get("name", bp.name)).strip()[:80] or bp.name
    bp.tag, bp.folder = str(payload.get("tag", bp.tag))[:40], str(payload.get("folder", bp.folder))[:40]
    bp.source_url = str(payload.get("source", bp.source_url))[:500]
    status = str(payload.get("status", bp.status))
    bp.status = status if status in SAFE_STATUS else bp.status
    bp.craft_minutes = max(0, int(payload.get("craftMinutes", bp.craft_minutes) or 0))
    if image_data:
        bp.image_data, bp.image_mime = image_data, image_mime
    if pattern:
        bp.pattern_json = json.dumps(pattern, ensure_ascii=False)
        bp.grid_columns = int(pattern.get("columns")) if pattern.get("columns") else bp.grid_columns
        bp.grid_rows = int(pattern.get("rows")) if pattern.get("rows") else bp.grid_rows
    replace_blueprint_items(bp, items)
    audit("blueprint.update", {"id": bp.id})
    db.session.commit()
    return jsonify({"item": serialize_blueprint(bp, True)})


@app.delete("/api/blueprints/<blueprint_id>")
@login_required
def delete_blueprint(user: User, blueprint_id: str):
    bp = Blueprint.query.filter_by(id=blueprint_id, user_id=user.id).first_or_404()
    BlueprintItem.query.filter_by(blueprint_id=bp.id).delete()
    db.session.delete(bp)
    audit("blueprint.delete", {"id": blueprint_id})
    db.session.commit()
    return jsonify({"status": "ok"})


@app.get("/api/blueprints/<blueprint_id>/image")
def blueprint_image(blueprint_id: str):
    bp = db.session.get(Blueprint, blueprint_id)
    if not bp or not bp.image_data:
        return Response(status=404)
    user = current_user()
    if not bp.share_token and (not user or user.id != bp.user_id):
        return Response(status=403)
    return send_file(io.BytesIO(bp.image_data), mimetype=bp.image_mime or "image/webp", max_age=3600)


@app.post("/api/blueprints/calculate")
@login_required
def calculate_blueprints(user: User):
    totals: Counter[str] = Counter()
    for selection in json_body().get("selections", [])[:100]:
        bp = Blueprint.query.filter_by(id=str(selection.get("id")), user_id=user.id).first()
        if bp:
            multiplier = min(max(int(selection.get("count", 1)), 1), 99)
            for item in BlueprintItem.query.filter_by(blueprint_id=bp.id).all():
                totals[item.color_code] += item.quantity * multiplier
    inventory = {row.color_code: row.quantity for row in Inventory.query.filter_by(user_id=user.id).all()}
    colors = {row.code: row.hex_value for row in PaletteColor.query.all()}
    items = [{"id": code, "hex": colors.get(code), "needed": needed, "current": inventory.get(code, 0),
              "remain": inventory.get(code, 0) - needed, "shortage": max(0, needed - inventory.get(code, 0))}
             for code, needed in totals.items()]
    items.sort(key=lambda item: (item["shortage"], item["needed"]), reverse=True)
    return jsonify({"items": items, "totalNeeded": sum(totals.values()), "shortageColors": sum(item["shortage"] > 0 for item in items)})


@app.post("/api/blueprints/<blueprint_id>/consume")
@login_required
def consume_blueprint(user: User, blueprint_id: str):
    bp = Blueprint.query.filter_by(id=blueprint_id, user_id=user.id).first_or_404()
    count = min(max(int(json_body().get("count", 1)), 1), 99)
    raw_items = [{"id": item.color_code, "quantity": item.quantity * count} for item in BlueprintItem.query.filter_by(blueprint_id=bp.id).all()]
    operation, items, error = normalize_transaction_items({"operation": "checkout", "items": raw_items}, user)
    if error:
        return jsonify({"error": error}), 400
    batch_id = str(uuid.uuid4())
    for inventory, delta in items:
        inventory.quantity += delta
        db.session.add(InventoryTransaction(user_id=user.id, color_code=inventory.color_code, operation=operation,
                                             delta=delta, balance_after=inventory.quantity, remark=f"制作图纸：{bp.name}",
                                             source="blueprint", batch_id=batch_id))
    bp.status = "拼制中" if bp.status == "待拼" else bp.status
    audit("blueprint.consume", {"id": bp.id, "count": count, "batchId": batch_id})
    db.session.commit()
    return jsonify({"status": "ok", "batchId": batch_id})


@app.put("/api/blueprints/<blueprint_id>/progress")
@login_required
def update_progress(user: User, blueprint_id: str):
    bp = Blueprint.query.filter_by(id=blueprint_id, user_id=user.id).first_or_404()
    progress = json_body().get("progress", {})
    if not isinstance(progress, dict) or len(json.dumps(progress)) > 400_000:
        return jsonify({"error": "进度数据无效"}), 400
    bp.progress_json = json.dumps(progress, ensure_ascii=False)
    bp.status = "拼制中" if progress and bp.status == "待拼" else bp.status
    db.session.commit()
    return jsonify({"status": "ok"})


@app.post("/api/blueprints/<blueprint_id>/share")
@login_required
def share_blueprint(user: User, blueprint_id: str):
    bp = Blueprint.query.filter_by(id=blueprint_id, user_id=user.id).first_or_404()
    enabled = bool(json_body().get("enabled", True))
    bp.share_token = secrets.token_urlsafe(18) if enabled and not bp.share_token else (bp.share_token if enabled else None)
    db.session.commit()
    return jsonify({"shareToken": bp.share_token, "shareUrl": f"{request.host_url}?share={bp.share_token}" if bp.share_token else ""})


@app.get("/api/share/<token>")
def public_share(token: str):
    bp = Blueprint.query.filter_by(share_token=token).first_or_404()
    return jsonify({"item": serialize_blueprint(bp, True), "owner": db.session.get(User, bp.user_id).username})


def srgb_to_linear(value: float) -> float:
    value /= 255.0
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    r, g, b = (srgb_to_linear(channel) for channel in rgb)
    x = (r * 0.4124 + g * 0.3576 + b * 0.1805) / 0.95047
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = (r * 0.0193 + g * 0.1192 + b * 0.9505) / 1.08883

    def pivot(value: float) -> float:
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    fx, fy, fz = pivot(x), pivot(y), pivot(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def nearest_color(rgb, palette, cache):
    key = tuple(channel // 8 for channel in rgb)
    if key in cache:
        return cache[key]
    lab = rgb_to_lab(rgb)
    code, hex_value, _ = min(palette, key=lambda item: sum((lab[i] - item[2][i]) ** 2 for i in range(3)))
    result = code, hex_value, hex_to_rgb(hex_value)
    cache[key] = result
    return result


def quantize_pattern(image: Image.Image, columns: int, rows: int, dither: bool, max_colors: int | None):
    image = ImageOps.exif_transpose(image).convert("RGBA").resize((columns, rows), Image.Resampling.LANCZOS)
    palette_rows = PaletteColor.query.order_by(PaletteColor.sort_order).all()
    palette = [(row.code, row.hex_value, rgb_to_lab(hex_to_rgb(row.hex_value))) for row in palette_rows]
    cache, cells, hex_cells, counts = {}, [], [], Counter()
    pixels = [[list(image.getpixel((x, y))[:3]) + [image.getpixel((x, y))[3]] for x in range(columns)] for y in range(rows)]
    for y in range(rows):
        for x in range(columns):
            old = pixels[y][x]
            if old[3] < 48:
                cells.append(None)
                hex_cells.append(None)
                continue
            rgb = tuple(max(0, min(255, round(channel))) for channel in old[:3])
            code, hex_value, mapped_rgb = nearest_color(rgb, palette, cache)
            cells.append(code)
            hex_cells.append(hex_value)
            counts[code] += 1
            if dither:
                error = [rgb[i] - mapped_rgb[i] for i in range(3)]
                for dx, dy, weight in ((1, 0, 7 / 16), (-1, 1, 3 / 16), (0, 1, 5 / 16), (1, 1, 1 / 16)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < columns and 0 <= ny < rows:
                        for channel in range(3):
                            pixels[ny][nx][channel] += error[channel] * weight
    if max_colors and 1 < max_colors < len(counts):
        restricted = [entry for entry in palette if entry[0] in {code for code, _ in counts.most_common(max_colors)}]
        remapped_cells, remapped_hex, remapped_counts = [], [], Counter()
        for index, code in enumerate(cells):
            if code is None:
                remapped_cells.append(None)
                remapped_hex.append(None)
                continue
            new_code, new_hex, _ = nearest_color(hex_to_rgb(hex_cells[index]), restricted, {})
            remapped_cells.append(new_code)
            remapped_hex.append(new_hex)
            remapped_counts[new_code] += 1
        cells, hex_cells, counts = remapped_cells, remapped_hex, remapped_counts
    hex_map = {row.code: row.hex_value for row in palette_rows}
    return {"columns": columns, "rows": rows, "cells": cells, "hex": hex_cells,
            "items": [{"id": code, "hex": hex_map[code], "quantity": count} for code, count in counts.most_common()]}


def isolate_subject(image: Image.Image, margin_percent: int = 8) -> tuple[Image.Image, dict[str, Any]]:
    """Remove only edge-connected background, preserving enclosed light areas in the subject."""
    source = ImageOps.exif_transpose(image).convert("RGBA")
    original_width, original_height = source.size
    scale = min(1.0, 320 / max(original_width, original_height))
    small = source.resize((max(1, round(original_width * scale)), max(1, round(original_height * scale))),
                          Image.Resampling.BILINEAR)
    width, height = small.size
    rgba = list(small.get_flattened_data())
    border_indices = set(range(width)) | set(range((height - 1) * width, height * width))
    border_indices |= {y * width for y in range(height)} | {y * width + width - 1 for y in range(height)}
    opaque_border = [rgba[i][:3] for i in border_indices if rgba[i][3] > 32]
    if not opaque_border:
        return source, {"mode": "subject", "applied": False, "reason": "transparent-image"}
    channels = [sorted(pixel[channel] for pixel in opaque_border) for channel in range(3)]
    background = tuple(channel[len(channel) // 2] for channel in channels)
    border_distances = sorted(math.sqrt(sum((pixel[i] - background[i]) ** 2 for i in range(3))) for pixel in opaque_border)
    threshold = min(72.0, max(24.0, border_distances[round((len(border_distances) - 1) * .85)] + 14.0))
    background_like = [pixel[3] <= 32 or math.sqrt(sum((pixel[i] - background[i]) ** 2 for i in range(3))) <= threshold
                       for pixel in rgba]
    connected = bytearray(width * height)
    queue = [i for i in border_indices if background_like[i]]
    for i in queue:
        connected[i] = 1
    cursor = 0
    while cursor < len(queue):
        index = queue[cursor]
        cursor += 1
        x, y = index % width, index // width
        for neighbor in ((index - 1 if x else -1), (index + 1 if x + 1 < width else -1),
                         (index - width if y else -1), (index + width if y + 1 < height else -1)):
            if neighbor >= 0 and not connected[neighbor] and background_like[neighbor]:
                connected[neighbor] = 1
                queue.append(neighbor)
    subject = bytearray(255 if rgba[i][3] > 32 and not connected[i] else 0 for i in range(width * height))
    visited = bytearray(width * height)
    components: list[tuple[list[int], tuple[int, int, int, int], bool]] = []
    for start, value in enumerate(subject):
        if not value or visited[start]:
            continue
        pixels, component_queue = [], [start]
        visited[start] = 1
        min_x = max_x = start % width
        min_y = max_y = start // width
        touches_edge = False
        component_cursor = 0
        while component_cursor < len(component_queue):
            index = component_queue[component_cursor]
            component_cursor += 1
            pixels.append(index)
            x, y = index % width, index // width
            min_x, max_x, min_y, max_y = min(min_x, x), max(max_x, x), min(min_y, y), max(max_y, y)
            touches_edge = touches_edge or x == 0 or y == 0 or x == width - 1 or y == height - 1
            for neighbor in ((index - 1 if x else -1), (index + 1 if x + 1 < width else -1),
                             (index - width if y else -1), (index + width if y + 1 < height else -1)):
                if neighbor >= 0 and subject[neighbor] and not visited[neighbor]:
                    visited[neighbor] = 1
                    component_queue.append(neighbor)
        components.append((pixels, (min_x, min_y, max_x + 1, max_y + 1), touches_edge))
    if components:
        def component_score(component):
            pixels, box, touches_edge = component
            center_x, center_y = (box[0] + box[2]) / 2 / width, (box[1] + box[3]) / 2 / height
            centrality = max(.15, 1 - math.hypot(center_x - .5, center_y - .5))
            return len(pixels) * centrality * (.005 if touches_edge else 1)

        primary = max(components, key=component_score)
        primary_pixels, primary_box, _ = primary
        pad_x, pad_y = (primary_box[2] - primary_box[0]) * .06, (primary_box[3] - primary_box[1]) * .06
        keep_box = (primary_box[0] - pad_x, primary_box[1] - pad_y,
                    primary_box[2] + pad_x, primary_box[3] + pad_y)
        kept = bytearray(width * height)
        minimum_piece = max(3, round(len(primary_pixels) * .004))
        for pixels, box, touches_edge in components:
            center_x, center_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if (pixels is primary_pixels or (not touches_edge and len(pixels) >= minimum_piece and
                                             keep_box[0] <= center_x <= keep_box[2] and keep_box[1] <= center_y <= keep_box[3])):
                for index in pixels:
                    kept[index] = 255
        subject = kept
    mask_small = Image.frombytes("L", (width, height), bytes(subject))
    bbox_small = mask_small.getbbox()
    if not bbox_small:
        return source, {"mode": "subject", "applied": False, "reason": "subject-not-found"}
    foreground_ratio = sum(1 for value in subject if value) / max(1, width * height)
    if foreground_ratio < .004 or foreground_ratio > .94:
        return source, {"mode": "subject", "applied": False, "reason": "low-confidence"}
    left, top, right, bottom = bbox_small
    margin = max(1, round(max(right - left, bottom - top) * margin_percent / 100))
    left, top, right, bottom = max(0, left - margin), max(0, top - margin), min(width, right + margin), min(height, bottom + margin)
    original_box = (max(0, round(left / scale)), max(0, round(top / scale)),
                    min(original_width, round(right / scale)), min(original_height, round(bottom / scale)))
    full_mask = mask_small.resize(source.size, Image.Resampling.NEAREST)
    isolated = source.copy()
    isolated.putalpha(ImageChops.multiply(source.getchannel("A"), full_mask))
    cropped = isolated.crop(original_box)
    return cropped, {"mode": "subject", "applied": True, "box": list(original_box),
                     "originalWidth": original_width, "originalHeight": original_height,
                     "marginPercent": margin_percent}


@app.post("/api/analyze")
@login_required
def analyze(user: User):
    if rate_limited(f"analyze:{user.id}", 30, 3600):
        return jsonify({"error": "本小时识图次数已达上限"}), 429
    upload = request.files.get("image")
    if not upload or not upload.filename:
        return jsonify({"error": "请选择图片"}), 400
    try:
        columns = min(max(int(request.form.get("columns", 48)), 8), 128)
        requested_rows = int(request.form.get("rows", 0))
        max_colors = int(request.form.get("maxColors", 0)) or None
        max_colors = min(max(max_colors, 2), 221) if max_colors else None
        with Image.open(upload.stream) as image:
            if image.width * image.height > 30_000_000:
                return jsonify({"error": "图片像素过大"}), 400
            crop_mode = request.form.get("cropMode", "subject")
            margin_percent = min(max(int(request.form.get("cropMargin", 8)), 0), 25)
            prepared, crop = isolate_subject(image, margin_percent) if crop_mode == "subject" else (
                ImageOps.exif_transpose(image).convert("RGBA"), {"mode": "full", "applied": False})
            rows = min(max(requested_rows or round(columns * prepared.height / prepared.width), 8), 128)
            result = quantize_pattern(prepared, columns, rows, request.form.get("dither") == "true", max_colors)
            result["crop"] = crop
    except (ValueError, OSError) as error:
        return jsonify({"error": f"无法识别图片：{error}"}), 400
    audit("ai.quantize", {"columns": columns, "rows": rows, "colors": len(result["items"]), "crop": result["crop"]})
    db.session.commit()
    return jsonify({"status": "ok", "result": result, "message": "已完成色卡匹配，请确认后再保存或扣库存"})


@app.post("/api/pattern/export.png")
@login_required
def export_pattern(_user: User):
    body = json_body()
    cells = body.get("cells", [])
    columns = min(max(int(body.get("columns", 0)), 1), 128)
    rows = min(max(int(body.get("rows", 0)), 1), 128)
    if len(cells) != columns * rows:
        return jsonify({"error": "图纸网格数据无效"}), 400
    colors = {row.code: row.hex_value for row in PaletteColor.query.all()}
    cell_size = 24 if columns <= 64 else 14
    canvas = Image.new("RGB", (columns * cell_size + 1, rows * cell_size + 1), "white")
    draw = ImageDraw.Draw(canvas)
    for index, code in enumerate(cells):
        x, y = index % columns, index // columns
        box = (x * cell_size, y * cell_size, (x + 1) * cell_size, (y + 1) * cell_size)
        draw.rectangle(box, fill=colors.get(code, "#ffffff"), outline="#d1d5db")
        if code and cell_size >= 20:
            draw.text((box[0] + 2, box[1] + 6), str(code), fill="#111827")
    output = io.BytesIO()
    canvas.save(output, "PNG", optimize=True)
    output.seek(0)
    return send_file(output, mimetype="image/png", as_attachment=True, download_name="pingdou-pattern.png")


@app.get("/api/stats")
@login_required
def stats(user: User):
    rows = InventoryTransaction.query.filter_by(user_id=user.id).order_by(InventoryTransaction.created_at).all()
    total_in, total_out, daily, consumption = 0, 0, {}, Counter()
    for row in rows:
        key = row.created_at.date().isoformat()
        daily.setdefault(key, {"in": 0, "out": 0})
        if row.delta > 0:
            total_in += row.delta
            daily[key]["in"] += row.delta
        else:
            total_out += -row.delta
            daily[key]["out"] += -row.delta
            consumption[row.color_code] += -row.delta
    colors = {row.code: row.hex_value for row in PaletteColor.query.all()}
    return jsonify({"totalIn": total_in, "totalOut": total_out,
                    "current": sum(row.quantity for row in Inventory.query.filter_by(user_id=user.id).all()),
                    "inCount": sum(row.delta > 0 for row in rows), "outCount": sum(row.delta < 0 for row in rows),
                    "blueprintCount": Blueprint.query.filter_by(user_id=user.id).count(),
                    "daily": [{"date": key, **value} for key, value in sorted(daily.items())[-30:]],
                    "topConsumption": [{"id": code, "hex": colors.get(code), "quantity": quantity}
                                       for code, quantity in consumption.most_common(20)]})


@app.get("/api/settings")
@login_required
def settings(user: User):
    return jsonify({"user": serialize_user(user), "production": {
        "durableDatabase": bool(os.getenv("DATABASE_URL")), "emailConfigured": bool(os.getenv("SMTP_HOST")),
        "appUrl": os.getenv("APP_URL", request.host_url.rstrip("/"))}})


@app.put("/api/settings")
@login_required
def update_settings(user: User):
    body = json_body()
    username = str(body.get("username", user.username)).strip()[:40]
    threshold = min(max(int(body.get("lowThreshold", user.settings["lowThreshold"])), 0), 1_000_000)
    if not username:
        return jsonify({"error": "用户名不能为空"}), 400
    user.username = username
    settings_value = user.settings
    settings_value["lowThreshold"] = threshold
    user.settings_json = json.dumps(settings_value, ensure_ascii=False)
    audit("settings.update", {"lowThreshold": threshold})
    db.session.commit()
    return jsonify({"user": serialize_user(user)})


@app.get("/api/account/export")
@login_required
def export_account(user: User):
    inventory = Inventory.query.filter_by(user_id=user.id).order_by(Inventory.color_code).all()
    transactions = InventoryTransaction.query.filter_by(user_id=user.id).order_by(InventoryTransaction.created_at).all()
    blueprints = Blueprint.query.filter_by(user_id=user.id).order_by(Blueprint.created_at).all()
    return jsonify({
        "exportedAt": utcnow().isoformat(),
        "profile": {"email": user.email, "username": user.username, "settings": user.settings},
        "inventory": [{"id": row.color_code, "quantity": row.quantity, "threshold": row.low_threshold} for row in inventory],
        "transactions": [{"id": row.id, "code": row.color_code, "operation": row.operation, "delta": row.delta,
                          "balanceAfter": row.balance_after, "remark": row.remark, "batchId": row.batch_id,
                          "createdAt": row.created_at.isoformat()} for row in transactions],
        "blueprints": [serialize_blueprint(bp, True) for bp in blueprints],
        "note": "为控制导出体积，图纸图片未嵌入；用豆明细、网格与进度已包含。",
    })


@app.delete("/api/account")
@login_required
def delete_account(user: User):
    body = json_body()
    if body.get("confirm") != "DELETE" or not check_password_hash(user.password_hash, str(body.get("password", ""))):
        return jsonify({"error": "密码或确认文本不正确"}), 400
    remove_user_data(user)
    db.session.commit()
    session.clear()
    return jsonify({"status": "ok"})


@app.post("/api/visits")
def track_visit():
    body = json_body()
    visitor, duration = str(body.get("visitorId", ""))[:120], min(max(int(body.get("durationSeconds", 0) or 0), 0), 86_400)
    if not visitor:
        return jsonify({"status": "ok"})
    digest = hashlib.sha256(f"{app.config['SECRET_KEY']}:{visitor}".encode()).hexdigest()[:24]
    row = VisitDaily.query.filter_by(day=date.today()).first()
    if not row:
        row = VisitDaily(day=date.today())
        db.session.add(row)
    hashes = set(json.loads(row.visitor_hashes_json or "[]"))
    row.total_visits += 1
    row.total_duration_seconds += duration
    hashes.add(digest)
    row.visitor_hashes_json = json.dumps(sorted(hashes))
    db.session.commit()
    return jsonify({"status": "ok"})


@app.get("/api/admin/visits")
@admin_required
def admin_visits(_user: User):
    rows = VisitDaily.query.order_by(VisitDaily.day.desc()).limit(90).all()
    return jsonify({"items": [{"date": row.day.isoformat(), "visits": row.total_visits,
                                "uniqueVisitors": len(json.loads(row.visitor_hashes_json or "[]")),
                                "durationSeconds": row.total_duration_seconds} for row in rows]})


@app.post("/api/admin/import-legacy")
@admin_required
def admin_import_legacy(user: User):
    body = json_body()
    if body.get("confirm") != "IMPORT" or not isinstance(body.get("data"), dict):
        return jsonify({"error": "迁移确认信息无效"}), 400
    raw = body["data"]
    batch_id = str(uuid.uuid4())
    inventory_count = 0
    for entry in raw.get("inventory", [])[:500]:
        code = str(entry.get("id", "")).strip().upper()
        try:
            quantity = int(entry.get("quantity", 0) or 0)
        except (ValueError, TypeError):
            continue
        inventory = Inventory.query.filter_by(user_id=user.id, color_code=code).with_for_update().first()
        if not inventory:
            continue
        delta = quantity - inventory.quantity
        inventory.quantity = quantity
        inventory_count += 1
        if delta:
            db.session.add(InventoryTransaction(user_id=user.id, color_code=code, operation="set", delta=delta,
                                                 balance_after=quantity, remark="旧版数据迁移", source="migration", batch_id=batch_id))
    blueprint_count = 0
    for entry in raw.get("blueprints", [])[:500]:
        legacy_id = str(entry.get("id") or uuid.uuid4())[:36]
        if Blueprint.query.filter_by(id=legacy_id, user_id=user.id).first():
            continue
        image_data, image_mime = decode_data_image(str(entry.get("image", "")))
        if image_data:
            try:
                image_data, image_mime = compress_image(image_data)
            except Exception:
                image_data, image_mime = None, None
        status = str(entry.get("status") or "待拼")
        bp = Blueprint(id=legacy_id, user_id=user.id, name=str(entry.get("name") or "未命名图纸")[:80],
                       tag=str(entry.get("tag") or "默认")[:40], source_url=str(entry.get("source") or "")[:500],
                       status=status if status in SAFE_STATUS else "待拼", image_data=image_data, image_mime=image_mime)
        db.session.add(bp)
        db.session.flush()
        replace_blueprint_items(bp, entry.get("items", []))
        blueprint_count += 1
    audit("migration.legacy_import", {"inventoryCount": inventory_count, "blueprintCount": blueprint_count,
                                      "batchId": batch_id})
    db.session.commit()
    return jsonify({"status": "ok", "inventoryCount": inventory_count, "blueprintCount": blueprint_count})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")
