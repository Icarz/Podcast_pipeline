r"""One-time YouTube OAuth authorization.

Opens a browser, you log in + approve, and it prints a refresh token to paste
into .env as YOUTUBE_REFRESH_TOKEN. Run once; the refresh token is long-lived.

    .\venv\Scripts\python.exe get_youtube_token.py
"""

import os
import sys

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Upload scope: lets us push videos to the channel later.
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main():
    load_dotenv()

    client_id = os.getenv("YOUTUBE_CLIENT_ID")
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit(
            "Missing YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET in .env. "
            "Add them (from a Google Cloud OAuth 'Desktop app' client) and re-run."
        )

    # Build the client config in-memory so no client_secrets.json is needed.
    # "installed" is the client type for the desktop loopback flow.
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # access_type=offline + prompt=consent forces Google to return a refresh token
    # even if this client was authorized before.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    if not creds.refresh_token:
        sys.exit(
            "No refresh token returned. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run this again."
        )

    # Print the token FIRST so it's always captured, even if the optional
    # sanity check below fails (e.g. the upload-only scope can't read channels).
    print("\n" + "=" * 60)
    print("YOUTUBE_REFRESH_TOKEN:\n")
    print(creds.refresh_token)
    print("\nCopy the line above into .env as:")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)

    # Sanity check: confirm we authed against the intended channel.
    # channels().list(mine=True) is a READ, which the youtube.upload scope does
    # NOT grant — so a 403 here is expected and harmless. Don't crash on it.
    try:
        youtube = build("youtube", "v3", credentials=creds)
        resp = youtube.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        channel_title = items[0]["snippet"]["title"] if items else "(no channel found)"
        channel_id = items[0]["id"] if items else "(unknown)"
        print("\nAUTHENTICATED CHANNEL:", channel_title)
        print("CHANNEL ID:", channel_id)
        print("=" * 60)
    except Exception as exc:
        print("\nUpload scope confirmed, channel read skipped (expected).")
        print(f"(channels().list was rejected: {exc.__class__.__name__})")
        print("=" * 60)


if __name__ == "__main__":
    main()
