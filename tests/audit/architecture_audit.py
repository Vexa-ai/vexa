#!/usr/bin/env python3
"""
Architecture compliance audit: enforces design principles and guardrails.

Checks:
    - No billing tables (Stripe is source of truth)
    - Stateless services (no local file writes for state)
    - Token scope enforcement
    - Durable delivery patterns (retry paths for external delivery)
    - Self-hostable (no hardcoded external URLs)
    - No Alembic migrations or schema changes (stable schema)

Usage:
    python tests/audit/architecture_audit.py [--path /path/to/repo]
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".next", "dist", "build",
    "venv", ".venv", ".eggs", "tests",
}


class Finding:
    def __init__(self, category, severity, message, file_path=None, line_num=None, line_text=None):
        self.category = category
        self.severity = severity
        self.message = message
        self.file_path = file_path
        self.line_num = line_num
        self.line_text = (line_text or "").strip()

    def __str__(self):
        loc = ""
        if self.file_path:
            loc = f" {self.file_path}"
            if self.line_num:
                loc += f":{self.line_num}"
        detail = f"\n    > {self.line_text}" if self.line_text else ""
        return f"  [{self.severity}]{loc}: {self.message}{detail}"


def check_no_billing_tables(repo_root):
    """Verify no billing/payment tables are created in the schema."""
    findings = []
    billing_patterns = [
        (r'CREATE\s+TABLE\s+\w*(?:billing|payment|invoice|subscription|plan|price)', "Billing table creation"),
        (r'class\s+\w*(?:Billing|Payment|Invoice|Subscription|Plan|Price)\s*\(.*Base\)', "Billing SQLAlchemy model"),
    ]

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not any(fname.endswith(ext) for ext in (".py", ".sql")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        for pattern, message in billing_patterns:
                            if re.search(pattern, line, re.IGNORECASE):
                                findings.append(Finding(
                                    "NO_BILLING", "ERROR", message,
                                    fpath, line_num, line
                                ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def check_no_new_migrations(repo_root):
    """Verify no new Alembic migrations or schema-altering SQL."""
    findings = []

    # Check for new migration files (warn about their existence)
    alembic_dirs = []
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        if "versions" in dirs and "alembic" in root.lower():
            alembic_dirs.append(os.path.join(root, "versions"))
        # Also check for versions dirs next to alembic.ini
        if "alembic.ini" in files:
            alembic_dir = os.path.join(root, "alembic", "versions")
            if os.path.isdir(alembic_dir):
                alembic_dirs.append(alembic_dir)

    # Check for ALTER TABLE / CREATE TABLE in non-migration files
    schema_patterns = [
        (r'ALTER\s+TABLE', "ALTER TABLE statement"),
        (r'CREATE\s+TABLE', "CREATE TABLE statement"),
        (r'DROP\s+TABLE', "DROP TABLE statement"),
        (r'ADD\s+COLUMN', "ADD COLUMN statement"),
        (r'DROP\s+COLUMN', "DROP COLUMN statement"),
    ]

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != "alembic"]
        for fname in files:
            if not any(fname.endswith(ext) for ext in (".py", ".sql")):
                continue
            # Skip alembic migration files
            if "alembic" in root or "versions" in root:
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        # Skip comments
                        stripped = line.strip()
                        if stripped.startswith("#") or stripped.startswith("--"):
                            continue
                        for pattern, message in schema_patterns:
                            if re.search(pattern, line, re.IGNORECASE):
                                # Skip if it's in a string that's clearly a check/audit
                                if "audit" in fpath.lower() or "test" in fpath.lower():
                                    continue
                                findings.append(Finding(
                                    "STABLE_SCHEMA", "WARN", message,
                                    fpath, line_num, line
                                ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def check_stateless_services(repo_root):
    """Verify services don't write local file state."""
    findings = []
    stateful_patterns = [
        (r'open\([^)]*["\']w["\']', "File write operation"),
        (r'\.write\(', "File write call"),
        (r'pickle\.dump\(', "Pickle dump (local state)"),
        (r'shelve\.open\(', "Shelve open (local state)"),
        (r'sqlite3\.connect\(', "SQLite connection (local database)"),
    ]

    services_dir = os.path.join(repo_root, "services")
    if not os.path.isdir(services_dir):
        return findings

    for root, dirs, files in os.walk(services_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        # Skip logging and test files
                        if "log" in line.lower() and "write" in line.lower():
                            continue
                        if "test" in fname.lower():
                            continue
                        for pattern, message in stateful_patterns:
                            if re.search(pattern, line):
                                # Filter out logging, temporary files, stdout
                                if any(kw in line.lower() for kw in [
                                    "log", "stdout", "stderr", "tempfile",
                                    "temporary", "/tmp", "stringio",
                                ]):
                                    continue
                                findings.append(Finding(
                                    "STATELESS", "WARN",
                                    f"Possible local state: {message}",
                                    fpath, line_num, line
                                ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def check_self_hostable(repo_root):
    """Verify no hardcoded external URLs that break self-hosting."""
    findings = []
    # Hosted service URLs that should be configurable
    hosted_patterns = [
        (r'https?://[a-z]+\.vexa\.ai', "Hardcoded vexa.ai URL"),
        (r'https?://[a-z]+\.vexa\.com', "Hardcoded vexa.com URL"),
    ]

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not any(fname.endswith(ext) for ext in (".py", ".js", ".ts", ".tsx", ".yml", ".yaml")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        # Skip comments and env-example files
                        stripped = line.strip()
                        if stripped.startswith("#") or stripped.startswith("//"):
                            continue
                        if "env-example" in fpath or "env.example" in fpath:
                            continue
                        for pattern, message in hosted_patterns:
                            if re.search(pattern, line, re.IGNORECASE):
                                # Check if it's a fallback/default (less severe) vs hardcoded (more severe)
                                if "environ" in line or "getenv" in line or "process.env" in line:
                                    severity = "INFO"
                                    message += " (used as default/fallback)"
                                else:
                                    severity = "WARN"
                                findings.append(Finding(
                                    "SELF_HOSTABLE", severity, message,
                                    fpath, line_num, line
                                ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def check_token_scoping(repo_root):
    """Verify token scope prefixes are used consistently."""
    findings = []
    # Look for token validation that doesn't check scope prefix
    token_check_patterns = [
        r'(?:validate|verify|check).*token',
        r'authorization.*bearer',
        r'api[_-]?key.*validate',
    ]

    services_dir = os.path.join(repo_root, "services")
    if not os.path.isdir(services_dir):
        return findings

    for root, dirs, files in os.walk(services_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read()

                # If file handles tokens, check for scope prefix validation
                has_token_handling = any(
                    re.search(p, content, re.IGNORECASE) for p in token_check_patterns
                )
                if has_token_handling:
                    has_scope_check = any(kw in content for kw in [
                        "vxa_bot_", "vxa_tx_", "vxa_user_",
                        "token_scope", "scope_prefix", "startswith",
                    ])
                    if not has_scope_check:
                        findings.append(Finding(
                            "TOKEN_SCOPE", "INFO",
                            "Token handling without visible scope prefix check",
                            fpath
                        ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def check_durable_delivery(repo_root):
    """Check that external delivery (webhooks, notifications) has retry paths."""
    findings = []

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read()
                    lines = content.split("\n")

                # Check for webhook/notification sends without retry logic
                for line_num, line in enumerate(lines, 1):
                    if re.search(r'(?:webhook|notify|deliver|dispatch)', line, re.IGNORECASE):
                        if re.search(r'(?:requests\.post|httpx\.post|aiohttp)', line):
                            # Check surrounding context for retry logic
                            start = max(0, line_num - 10)
                            end = min(len(lines), line_num + 10)
                            context = "\n".join(lines[start:end])
                            has_retry = any(kw in context.lower() for kw in [
                                "retry", "queue", "backoff", "attempt",
                                "celery", "rq", "redis", "enqueue",
                            ])
                            if not has_retry:
                                findings.append(Finding(
                                    "DURABLE_DELIVERY", "WARN",
                                    "External delivery without visible retry path",
                                    fpath, line_num, line
                                ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def check_abandoned_directories(repo_root):
    """Scan for directories with no recent git activity and not in the build system."""
    findings = []

    # Collect build-system references
    build_content = ""
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if fname in ("Makefile", "docker-compose.yml", "docker-compose.yaml",
                         "docker-compose.override.yml") or fname.startswith("Dockerfile"):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        build_content += f.read() + "\n"
                except (PermissionError, OSError):
                    pass

    # Check top-level and service-level directories
    for top_entry in sorted(os.listdir(repo_root)):
        top_path = os.path.join(repo_root, top_entry)
        if not os.path.isdir(top_path) or top_entry in SKIP_DIRS or top_entry.startswith("."):
            continue

        # Check if directory name appears in any build file
        in_build = top_entry in build_content

        # Check latest git activity
        try:
            result = subprocess.check_output(
                ["git", "log", "-1", "--format=%aI", "--", top_entry + "/"],
                cwd=repo_root, stderr=subprocess.DEVNULL, text=True,
            ).strip()
            if result:
                from datetime import datetime, timezone
                last_mod = datetime.fromisoformat(result)
                age_days = (datetime.now(timezone.utc) - last_mod).days
                if age_days > 180 and not in_build:
                    findings.append(Finding(
                        "ABANDONED_DIR", "WARN",
                        f"Directory '{top_entry}/' not in build system and last git activity {age_days} days ago",
                        top_entry + "/"
                    ))
        except (subprocess.SubprocessError, ValueError):
            pass

    return findings


def check_standalone_scripts(repo_root):
    """Find Python files with if __name__ == '__main__' not referenced in any Makefile/script."""
    findings = []

    # Collect Makefile, shell script, and CI content
    script_content = ""
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if fname in ("Makefile",) or fname.endswith((".sh", ".yml", ".yaml")):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", errors="ignore") as f:
                        script_content += f.read() + "\n"
                except (PermissionError, OSError):
                    pass

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, repo_root)
            # Skip test files and audit scripts
            if "test" in rel_path.lower() or "audit" in rel_path.lower():
                continue
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read()
                if '__name__' in content and "'__main__'" in content or '__name__' in content and '"__main__"' in content:
                    # Check if this file is referenced in any build/script file
                    if fname not in script_content and rel_path not in script_content:
                        findings.append(Finding(
                            "STANDALONE_SCRIPT", "INFO",
                            f"Standalone script not referenced in Makefile or shell scripts",
                            fpath
                        ))
            except (PermissionError, UnicodeDecodeError):
                pass

    return findings


def check_duplicate_functions(repo_root):
    """Find functions with the same name defined in multiple files."""
    findings = []
    func_locations = {}  # name -> [file_paths]

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, repo_root)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    for line in f:
                        m = re.match(r'^def\s+([A-Za-z_]\w+)\s*\(', line)
                        if m:
                            name = m.group(1)
                            # Skip private, test, magic, and trivial names
                            if name.startswith("_") or name.startswith("test"):
                                continue
                            if name in ("setup", "main", "run", "start", "init",
                                        "get", "set", "create", "delete", "update",
                                        "handle", "process"):
                                continue
                            func_locations.setdefault(name, []).append(rel_path)
            except (PermissionError, UnicodeDecodeError):
                pass

    for name, locations in func_locations.items():
        if len(locations) > 1:
            findings.append(Finding(
                "DUPLICATE_FUNC", "INFO",
                f"Function '{name}' defined in {len(locations)} files: {', '.join(locations[:5])}",
            ))

    return findings


def main():
    parser = argparse.ArgumentParser(description="Architecture compliance audit")
    parser.add_argument("--path", default=None, help="Repository root path")
    args = parser.parse_args()

    repo_root = args.path or str(Path(__file__).parent.parent.parent)

    print("=" * 60)
    print("  Vexa Architecture Compliance Audit")
    print("=" * 60)
    print(f"  Scanning: {repo_root}")
    print("")

    all_findings = []

    checks = [
        ("No billing tables", check_no_billing_tables),
        ("Stable schema (no migrations)", check_no_new_migrations),
        ("Stateless services", check_stateless_services),
        ("Self-hostable (no hardcoded URLs)", check_self_hostable),
        ("Token scope enforcement", check_token_scoping),
        ("Durable delivery", check_durable_delivery),
        ("Abandoned directories", check_abandoned_directories),
        ("Standalone scripts", check_standalone_scripts),
        ("Duplicate functions", check_duplicate_functions),
    ]

    for name, check_fn in checks:
        print(f"[Checking: {name}...]")
        findings = check_fn(repo_root)
        all_findings.extend(findings)
        if findings:
            for f in findings:
                print(f)
        else:
            print("  OK")
        print("")

    # Summary
    by_severity = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for f in all_findings:
        by_severity[f.severity] = by_severity.get(f.severity, 0) + 1

    print("=" * 60)
    print(f"  Summary: {by_severity['ERROR']} errors, {by_severity['WARN']} warnings, {by_severity['INFO']} info")
    print("=" * 60)

    if by_severity["ERROR"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
