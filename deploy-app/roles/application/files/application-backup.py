#!/usr/bin/python3
import argparse
import fcntl
import grp
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid


def clean_environment():
    return {
        key: value for key, value in os.environ.items()
        if not key.startswith(("RESTIC_", "AWS_", "PG"))
    }


def restic_environment(config):
    environment = clean_environment()
    environment.update({
        "RESTIC_REPOSITORY": config["repository"],
        "RESTIC_PASSWORD": config["password"],
        "RESTIC_CACHE_DIR": str(Path(config["state_dir"]) / "cache"),
        "AWS_DEFAULT_REGION": config["region"],
        "AWS_ACCESS_KEY_ID": config["access_key_id"],
        "AWS_SECRET_ACCESS_KEY": config["secret_access_key"],
        "AWS_EC2_METADATA_DISABLED": "true",
    })
    return environment


def restic(config, arguments, *, display=False, capture=False):
    return subprocess.run(
        ["restic", *arguments],
        env=restic_environment(config),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else (None if display else subprocess.DEVNULL),
        stderr=subprocess.PIPE,
        check=True,
    )


def postgres(arguments, *, input_file=subprocess.DEVNULL, output=subprocess.DEVNULL):
    return subprocess.run(
        ["runuser", "--user", "postgres", "--", *arguments],
        env=clean_environment(),
        stdin=input_file,
        stdout=output,
        stderr=subprocess.PIPE,
        check=True,
    )


def backup(config):
    media = Path(config["media_dir"])
    if media.is_symlink() or not media.is_dir():
        raise ValueError("Media directory is missing or is a symlink")
    retention = int(config["retention_days"])
    if retention < 1:
        raise ValueError("Retention must be positive")
    state = Path(config["state_dir"])
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state / "backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        dump = state / "database.dump"
        try:
            with os.fdopen(os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as output:
                postgres(
                    [
                        "pg_dump",
                        "--host=/var/run/postgresql", "--no-password",
                        "--format=custom", "--dbname=" + config["database"],
                    ],
                    output=output,
                )
            subprocess.run(
                ["pg_restore", "--list", str(dump)],
                env=clean_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=True,
            )
            pending = "pending-" + uuid.uuid4().hex
            restic(config, [
                "backup", "--host", config["app"], "--tag", config["app"],
                "--tag", pending,
                str(dump), str(media),
            ])
            restic(config, [
                "tag", "--host", config["app"], "--tag", config["app"] + "," + pending,
                "--add", "app-backup-complete", "--remove", pending,
            ])
            restic(config, [
                "forget", "--host", config["app"], "--tag", config["app"] + ",app-backup-complete",
                "--group-by", "host,tags", "--keep-within", f"{retention}d", "--prune",
            ])
        finally:
            dump.unlink(missing_ok=True)


def snapshots(config, snapshot=None):
    arguments = ["snapshots", "--json", "--host", config["app"],
                 "--tag", config["app"] + ",app-backup-complete"]
    if snapshot is not None:
        if not re.fullmatch(r"[0-9a-f]{8,64}", snapshot):
            raise ValueError("Supply an explicit hexadecimal snapshot ID")
        arguments.append(snapshot)
    entries = json.loads(restic(config, arguments, capture=True).stdout)
    if not isinstance(entries, list) or any(
        entry["hostname"] != config["app"]
        or not {config["app"], "app-backup-complete"}.issubset(entry.get("tags", []))
        for entry in entries
    ):
        raise ValueError("Snapshot does not belong to this application")
    return entries


def restore_context(config):
    for key in ("app", "database", "database_user"):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", config[key]):
            raise ValueError("Invalid application or database identity")
    return {key: config[key] for key in (
        "app", "database", "database_user", "media_dir", "state_dir", "media_user", "media_group",
    )}


def restored_paths(config, stage):
    dump = stage / (Path(config["state_dir"]) / "database.dump").relative_to("/")
    media = stage / Path(config["media_dir"]).relative_to("/")
    if (
        dump.is_symlink() or not dump.is_file() or media.is_symlink() or not media.is_dir()
        or not dump.resolve().is_relative_to(stage.resolve())
        or not media.resolve().is_relative_to(stage.resolve())
    ):
        raise ValueError("Snapshot is missing its database dump or media")
    return dump, media


def prepare_restore(config, snapshot):
    context = restore_context(config)
    entries = snapshots(config, snapshot)
    expected = {str(Path(config["state_dir"]) / "database.dump"), config["media_dir"]}
    if len(entries) != 1 or not entries[0]["id"].startswith(snapshot) or set(entries[0]["paths"]) != expected:
        raise ValueError("Snapshot is ambiguous or has unexpected backup paths")
    state = Path(config["state_dir"])
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="restore-", dir=state))
    restic(config, ["restore", entries[0]["id"], "--verify", "--target", str(stage)])
    stage.chmod(0o700)
    dump, _ = restored_paths(config, stage)
    subprocess.run(
        ["pg_restore", "--list", str(dump)], env=clean_environment(),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True,
    )
    (stage / "restore.json").write_text(json.dumps(context | {"snapshot": entries[0]["id"]}))
    return {"stage": str(stage), "snapshot": entries[0]["id"]}


