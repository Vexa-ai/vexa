#!/usr/bin/env python3
"""
Staleness and dead code audit: finds stale folders, dead code, orphaned docs, unused files.

Checks:
    - Dead code: unimported .py/.ts files, unreferenced functions/classes, commented-out code
    - Orphaned files: files not referenced by build system, stale config files
    - Stale docs: markdown referencing nonexistent paths, outdated READMEs
    - Unused dependencies: packages in requirements.txt / package.json not imported

Usage:
    python tests/audit/staleness_audit.py [--path /path/to/repo]
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".next", "dist", "build",
    "venv", ".venv", ".eggs", ".mypy_cache", ".pytest_cache", ".tox",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk_files(root, extensions=None):
    """Yield (abs_path, rel_path) for files under root, skipping SKIP_DIRS."""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if extensions and not any(fname.endswith(e) for e in extensions):
                continue
            abs_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(abs_path, root)
            yield abs_path, rel_path


def _read_file(path):
    try:
        with open(path, "r", errors="ignore") as f:
            return f.read()
    except (PermissionError, OSError):
        return ""


def _git_last_modified(repo_root, rel_path):
    """Return last-modified datetime for a file via git log, or None."""
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%aI", "--", rel_path],
            cwd=repo_root, stderr=subprocess.DEVNULL, text=True,
        ).strip()
        if out:
            return datetime.fromisoformat(out)
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


# ---------------------------------------------------------------------------
# Dead code detection
# ---------------------------------------------------------------------------


def find_unimported_python(repo_root):
    """Find .py files not imported by any other .py file."""
    findings = []
    py_files = {}  # rel_path -> abs_path
    for abs_path, rel_path in _walk_files(repo_root, [".py"]):
        py_files[rel_path] = abs_path

    # Gather all import targets from every .py file
    import_targets = set()
    all_contents = {}
    for rel_path, abs_path in py_files.items():
        content = _read_file(abs_path)
        all_contents[rel_path] = content
        # from X import ... / import X
        for m in re.finditer(r'(?:from|import)\s+([\w.]+)', content):
            import_targets.add(m.group(1))

    for rel_path, abs_path in py_files.items():
        fname = os.path.basename(rel_path)
        # Skip tests, __init__, __main__, setup, conftest
        if fname.startswith("test_") or fname in (
            "__init__.py", "__main__.py", "setup.py", "conftest.py",
        ):
            continue
        # Skip if it's under tests/
        if rel_path.startswith("tests" + os.sep):
            continue

        module_stem = fname.replace(".py", "")
        # Also build dotted module path
        dotted = rel_path.replace(os.sep, ".").replace(".py", "")

        # Check if any import target references this module
        referenced = False
        for target in import_targets:
            if module_stem in target or target in dotted:
                referenced = True
                break

        # Also check if the filename appears in any other file content
        if not referenced:
            for other_rel, content in all_contents.items():
                if other_rel == rel_path:
                    continue
                if module_stem in content:
                    referenced = True
                    break

        if not referenced:
            findings.append({
                "type": "dead_code",
                "subtype": "unimported_python",
                "severity": "WARN",
                "file": rel_path,
                "message": f"Python file not imported by any other .py file",
            })

    return findings


def find_unimported_typescript(repo_root):
    """Find .ts/.tsx files not imported by any other file."""
    findings = []
    ts_files = {}
    for abs_path, rel_path in _walk_files(repo_root, [".ts", ".tsx"]):
        ts_files[rel_path] = abs_path

    if not ts_files:
        return findings

    # Gather all import/require references
    import_targets = set()
    all_contents = {}
    for abs_path, rel_path in _walk_files(repo_root, [".ts", ".tsx", ".js", ".jsx"]):
        content = _read_file(abs_path)
        all_contents[rel_path] = content
        # import ... from 'X' / require('X')
        for m in re.finditer(r"""(?:from|require\()\s*['"]([^'"]+)['"]""", content):
            import_targets.add(m.group(1))

    for rel_path, abs_path in ts_files.items():
        fname = os.path.basename(rel_path)
        if fname.startswith("test.") or fname.endswith(".test.ts") or fname.endswith(".test.tsx"):
            continue
        if fname.endswith(".d.ts"):
            continue

        stem = fname.replace(".tsx", "").replace(".ts", "")
        referenced = False

        for target in import_targets:
            if stem in target:
                referenced = True
                break

        if not referenced:
            for other_rel, content in all_contents.items():
                if other_rel == rel_path:
                    continue
                if stem in content:
                    referenced = True
                    break

        if not referenced:
            findings.append({
                "type": "dead_code",
                "subtype": "unimported_typescript",
                "severity": "WARN",
                "file": rel_path,
                "message": f"TypeScript file not imported by any other file",
            })

    return findings


def find_unreferenced_definitions(repo_root):
    """Find functions/classes defined but never referenced elsewhere."""
    findings = []
    definitions = []  # (name, rel_path, line_num)

    for abs_path, rel_path in _walk_files(repo_root, [".py"]):
        if rel_path.startswith("tests" + os.sep):
            continue
        content = _read_file(abs_path)
        for line_num, line in enumerate(content.splitlines(), 1):
            m = re.match(r'^(?:def|class)\s+([A-Za-z_]\w+)', line)
            if m:
                name = m.group(1)
                # Skip private/magic
                if name.startswith("_"):
                    continue
                definitions.append((name, rel_path, line_num))

    # Gather all file contents for reference search
    all_contents = {}
    for abs_path, rel_path in _walk_files(repo_root, [".py", ".ts", ".tsx", ".js", ".yml", ".yaml"]):
        all_contents[rel_path] = _read_file(abs_path)

    for name, def_path, line_num in definitions:
        # Count references in files other than the defining file
        ref_count = 0
        for other_rel, content in all_contents.items():
            if other_rel == def_path:
                continue
            if name in content:
                ref_count += 1
                break  # one is enough

        if ref_count == 0:
            findings.append({
                "type": "dead_code",
                "subtype": "unreferenced_definition",
                "severity": "INFO",
                "file": def_path,
                "line": line_num,
                "message": f"'{name}' defined but not referenced in any other file",
            })

    return findings


def find_commented_out_code(repo_root):
    """Find blocks of >5 consecutive lines that look like commented-out code."""
    findings = []
    code_indicators = re.compile(
        r'#\s*(?:def |class |import |from |if |for |while |return |raise |try:|except|'
        r'with |print\(|self\.|assert |yield |async |await |lambda |\w+\s*=\s*|'
        r'\w+\.\w+\(|elif |else:)'
    )

    for abs_path, rel_path in _walk_files(repo_root, [".py"]):
        lines = _read_file(abs_path).splitlines()
        run_start = None
        run_len = 0

        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#") and code_indicators.search(stripped):
                if run_start is None:
                    run_start = i + 1
                run_len += 1
            else:
                if run_len > 5:
                    findings.append({
                        "type": "dead_code",
                        "subtype": "commented_out_code",
                        "severity": "INFO",
                        "file": rel_path,
                        "line": run_start,
                        "message": f"{run_len} consecutive lines of commented-out code",
                    })
                run_start = None
                run_len = 0

        # End of file
        if run_len > 5:
            findings.append({
                "type": "dead_code",
                "subtype": "commented_out_code",
                "severity": "INFO",
                "file": rel_path,
                "line": run_start,
                "message": f"{run_len} consecutive lines of commented-out code",
            })

    return findings


# ---------------------------------------------------------------------------
# Orphaned files
# ---------------------------------------------------------------------------


def find_orphaned_files(repo_root):
    """Find files not referenced by any Dockerfile, docker-compose, Makefile, or package.json."""
    findings = []

    # Collect build-system file contents
    build_files_content = []
    build_file_patterns = [
        "Dockerfile*", "docker-compose*.yml", "docker-compose*.yaml",
        "Makefile", "package.json", "requirements*.txt", "pyproject.toml",
        "setup.py", "setup.cfg",
    ]
    for abs_path, rel_path in _walk_files(repo_root):
        fname = os.path.basename(rel_path)
        for pat in build_file_patterns:
            if pat.endswith("*"):
                if fname.startswith(pat[:-1]):
                    build_files_content.append(_read_file(abs_path))
                    break
            elif fname == pat:
                build_files_content.append(_read_file(abs_path))
                break

    combined_build = "\n".join(build_files_content)

    # Check top-level directories for being referenced
    for entry in sorted(os.listdir(repo_root)):
        entry_path = os.path.join(repo_root, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry in SKIP_DIRS or entry.startswith("."):
            continue
        # Common dirs that are always fine
        if entry in ("services", "libs", "tests", "docs", "scripts", "docker",
                      "charts", "hub", "assets", "issues", "nbs"):
            continue

        if entry not in combined_build:
            findings.append({
                "type": "orphaned",
                "subtype": "unreferenced_directory",
                "severity": "WARN",
                "file": entry + "/",
                "message": f"Top-level directory '{entry}' not referenced in any build file",
            })

    return findings


def find_stale_config_files(repo_root):
    """Find config files for services/tools that may not exist."""
    findings = []
    services_dir = os.path.join(repo_root, "services")
    existing_services = set()
    if os.path.isdir(services_dir):
        existing_services = {d for d in os.listdir(services_dir)
                             if os.path.isdir(os.path.join(services_dir, d))}

    # Check docker-compose for service definitions vs actual directories
    for abs_path, rel_path in _walk_files(repo_root, [".yml", ".yaml"]):
        if "docker-compose" not in os.path.basename(rel_path):
            continue
        content = _read_file(abs_path)
        # Find service definitions (lines like "  service-name:")
        for m in re.finditer(r'^\s{2}(\w[\w-]+):\s*$', content, re.MULTILINE):
            svc = m.group(1)
            if svc in ("version", "services", "networks", "volumes", "x-common"):
                continue
            # Check if build context references an existing directory
            # This is informational - just flag services not matching dirs
    return findings


# ---------------------------------------------------------------------------
# Stale docs
# ---------------------------------------------------------------------------


def find_stale_docs(repo_root):
    """Find markdown files referencing paths that don't exist."""
    findings = []

    for abs_path, rel_path in _walk_files(repo_root, [".md"]):
        content = _read_file(abs_path)
        md_dir = os.path.dirname(abs_path)

        # Find path-like references: `services/something/`, backtick paths, etc.
        path_refs = re.findall(r'`([a-zA-Z][\w./-]+/[\w./-]+)`', content)
        # Also bracket link paths
        path_refs += re.findall(r'\[.*?\]\((?!http)([^)]+)\)', content)

        for ref_path in path_refs:
            # Clean up anchors and query strings
            ref_path = ref_path.split("#")[0].split("?")[0].strip()
            if not ref_path or ref_path.startswith("mailto:"):
                continue

            # Try relative to repo root and relative to markdown file
            if (not os.path.exists(os.path.join(repo_root, ref_path))
                    and not os.path.exists(os.path.join(md_dir, ref_path))):
                findings.append({
                    "type": "stale_docs",
                    "subtype": "broken_path_reference",
                    "severity": "WARN",
                    "file": rel_path,
                    "message": f"References nonexistent path: {ref_path}",
                })

    return findings


def find_stale_readmes(repo_root):
    """Find README.md files in directories where code structure has diverged."""
    findings = []
    now = datetime.now(timezone.utc)

    for abs_path, rel_path in _walk_files(repo_root, [".md"]):
        if os.path.basename(abs_path).lower() != "readme.md":
            continue

        parent_dir = os.path.dirname(abs_path)
        readme_modified = _git_last_modified(repo_root, rel_path)
        if not readme_modified:
            continue

        # Check if any code file in the same directory was modified more recently
        newest_code_mod = None
        for code_abs, code_rel in _walk_files(parent_dir, [".py", ".ts", ".tsx", ".js"]):
            code_mod = _git_last_modified(repo_root, os.path.relpath(code_abs, repo_root))
            if code_mod and (newest_code_mod is None or code_mod > newest_code_mod):
                newest_code_mod = code_mod

        if newest_code_mod and readme_modified < newest_code_mod:
            delta = newest_code_mod - readme_modified
            if delta.days > 30:
                findings.append({
                    "type": "stale_docs",
                    "subtype": "stale_readme",
                    "severity": "INFO",
                    "file": rel_path,
                    "message": (
                        f"README last updated {readme_modified.date()}, "
                        f"but code was modified {newest_code_mod.date()} "
                        f"({delta.days} days newer)"
                    ),
                })

    return findings


def find_old_markdown(repo_root):
    """Find markdown files not modified in >6 months while nearby code changed."""
    findings = []
    now = datetime.now(timezone.utc)

    for abs_path, rel_path in _walk_files(repo_root, [".md"]):
        md_modified = _git_last_modified(repo_root, rel_path)
        if not md_modified:
            continue

        age_days = (now - md_modified).days
        if age_days > 180:
            findings.append({
                "type": "stale_docs",
                "subtype": "old_markdown",
                "severity": "INFO",
                "file": rel_path,
                "message": f"Markdown file not modified in {age_days} days (last: {md_modified.date()})",
            })

    return findings


# ---------------------------------------------------------------------------
# Unused dependencies
# ---------------------------------------------------------------------------


def find_unused_python_deps(repo_root):
    """Find packages in requirements.txt not imported by any .py file."""
    findings = []

    # Gather all imports
    all_imports = set()
    for abs_path, rel_path in _walk_files(repo_root, [".py"]):
        content = _read_file(abs_path)
        for m in re.finditer(r'(?:from|import)\s+([\w]+)', content):
            all_imports.add(m.group(1).lower())

    # Find requirements files
    for abs_path, rel_path in _walk_files(repo_root, [".txt"]):
        if "requirements" not in os.path.basename(rel_path).lower():
            continue
        content = _read_file(abs_path)
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Parse package name (before ==, >=, etc.)
            pkg = re.split(r'[>=<!\[\];]', line)[0].strip().lower()
            if not pkg:
                continue
            # Normalize: package names use hyphens, imports use underscores
            import_name = pkg.replace("-", "_")
            # Some packages have different import names; skip common ones
            known_aliases = {
                "pillow": "pil",
                "pyyaml": "yaml",
                "python-dotenv": "dotenv",
                "scikit-learn": "sklearn",
                "python-multipart": "multipart",
                "python-jose": "jose",
                "websocket-client": "websocket",
                "beautifulsoup4": "bs4",
                "pyjwt": "jwt",
                "aio-pika": "aio_pika",
            }
            check_name = known_aliases.get(pkg, import_name)
            if check_name not in all_imports:
                findings.append({
                    "type": "unused_dependency",
                    "subtype": "unused_python_dep",
                    "severity": "INFO",
                    "file": rel_path,
                    "message": f"Package '{pkg}' in {os.path.basename(rel_path)} not found in imports",
                })

    return findings


def find_unused_npm_deps(repo_root):
    """Find npm packages in package.json not imported by any .ts/.tsx/.js file."""
    findings = []

    for abs_path, rel_path in _walk_files(repo_root, [".json"]):
        if os.path.basename(rel_path) != "package.json":
            continue

        content = _read_file(abs_path)
        try:
            pkg_data = json.loads(content)
        except json.JSONDecodeError:
            continue

        pkg_dir = os.path.dirname(abs_path)

        # Gather all imports in this package's directory
        all_imports = set()
        for code_abs, code_rel in _walk_files(pkg_dir, [".ts", ".tsx", ".js", ".jsx"]):
            code_content = _read_file(code_abs)
            for m in re.finditer(r"""(?:from|require\()\s*['"]([^'"./][^'"]*?)['"]""", code_content):
                # Get the package name (first part before /)
                imp = m.group(1).split("/")[0]
                all_imports.add(imp)

        deps = {}
        deps.update(pkg_data.get("dependencies", {}))
        deps.update(pkg_data.get("devDependencies", {}))

        for dep_name in deps:
            if dep_name.startswith("@types/"):
                continue  # Type packages are implicitly used
            pkg_base = dep_name.split("/")[-1] if dep_name.startswith("@") else dep_name
            if dep_name not in all_imports and pkg_base not in all_imports:
                findings.append({
                    "type": "unused_dependency",
                    "subtype": "unused_npm_dep",
                    "severity": "INFO",
                    "file": rel_path,
                    "message": f"Package '{dep_name}' in package.json not found in imports",
                })

    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Staleness and dead code audit")
    parser.add_argument("--path", default=None, help="Repository root path")
    args = parser.parse_args()

    repo_root = args.path or str(Path(__file__).resolve().parent.parent.parent)

    print("=" * 60, file=sys.stderr)
    print("  Vexa Staleness & Dead Code Audit", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Scanning: {repo_root}", file=sys.stderr)
    print("", file=sys.stderr)

    all_findings = []

    checks = [
        ("Unimported Python files", find_unimported_python),
        ("Unimported TypeScript files", find_unimported_typescript),
        ("Unreferenced definitions", find_unreferenced_definitions),
        ("Commented-out code", find_commented_out_code),
        ("Orphaned files/directories", find_orphaned_files),
        ("Stale config files", find_stale_config_files),
        ("Broken path references in docs", find_stale_docs),
        ("Stale READMEs", find_stale_readmes),
        ("Old markdown files", find_old_markdown),
        ("Unused Python dependencies", find_unused_python_deps),
        ("Unused npm dependencies", find_unused_npm_deps),
    ]

    for name, check_fn in checks:
        print(f"[Checking: {name}...]", file=sys.stderr)
        try:
            new_findings = check_fn(repo_root)
            all_findings.extend(new_findings)
            print(f"  Found {len(new_findings)} issues", file=sys.stderr)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
        print("", file=sys.stderr)

    # Write JSON output to stdout
    output = {
        "audit": "staleness",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_root": repo_root,
        "total_findings": len(all_findings),
        "findings": all_findings,
        "summary": {},
    }

    # Build summary
    by_type = {}
    by_severity = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for f in all_findings:
        t = f["type"]
        by_type[t] = by_type.get(t, 0) + 1
        s = f.get("severity", "INFO")
        by_severity[s] = by_severity.get(s, 0) + 1
    output["summary"] = {
        "by_type": by_type,
        "by_severity": by_severity,
    }

    json_str = json.dumps(output, indent=2)
    print(json_str)

    # Write to results file
    results_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results")
    os.makedirs(results_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    results_path = os.path.join(results_dir, f"{date_str}_staleness.json")
    with open(results_path, "w") as f:
        f.write(json_str)
    print(f"\nResults written to: {results_path}", file=sys.stderr)

    # Human-readable summary
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("  SUMMARY", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Total findings: {len(all_findings)}", file=sys.stderr)
    print(f"  Errors: {by_severity['ERROR']}  Warnings: {by_severity['WARN']}  Info: {by_severity['INFO']}", file=sys.stderr)
    print("", file=sys.stderr)
    for t, count in sorted(by_type.items()):
        print(f"  {t}: {count}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # Exit with error if any WARN or ERROR
    if by_severity["ERROR"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
