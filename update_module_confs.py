#!/usr/bin/env python3
"""Bring AzerothCore .conf files in line with their .conf.dist templates.

Each conf is rebuilt from its dist: the dist's comments, layout and key order are kept, and the
values come from your conf. Keys that are new in the dist are added (or written commented out if
you decline them). Keys that only exist in your conf are kept in a block at the end of the file.
After a run, `diff X.conf.dist X.conf` shows only your changed values and that block.

Scanned files: <etc>/*.conf.dist (worldserver, authserver, dbimport) and <etc>/modules/*.conf.dist.
"""
import argparse
import datetime
import difflib
import json
import os
import pydoc
import re
import shutil
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "settings.json")

# An active "Key = value" line: not a comment, not a [section] header.
KEY_LINE = re.compile(r"^(\s*)([^\s#=\[][^=]*?)(\s*=\s*)(.*)$")

EXTRA_HEADER = [
    "",
    "###################################################################################################",
    "# KEYS NOT IN DIST (obsolete, renamed or custom)",
    "#",
    "#    Kept by update_module_confs.py: these keys are not in the current .conf.dist.",
    "#    Delete them once you no longer need them.",
    "###################################################################################################",
    "",
]


# --- Settings ------------------------------------------------------------------------------------

def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def ask_etc_dir(current=None):
    print("Enter the path to your config folder (the 'etc' folder holding worldserver.conf;")
    print("module confs are read from its 'modules' subfolder).")
    while True:
        prompt = f"Config folder [{current}]: " if current else "Config folder: "
        answer = input(prompt).strip() or (current or "")
        path = os.path.abspath(os.path.expanduser(answer)) if answer else ""
        if path and os.path.isdir(path):
            return path
        print("  Not a valid directory, try again.")


def resolve_etc_dir(args):
    if args.etc_dir:
        path = os.path.abspath(os.path.expanduser(args.etc_dir))
        if not os.path.isdir(path):
            sys.exit(f"{path} is not a directory.")
        return path

    settings = load_settings()
    saved = settings.get("etc_dir")
    if saved and not os.path.isdir(saved):
        print(f"Saved config folder {saved} no longer exists.")
        saved = None
    if saved and not args.reconfigure:
        return saved

    path = ask_etc_dir(saved)
    settings["etc_dir"] = path
    save_settings(settings)
    print(f"Saved to {SETTINGS_PATH}\n")
    return path


# --- Parsing -------------------------------------------------------------------------------------

def read_lines(path):
    with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return text, lines


class Conf:
    """Active keys of a conf file: values, raw lines, first-seen order and duplicates."""

    def __init__(self, lines):
        self.values = {}
        self.lines = {}
        self.order = []
        self.duplicates = []
        for line in lines:
            m = KEY_LINE.match(line)
            if not m:
                continue
            key, value = m.group(2), m.group(4)
            if key in self.values:
                if key not in self.duplicates:
                    self.duplicates.append(key)
            else:
                self.order.append(key)
            # Last occurrence wins, as in the server's config loader.
            self.values[key] = value
            self.lines[key] = line


def dist_key_docs(dist_lines):
    """Map each dist key to the comment block right above it, for the review prompt."""
    docs = {}
    block = []
    for line in dist_lines:
        m = KEY_LINE.match(line)
        if m:
            while block and not block[0].strip():
                block.pop(0)
            docs.setdefault(m.group(2), list(block))
            block = []
        else:
            block.append(line)
    return docs


# --- Rebuild -------------------------------------------------------------------------------------

def rebuild(dist_lines, conf, accepted_new):
    """Return the conf text rebuilt on the dist template."""
    out = []
    dist_keys = set()
    for line in dist_lines:
        m = KEY_LINE.match(line)
        if not m:
            out.append(line)
            continue
        indent, key, sep, _ = m.groups()
        dist_keys.add(key)
        if key in conf.values:
            out.append(indent + key + sep + conf.values[key])
        elif key in accepted_new:
            out.append(line)
        else:
            out.append("# " + line)

    extras = [k for k in conf.order if k not in dist_keys]
    if extras:
        while out and not out[-1].strip():
            out.pop()
        out.extend(EXTRA_HEADER)
        out.extend(conf.lines[k] for k in extras)
    return "\n".join(out) + "\n"


class Pair:
    def __init__(self, etc_dir, dist_path):
        self.dist_path = dist_path
        self.conf_path = dist_path[:-len(".dist")]
        self.name = os.path.relpath(self.conf_path, etc_dir)
        self.has_conf = os.path.exists(self.conf_path)
        if not self.has_conf:
            return
        _, self.dist_lines = read_lines(dist_path)
        self.conf_text, conf_lines = read_lines(self.conf_path)
        self.conf = Conf(conf_lines)
        dist = Conf(self.dist_lines)
        self.missing = [k for k in dist.order if k not in self.conf.values]
        self.extras = [k for k in self.conf.order if k not in dist.values]
        self.changed = rebuild(self.dist_lines, self.conf, set(self.missing)) != self.conf_text

    def status(self):
        if not self.has_conf:
            return "no conf file (copy the dist to create it)"
        parts = []
        if self.missing:
            parts.append(f"{len(self.missing)} new")
        if self.extras:
            parts.append(f"{len(self.extras)} conf-only")
        if not self.changed:
            return "up to date" + (f" ({', '.join(parts)})" if parts else "")
        if not self.missing:
            parts.append("layout differs from dist")
        return ", ".join(parts)