def apply_restore(config, path):
    context = restore_context(config)
    state = Path(config["state_dir"]).resolve()
    stage = Path(path)
    if stage.is_symlink() or stage.parent.resolve() != state or not stage.name.startswith("restore-"):
        raise ValueError("Invalid private restore directory")
    manifest_path = stage / "restore.json"
    manifest = json.loads(manifest_path.read_text())
    if any(manifest.get(key) != value for key, value in context.items()) or "recovery" in manifest:
        raise ValueError("Restore context changed or this stage has already been applied")
    dump, staged_media = restored_paths(config, stage)
    media = Path(config["media_dir"])
    if media.is_symlink() or not media.is_dir():
        raise ValueError("Live media directory is missing or is a symlink")
    uid = pwd.getpwnam(config["media_user"]).pw_uid
    gid = grp.getgrnam(config["media_group"]).gr_gid
    with (state / "backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        token = uuid.uuid4().hex[:12]
        replacement = Path(tempfile.mkdtemp(prefix=".restore-media-", dir=media.parent))
        previous_media = media.with_name(".media-before-" + token)
        if os.path.lexists(previous_media):
            raise ValueError("Previous media recovery path already exists")
        restored_database = config["database"][:35] + "_restore_" + token
        previous_database = config["database"][:35] + "_before_" + token
        recovery = {
            "previous_database": previous_database, "restored_database": restored_database,
            "previous_media": str(previous_media), "replacement_media": str(replacement),
        }
        manifest["recovery"] = recovery
        manifest_path.write_text(json.dumps(manifest))
        shutil.copytree(staged_media, replacement, symlinks=True, dirs_exist_ok=True)
        for parent, directories, files in os.walk(replacement, followlinks=False):
            paths = [Path(parent), *(Path(parent) / name for name in files)]
            paths.extend(Path(parent) / name for name in directories if (Path(parent) / name).is_symlink())
            for item in paths:
                os.chown(item, uid, gid, follow_symlinks=False)
                if not item.is_symlink():
                    item.chmod(0o2750 if item.is_dir() else 0o640)
        connection = ["--host=/var/run/postgresql", "--no-password"]
        postgres(["createdb", *connection, "--template=template0",
                  "--owner=" + config["database_user"], restored_database])
        with dump.open("rb") as source:
            postgres(["pg_restore", *connection, "--exit-on-error", "--no-owner", "--no-privileges",
                      "--role=" + config["database_user"], "--dbname=" + restored_database], input_file=source)
        media.rename(previous_media)
        replacement.rename(media)
        database = config["database"]
        postgres([
            "psql", *connection, "--no-psqlrc", "--dbname=postgres", "--set=ON_ERROR_STOP=1",
            "--command=" + (
                "BEGIN; "
                f"SELECT pg_terminate_backend(pid, 5000) FROM pg_stat_activity WHERE datname = '{database}'; "
                f'ALTER DATABASE "{database}" RENAME TO "{previous_database}"; '
                f'ALTER DATABASE "{previous_database}" WITH ALLOW_CONNECTIONS false; '
                f'ALTER DATABASE "{restored_database}" RENAME TO "{database}"; COMMIT;'
            ),
        ])
        manifest["applied"] = True
        manifest_path.write_text(json.dumps(manifest))
        return recovery | {"stage": str(stage), "snapshot": manifest["snapshot"]}


def interrupted(signum, frame):
    raise InterruptedError("Backup interrupted")


def main():
    parser = argparse.ArgumentParser(description="Encrypted PostgreSQL and media backups.")
    parser.add_argument("config", type=Path)
    parser.add_argument("command", choices=["run", "restic", "list", "prepare-restore", "apply-restore"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        config = json.loads(args.config.read_text())
        if args.command == "run":
            if args.arguments:
                raise ValueError("Unexpected backup arguments")
            backup(config)
            print("Database and media backup completed; retention applied.")
        elif args.command == "restic":
            if not args.arguments:
                raise ValueError("Supply a restic command")
            restic(config, args.arguments, display=True)
        elif args.command == "list":
            if args.arguments:
                raise ValueError("Unexpected listing arguments")
            print(json.dumps(snapshots(config)))
        elif len(args.arguments) != 1:
            raise ValueError("Supply exactly one snapshot ID or private staging path")
        elif args.command == "prepare-restore":
            print(json.dumps(prepare_restore(config, args.arguments[0])))
        else:
            print(json.dumps(apply_restore(config, args.arguments[0])))
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError):
        print(
            "Application backup command failed; check configuration, storage access, "
            "repository initialization and free disk space.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
