"""All-or-nothing publication of the file set one command produces (compare-windows: JSON, CSV, HTML, PNG).

The writers put every file of the new generation into a scratch directory inside the output directory; commit() then
swaps the whole set in with the protocol of pipeline._ComparisonStream (Phase A), generalised from two fixed names to an
allowlisted, variable set of names:

  1. fsync every staged file; list the files of the previous generation (names matching the allowlist)
  2. write the journal {old, new} atomically (partial file, then rename)       -- no journal yet: nothing was replaced
  3. hard-link (copy as a fallback) every previous file into <prev>/           -- recovery without marker = restore these
  4. os.replace every staged file over its final name, delete previous files the new set does not contain
  5. COMMIT: atomically rename <marker>.partial -> <marker>
  6. delete the backups, then the journal, then the marker                      -- recovery with marker = finish this deletion

The rollback material (backups, journal) is only destroyed after the marker exists, and recovery acts only on a journal
or marker it read successfully; state it cannot read raises <code> and changes nothing. After a recovery that completed
the output holds exactly the previous generation (nothing committed) or exactly the new one (committed). Between a process
death and the next begin() the directory can hold a mix of both; the dot files .<stem>.publishing / .committed / .prev
show that. Files that do not match the allowlist are never read, moved or deleted. One writer per output directory.

Every filesystem step goes through one of the small static methods below, so tests can fail any of them (and a test checks
that no raw os/shutil mutation exists outside them).
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat

from .numeric_schema import NumericContractError

JOURNAL_SCHEMA = 'set_publication_v1'
# Everything compare-windows writes (local_delta.py / local_delta_report.py); nothing else in the directory is ours.
LOCAL_DELTA_FILES = re.compile(
    r'local_delta\.json|local_delta_rows\.csv|window_sensitivity\.json|window_sensitivity_rows\.csv|'
    r'window_sensitivity_rank_agreement\.csv|local_delta\.html|local_delta_thumbs\.json|'
    r'local_delta_thumb_[0-9a-f]{12}\.png|local_delta_overview_[ab]_[0-9a-f]{12}\.png')


@contextmanager
def staged_file(path, *, newline=None):
    """Text file that appears under `path` only when the block completes (a temp sibling is renamed into place)."""
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        with open(str(tmp), 'w', encoding='utf-8', newline=newline) as handle:
            yield handle
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise


class SetPublication:
    def __init__(self, output, allowed=LOCAL_DELTA_FILES, *, stem='local_delta', code='local_delta_recovery_unreadable'):
        self.output = Path(output)
        self._allowed = allowed
        self._stem = stem
        self._code = code
        self.staging = None
        self._created = False

    # -- control files -------------------------------------------------------------------------------------------
    def _journal(self):
        return self.output / f'.{self._stem}.publishing'

    def _marker(self):
        return self.output / f'.{self._stem}.committed'

    def _backups(self):
        return self.output / f'.{self._stem}.prev'

    def _partial(self, path):
        return path.with_name(path.name + '.partial')

    def _is_staging_name(self, name):
        return name.startswith(f'.{self._stem}.staging.')

    # -- every filesystem step (the only place os/shutil are touched) -------------------------------------------
    @staticmethod
    def _present(path):
        """Exists or not; an error other than "not there" is raised (Path.exists() hides it on Python >= 3.12)."""
        try:
            os.lstat(str(path))
        except (FileNotFoundError, NotADirectoryError):
            return False
        return True

    @staticmethod
    def _is_regular(path):
        return stat.S_ISREG(os.lstat(str(path)).st_mode)

    @staticmethod
    def _read_text(path):
        return Path(path).read_text(encoding='utf-8')

    @staticmethod
    def _listdir(path):
        return sorted(os.listdir(str(path)))

    @staticmethod
    def _create(path, text):
        with open(str(path), 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync(path):
        with open(str(path), 'rb') as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _replace(source, destination):
        os.replace(str(source), str(destination))

    @staticmethod
    def _unlink(path):
        try:
            os.unlink(str(path))
        except FileNotFoundError:
            pass

    @staticmethod
    def _link(source, destination):
        os.link(str(source), str(destination))

    @staticmethod
    def _copy(source, destination):
        shutil.copyfile(str(source), str(destination))
        with open(str(destination), 'rb') as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _makedirs(path):
        os.makedirs(str(path), exist_ok=True)

    @staticmethod
    def _mkdir(path):
        try:
            os.mkdir(str(path))
        except FileExistsError:
            pass

    @staticmethod
    def _rmdir(path):
        os.rmdir(str(path))

    @staticmethod
    def _rmtree(path):
        shutil.rmtree(str(path))

    def _sync_dir(self):
        try:
            dir_fd = os.open(str(self.output), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)

    # -- life cycle -----------------------------------------------------------------------------------------------
    def begin(self):
        """Repairs an interrupted earlier publication, then opens an empty scratch directory and returns its path."""
        self._created = not self._present(self.output)
        self._makedirs(self.output)
        try:
            self._recover()
            if self._present(self._marker()) or self._present(self._journal()):
                raise OSError(errno.EIO, 'an earlier publication could not be cleaned up completely: '
                              + str(self._journal()))
            for name in self._listdir(self.output):  # scratch left by a killed run (never part of any generation)
                if self._is_staging_name(name):
                    try:
                        self._rmtree(self.output / name)
                    except OSError:
                        pass
            self.staging = self.output / f'.{self._stem}.staging.{os.getpid()}.{secrets.token_hex(4)}'
            self._mkdir(self.staging)
        except BaseException:
            self.abort()
            raise
        return self.staging

    def final_path(self, staged):
        return str(self.output / Path(staged).name)

    def abort(self):
        """Drops the scratch directory (after a commit it is empty). The published generation is never touched here."""
        if self.staging is not None:
            try:
                self._rmtree(self.staging)
            except OSError:
                pass
            self.staging = None
        if self._created:
            self._created = False
            try:
                self._rmdir(self.output)  # only succeeds when this run created it and nothing was published into it
            except OSError:
                pass

    def commit(self):
        names = self._staged_names()
        old = self._generation_names()
        for name in names:
            self._fsync(self.staging / name)
        journal = self._journal()
        try:
            partial = self._partial(journal)
            self._create(partial, json.dumps({'schema': JOURNAL_SCHEMA, 'old': old, 'new': names}))
            self._replace(partial, journal)  # never a half-written journal
            if old:
                self._mkdir(self._backups())
                for name in old:
                    self._backup(self.output / name, self._backups() / name)
            for name in names:
                self._replace(self.staging / name, self.output / name)
            for name in old:
                if name not in names:
                    self._unlink(self.output / name)
            self._sync_dir()
            pending = self._partial(self._marker())
            self._create(pending, 'committed')
            self._replace(pending, self._marker())  # the commit decision
        except BaseException:
            self._recover()
            raise
        self._finish_commit()

    def _staged_names(self):
        names = self._listdir(self.staging)
        for name in names:
            if not self._allowed.fullmatch(name):
                raise ValueError(f'{name} is not one of the files this command publishes')
            if not self._is_regular(self.staging / name):
                raise ValueError(f'{name} is not a regular file')
        if not names:
            raise ValueError('nothing was staged')
        return names

    def _generation_names(self):
        names = []
        for name in self._listdir(self.output):
            if self._allowed.fullmatch(name):
                if not self._is_regular(self.output / name):
                    raise OSError(errno.EEXIST, f'{self.output / name} is not a regular file; move it away first')
                names.append(name)
        return names

    def _backup(self, source, backup):
        if self._present(backup):
            self._unlink(backup)
        try:
            self._link(source, backup)
            return
        except OSError:
            pass
        partial = self._partial(backup)
        self._copy(source, partial)
        self._replace(partial, backup)

    # -- recovery ---------------------------------------------------------------------------------------------------
    def _unreadable(self, path, why):
        return NumericContractError(
            self._code,
            '%s cannot be used (%s); nothing in %s was changed. Fix the cause and run the command again, or restore the '
            'previous files from %s by hand and delete the .%s.* control files'
            % (path, why, self.output, self._backups(), self._stem))

    def _discard_material(self):
        """Delete the backups, then the journal (partial copies too); False (nothing more deleted) at the first error."""
        try:
            backups = self._backups()
            if self._present(backups):
                for name in self._listdir(backups):
                    self._unlink(backups / name)
                self._rmdir(backups)
            self._unlink(self._partial(self._journal()))
            self._unlink(self._journal())
        except OSError:
            return False
        return True

    def _finish_commit(self):
        """Roll forward: the marker exists, so the new generation is final; drop the rollback material, marker last."""
        if self._discard_material():
            try:
                self._unlink(self._partial(self._marker()))
                self._unlink(self._marker())
            except OSError:
                pass

    def _recover(self):
        """Forward when the commit marker exists, otherwise back. Idempotent; fails closed on unreadable state."""
        try:
            committed = self._present(self._marker())
        except OSError as exc:
            raise self._unreadable(self._marker(), exc) from exc
        if committed:
            self._finish_commit()
        else:
            self._rollback()

    def _read_journal(self):
        journal = self._journal()
        try:
            data = json.loads(self._read_text(journal))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:  # unreadable or corrupt: unknown how far the publication got
            raise self._unreadable(journal, exc) from exc
        if (not isinstance(data, dict) or set(data) != {'schema', 'old', 'new'} or data['schema'] != JOURNAL_SCHEMA
                or any(not isinstance(data[key], list) or len(set(data[key])) != len(data[key])
                       or any(not isinstance(name, str) or not self._allowed.fullmatch(name) for name in data[key])
                       for key in ('old', 'new'))):
            raise self._unreadable(journal, 'unexpected content')
        return data

    def _rollback(self):
        """Restore the generation recorded in the journal; idempotent, safe to repeat after a crash."""
        data = self._read_journal()
        if data is None:  # the journal is renamed into place first: no journal, nothing was replaced
            self._unlink(self._partial(self._journal()))
            self._unlink(self._partial(self._marker()))
            return
        old, new = data['old'], data['new']
        try:
            has_prev = {name: self._present(self._backups() / name) for name in old}
            for name in old:
                if not has_prev[name] and not self._present(self.output / name):
                    raise self._unreadable(self.output / name, 'neither the file nor its backup exists')
        except OSError as exc:
            raise self._unreadable(self.output, exc) from exc
        for name in old:
            if has_prev[name]:
                self._replace(self._backups() / name, self.output / name)
        for name in new:
            if name not in old:
                self._unlink(self.output / name)
        self._unlink(self._partial(self._marker()))
        self._discard_material()
