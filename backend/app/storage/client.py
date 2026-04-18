"""Object storage abstraction supporting MinIO/S3 and Google Cloud Storage.

Usage:
    client = create_storage_client(settings)
    url = await client.presigned_upload_url("sources/abc/data.json")
    url = await client.presigned_download_url("sources/abc/data.json")
    data = await client.get_object("sources/abc/data.json")
    objects = await client.list_objects("sources/abc/")
    await client.delete_object("sources/abc/data.json")
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("[storage]")


class StorageClient(ABC):
    """Abstract interface for object storage."""

    @abstractmethod
    async def presigned_upload_url(
        self, key: str, content_type: str = "application/octet-stream", expires: int = 3600
    ) -> str: ...

    @abstractmethod
    async def presigned_download_url(self, key: str, expires: int = 3600) -> str: ...

    @abstractmethod
    async def get_object(self, key: str) -> bytes: ...

    @abstractmethod
    async def get_object_text(self, key: str) -> str: ...

    @abstractmethod
    async def list_objects(self, prefix: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def delete_object(self, key: str) -> None: ...

    @abstractmethod
    async def object_exists(self, key: str) -> bool: ...


# ── MinIO / S3 implementation ────────────────────────────────────────

class S3StorageClient(StorageClient):
    """S3-compatible storage (MinIO, AWS S3, DigitalOcean Spaces, etc.)."""

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
        use_ssl: bool = True,
        public_endpoint: str = "",
    ) -> None:
        import boto3
        from botocore.config import Config

        self._bucket = bucket
        self._endpoint = endpoint
        self._public_endpoint = public_endpoint or endpoint

        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4"),
        )

        # Separate client for presigned URLs that use the public endpoint
        # (browser-reachable, e.g. http://localhost:9000 vs http://minio:9000)
        if public_endpoint and public_endpoint != endpoint:
            self._s3_public = boto3.client(
                "s3",
                endpoint_url=public_endpoint,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=Config(signature_version="s3v4"),
            )
        else:
            self._s3_public = self._s3

        # Ensure bucket exists
        try:
            self._s3.head_bucket(Bucket=bucket)
        except Exception:
            logger.info("Creating bucket: %s", bucket)
            self._s3.create_bucket(Bucket=bucket)

    async def presigned_upload_url(
        self, key: str, content_type: str = "application/octet-stream", expires: int = 3600
    ) -> str:
        return await asyncio.to_thread(
            self._s3_public.generate_presigned_url,
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires,
        )

    async def presigned_download_url(self, key: str, expires: int = 3600) -> str:
        return await asyncio.to_thread(
            self._s3_public.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires,
        )

    async def get_object(self, key: str) -> bytes:
        resp = await asyncio.to_thread(
            self._s3.get_object, Bucket=self._bucket, Key=key
        )
        return resp["Body"].read()

    async def get_object_text(self, key: str) -> str:
        data = await self.get_object(key)
        return data.decode("utf-8")

    async def list_objects(self, prefix: str) -> list[dict[str, Any]]:
        resp = await asyncio.to_thread(
            self._s3.list_objects_v2, Bucket=self._bucket, Prefix=prefix
        )
        objects = []
        for obj in resp.get("Contents", []):
            objects.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })
        return objects

    async def delete_object(self, key: str) -> None:
        await asyncio.to_thread(
            self._s3.delete_object, Bucket=self._bucket, Key=key
        )

    async def object_exists(self, key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._s3.head_object, Bucket=self._bucket, Key=key
            )
            return True
        except Exception:
            return False


# ── Google Cloud Storage implementation ──────────────────────────────

class GCSStorageClient(StorageClient):
    """Google Cloud Storage backend."""

    def __init__(
        self,
        bucket: str,
        project_id: str = "",
        credentials_json: str = "",
    ) -> None:
        from google.cloud import storage as gcs

        if credentials_json:
            import json
            from google.oauth2 import service_account

            info = json.loads(credentials_json)
            credentials = service_account.Credentials.from_service_account_info(info)
            self._client = gcs.Client(project=project_id or info.get("project_id"), credentials=credentials)
        else:
            self._client = gcs.Client(project=project_id or None)

        self._bucket_name = bucket
        self._bucket = self._client.bucket(bucket)

        # Ensure bucket exists
        if not self._bucket.exists():
            logger.info("Creating GCS bucket: %s", bucket)
            self._client.create_bucket(bucket)

    async def presigned_upload_url(
        self, key: str, content_type: str = "application/octet-stream", expires: int = 3600
    ) -> str:
        import datetime

        blob = self._bucket.blob(key)
        return await asyncio.to_thread(
            blob.generate_signed_url,
            version="v4",
            expiration=datetime.timedelta(seconds=expires),
            method="PUT",
            content_type=content_type,
        )

    async def presigned_download_url(self, key: str, expires: int = 3600) -> str:
        import datetime

        blob = self._bucket.blob(key)
        return await asyncio.to_thread(
            blob.generate_signed_url,
            version="v4",
            expiration=datetime.timedelta(seconds=expires),
            method="GET",
        )

    async def get_object(self, key: str) -> bytes:
        blob = self._bucket.blob(key)
        return await asyncio.to_thread(blob.download_as_bytes)

    async def get_object_text(self, key: str) -> str:
        blob = self._bucket.blob(key)
        return await asyncio.to_thread(blob.download_as_text)

    async def list_objects(self, prefix: str) -> list[dict[str, Any]]:
        blobs = await asyncio.to_thread(
            lambda: list(self._client.list_blobs(self._bucket_name, prefix=prefix))
        )
        return [
            {
                "key": b.name,
                "size": b.size,
                "last_modified": b.updated.isoformat() if b.updated else "",
            }
            for b in blobs
        ]

    async def delete_object(self, key: str) -> None:
        blob = self._bucket.blob(key)
        await asyncio.to_thread(blob.delete)

    async def object_exists(self, key: str) -> bool:
        blob = self._bucket.blob(key)
        return await asyncio.to_thread(blob.exists)


# ── factory ──────────────────────────────────────────────────────────

def create_storage_client(settings: Any) -> StorageClient:
    """Create the appropriate storage client based on settings."""
    provider = settings.storage_provider

    if provider == "gcs":
        return GCSStorageClient(
            bucket=settings.storage_bucket,
            project_id=settings.google_project_id,
            credentials_json=settings.gcs_credentials_json,
        )

    # Default: s3 / minio
    return S3StorageClient(
        endpoint=settings.storage_endpoint,
        access_key=settings.storage_access_key,
        secret_key=settings.storage_secret_key,
        bucket=settings.storage_bucket,
        region=settings.storage_region,
        use_ssl=settings.storage_endpoint.startswith("https"),
        public_endpoint=settings.storage_public_endpoint,
    )
