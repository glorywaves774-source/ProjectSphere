"""File storage: local disk (default) or any S3-compatible object store.

Set STORAGE_BACKEND=s3 with S3_BUCKET (and S3_ENDPOINT_URL for providers such as
Cloudflare R2, Backblaze B2, DigitalOcean Spaces or Wasabi; AWS needs only the
region). Credentials come from S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY.
Objects are stored under random keys, never under the uploaded file name, and
S3 objects are encrypted at rest.
"""
import os
import uuid
from pathlib import Path

from flask import current_app


class StorageError(RuntimeError):
    pass


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise StorageError("Invalid storage key")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError("File missing from storage") from exc

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass


class S3Storage:
    def __init__(self, bucket: str, *, client=None, **client_kwargs):
        import boto3

        self.bucket = bucket
        self.client = client or boto3.client("s3", **{k: v for k, v in client_kwargs.items() if v})

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               ServerSideEncryption="AES256")

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:  # botocore ClientError and friends
            raise StorageError("File missing from storage") from exc

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def get_storage():
    store = current_app.extensions.get("ps_storage")
    if store is None:
        cfg = current_app.config
        if cfg.get("STORAGE_BACKEND") == "s3":
            store = S3Storage(cfg["S3_BUCKET"], region_name=cfg.get("S3_REGION"),
                              endpoint_url=cfg.get("S3_ENDPOINT_URL"),
                              aws_access_key_id=cfg.get("S3_ACCESS_KEY_ID"),
                              aws_secret_access_key=cfg.get("S3_SECRET_ACCESS_KEY"))
        else:
            store = LocalStorage(cfg["UPLOAD_ROOT"])
        current_app.extensions["ps_storage"] = store
    return store


def new_key(org_id) -> str:
    return f"{org_id}/{uuid.uuid4().hex}"
