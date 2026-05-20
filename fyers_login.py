"""
fyers_login.py — auto-refresh Fyers v3 access token via TOTP.

Reads credentials from `credentials.json` (same folder, gitignored).
Writes fresh access token to `token.txt`.
Caches per-day in `token_cache.json` so re-running same day is free.

Endpoints verified Nov 2025 (FabTrader / community).
If Fyers changes internal endpoints, only the URLs at the top need updating.
"""
import os
import sys
import json
import time
import base64
import requests
import pyotp
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from fyers_apiv3 import fyersModel

HERE = os.path.dirname(os.path.abspath(__file__))
CREDS_PATH = os.path.join(HERE, "credentials.json")
TOKEN_FILE = os.path.join(HERE, "token.txt")
TOKEN_CACHE = os.path.join(HERE, "token_cache.json")

URL_SEND_LOGIN_OTP = "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2"
URL_VERIFY_OTP     = "https://api-t2.fyers.in/vagator/v2/verify_otp"
URL_VERIFY_PIN     = "https://api-t2.fyers.in/vagator/v2/verify_pin_v2"
URL_TOKEN          = "https://api-t1.fyers.in/api/v3/token"


def b64(s):
    return base64.b64encode(str(s).encode("ascii")).decode("ascii")


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_creds():
    if not os.path.exists(CREDS_PATH):
        raise FileNotFoundError(
            f"{CREDS_PATH} missing. Copy credentials.template.json → credentials.json and fill it in."
        )
    with open(CREDS_PATH) as f:
        return json.load(f)


def cached_token_today():
    if not os.path.exists(TOKEN_CACHE):
        return None
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        with open(TOKEN_CACHE) as f:
            return json.load(f).get(today)
    except Exception:
        return None


def save_token(access_token):
    today = datetime.now().strftime("%Y-%m-%d")
    with open(TOKEN_FILE, "w") as f:
        f.write(access_token)
    cache = {}
    if os.path.exists(TOKEN_CACHE):
        try:
            with open(TOKEN_CACHE) as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    cache[today] = access_token
    # keep last 7 days only
    keys = sorted(cache.keys())[-7:]
    cache = {k: cache[k] for k in keys}
    with open(TOKEN_CACHE, "w") as f:
        json.dump(cache, f, indent=2)


def generate_fresh_token(creds):
    client_id_full = f"{creds['APP_ID']}-{creds['APP_TYPE']}"

    # Step 1: send_login_otp
    log("Step 1/5: send_login_otp")
    r = requests.post(URL_SEND_LOGIN_OTP, json={
        "fy_id": b64(creds["FY_ID"]),
        "app_id": "2",
    }, timeout=15).json()
    if "request_key" not in r:
        raise RuntimeError(f"send_login_otp failed: {r}")
    req_key = r["request_key"]

    # Step 2: verify_otp (TOTP). Avoid 30s rollover window.
    if datetime.now().second % 30 >= 27:
        log("Near TOTP rollover, waiting 5s...")
        time.sleep(5)
    totp_code = pyotp.TOTP(creds["TOTP_KEY"]).now()
    log(f"Step 2/5: verify_otp (code={totp_code})")
    r = requests.post(URL_VERIFY_OTP, json={
        "request_key": req_key,
        "otp": totp_code,
    }, timeout=15).json()
    if "request_key" not in r:
        raise RuntimeError(f"verify_otp failed: {r}")
    req_key = r["request_key"]

    # Step 3: verify_pin
    log("Step 3/5: verify_pin")
    sess = requests.Session()
    r = sess.post(URL_VERIFY_PIN, json={
        "request_key": req_key,
        "identity_type": "pin",
        "identifier": b64(creds["PIN"]),
    }, timeout=15).json()
    if r.get("s") != "ok":
        raise RuntimeError(f"verify_pin failed: {r}")
    vagator_token = r["data"]["access_token"]

    # Step 4: get auth_code
    log("Step 4/5: get auth_code")
    sess.headers.update({"authorization": f"Bearer {vagator_token}"})
    r = sess.post(URL_TOKEN, json={
        "fyers_id":      creds["FY_ID"],
        "app_id":        creds["APP_ID"],
        "redirect_uri":  creds["REDIRECT_URI"],
        "appType":       creds["APP_TYPE"],
        "code_challenge": "",
        "state":         "auto",
        "scope":         "",
        "nonce":         "",
        "response_type": "code",
        "create_cookie": True,
    }, timeout=15).json()
    if "Url" not in r:
        raise RuntimeError(f"token endpoint failed: {r}")
    auth_code = parse_qs(urlparse(r["Url"]).query)["auth_code"][0]

    # Step 5: exchange auth_code via SDK
    log("Step 5/5: exchange auth_code → access_token")
    session = fyersModel.SessionModel(
        client_id=client_id_full,
        secret_key=creds["SECRET_KEY"],
        redirect_uri=creds["REDIRECT_URI"],
        response_type="code",
        grant_type="authorization_code",
    )
    session.set_token(auth_code)
    resp = session.generate_token()
    if "access_token" not in resp:
        raise RuntimeError(f"generate_token failed: {resp}")
    return resp["access_token"]


def main():
    log("=== fyers_login.py start ===")

    cached = cached_token_today()
    if cached:
        with open(TOKEN_FILE, "w") as f:
            f.write(cached)
        log("Cached token for today found. Reused. Exiting.")
        return 0

    try:
        creds = load_creds()
        token = generate_fresh_token(creds)
        save_token(token)
        log(f"SUCCESS. Token length={len(token)}. Written to token.txt.")
        return 0
    except Exception as e:
        log(f"FAILED: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
