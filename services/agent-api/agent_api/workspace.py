"""Workspace sync between agent containers and S3-compatible storage.

Runs `aws s3 sync` inside the container via `docker exec`.
Supports both S3/MinIO backends and local filesystem fallback.
"""

import asyncio
import logging
import shlex
from typing import Optional, Protocol

from agent_api import config

logger = logging.getLogger("agent_api.workspace")


class ExecProtocol(Protocol):
    """Protocol for executing commands in a container."""

    async def exec_simple(self, container: str, cmd: list[str]) -> Optional[str]: ...


# --- Container exec helper ---

async def _exec(container: str, cmd: str, timeout: int = 120) -> tuple[int, str]:
    """Run a shell command inside a container, return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", container, "bash", "-c", cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        proc.kill()
        return 1, "timeout"


# --- S3 helpers ---

def _s3_uri(user_id: str, workspace_name: str = "default") -> str:
    return f"s3://{config.S3_BUCKET}/workspaces/{user_id}/{workspace_name}/"


def _env_args() -> str:
    parts = []
    if config.S3_ENDPOINT:
        parts.append(f"--endpoint-url {config.S3_ENDPOINT}")
    return " ".join(parts)


_SYNC_EXCLUDES = (
    '--exclude ".claude/.session" '
    '--exclude ".claude/.chat-prompt.txt" '
    '--exclude ".claude/.agent-prompt.txt"'
)


# --- Sync operations ---

async def sync_down(user_id: str, container: str, workspace_name: str = "default") -> bool:
    """Download workspace from S3 into /workspace/ inside the container."""
    if config.STORAGE_BACKEND != "s3":
        logger.debug(f"Storage backend is {config.STORAGE_BACKEND}, skipping sync_down")
        return True

    s3_uri = _s3_uri(user_id, workspace_name)
    workspace = config.WORKSPACE_PATH
    cmd = f"aws s3 sync {s3_uri} {workspace}/ {_env_args()} {_SYNC_EXCLUDES} 2>&1"
    logger.info(f"Sync down: {s3_uri} -> {workspace}/ in {container}")
    rc, out = await _exec(container, cmd)
    if rc != 0:
        logger.error(f"Sync down FAILED for {user_id}/{workspace_name} (rc={rc}): {out}")
    return rc == 0


async def sync_up(user_id: str, container: str, workspace_name: str = "default") -> bool:
    """Git commit then upload workspace to S3."""
    if config.STORAGE_BACKEND != "s3":
        logger.debug(f"Storage backend is {config.STORAGE_BACKEND}, skipping sync_up")
        return True

    committed = await git_commit(user_id, container)
    if not committed:
        logger.warning(f"Git commit failed for {user_id}, proceeding with sync anyway")

    s3_uri = _s3_uri(user_id, workspace_name)
    workspace = config.WORKSPACE_PATH
    cmd = f"aws s3 sync {workspace}/ {s3_uri} {_env_args()} --delete {_SYNC_EXCLUDES} 2>&1"
    logger.info(f"Sync up: {workspace}/ in {container} -> {s3_uri}")
    rc, out = await _exec(container, cmd)
    if rc != 0:
        logger.error(f"Sync up FAILED for {user_id}/{workspace_name} (rc={rc}): {out}")
    return rc == 0


async def sync_up_s3_only(user_id: str, container: str, workspace_name: str = "default") -> bool:
    """Upload workspace to S3 WITHOUT git commit. Safety-net sync —
    catches changes even if the agent didn't explicitly save.
    The agent's `vexa workspace save` does git commit + S3 (sync_up).
    This only does S3."""
    if config.STORAGE_BACKEND != "s3":
        return True

    s3_uri = _s3_uri(user_id, workspace_name)
    ws = config.WORKSPACE_PATH
    cmd = f"aws s3 sync {ws}/ {s3_uri} {_env_args()} --delete {_SYNC_EXCLUDES} 2>&1"
    rc, out = await _exec(container, cmd)
    if rc != 0:
        logger.warning(f"Periodic S3 sync failed for {user_id}/{workspace_name} (rc={rc}): {out}")
    return rc == 0


async def git_commit(user_id: str, container: str) -> bool:
    """Git add + commit inside workspace. Returns True if commit was made."""
    workspace = config.WORKSPACE_PATH
    cmd = (
        f'cd {workspace} && '
        'git add -A && '
        'STATUS=$(git status --porcelain) && '
        'if [ -n "$STATUS" ]; then '
        '  STAMP=$(date -u +%Y-%m-%dT%H-%M-%S); '
        '  git commit -m "save $STAMP"; '
        'fi'
    )
    rc, out = await _exec(container, cmd, timeout=30)
    if rc != 0:
        logger.warning(f"Git commit issue for {user_id}: {out}")
        return False
    if "save" in out:
        logger.info(f"Git commit for {user_id}: {out.splitlines()[-1]}")
    return True


def _get_s3_client():
    """Get boto3 S3 client with configured credentials."""
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT or None,
        aws_access_key_id=config.S3_ACCESS_KEY or None,
        aws_secret_access_key=config.S3_SECRET_KEY or None,
        region_name="us-east-1",
    )


async def workspace_exists(user_id: str, workspace_name: str = "default") -> bool:
    """Check if a workspace prefix exists in S3."""
    if config.STORAGE_BACKEND != "s3":
        return False
    try:
        s3 = _get_s3_client()
        resp = s3.list_objects_v2(
            Bucket=config.S3_BUCKET,
            Prefix=f"workspaces/{user_id}/{workspace_name}/",
            MaxKeys=1,
        )
        return resp.get("KeyCount", 0) > 0
    except Exception as e:
        logger.warning(f"workspace_exists check failed: {e}")
        return False


# --- Legacy migration ---


async def migrate_legacy_workspaces():
    """One-time migration: move workspaces/{uid}/ files to workspaces/{uid}/default/.

    Old format: files directly under workspaces/{uid}/
    New format: files under workspaces/{uid}/{workspace_name}/
    """
    if config.STORAGE_BACKEND != "s3":
        return
    s3 = _get_s3_client()
    # List top-level prefixes under workspaces/
    resp = s3.list_objects_v2(Bucket=config.S3_BUCKET, Prefix="workspaces/", Delimiter="/")
    for cp in resp.get("CommonPrefixes", []):
        user_prefix = cp["Prefix"]  # "workspaces/{uid}/"
        # Check if this has files directly (old format) vs subdirectories (new format)
        files_resp = s3.list_objects_v2(
            Bucket=config.S3_BUCKET, Prefix=user_prefix, Delimiter="/", MaxKeys=5,
        )
        direct_files = files_resp.get("Contents", [])
        if direct_files:
            # Has files directly under user_id — old format, needs migration
            uid = user_prefix.rstrip("/").split("/")[-1]
            logger.info(f"Migrating legacy workspace for user {uid}")
            all_resp = s3.list_objects_v2(
                Bucket=config.S3_BUCKET, Prefix=user_prefix, MaxKeys=1000,
            )
            for obj in all_resp.get("Contents", []):
                old_key = obj["Key"]
                rel = old_key[len(user_prefix):]
                # Skip if already in a subfolder (new format coexists)
                if "/" in rel and rel.split("/")[0] == "default":
                    continue
                new_key = f"{user_prefix}default/{rel}"
                s3.copy_object(
                    Bucket=config.S3_BUCKET,
                    CopySource={"Bucket": config.S3_BUCKET, "Key": old_key},
                    Key=new_key,
                )
                s3.delete_object(Bucket=config.S3_BUCKET, Key=old_key)
            logger.info(f"Migrated workspace for user {uid} to default/")


# --- Workspace template operations (S3, pre-container) ---


async def upload_workspace(user_id: str, name: str, tar_bytes: bytes) -> dict:
    """Extract tar.gz and upload files to S3 workspace prefix."""
    import io
    import tarfile
    s3 = _get_s3_client()
    prefix = f"workspaces/{user_id}/{name}/"

    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        file_count = 0
        for member in tar.getmembers():
            if member.isfile():
                f = tar.extractfile(member)
                if f:
                    key = prefix + member.name.lstrip("./")
                    s3.put_object(Bucket=config.S3_BUCKET, Key=key, Body=f.read())
                    file_count += 1
    return {"name": name, "file_count": file_count}


async def list_workspaces(user_id: str) -> list[dict]:
    """List workspace names for a user from S3 prefixes."""
    s3 = _get_s3_client()
    prefix = f"workspaces/{user_id}/"
    resp = s3.list_objects_v2(Bucket=config.S3_BUCKET, Prefix=prefix, Delimiter="/")
    workspaces = []
    for cp in resp.get("CommonPrefixes", []):
        name = cp["Prefix"].rstrip("/").split("/")[-1]
        workspaces.append({"name": name})
    return workspaces


async def delete_workspace(user_id: str, name: str) -> bool:
    """Delete all objects under a workspace prefix."""
    s3 = _get_s3_client()
    prefix = f"workspaces/{user_id}/{name}/"
    resp = s3.list_objects_v2(Bucket=config.S3_BUCKET, Prefix=prefix)
    objects = resp.get("Contents", [])
    if objects:
        s3.delete_objects(
            Bucket=config.S3_BUCKET,
            Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
        )
    return True


async def list_workspace_files_s3(user_id: str, name: str) -> list[str]:
    """List files in a specific workspace from S3."""
    s3 = _get_s3_client()
    prefix = f"workspaces/{user_id}/{name}/"
    resp = s3.list_objects_v2(Bucket=config.S3_BUCKET, Prefix=prefix)
    files = []
    for obj in resp.get("Contents", []):
        rel = obj["Key"][len(prefix):]
        if rel:
            files.append(rel)
    return files


async def write_workspace_file_s3(user_id: str, name: str, path: str, content: str) -> bool:
    """Write a single file to a workspace in S3."""
    s3 = _get_s3_client()
    key = f"workspaces/{user_id}/{name}/{path}"
    s3.put_object(Bucket=config.S3_BUCKET, Key=key, Body=content.encode())
    return True


# --- Workspace init operations ---


async def is_workspace_empty(container: str) -> bool:
    """Check if /workspace/ is empty (no user files, ignoring .git)."""
    workspace = config.WORKSPACE_PATH
    rc, out = await _exec(
        container,
        f"find {workspace} -not -path '*/.git/*' -not -name '.git' "
        f"-not -name '.gitkeep' -type f | head -1",
        timeout=10,
    )
    return rc != 0 or not out.strip()


async def init_from_template(container: str, template: str = "knowledge") -> bool:
    """Copy template files into an empty workspace."""
    workspace = config.WORKSPACE_PATH
    template_path = f"/templates/{template}"
    # Check template exists in the container image
    rc, _ = await _exec(container, f"test -d {template_path} && echo OK", timeout=5)
    if rc != 0:
        logger.warning(f"Template {template} not found at {template_path}")
        return False

    cmd = f"cp -r {template_path}/. {workspace}/"
    rc, out = await _exec(container, cmd, timeout=30)
    if rc != 0:
        logger.error(f"Template init failed: {out}")
        return False

    # Init git repo in workspace
    git_cmd = (
        f"cd {workspace} && "
        "git init && "
        'git config user.name "vexa" && '
        'git config user.email "vexa@system" && '
        "git add -A && "
        'git commit -m "init from template"'
    )
    rc, out = await _exec(container, git_cmd, timeout=30)
    if rc != 0:
        logger.warning(f"Git init after template copy: {out}")

    logger.info(f"Template '{template}' initialized in {container}")
    return True


async def git_clone_init(container: str, repo_url: str, branch: str = "main",
                         token: str = "") -> bool:
    """Clone a git repo into an empty workspace."""
    workspace = config.WORKSPACE_PATH

    # Validate URL scheme — only https:// allowed
    if not repo_url.startswith("https://"):
        logger.error(f"Rejected git clone URL with non-https scheme: {repo_url[:50]}")
        return False

    # Build clone URL with token if provided
    if token and "://" in repo_url:
        # Insert token into URL: https://github.com/... → https://{token}@github.com/...
        proto, rest = repo_url.split("://", 1)
        clone_url = f"{proto}://{token}@{rest}"
    else:
        clone_url = repo_url

    # Clone to temp dir, move contents into workspace (which may already exist as a mountpoint)
    safe_branch = shlex.quote(branch)
    safe_url = shlex.quote(clone_url)
    cmd = (
        f"git clone --branch {safe_branch} --single-branch {safe_url} /tmp/_ws_clone && "
        f"cp -a /tmp/_ws_clone/. {workspace}/ && "
        f"rm -rf /tmp/_ws_clone"
    )
    rc, out = await _exec(container, cmd, timeout=120)
    if rc != 0:
        logger.error(f"Git clone failed for {repo_url}: {out}")
        return False

    # Strip token from remote URL to prevent leaking to workspace/MinIO
    safe_repo = shlex.quote(repo_url)
    await _exec(container, f"cd {workspace} && git remote set-url origin {safe_repo}", timeout=10)

    logger.info(f"Git cloned {repo_url} ({branch}) into {container}")
    return True


# --- File operations for REST API ---

async def sync_to_container(container: str, workspace_path: str,
                            files: dict[str, str]) -> bool:
    """Push files into a container's workspace.

    Args:
        container: Container name.
        workspace_path: Path inside container (e.g. /workspace).
        files: Dict of {relative_path: content}.

    Returns:
        True if all files were written successfully.
    """
    import base64
    import os.path

    for rel_path, content in files.items():
        full_path = f"{workspace_path}/{rel_path}"
        parent = os.path.dirname(full_path)
        if parent:
            await _exec(container, f"mkdir -p {parent}")
        encoded = base64.b64encode(content.encode()).decode()
        rc, _ = await _exec(container, f"echo '{encoded}' | base64 -d > {full_path}")
        if rc != 0:
            logger.error(f"Failed to write {rel_path} to {container}")
            return False
    return True


async def sync_from_container(container: str, workspace_path: str) -> dict[str, str]:
    """Read workspace files from a container.

    Returns:
        Dict of {relative_path: content} for all non-git files.
    """
    rc, listing = await _exec(
        container,
        f"find {workspace_path} -not -path '*/.git/*' -not -name '.git' "
        f"-not -name '.gitkeep' -type f",
    )
    if rc != 0 or not listing:
        return {}

    files = {}
    for filepath in listing.strip().split("\n"):
        filepath = filepath.strip()
        if not filepath:
            continue
        rc, content = await _exec(container, f"cat '{filepath}'")
        if rc == 0:
            rel = filepath.replace(f"{workspace_path}/", "", 1)
            files[rel] = content
    return files
