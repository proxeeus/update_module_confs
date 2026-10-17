# update_module_confs

Keeps AzerothCore `.conf` files in line with their `.conf.dist` templates after a core or module update.

## What it does

The script scans `<etc>/*.conf.dist` (`worldserver`, `authserver`, `dbimport`) and `<etc>/modules/*.conf.dist`.
Each `X.conf` that sits next to a dist is **rebuilt on the dist template**:

- The dist's comments, sections and key order are used as they are.
- Each key keeps the value from your conf, exactly as you wrote it.
- Keys that are new in the dist are added at their default value. You can also review them one by one, or
  skip them; a skipped key is written commented out and offered again on the next run.
- When a module renames the first part of its keys (for example `AiPlayerbot.MaxRandomBots` became
  `Playerbots.MaxRandomBots`), your values are carried over to the new names. A prefix change counts as a
  rename only when it covers at least 3 keys whose names are otherwise identical.
- Keys that exist only in your conf (obsolete, renamed or custom) are kept in a
  `KEYS NOT IN DIST` block at the end of the file. When a conf-only key looks like one of the new keys,
  the summary prints a "renamed to …?" hint.

After a run, `diff X.conf.dist X.conf` shows only your changed values and the end block. Running the script
again changes nothing until a dist changes.

Before a file is changed, it is copied to `X.conf.bak.YYYYMMDD-HHMMSS`. Files that are already up to date
are not touched.

> The first run also replaces any comment lines in your confs that no longer match the dist, including notes
> you wrote by hand. Use **D** (diff) before writing, or keep the backup.

## Refreshing the dists

Before comparing, the script copies the current `.conf.dist` files from your `azerothcore-wotlk` source folder
into the `etc` folder, so the confs are compared with the dists of the code you build:

| Source | Copied to |
|---|---|
| `src/server/apps/worldserver/worldserver.conf.dist` | `<etc>/` |
| `src/server/apps/authserver/authserver.conf.dist` | `<etc>/` |
| `src/tools/dbimport/dbimport.conf.dist` | `<etc>/` |
| `modules/*/conf/*.conf.dist` | `<etc>/modules/` |

A dist is copied only when its content differs from the deployed one, and each copied file is printed.
Module dists that have no `.conf` in `<etc>/modules/` yet are listed, so you can create them from the dist.

This matters with the Docker setup: the container entrypoint copies the dists with `cp -n`, so a dist that is
already in the mounted `etc` folder is never updated and new keys (for example `Cluster.Enabled`) go unnoticed.

## Usage

```bash
python3 update_module_confs.py
```

The first run asks for your `etc` folder (the one holding `worldserver.conf`) and your `azerothcore-wotlk`
source folder, and saves both to `settings.json` next to the script. Later runs use them without asking.

The menu lists every file with its status (`41 new, 14 conf-only`, `layout differs from dist`,
`up to date`). Type `A` to update every file that needs it, or numbers such as `1,3,5`. For each file you
are asked how to handle new keys, then whether to write, with an option to view the diff first.

| Option | Effect |
|---|---|
| `--etc-dir PATH` | Use this config folder for this run only (the saved folder stays as it is) |
| `--source-dir PATH` | Use this `azerothcore-wotlk` source folder for this run only (the saved folder stays as it is) |
| `--reconfigure` | Ask for both folders again and save them |
| `--no-refresh` | Do not refresh the deployed `.conf.dist` files from the source folder |
| `--all` | Process every file that needs it, without the menu |
| `--yes` | Add all new keys and write without asking |
| `--dry-run` | List the dists that would be refreshed, print summaries and diffs (against those dists), write nothing |

Typical update: pull the core and modules, run `python3 update_module_confs.py --all --dry-run` to review,
then `python3 update_module_confs.py` to refresh the dists and apply.

## Requirements

Python 3.9 or later; no extra dependencies.

## License

This project is licensed under the GPL-3.0 License (see `LICENSE`).
