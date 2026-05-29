"""Object storage: upload assets to Cloudflare R2 (S3-compatible) via boto3."""

import logging
import os

import boto3

logger = logging.getLogger(__name__)


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["CLOUDFLARE_R2_ENDPOINT"],
        aws_access_key_id=os.environ["CLOUDFLARE_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["CLOUDFLARE_R2_SECRET_ACCESS_KEY"],
    )


def upload(file_path: str, key: str | None = None) -> str:
    """Upload ``file_path`` to R2 and return its public URL."""
    bucket = os.environ["CLOUDFLARE_R2_BUCKET"]
    key = key or os.path.basename(file_path)
    logger.info("Uploading %s to r2://%s/%s", file_path, bucket, key)

    client = _client()
    client.upload_file(file_path, bucket, key)

    public_base = os.environ.get("CLOUDFLARE_R2_PUBLIC_URL", "").rstrip("/")
    return f"{public_base}/{key}" if public_base else key
