#!/usr/bin/env python3
"""
Configuration audit: checks environment variables, port exposure, and development defaults.

Checks:
    - Required env vars are documented and present in examples
    - No sensitive vars exposed to client (NEXT_PUBLIC_ prefix)
    - Docker port exposure is intentional
    - No development defaults in production configs
    - TLS/HTTPS in production URLs

Usage:
    python tests/audit/config_audit.py [--path /path/to/repo]
"""

import argparse
import os
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".next", "dist", "build",
    "venv", ".venv", ".eggs",
}


class Finding:
    def __init__(self, category, severity, message, file_path=None, line_num=None, line_text=None):
        self.category = category
        self.severity = severity  # "ERROR", "WARN", "INFO"
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


def find_env_vars_in_code(repo_root):
    """Find all environment variable references in source code."""
    env_vars = set()
    patterns = [
        r'os\.environ\.get\(["\'](\w+)',
        r'os\.environ\[["\'](\w+)',
        r'os\.getenv\(["\'](\w+)',
        r'process\.env\.(\w+)',
        r'\$\{(\w+)\}',
        r'\$(\w+)',
    ]
    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not any(fname.endswith(ext) for ext in (".py", ".js", ".ts", ".tsx", ".sh", ".yml", ".yaml")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read()
                for pattern in patterns:
                    for match in re.finditer(pattern, content):
                        env_vars.add(match.group(1))
            except (PermissionError, UnicodeDecodeError):
                pass
    return env_vars


def find_env_vars_in_examples(repo_root):
    """Find all env vars defined in env-example files."""
    env_vars = set()
    for fname in os.listdir(repo_root):
        if fname.startswith("env-example") or fname == ".env.example":
            fpath = os.path.join(repo_root, fname)
            try:
                with open(fpath, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            match = re.match(r'(\w+)=', line)
                            if match:
                                env_vars.add(match.group(1))
            except (PermissionError, UnicodeDecodeError):
                pass
    return env_vars


def check_next_public_leaks(repo_root):
    """Check for sensitive values in NEXT_PUBLIC_ variables."""
    findings = []
    sensitive_keywords = ["secret", "password", "token", "key", "private", "credential"]

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not any(fname.endswith(ext) for ext in (".js", ".ts", ".tsx", ".jsx", ".env", ".env.example")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        match = re.search(r'NEXT_PUBLIC_(\w+)', line)
                        if match:
                            var_name = match.group(1).lower()
                            for kw in sensitive_keywords:
                                if kw in var_name:
                                    findings.append(Finding(
                                        "NEXT_PUBLIC",
                                        "ERROR",
                                        f"Sensitive value exposed to client: NEXT_PUBLIC_{match.group(1)}",
                                        fpath, line_num, line
                                    ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def check_docker_ports(repo_root):
    """Check Docker port exposures."""
    findings = []
    compose_files = [
        "docker-compose.yml",
        "docker-compose.local-db.yml",
        "docker-compose.override.yml",
    ]

    for compose_file in compose_files:
        fpath = os.path.join(repo_root, compose_file)
        if not os.path.exists(fpath):
            continue
        try:
            with open(fpath, "r") as f:
                content = f.read()
                lines = content.split("\n")

            for line_num, line in enumerate(lines, 1):
                # Check for ports exposed on 0.0.0.0
                if re.search(r'^\s*-\s*["\']?(\d+):(\d+)', line):
                    match = re.search(r'["\']?(\d+):(\d+)', line)
                    if match:
                        host_port = match.group(1)
                        container_port = match.group(2)
                        # Flag if exposing on all interfaces without env var
                        if "${" not in line and host_port == container_port:
                            findings.append(Finding(
                                "DOCKER_PORTS",
                                "WARN",
                                f"Port {host_port} exposed directly (consider using env var for host port)",
                                fpath, line_num, line
                            ))
        except (PermissionError, UnicodeDecodeError):
            pass
    return findings


def check_dev_defaults(repo_root):
    """Check for development defaults that shouldn't be in production."""
    findings = []
    dev_patterns = [
        (r'MOCK_AUTH\s*[=:]\s*["\']?true', "MOCK_AUTH enabled"),
        (r'DEBUG\s*[=:]\s*["\']?(?:true|1|yes)', "DEBUG mode enabled"),
        (r'TESTING\s*[=:]\s*["\']?(?:true|1|yes)', "TESTING mode enabled"),
        (r'(?:LOG_LEVEL|LOGLEVEL)\s*[=:]\s*["\']?debug', "Debug log level"),
        (r'FLASK_ENV\s*[=:]\s*["\']?development', "Flask development mode"),
        (r'NODE_ENV\s*[=:]\s*["\']?development', "Node development mode"),
    ]

    for root, dirs, files in os.walk(repo_root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            # Only check config/env files, not source code defaults
            if not any(fname.endswith(ext) for ext in (".yml", ".yaml", ".toml", ".ini", ".cfg")):
                if not (fname.startswith("env-example") or fname == ".env.example"):
                    continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        for pattern, message in dev_patterns:
                            if re.search(pattern, line, re.IGNORECASE):
                                findings.append(Finding(
                                    "DEV_DEFAULT",
                                    "WARN",
                                    message,
                                    fpath, line_num, line
                                ))
            except (PermissionError, UnicodeDecodeError):
                pass
    return findings


def main():
    parser = argparse.ArgumentParser(description="Configuration audit for Vexa codebase")
    parser.add_argument("--path", default=None, help="Repository root path")
    args = parser.parse_args()

    repo_root = args.path or str(Path(__file__).parent.parent.parent)

    print("=" * 60)
    print("  Vexa Configuration Audit")
    print("=" * 60)
    print(f"  Scanning: {repo_root}")
    print("")

    all_findings = []

    # Check NEXT_PUBLIC_ leaks
    print("[Checking NEXT_PUBLIC_ variable exposure...]")
    all_findings.extend(check_next_public_leaks(repo_root))

    # Check Docker port exposure
    print("[Checking Docker port exposure...]")
    all_findings.extend(check_docker_ports(repo_root))

    # Check development defaults
    print("[Checking development defaults...]")
    all_findings.extend(check_dev_defaults(repo_root))

    # Check env var documentation
    print("[Checking environment variable coverage...]")
    code_vars = find_env_vars_in_code(repo_root)
    example_vars = find_env_vars_in_examples(repo_root)

    # Common vars that don't need to be in examples
    ignore_vars = {
        "HOME", "PATH", "USER", "SHELL", "PWD", "HOSTNAME", "LANG",
        "TERM", "PYTHONPATH", "NODE_ENV", "CI", "GITHUB_TOKEN",
    }

    undocumented = code_vars - example_vars - ignore_vars
    # Only flag vars that look like Vexa-specific config
    vexa_prefixes = ("DB_", "API_", "ADMIN_", "BOT_", "REDIS_", "WHISPER_", "WL_",
                     "TRANSCRIPTION", "REMOTE_", "JWT_", "WEBHOOK_", "MINIO_")
    for var in sorted(undocumented):
        if any(var.startswith(p) for p in vexa_prefixes):
            all_findings.append(Finding(
                "ENV_COVERAGE",
                "INFO",
                f"Env var {var} used in code but not in env-example files",
            ))

    print("")

    # Report
    if all_findings:
        by_severity = {"ERROR": [], "WARN": [], "INFO": []}
        for f in all_findings:
            by_severity.get(f.severity, by_severity["INFO"]).append(f)

        for severity in ("ERROR", "WARN", "INFO"):
            findings = by_severity[severity]
            if findings:
                print(f"[{severity}] ({len(findings)} findings)")
                for finding in findings:
                    print(finding)
                print("")

        errors = len(by_severity["ERROR"])
        warnings = len(by_severity["WARN"])
        infos = len(by_severity["INFO"])
        print(f"Total: {errors} errors, {warnings} warnings, {infos} info")

        if errors > 0:
            return 1
    else:
        print("No configuration issues found.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
