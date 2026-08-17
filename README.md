# DUCAR FEST 3.0 — Ticketing Website

A Flask/Pydroid 3 event-ticketing website inspired by the supplied DUCAR FEST 3.0 ticket photo.

## Included
- Buyer registration/login required before checkout
- One organiser-owned event; buyers cannot create events or tickets
- VIP and GENERAL ticket tiers
- Quantity limits and inventory
- PayFast checkout integration (sandbox/live)
- PayFast ITN payment verification
- Admin dashboard with buyer/order/revenue visibility
- Manual admin confirmation before tickets are issued
- Unique ticket codes and Code 128 barcodes
- Downloadable ticket PNGs
- Admin phone-camera scanner and one-time check-in
- Creator dashboard for existing event/tier settings
- Optional email ticket delivery
- Optional WhatsApp ticket delivery via Twilio using a signed public ticket image URL
- Render Blueprint + PostgreSQL
- Supplied ticket photo and logo assets

## Important payment note
The app does NOT store card numbers. Customers enter their card/payment details on the PayFast checkout. To have funds settle to your bank account, you must have your own verified PayFast merchant account connected to your bank and enter its merchant credentials in Render. The code cannot safely or legitimately route card money directly to a bank without a payment provider.

PayFast documentation: https://payfast.io/integration/custom-integration/

## Local/Pydroid 3
1. Extract this folder.
2. Open Pydroid 3 terminal.
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env`.
5. Keep `PAYFAST_MODE=sandbox` while testing.
6. Run: `python app.py`
7. Open `http://127.0.0.1:5000`

There are **no demo dashboards, demo accounts, or demo payment orders** in this project.

Set `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `CREATOR_EMAIL`, and `CREATOR_PASSWORD` in `.env`/Render Environment Variables to create the real staff accounts. If these values are empty, no staff account is created.

## Render
This repo contains `render.yaml`. In GitHub, upload/commit the entire folder. In Render choose New > Blueprint and connect the repository. Render will create the web service and Postgres database from the Blueprint. Render's current Flask deployment docs use `pip install -r requirements.txt` and `gunicorn app:app`.

Set the real secret values requested by Render, especially:
- PAYFAST_MERCHANT_ID
- PAYFAST_MERCHANT_KEY
- PAYFAST_PASSPHRASE
- PUBLIC_BASE_URL (your https://....onrender.com URL)
- ADMIN_EMAIL / ADMIN_PASSWORD
- CREATOR_EMAIL / CREATOR_PASSWORD
- TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN
- TWILIO_WHATSAPP_FROM=whatsapp:+27649912526

For live PayFast payments, switch `PAYFAST_MODE=live` only after testing.

## WhatsApp
WhatsApp delivery is optional. This project is configured to use **+27 64 991 2526** as the Twilio WhatsApp sender (`whatsapp:+27649912526`). The number must actually be enabled/onboarded as a WhatsApp sender in your Twilio account; putting the number in the environment variable alone does not activate it. Configure the three TWILIO_* variables. For production/out-of-session delivery, use an approved WhatsApp template as required by WhatsApp/Twilio.

## Admin flow
1. Buyer registers and logs in.
2. Buyer selects ticket tier and quantity.
3. Buyer pays through PayFast.
4. PayFast sends the ITN to `/payfast/itn`.
5. Admin sees the paid order in `/admin`.
6. Admin clicks Confirm.
7. The app creates unique tickets and attempts email/WhatsApp delivery.
8. Buyer opens My Tickets and keeps the PNG ticket on their phone.
9. At the event, admin opens `/admin/scan`, grants camera permission, and scans the Code 128 barcode.
10. A valid ticket is accepted once; a second scan shows ALREADY USED.

## Production hardening
Before public sales, use HTTPS, a strong secret, a real transactional email provider, a verified WhatsApp sender/template, PayFast live credentials, PostgreSQL, backups, rate limiting/WAF, CSRF protection, and change all default credentials.
