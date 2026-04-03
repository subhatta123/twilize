"""CLI entry point for viewing and editing dashboard YAML rules.

Usage:
    twilize-rules show                     # Display all active rules
    twilize-rules show kpi                 # Display only KPI rules
    twilize-rules set kpi.font_size 24     # Change a rule value
    twilize-rules reset                    # Reset to built-in defaults
    twilize-rules export [path]            # Export current rules to YAML file
    twilize-rules path                     # Show rules file location
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def _load_rules():
    from twilize.dashboard_rules import load_rules
    return load_rules()


def _builtin_path() -> Path:
    return Path(__file__).parent / "references" / "dashboard_rules.yaml"


def _rules_package_path() -> Path:
    return Path(__file__).parent / "rules" / "dashboard_rules.yaml"


def cmd_show(args):
    """Display active rules, optionally filtered by section."""
    rules = _load_rules()
    if args.section:
        section = rules.get(args.section)
        if section is None:
            print(f"Unknown section '{args.section}'. Available: {', '.join(rules.keys())}")
            sys.exit(1)
        print(yaml.dump({args.section: section}, default_flow_style=False, sort_keys=False))
    else:
        print(yaml.dump(rules, default_flow_style=False, sort_keys=False))


def cmd_set(args):
    """Set a rule value (dot-notation: kpi.font_size 24)."""
    key_path = args.key.split(".")
    if len(key_path) < 2:
        print("Key must use dot notation: section.key (e.g., kpi.font_size)")
        sys.exit(1)

    # Load current rules and update
    rules_path = _rules_package_path()
    if not rules_path.exists():
        rules_path = _builtin_path()

    with open(rules_path, encoding="utf-8") as f:
        rules = yaml.safe_load(f) or {}

    # Navigate to the parent dict
    current = rules
    for part in key_path[:-1]:
        if part not in current:
            current[part] = {}
        current = current[part]

    # Parse value
    raw_value = args.value
    try:
        value = int(raw_value)
    except ValueError:
        try:
            value = float(raw_value)
        except ValueError:
            if raw_value.lower() in ("true", "yes"):
                value = True
            elif raw_value.lower() in ("false", "no"):
                value = False
            else:
                value = raw_value

    old_value = current.get(key_path[-1], "<unset>")
    current[key_path[-1]] = value

    with open(rules_path, "w", encoding="utf-8") as f:
        yaml.dump(rules, f, default_flow_style=False, sort_keys=False)

    print(f"Updated {args.key}: {old_value} → {value}")
    print(f"Saved to {rules_path}")


def cmd_reset(args):
    """Reset rules to built-in defaults."""
    builtin = _builtin_path()
    pkg = _rules_package_path()

    with open(builtin, encoding="utf-8") as f:
        content = f.read()

    if pkg.exists():
        with open(pkg, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"Rules reset to defaults at {pkg}")
    else:
        print(f"Built-in defaults at {builtin}")


def cmd_export(args):
    """Export current rules to a YAML file."""
    rules = _load_rules()
    output = Path(args.path) if args.path else Path.cwd() / "dashboard_rules.yaml"

    with open(output, "w", encoding="utf-8") as f:
        yaml.dump(rules, f, default_flow_style=False, sort_keys=False)

    print(f"Rules exported to {output}")


def cmd_path(args):
    """Show the location of rules files."""
    builtin = _builtin_path()
    pkg = _rules_package_path()
    cwd = Path.cwd() / "dashboard_rules.yaml"

    print("Rules search order:")
    print(f"  1. Next to data file:  <data_dir>/dashboard_rules.yaml")
    print(f"  2. Working directory:  {cwd} {'[EXISTS]' if cwd.exists() else ''}")
    print(f"  3. Package rules:     {pkg} {'[EXISTS]' if pkg.exists() else ''}")
    print(f"  4. Built-in defaults: {builtin} {'[EXISTS]' if builtin.exists() else ''}")


def main():
    parser = argparse.ArgumentParser(
        prog="twilize-rules",
        description="View and edit Tableau dashboard YAML rules",
    )
    sub = parser.add_subparsers(dest="command")

    # show
    p_show = sub.add_parser("show", help="Display active rules")
    p_show.add_argument("section", nargs="?", help="Section to display (e.g., kpi, charts, layout)")
    p_show.set_defaults(func=cmd_show)

    # set
    p_set = sub.add_parser("set", help="Set a rule value (dot notation)")
    p_set.add_argument("key", help="Rule key in dot notation (e.g., kpi.font_size)")
    p_set.add_argument("value", help="New value")
    p_set.set_defaults(func=cmd_set)

    # reset
    p_reset = sub.add_parser("reset", help="Reset rules to built-in defaults")
    p_reset.set_defaults(func=cmd_reset)

    # export
    p_export = sub.add_parser("export", help="Export current rules to YAML file")
    p_export.add_argument("path", nargs="?", help="Output path (default: ./dashboard_rules.yaml)")
    p_export.set_defaults(func=cmd_export)

    # path
    p_path = sub.add_parser("path", help="Show rules file locations")
    p_path.set_defaults(func=cmd_path)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
