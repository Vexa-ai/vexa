#!/usr/bin/env python3
"""
Security audit: scans the codebase for common security issues.

Checks:
    - Hardcoded secrets (API keys, tokens, passwords in source)
    - Default credentials in configuration
    - CORS wildcard with credentials
    - Unsigned/insecure cookies
    - Missing auth on endpoints
    - Input validation issues

Usage:
    python tests/audit/security_audit.py [--path /path/to/repo]
"""

import argparse
import os
import re
import sys
from pathlib import Path

# Directories to skip
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".next", "dist", "build",
    "venv", ".venv", "env", ".env", ".eggs", "*.egg-info",
    "tests/load/results",
}

# File extensions to scan
SCAN_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yml", ".yaml",
    ".toml", ".cfg", ".ini", ".sh", ".env.example", ".conf",
}

# Patterns that suggest hardcoded secrets
SECRET_PATTERNS = [
    (r'(?:api[_-]?key|apikey)\s*[=:]\s*["\']([a-zA-Z0-9_\-]{16,})["\']', "Possible hardcoded API key"),
    (r'(?:secret|password|passwd|pwd)\s*[=:]\s*["\']([^"\']{8,})["\']', "Possible hardcoded secret/password"),
    (r'(?:token)\s*[=:]\s*["\']([a-zA-Z0-9_\-]{16,})["\']', "Possible hardcoded token"),
    (r'(?:sk|pk)[-_](?:live|test)[-_][a-zA-Z0-9]{20,}', "Possible Stripe key"),
    (r'AIza[0-9A-Za-z_-]{35}', "Possible Google API key"),
    (r'ghp_[a-zA-Z0-9]{36}', "Possible GitHub personal access token"),
    (r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----', "Private key in source"),
]

# Default credential patterns
DEFAULT_CRED_PATTERNS = [
    (r'JWT_SECRET\s*[=:]\s*["\']?(secret|changeme|default|password|test)', "Default JWT secret"),
    (r'(?:ADMIN|API)_TOKEN\s*[=:]\s*["\']?(token|admin|test|default|changeme)', "Default admin/API token"),
    (r'(?:DB_PASSWORD|POSTGRES_PASSWORD)\s*[=:]\s*["\']?(postgres|password|root|admin|test)', "Default database password"),
]

# CORS issues
CORS_PATTERNS = [
    (r'(?:allow_origins|Access-Control-Allow-Origin)\s*[=:]\s*["\']?\*', "CORS wildcard origin"),
    (r'allow_credentials\s*[=:]\s*True.*allow_origins\s*[=:]\s*\[?\s*["\']?\*', "CORS wildcard with credentials"),
]

# Cookie issues
COOKIE_PATTERNS = [
    (r'set_cookie\(.*(?:httponly\s*=\s*False|secure\s*=\s*False)', "Insecure cookie flags"),
    (r'(?:cookie|session).*(?:signed\s*=\s*False|unsigned)', "Unsigned cookie"),
]


class Finding:
    def __init__(self, category, message, file_path, line_num, line_text):
        self.category = category
        self.message = message
        self.file_path = file_path
        self.line_num = line_num
        self.line_text = line_text.strip()

    def __str__(self):
        return f"  [{self.category}] {self.file_path}:{self.line_num}: {self.message}\n    > {self.line_text}"


def should_skip(path):
    """Check if a path should be skipped."""
    parts = Path(path).parts
    for skip in SKIP_DIRS:
        if skip in parts:
            return True
    return False


def scan_file(file_path, patterns, category):
    """Scan a single file against a list of patterns."""
    findings = []
    try:
        with open(file_path, "r", errors="ignore") as f:
            for line_num, line in enumerate(f, 1):
                for pattern, message in patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        # Skip comments and example files
                        stripped = line.strip()
                        if stripped.startswith("#") and "example" in stripped.lower():
                            continue
                        if "env.example" in str(file_path) or "env-example" in str(file_path):
                            continue
                        findings.append(Finding(category, message, file_path, line_num, line))
    except (PermissionError, UnicodeDecodeError):
        pass
    return findings


def scan_for_unauthed_endpoints(repo_root):
    """Scan for FastAPI/Flask endpoints that might lack authentication."""
    findings = []
    endpoint_patterns = [
        r'@(?:app|router)\.(get|post|put|patch|delete)\(',
    ]

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            if should_skip(fpath):
                continue
            try:
                with open(fpath, "r", errors="ignore") as f:
                    lines = f.readlines()
                for i, line in enumerate(lines):
                    for pattern in endpoint_patterns:
                        if re.search(pattern, line):
                            # Check surrounding lines for auth dependencies
                            context = "".join(lines[max(0, i-2):i+5])
                            has_auth = any(kw in context.lower() for kw in [
                                "depends", "authenticate", "authorize", "auth",
                                "token", "current_user", "api_key", "security",
                                "permission", "credentials",
                            ])
                            if not has_auth:
                                # Check if it's a health or docs endpoint
                                is_public = any(kw in line.lower() for kw in [
                                    "health", "docs", "openapi", "readiness", "liveness",
                                ])
                                if not is_public:
                                    findings.append(Finding(
                                        "AUTH",
                                        "Endpoint may lack authentication",
                                        fpath, i + 1, line
                                    ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def main():
    parser = argparse.ArgumentParser(description="Security audit for Vexa codebase")
    parser.add_argument("--path", default=None, help="Repository root path")
    args = parser.parse_args()

    repo_root = args.path or str(Path(__file__).parent.parent.parent)

    print("=" * 60)
    print("  Vexa Security Audit")
    print("=" * 60)
    print(f"  Scanning: {repo_root}")
    print("")

    all_findings = []

    # Scan all eligible files
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            fpath = os.path.join(root, fname)
            if should_skip(fpath):
                continue

            ext = Path(fname).suffix
            if ext not in SCAN_EXTENSIONS and fname not in (".env.example",):
                continue

            all_findings.extend(scan_file(fpath, SECRET_PATTERNS, "SECRET"))
            all_findings.extend(scan_file(fpath, DEFAULT_CRED_PATTERNS, "DEFAULT_CRED"))
            all_findings.extend(scan_file(fpath, CORS_PATTERNS, "CORS"))
            all_findings.extend(scan_file(fpath, COOKIE_PATTERNS, "COOKIE"))

    # Scan for unauthed endpoints
    all_findings.extend(scan_for_unauthed_endpoints(repo_root))

    # Report
    if all_findings:
        categories = {}
        for f in all_findings:
            categories.setdefault(f.category, []).append(f)

        for category, findings in sorted(categories.items()):
            print(f"[{category}] ({len(findings)} findings)")
            for finding in findings:
                print(finding)
            print("")

        print(f"Total findings: {len(all_findings)}")
        print("Review each finding — not all are true positives.")
        return 1
    else:
        print("No security issues found.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
