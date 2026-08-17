
import os, io, csv, secrets, hashlib, hmac, urllib.parse, smtplib, uuid
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import wraps

import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, abort, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageDraw, ImageFont
import qrcode
import barcode
from barcode.writer import ImageWriter
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-in-production")
database_url = os.getenv("DATABASE_URL", "sqlite:///ducar_fest.db")
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)
app.jinja_env.globals["min"] = min

EVENT_NAME = os.getenv("EVENT_NAME", "DUCAR FEST 3.0")
EVENT_TAGLINE = os.getenv("EVENT_TAGLINE", "Lekempo Edition")
EVENT_DATE = os.getenv("EVENT_DATE", "26 December 2026")
EVENT_VENUE = os.getenv("EVENT_VENUE", "BOCHUM, GEMARK — BABIRWA BAR LOUNGE")
CURRENCY = os.getenv("CURRENCY", "R")
MAX_PER_ORDER = int(os.getenv("MAX_PER_ORDER", "10"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
PAYFAST_MODE = os.getenv("PAYFAST_MODE", "sandbox").lower()
PAYFAST_MERCHANT_ID = os.getenv("PAYFAST_MERCHANT_ID", "")
PAYFAST_MERCHANT_KEY = os.getenv("PAYFAST_MERCHANT_KEY", "")
PAYFAST_PASSPHRASE = os.getenv("PAYFAST_PASSPHRASE", "")
PAYFAST_PROCESS_URL = "https://sandbox.payfast.co.za/eng/process" if PAYFAST_MODE == "sandbox" else "https://www.payfast.co.za/eng/process"

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
CREATOR_EMAIL = os.getenv("CREATOR_EMAIL", "").strip().lower()
CREATOR_PASSWORD = os.getenv("CREATOR_PASSWORD", "")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False)
    phone = db.Column(db.String(40))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default="buyer", nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    orders = db.relationship("Order", backref="buyer", lazy=True)

class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), nullable=False)
    tagline = db.Column(db.String(160))
    event_date = db.Column(db.String(80), nullable=False)
    venue = db.Column(db.String(240), nullable=False)
    description = db.Column(db.Text)
    sales_open = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class TicketTier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    price = db.Column(db.Numeric(12,2), nullable=False)
    inventory = db.Column(db.Integer, nullable=False, default=100)
    max_per_order = db.Column(db.Integer, nullable=False, default=10)
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(32), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    tier_id = db.Column(db.Integer, db.ForeignKey("ticket_tier.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.Numeric(12,2), nullable=False)
    payment_status = db.Column(db.String(40), default="pending")
    admin_status = db.Column(db.String(40), default="waiting")
    payfast_payment_id = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    confirmed_at = db.Column(db.DateTime)
    tier = db.relationship("TicketTier")

    tickets = db.relationship("Ticket", backref="order", lazy=True, cascade="all, delete-orphan")

class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("order.id"), nullable=False)
    checked_in = db.Column(db.Boolean, default=False)
    checked_in_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

def current_user():
    uid = session.get("user_id")
    return User.query.get(uid) if uid else None

@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "event": Event.query.first(), "currency": CURRENCY}

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in before using the ticket website.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def role_required(*roles):
    def deco(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user or user.role not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return deco

def money(v):
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def payfast_signature(data):
    # PayFast signs the URL-encoded name=value pairs, excluding signature.
    pairs = []
    for k, v in data.items():
        if k == "signature" or v is None or v == "":
            continue
        pairs.append(f"{k}={urllib.parse.quote_plus(str(v).strip())}")
    encoded = "&".join(pairs)
    if PAYFAST_PASSPHRASE:
        encoded += f"&passphrase={urllib.parse.quote_plus(PAYFAST_PASSPHRASE.strip())}"
    return hashlib.md5(encoded.encode("utf-8")).hexdigest()

def verify_payfast_signature(data):
    supplied = str(data.get("signature", ""))
    return bool(supplied) and hmac.compare_digest(supplied, payfast_signature(data))

def send_email(to_email, subject, body, attachment_bytes=None, filename="ticket.png", mimetype="image/png"):
    host = os.getenv("SMTP_HOST")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_FROM", user or "tickets@example.com")
    if not host or not user or not password:
        return False, "SMTP not configured"
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email
    msg.set_content(body)
    if attachment_bytes:
        maintype, subtype = mimetype.split("/", 1)
        msg.add_attachment(attachment_bytes, maintype=maintype, subtype=subtype, filename=filename)
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    return True, "sent"

def send_whatsapp(phone, media_url, body):
    sid = os.getenv("TWILIO_ACCOUNT_SID")
    token = os.getenv("TWILIO_AUTH_TOKEN")
    sender = os.getenv("TWILIO_WHATSAPP_FROM")
    if not sid or not token or not sender or not phone or not media_url:
        return False, "WhatsApp/Twilio not configured"
    to = phone if phone.startswith("whatsapp:") else "whatsapp:" + phone
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    payload = {"To": to, "From": sender, "Body": body, "MediaUrl": media_url}
    r = requests.post(url, data=payload, auth=(sid, token), timeout=20)
    return r.ok, r.text[:300]

def _font(size, bold=False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def media_token(code):
    return hmac.new(app.config["SECRET_KEY"].encode("utf-8"), code.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

def make_ticket_image(ticket):
    W, H = 1500, 720
    im = Image.new("RGB", (W, H), (255, 205, 0))
    d = ImageDraw.Draw(im)
    # clean professional card based on the supplied yellow ticket
    d.rounded_rectangle((18,18,W-18,H-18), radius=32, outline=(25,25,25), width=8)
    d.rectangle((18,18,28,H-18), fill=(210,20,30))
    logo_path = os.path.join(app.root_path, "static", "images", "brand-mark.png")
    if os.path.exists(logo_path):
        mark = Image.open(logo_path).convert("RGBA").resize((120,120))
        im.paste(mark, (1300,65), mark)
    d.text((75,55), EVENT_NAME, font=_font(64, True), fill=(15,15,15))
    d.text((78,130), EVENT_TAGLINE, font=_font(34), fill=(190,20,30))
    d.text((75,205), "VIP TICKET", font=_font(58, True), fill=(15,15,15))
    d.text((75,285), EVENT_DATE, font=_font(46, True), fill=(15,15,15))
    d.text((75,350), EVENT_VENUE, font=_font(31, True), fill=(15,15,15))
    d.text((75,420), f"Ticket ID: {ticket.code}", font=_font(28, True), fill=(15,15,15))
    d.text((75,468), f"Order: {ticket.order.reference}", font=_font(26), fill=(30,30,30))
    # barcode
    buf = io.BytesIO()
    cls = barcode.get_barcode_class("code128")
    cls(ticket.code, writer=ImageWriter()).write(buf, options={"module_height": 15, "font_size": 0, "quiet_zone": 3})
    buf.seek(0)
    bc = Image.open(buf).convert("RGB")
    bc.thumbnail((520,170))
    im.paste(bc, (870,465))
    d.text((885,650), "Present this ticket on your phone at entry", font=_font(22, True), fill=(20,20,20))
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out.getvalue()

def make_qr_png(ticket):
    payload = f"{PUBLIC_BASE_URL}/admin/scan?code={urllib.parse.quote(ticket.code)}" if PUBLIC_BASE_URL else ticket.code
    qr = qrcode.make(payload)
    out = io.BytesIO()
    qr.save(out, format="PNG")
    out.seek(0)
    return out.getvalue()

def seed():
    db.create_all()
    ev = Event.query.first()
    if not ev:
        ev = Event(name=EVENT_NAME, tagline=EVENT_TAGLINE, event_date=EVENT_DATE, venue=EVENT_VENUE,
                   description="Official DUCAR FEST 3.0 ticketing website. Tickets are issued only by the organiser.",
                   sales_open=True)
        db.session.add(ev)
        db.session.add_all([
            TicketTier(name="VIP", price=Decimal("100.00"), inventory=500, max_per_order=MAX_PER_ORDER, sort_order=1),
            TicketTier(name="GENERAL", price=Decimal("50.00"), inventory=1000, max_per_order=MAX_PER_ORDER, sort_order=2),
        ])
    # Create real staff accounts only when credentials are explicitly supplied in environment variables.
    # No demo/admin/creator accounts are created by default.
    if ADMIN_EMAIL and ADMIN_PASSWORD and not User.query.filter_by(email=ADMIN_EMAIL).first():
        db.session.add(User(name="Administrator", email=ADMIN_EMAIL, password_hash=generate_password_hash(ADMIN_PASSWORD), role="admin"))
    if CREATOR_EMAIL and CREATOR_PASSWORD and not User.query.filter_by(email=CREATOR_EMAIL).first():
        db.session.add(User(name="Event Creator", email=CREATOR_EMAIL, password_hash=generate_password_hash(CREATOR_PASSWORD), role="creator"))
    db.session.commit()

@app.route("/")
def home():
    tiers = TicketTier.query.filter_by(active=True).order_by(TicketTier.sort_order).all()
    return render_template("home.html", tiers=tiers)

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name, email, phone = request.form["name"].strip(), request.form["email"].strip().lower(), request.form["phone"].strip()
        password = request.form["password"]
        if User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "danger")
            return render_template("register.html")
        u = User(name=name, email=email, phone=phone, password_hash=generate_password_hash(password), role="buyer")
        db.session.add(u); db.session.commit()
        session["user_id"] = u.id
        flash("Account created. You can now buy tickets.", "success")
        return redirect(url_for("home"))
    return render_template("register.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email, password = request.form["email"].strip().lower(), request.form["password"]
        u = User.query.filter_by(email=email).first()
        if u and check_password_hash(u.password_hash, password):
            session.clear(); session["user_id"] = u.id
            nxt = request.args.get("next")
            if u.role == "admin": return redirect(url_for("admin_dashboard"))
            if u.role == "creator": return redirect(url_for("creator_dashboard"))
            return redirect(nxt or url_for("home"))
        flash("Incorrect email or password.", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/checkout", methods=["POST"])
@login_required
def checkout():
    ev = Event.query.first()
    if not ev.sales_open:
        flash("Ticket sales are currently closed.", "warning")
        return redirect(url_for("home"))
    tier = TicketTier.query.get_or_404(int(request.form["tier_id"]))
    qty = int(request.form["quantity"])
    if qty < 1 or qty > min(MAX_PER_ORDER, tier.max_per_order):
        flash(f"Maximum {min(MAX_PER_ORDER, tier.max_per_order)} tickets per order.", "danger")
        return redirect(url_for("home"))
    if qty > tier.inventory:
        flash("Not enough tickets left.", "danger")
        return redirect(url_for("home"))
    ref = "DUC-" + secrets.token_hex(5).upper()
    order = Order(reference=ref, user_id=current_user().id, tier_id=tier.id, quantity=qty,
                  amount=money(tier.price) * qty, payment_status="pending", admin_status="waiting")
    tier.inventory -= qty
    db.session.add(order); db.session.commit()
    if PAYFAST_MERCHANT_ID and PAYFAST_MERCHANT_KEY and PAYFAST_PASSPHRASE:
        return redirect(url_for("payfast_checkout", order_ref=order.reference))
    # No demo checkout: real payment configuration is required before an order is created.
    db.session.delete(order)
    tier.inventory += qty
    db.session.commit()
    flash("Online payments are not configured yet. Please contact the organiser.", "danger")
    return redirect(url_for("home"))

@app.route("/payfast/<order_ref>")
@login_required
def payfast_checkout(order_ref):
    order = Order.query.filter_by(reference=order_ref).first_or_404()
    if order.user_id != current_user().id and current_user().role != "admin":
        abort(403)
    data = {
        "merchant_id": PAYFAST_MERCHANT_ID,
        "merchant_key": PAYFAST_MERCHANT_KEY,
        "return_url": url_for("payment_return", ref=order.reference, _external=True),
        "cancel_url": url_for("payment_cancel", ref=order.reference, _external=True),
        "notify_url": url_for("payfast_itn", _external=True),
        "name_first": current_user().name.split()[0],
        "name_last": " ".join(current_user().name.split()[1:]) or current_user().name,
        "email_address": current_user().email,
        "m_payment_id": order.reference,
        "amount": f"{Decimal(order.amount):.2f}",
        "item_name": f"{EVENT_NAME} — {order.tier.name} x {order.quantity}",
    }
    data["signature"] = payfast_signature(data)
    return render_template("payfast.html", process_url=PAYFAST_PROCESS_URL, data=data)

@app.route("/payment/return/<ref>")
@login_required
def payment_return(ref):
    return render_template("payment_return.html", ref=ref)

@app.route("/payment/cancel/<ref>")
@login_required
def payment_cancel(ref):
    return render_template("payment_cancel.html", ref=ref)

@app.route("/payfast/itn", methods=["POST"])
def payfast_itn():
    data = request.form.to_dict()
    if not verify_payfast_signature(data):
        return "invalid signature", 400
    ref = data.get("m_payment_id")
    order = Order.query.filter_by(reference=ref).first()
    if not order:
        return "order not found", 404
    try:
        paid = money(data.get("amount_gross", "0"))
    except Exception:
        return "invalid amount", 400
    if paid != money(order.amount):
        return "amount mismatch", 400
    if data.get("payment_status") == "COMPLETE":
        order.payment_status = "paid"
        order.payfast_payment_id = data.get("pf_payment_id")
        db.session.commit()
    return "OK", 200

@app.route("/order/<ref>")
@login_required
def order_detail(ref):
    order = Order.query.filter_by(reference=ref).first_or_404()
    if order.user_id != current_user().id and current_user().role not in ("admin","creator"):
        abort(403)
    return render_template("order.html", order=order)

@app.route("/my-tickets")
@login_required
def my_tickets():
    orders = Order.query.filter_by(user_id=current_user().id).order_by(Order.created_at.desc()).all()
    return render_template("my_tickets.html", orders=orders)

@app.route("/ticket/<code>.png")
@login_required
def ticket_png(code):
    t = Ticket.query.filter_by(code=code).first_or_404()
    if t.order.user_id != current_user().id and current_user().role != "admin":
        abort(403)
    return send_file(io.BytesIO(make_ticket_image(t)), mimetype="image/png", download_name=f"{t.code}.png")

@app.route("/public-ticket/<code>/<token>.png")
def public_ticket_png(code, token):
    if not hmac.compare_digest(token, media_token(code)):
        abort(404)
    t = Ticket.query.filter_by(code=code).first_or_404()
    if t.order.admin_status != "confirmed":
        abort(404)
    return send_file(io.BytesIO(make_ticket_image(t)), mimetype="image/png", download_name=f"{t.code}.png")

@app.route("/ticket/<code>/qr.png")
@login_required
def ticket_qr(code):
    t = Ticket.query.filter_by(code=code).first_or_404()
    if t.order.user_id != current_user().id and current_user().role != "admin":
        abort(403)
    return send_file(io.BytesIO(make_qr_png(t)), mimetype="image/png", download_name=f"{t.code}-qr.png")

@app.route("/admin")
@role_required("admin")
def admin_dashboard():
    stats = {
        "buyers": User.query.filter_by(role="buyer").count(),
        "orders": Order.query.count(),
        "paid": Order.query.filter_by(payment_status="paid").count(),
        "revenue": sum((Decimal(o.amount) for o in Order.query.filter_by(payment_status="paid").all()), Decimal("0")),
        "checked": Ticket.query.filter_by(checked_in=True).count(),
    }
    orders = Order.query.order_by(Order.created_at.desc()).limit(200).all()
    return render_template("admin.html", stats=stats, orders=orders)

@app.route("/admin/order/<int:order_id>/confirm", methods=["POST"])
@role_required("admin")
def admin_confirm(order_id):
    order = Order.query.get_or_404(order_id)
    if order.payment_status != "paid":
        flash("This order has not been marked as paid by the payment gateway.", "danger")
        return redirect(url_for("admin_dashboard"))
    if not order.tickets:
        for _ in range(order.quantity):
            db.session.add(Ticket(code="DCR-" + secrets.token_hex(6).upper(), order_id=order.id))
    order.admin_status = "confirmed"
    order.confirmed_at = datetime.now(timezone.utc)
    db.session.commit()
    # Send each ticket by email and WhatsApp when configured.
    for t in order.tickets:
        img_bytes = make_ticket_image(t)
        send_email(order.buyer.email, f"{EVENT_NAME} — Ticket {t.code}",
                   f"Your ticket {t.code} is confirmed. Present the ticket on your phone at the event.", img_bytes, f"{t.code}.png")
        media_url = f"{PUBLIC_BASE_URL}/public-ticket/{t.code}/{media_token(t.code)}.png" if PUBLIC_BASE_URL else ""
        send_whatsapp(order.buyer.phone, media_url,
                      f"{EVENT_NAME}: your ticket {t.code} is confirmed. Please keep it on your phone.")
    flash("Order confirmed. Ticket(s) generated and delivery attempted.", "success")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/order/<int:order_id>/reject", methods=["POST"])
@role_required("admin")
def admin_reject(order_id):
    order = Order.query.get_or_404(order_id)
    if order.admin_status != "confirmed":
        order.admin_status = "rejected"
        order.tier.inventory += order.quantity
        db.session.commit()
    flash("Order rejected and inventory restored.", "info")
    return redirect(url_for("admin_dashboard"))

@app.route("/admin/scan")
@role_required("admin")
def admin_scan():
    return render_template("scanner.html")

@app.route("/api/scan/<code>")
@role_required("admin")
def api_scan(code):
    t = Ticket.query.filter_by(code=code.strip().upper()).first()
    if not t:
        return jsonify(ok=False, message="Ticket not found. This code was not issued by this website.")
    if t.order.admin_status != "confirmed" or t.order.payment_status != "paid":
        return jsonify(ok=False, message="Ticket exists but is not confirmed.")
    if t.checked_in:
        return jsonify(ok=False, message="ALREADY USED", ticket=t.code, buyer=t.order.buyer.name)
    t.checked_in = True
    t.checked_in_at = datetime.now(timezone.utc)
    db.session.commit()
    return jsonify(ok=True, message="VALID TICKET — ENTRY APPROVED", ticket=t.code, buyer=t.order.buyer.name)

@app.route("/admin/buyers")
@role_required("admin")
def admin_buyers():
    buyers = User.query.filter_by(role="buyer").order_by(User.created_at.desc()).all()
    return render_template("buyers.html", buyers=buyers)

@app.route("/creator")
@role_required("creator","admin")
def creator_dashboard():
    ev = Event.query.first()
    tiers = TicketTier.query.order_by(TicketTier.sort_order).all()
    return render_template("creator.html", ev=ev, tiers=tiers)

@app.route("/creator/update", methods=["POST"])
@role_required("creator","admin")
def creator_update():
    ev = Event.query.first()
    ev.name = request.form["name"].strip()
    ev.tagline = request.form["tagline"].strip()
    ev.event_date = request.form["event_date"].strip()
    ev.venue = request.form["venue"].strip()
    ev.sales_open = "sales_open" in request.form
    for tier in TicketTier.query.all():
        tier.price = money(request.form.get(f"price_{tier.id}", tier.price))
        tier.inventory = max(0, int(request.form.get(f"inventory_{tier.id}", tier.inventory)))
        tier.max_per_order = max(1, min(50, int(request.form.get(f"max_{tier.id}", tier.max_per_order))))
        tier.active = f"active_{tier.id}" in request.form
    db.session.commit()
    flash("Event and ticket settings updated.", "success")
    return redirect(url_for("creator_dashboard"))

@app.route("/health")
def health():
    return {"ok": True, "service": "Ducar Fest Ticketing"}

@app.cli.command("init-db")
def init_db():
    seed()
    print("Database initialized.")

with app.app_context():
    seed()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