def find_pairs(etc_dir):
    pairs = []
    for folder in (etc_dir, os.path.join(etc_dir, "modules")):
        if not os.path.isdir(folder):
            continue
        for fname in sorted(os.listdir(folder), key=str.lower):
            if fname.endswith(".conf.dist"):
                pairs.append(Pair(etc_dir, os.path.join(folder, fname)))
    return pairs


# --- Interaction ---------------------------------------------------------------------------------

def ask(prompt, choices, default):
    while True:
        answer = input(prompt).strip().lower() or default
        if answer in choices:
            return answer
        print(f"  Please answer one of: {', '.join(choices)}")


def show(text, use_pager):
    if use_pager and sys.stdout.isatty():
        pydoc.pager(text)
    else:
        print(text)


def unified_diff(pair, new_text):
    old = pair.conf_text.replace("\r\n", "\n").splitlines(keepends=True)
    new = new_text.splitlines(keepends=True)
    return "".join(difflib.unified_diff(old, new, pair.name + " (current)", pair.name + " (rebuilt)"))


def choose_new_keys(pair, args):
    if not pair.missing:
        return set()
    if args.yes or args.dry_run:
        return set(pair.missing)
    choice = ask("  New keys: [A]dd all at default / [R]eview each / [S]kip all (written commented out) [A]: ",
                 ("a", "r", "s"), "a")
    if choice == "a":
        return set(pair.missing)
    if choice == "s":
        return set()
    docs = dist_key_docs(pair.dist_lines)
    dist = Conf(pair.dist_lines)
    accepted = set()
    for key in pair.missing:
        print()
        print("\n".join(docs.get(key, [])))
        print(dist.lines[key])
        if ask(f"  Add {key}? (Y/n): ", ("y", "n", "yes", "no"), "y").startswith("y"):
            accepted.add(key)
    return accepted


def backup(path):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"{path}.bak.{stamp}"
    shutil.copy2(path, bak)
    return bak


def process(pair, args):
    print(f"\n=== {pair.name}")
    if not pair.has_conf:
        print(f"  {pair.status()}, skipping.")
        return
    for key in pair.conf.duplicates:
        print(f"  Warning: {key} is set more than once; keeping the last value.")
    if pair.missing:
        print(f"  {len(pair.missing)} new key(s) in dist:")
        for key in pair.missing:
            print(f"    + {key}")
    if pair.extras:
        print(f"  {len(pair.extras)} conf-only key(s), kept at the end of the file:")
        for key in pair.extras:
            hint = difflib.get_close_matches(key, pair.missing, n=1, cutoff=0.6)
            print(f"    ? {key}" + (f"   (renamed to {hint[0]}?)" if hint else ""))

    new_text = rebuild(pair.dist_lines, pair.conf, choose_new_keys(pair, args))
    if new_text == pair.conf_text:
        print("  Already up to date.")
        return
    if not pair.missing:
        print("  Layout/comments differ from dist: the file will be rebuilt on the dist template.")

    if args.dry_run:
        show(unified_diff(pair, new_text), use_pager=False)
        print("  Dry run: nothing written.")
        return
    if not args.yes:
        while True:
            choice = ask("  Write changes? [Y]es / [D]iff first / [N]o [Y]: ", ("y", "d", "n"), "y")
            if choice != "d":
                break
            show(unified_diff(pair, new_text), use_pager=True)
        if choice == "n":
            print("  Skipped.")
            return

    bak = backup(pair.conf_path)
    with open(pair.conf_path, "w", encoding="utf-8", errors="surrogateescape", newline="\n") as f:
        f.write(new_text)
    print(f"  Written (backup: {os.path.basename(bak)}).")


def print_menu(etc_dir, pairs):
    print(f"\nConfig folder: {etc_dir}")
    width = max(len(p.name) for p in pairs)
    for idx, pair in enumerate(pairs, 1):
        print(f"  {idx:2}. {pair.name:<{width}}  {pair.status()}")
    print("\n  A: update every file that needs it")
    print("  1,3,5: update those files")
    print("  Q: quit")


def needs_work(pairs):
    return [p for p in pairs if p.has_conf and p.changed]


def menu_loop(etc_dir, args):
    while True:
        pairs = find_pairs(etc_dir)
        if not pairs:
            print(f"No .conf.dist files found in {etc_dir} or its modules folder.")
            return
        print_menu(etc_dir, pairs)
        choice = input("Select: ").strip().lower()
        if choice == "q":
            return
        if choice == "a":
            selected = needs_work(pairs)
            if not selected:
                print("Everything is up to date.")
        else:
            nums = [int(x) for x in choice.split(",") if x.strip().isdigit()]
            selected = [pairs[n - 1] for n in nums if 0 < n <= len(pairs)]
            if not selected:
                print("Invalid selection.")
        for pair in selected:
            process(pair, args)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--etc-dir", help="config folder for this run only (does not change the saved one)")
    parser.add_argument("--reconfigure", action="store_true", help="ask for the config folder again and save it")
    parser.add_argument("--all", action="store_true", help="process every file that needs it, without the menu")
    parser.add_argument("--yes", action="store_true", help="add all new keys and write without asking")
    parser.add_argument("--dry-run", action="store_true", help="show summaries and diffs, write nothing")
    args = parser.parse_args()

    print("AzerothCore Config Updater")
    print("--------------------------")
    try:
        etc_dir = resolve_etc_dir(args)
        if args.all:
            selected = needs_work(find_pairs(etc_dir))
            if not selected:
                print("Everything is up to date.")
            for pair in selected:
                process(pair, args)
        else:
            menu_loop(etc_dir, args)
    except (EOFError, KeyboardInterrupt):
        print()
    print("Goodbye!")


if __name__ == "__main__":
    main()
