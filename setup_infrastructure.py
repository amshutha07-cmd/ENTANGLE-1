"""
setup_infrastructure.py
-----------------------
One-time bootstrap: creates all 11 regional S3 shard buckets.

Credentials are read from environment variables (never hardcoded):
    AWS_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY

Run:
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... python setup_infrastructure.py
OR configure ~/.aws/credentials and let boto3 pick them up automatically.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Configuration ───────────────────────────────────────────────────────────

# The 11 global shard regions
REGIONS: list[str] = [
    "us-east-1",
    "us-west-2",
    "eu-central-1",
    "ap-southeast-1",
    "ap-northeast-1",
    "sa-east-1",
    "us-east-2",
    "eu-west-1",
    "ap-south-1",
    "ca-central-1",
    "eu-west-3",
]

BUCKET_PREFIX = "ansx-vault-shard"


@dataclass
class DeploymentResult:
    region: str
    bucket_name: str
    success: bool
    error: Optional[str] = None


def _get_credentials() -> tuple[str, str]:
    """
    Loads AWS credentials exclusively from environment variables.
    Raises EnvironmentError if either variable is missing.
    """
    access_key = os.environ.get("AWS_ACCESS_KEY_ID")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY")

    missing = [k for k, v in [("AWS_ACCESS_KEY_ID", access_key), ("AWS_SECRET_ACCESS_KEY", secret_key)] if not v]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}\n"
            "Set them before running this script or configure ~/.aws/credentials."
        )

    return access_key, secret_key  # type: ignore[return-value]


def _create_bucket(s3_client, bucket_name: str, region: str) -> None:
    """Creates a single S3 bucket with versioning and server-side encryption."""
    # us-east-1 doesn't accept a LocationConstraint
    if region == "us-east-1":
        s3_client.create_bucket(Bucket=bucket_name)
    else:
        s3_client.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": region},
        )

    # Block all public access
    s3_client.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    # Enable versioning for shard recovery
    s3_client.put_bucket_versioning(
        Bucket=bucket_name,
        VersioningConfiguration={"Status": "Enabled"},
    )

    # AES-256 server-side encryption by default
    s3_client.put_bucket_encryption(
        Bucket=bucket_name,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "AES256"
                    },
                    "BucketKeyEnabled": True,
                }
            ]
        },
    )


def create_global_buckets(dry_run: bool = False) -> list[DeploymentResult]:
    """
    Creates all 11 shard buckets and returns a list of DeploymentResult objects.
    Raises EnvironmentError if credentials are not set.
    """
    access_key, secret_key = _get_credentials()
    results: list[DeploymentResult] = []

    for i, region in enumerate(REGIONS):
        bucket_name = f"{BUCKET_PREFIX}-{i + 1}-{region}"
        logger.info("Deploying shard node %d/%d → %s ...", i + 1, len(REGIONS), region)

        if dry_run:
            logger.info("  [DRY RUN] Would create: %s", bucket_name)
            results.append(DeploymentResult(region=region, bucket_name=bucket_name, success=True))
            continue

        try:
            s3 = boto3.client(
                "s3",
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
            _create_bucket(s3, bucket_name, region)
            logger.info("  ✅ Online: %s", bucket_name)
            results.append(DeploymentResult(region=region, bucket_name=bucket_name, success=True))
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "BucketAlreadyOwnedByYou":
                logger.warning("  ⚠️  Already owned: %s", bucket_name)
                results.append(DeploymentResult(region=region, bucket_name=bucket_name, success=True))
            else:
                logger.error("  ❌ Failed [%s]: %s", region, e)
                results.append(DeploymentResult(region=region, bucket_name=bucket_name, success=False, error=str(e)))
        except (BotoCoreError, Exception) as e:
            logger.error("  ❌ Failed [%s]: %s", region, e)
            results.append(DeploymentResult(region=region, bucket_name=bucket_name, success=False, error=str(e)))

    return results


def _print_summary(results: list[DeploymentResult]) -> None:
    ok  = sum(1 for r in results if r.success)
    bad = len(results) - ok
    print("\n" + "─" * 50)
    print(f"  DEPLOYMENT COMPLETE  |  ✅ {ok} succeeded  |  ❌ {bad} failed")
    print("─" * 50)
    if bad:
        print("\nFailed regions:")
        for r in results:
            if not r.success:
                print(f"  • {r.region}: {r.error}")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if dry:
        logger.info("=== DRY RUN (no buckets will be created) ===")

    try:
        results = create_global_buckets(dry_run=dry)
    except EnvironmentError as e:
        logger.critical(str(e))
        sys.exit(1)

    _print_summary(results)
    sys.exit(0 if all(r.success for r in results) else 1)