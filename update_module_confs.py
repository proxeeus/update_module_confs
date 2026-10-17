#!/usr/bin/env python3
"""Bring AzerothCore .conf files in line with their .conf.dist templates.

Each conf is rebuilt from its dist: the dist's comments, layout and key order are kept, and the
values come from your conf. Keys that are new in the dist are added (or written commented out if
you decline them). Keys that only exist in your conf are kept in a block at the end of the file.
After a run, `diff X.conf.dist X.conf` shows only your changed values and that block.

Scanned files: <etc>/*.conf.dist (worldserver, authserver, dbimport) and <etc>/modules/*.conf.dist.
Before that, the deployed dists are refreshed from your azerothcore-wotlk source folder (skip with
--no-refresh).
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

# Core dists in the azerothcore-wotlk source tree; they are deployed to <etc>/.
CORE_DISTS = (
    "src/server/apps/worldserver/worldserver.conf.dist",
    "src/server/apps/authserver/authserver.conf.dist",
    "src/tools/dbimport/dbimport.conf.dist",
)

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


def ask_folder(label, current, is_valid, error):
    while True:
        prompt = f"{label} [{current}]: " if current else f"{label}: "
        answer = input(prompt).strip() or (current or "")
        path = os.path.abspath(os.path.expanduser(answer)) if answer else ""
        if path and is_valid(path):
            return path
        print(f"  {error}, try again.")


def ask_etc_dir(current=None):
    print("Enter the path to your config folder (the 'etc' folder holding worldserver.conf;")
    print("module confs are read from its 'modules' subfolder).")
    return ask_folder("Config folder", current, os.path.isdir, "Not a valid directory")


def is_source_dir(path):
    return os.path.isfile(os.path.join(path, CORE_DISTS[0]))


def ask_source_dir(current=None):
    print("Enter the path to your azerothcore-wotlk source folder (the one holding src/ and modules/);")
    print("the .conf.dist files in the config folder are refreshed from it before each run.")
    return ask_folder("Source folder", current, is_source_dir, f"No {CORE_DISTS[0]} there")


def resolve_folder(override, key, label, is_valid, ask_fn, reconfigure):
    """Folder from the command line, else from settings.json, else asked for and saved."""
    if override:
        path = os.path.abspath(os.path.expanduser(override))
        if not is_valid(path):
            sys.exit(f"{path} is not a valid {label.lower()}.")
        return path

    settings = load_settings()
    saved = settings.get(key)
    if saved and not is_valid(saved):
        print(f"Saved {label.lower()} {saved} is no longer valid.")
        saved = None
    if saved and not reconfigure:
        return saved

    path = ask_fn(saved)
    settings[key] = path
    save_settings(settings)
    print(f"Saved to {SETTINGS_PATH}\n")
    return path


def resolve_etc_dir(args):
    return resolve_folder(args.etc_dir, "etc_dir", "Config folder", os.path.isdir, ask_etc_dir,
                          args.reconfigure)


def resolve_source_dir(args):
    return resolve_folder(args.source_dir, "source_dir", "Source folder", is_source_dir, ask_source_dir,
                          args.reconfigure)


# --- Refresh dists from source -------------------------------------------------------------------

def source_dists(source_dir):
    """(source path, deployed path relative to etc) for each dist in the source tree."""
    result = [(os.path.join(source_dir, rel), os.path.basename(rel)) for rel in CORE_DISTS]
    modules_dir = os.path.join(source_dir, "modules")
    if os.path.isdir(modules_dir):
        for module in sorted(os.listdir(modules_dir), key=str.lower):
            conf_dir = os.path.join(modules_dir, module, "conf")
            if not os.path.isdir(conf_dir):
                continue
            for fname in sorted(os.listdir(conf_dir), key=str.lower):
                if fname.endswith(".conf.dist"):
                    result.append((os.path.join(conf_dir, fname), os.path.join("modules", fname)))
    return result


def same_content(a, b):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def refresh_dists(etc_dir, source_dir, dry_run):
    """Copy source dists that differ from the deployed ones into etc.

    Returns {deployed path: source path} for the copies a dry run skipped, so the comparison
    can still use the dists the real run would deploy.
    """
    print(f"\nRefreshing .conf.dist files from {source_dir}")
    pending = {}
    seen = {}
    refreshed = 0
    unconfigured = []
    for src, rel in source_dists(source_dir):
        if not os.path.isfile(src):
            print(f"  Warning: {os.path.relpath(src, source_dir)} not found in the source folder.")
            continue
        if rel in seen:
            print(f"  Warning: {os.path.relpath(src, source_dir)} has the same name as "
                  f"{os.path.relpath(seen[rel], source_dir)}; skipping it.")
            continue
        seen[rel] = src
        dest = os.path.join(etc_dir, rel)
        if rel.startswith("modules" + os.sep) and not os.path.exists(dest[:-len(".dist")]):
            unconfigured.append(rel[:-len(".dist")])
        if os.path.isfile(dest) and same_content(src, dest):
            continue
        action = "new" if not os.path.exists(dest) else "updated"
        refreshed += 1
        if dry_run:
            pending[dest] = src
            print(f"  Would refresh {rel} ({action})")
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(src, dest)
        print(f"  Refreshed {rel} ({action})")
    if not refreshed:
        print("  All deployed dists match the source.")
    if unconfigured:
        print("  Module dists with no deployed conf yet (copy the dist to create it):")
        for name in unconfigured:
            print(f"    - {name}")
    return pending


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
    def __init__(self, etc_dir, dist_path, read_from=None):
        self.dist_path = dist_path
        self.conf_path = dist_path[:-len(".dist")]
        self.name = os.path.relpath(self.conf_path, etc_dir)
        self.has_conf = os.path.exists(self.conf_path)
        if not self.has_conf:
            return
        _, self.dist_lines = read_lines(read_from or dist_path)
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


def find_pairs(etc_dir, pending=None):
    """Pairs for the deployed dists; `pending` maps dists a dry run did not copy to their source."""
    pending = pending or {}
    pairs = []
    for folder in (etc_dir, os.path.join(etc_dir, "modules")):
        names = set(os.listdir(folder)) if os.path.isdir(folder) else set()
        names.update(os.path.basename(p) for p in pending if os.path.dirname(p) == folder)
        for fname in sorted(names, key=str.lower):
            if fname.endswith(".conf.dist"):
                dist_path = os.path.join(folder, fname)
                pairs.append(Pair(etc_dir, dist_path, pending.get(dist_path)))
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


def menu_loop(etc_dir, args, pending):
    while True:
        pairs = find_pairs(etc_dir, pending)
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
    parser.add_argument("--source-dir", help="azerothcore-wotlk source folder for this run only "
                                             "(does not change the saved one)")
    parser.add_argument("--reconfigure", action="store_true", help="ask for both folders again and save them")
    parser.add_argument("--no-refresh", action="store_true",
                        help="do not refresh the deployed .conf.dist files from the source folder")
    parser.add_argument("--all", action="store_true", help="process every file that needs it, without the menu")
    parser.add_argument("--yes", action="store_true", help="add all new keys and write without asking")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the dists that would be refreshed, summaries and diffs; write nothing")
    args = parser.parse_args()

    print("AzerothCore Config Updater")
    print("--------------------------")
    try:
        etc_dir = resolve_etc_dir(args)
        pending = {}
        if not args.no_refresh:
            pending = refresh_dists(etc_dir, resolve_source_dir(args), args.dry_run)
        if args.all:
            selected = needs_work(find_pairs(etc_dir, pending))
            if not selected:
                print("Everything is up to date.")
            for pair in selected:
                process(pair, args)
        else:
            menu_loop(etc_dir, args, pending)
    except (EOFError, KeyboardInterrupt):
        print()
    print("Goodbye!")


if __name__ == "__main__":
    main()
