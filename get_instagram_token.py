"""One-time Instagram token helper (manual Facebook Login OAuth flow).

Walks the manual OAuth flow that exposes ``instagram_content_publish`` for
self-publishing (the Login-for-Business config wizard does not). Run it once,
paste the redirect ``code`` back, and it prints the long-lived token + IG
business account id to drop into ``.env`` as ``META_ACCESS_TOKEN`` and
``META_IG_USER_ID``.

Usage:
    .\\venv\\Scripts\\python.exe get_instagram_token.py
"""

import os
import sys
import urllib.parse

import requests
from dotenv import load_dotenv

GRAPH_VERSION = "v21.0"
DIALOG_BASE = f"https://www.facebook.com/{GRAPH_VERSION}/dialog/oauth"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"

# Must match EXACTLY between the login dialog and the code->token exchange.
REDIRECT_URI = "https://localhost/"

SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "pages_show_list",
    "pages_read_engagement",
    "business_management",
]


def _die(msg: str) -> None:
    print(f"\nERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _get(url: str, params: dict) -> dict:
    resp = requests.get(url, params=params, timeout=30)
    try:
        data = resp.json()
    except ValueError:
        _die(f"Non-JSON response from {url}: {resp.status_code} {resp.text[:300]}")
    if "error" in data:
        err = data["error"]
        _die(f"Graph API error: {err.get('message')} (type={err.get('type')}, code={err.get('code')})")
    return data


def main() -> None:
    load_dotenv()
    app_id = os.environ.get("META_APP_ID")
    app_secret = os.environ.get("META_APP_SECRET")
    if not app_id or not app_secret:
        _die("Set META_APP_ID and META_APP_SECRET in .env first.")

    # --- Step 2: print the Facebook Login URL ---
    auth_params = {
        "client_id": app_id,
        "redirect_uri": REDIRECT_URI,
        "scope": ",".join(SCOPES),
        "response_type": "code",
    }
    login_url = f"{DIALOG_BASE}?{urllib.parse.urlencode(auth_params)}"

    print("=" * 78)
    print("STEP 1 - Open this URL in your browser, approve as the Icarus Wings")
    print("account, and select the Page + @wearicaruswings:\n")
    print(login_url)
    print()
    print("Facebook will redirect to a URL like:")
    print(f"  {REDIRECT_URI}?code=AQ...#_")
    print("(The page itself will fail to load - that's fine. Copy the 'code'")
    print("value from the address bar. Drop the trailing '#_' if present.)")
    print("=" * 78)

    # --- Step 3: paste the code back ---
    code = input("\nPaste the code here: ").strip()
    if not code:
        _die("No code provided.")
    if "code=" in code:  # tolerate a full pasted URL
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query).get("code", [""])[0]
    code = code.split("#")[0].strip()
    if not code:
        _die("Could not parse a code from that input.")

    # --- Step 4: exchange code -> short-lived user token ---
    print("\nExchanging code for a short-lived token...")
    short = _get(
        f"{GRAPH_BASE}/oauth/access_token",
        {
            "client_id": app_id,
            "redirect_uri": REDIRECT_URI,
            "client_secret": app_secret,
            "code": code,
        },
    )
    short_token = short.get("access_token")
    if not short_token:
        _die(f"No access_token in response: {short}")

    # --- Step 5: exchange short-lived -> long-lived (60-day) token ---
    print("Exchanging for a long-lived (60-day) token...")
    longd = _get(
        f"{GRAPH_BASE}/oauth/access_token",
        {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
    )
    long_token = longd.get("access_token")
    if not long_token:
        _die(f"No long-lived access_token in response: {longd}")
    expires_days = round(int(longd.get("expires_in", 0)) / 86400) if longd.get("expires_in") else None

    # --- Step 6: find the Page, then its IG business account ---
    print("Fetching Pages (/me/accounts)...")
    accounts = _get(f"{GRAPH_BASE}/me/accounts", {"access_token": long_token})
    pages = accounts.get("data", [])
    if not pages:
        _die("No Pages returned. Make sure you granted the Page during login.")

    ig_id = None
    chosen_page = None
    for page in pages:
        info = _get(
            f"{GRAPH_BASE}/{page['id']}",
            {"fields": "instagram_business_account", "access_token": long_token},
        )
        iba = info.get("instagram_business_account")
        if iba and iba.get("id"):
            ig_id = iba["id"]
            chosen_page = page
            break

    if not ig_id:
        names = ", ".join(f"{p.get('name')} ({p['id']})" for p in pages)
        _die(f"No instagram_business_account linked to any Page. Pages seen: {names}")

    # --- Step 7: print results clearly ---
    print("\n" + "=" * 78)
    print("SUCCESS - paste these into your .env")
    print("=" * 78)
    print(f"Page: {chosen_page.get('name')} ({chosen_page['id']})")
    if expires_days is not None:
        print(f"Token valid ~{expires_days} days.")
    print()
    print(f"META_ACCESS_TOKEN={long_token}")
    print(f"META_IG_USER_ID={ig_id}")
    print("=" * 78)


if __name__ == "__main__":
    main()
