from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import Config

from backend.config import get_settings


def _client():
    settings = get_settings()
    if not settings.s3_bucket:
        raise ValueError("S3 bucket is not configured.")
    return boto3.client(
        "s3",
        region_name=settings.s3_region or "us-east-1",
        endpoint_url=settings.s3_endpoint or None,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        config=Config(signature_version="s3v4"),
    )


def upload_file(*, path: Path, key: str) -> None:
    client = _client()
    settings = get_settings()
    client.upload_file(str(path), settings.s3_bucket, key)


def upload_stream(*, file_obj: BinaryIO, key: str) -> None:
    client = _client()
    settings = get_settings()
    client.upload_fileobj(file_obj, settings.s3_bucket, key)


def download_file(*, key: str, destination: Path) -> None:
    client = _client()
    settings = get_settings()
    client.download_file(settings.s3_bucket, key, str(destination))
