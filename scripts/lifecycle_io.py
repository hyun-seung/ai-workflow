"""Private, bounded IO for explicit local lifecycle operations."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from native_record import unique_object, reject_constant
from prepare_handoff import private_destination


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read_json(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('lifecycle_input_too_large')
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def private_file(path, project):
    path = private_destination(path, project)
    if path.exists():
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('lifecycle_file_not_private')
    return path


def save(path, value, project):
    path = private_file(path, project)
    fd, temporary = tempfile.mkstemp(prefix='.lifecycle-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path, project):
    path = private_file(path, project)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('lifecycle_already_running') from None
        yield
    finally:
        os.close(fd)


def orca(cli, *args):
    # Explicit local host prevents inherited remote routing. No shell or fallback.
    result = subprocess.run([str(cli), *args, '--environment', 'local', '--json'],
                            capture_output=True, text=True, timeout=30)
    if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
        raise ValueError('orca_failed_use_formal_permission_path')
    data = json.loads(result.stdout, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(data, dict) or data.get('ok') is not True or not isinstance(data.get('result'), dict):
        raise ValueError('orca_unconfirmed_response')
    return data


def exited(snapshot, task_id, dispatch_id):
    result = snapshot.get('result', {})
    dispatch, worker, observation = (result.get(k, {}) for k in ('dispatch', 'worker', 'observation'))
    if (snapshot.get('ok') is not True or dispatch.get('id') != dispatch_id or
            dispatch.get('taskId') != task_id or worker.get('dispatchId') != dispatch_id):
        raise ValueError('worker_identity_mismatch')
    if (dispatch.get('hostScope', {}).get('kind') != 'local' or
            dispatch.get('status') not in {'completed', 'failed', 'cancelled'} or
            worker.get('state') not in {'succeeded', 'failed', 'cancelled'} or
            observation.get('exactWorker') is not True or observation.get('status') != 'exited'):
        raise ValueError('worker_exit_unconfirmed')
