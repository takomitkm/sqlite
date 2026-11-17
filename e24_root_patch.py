# -*- coding: utf-8 -*-
"""e24_root_patch.py - generate the bench variants from the committed
SQLite dir-sync patch (sqlite3-patch.c) and the vanilla amalgamation
(sqlite3-base.c), on the takomitkm/sqlite "bench" branch.

Variants:
  base     counter only (hook absent; counter stays 0 all run)  [in: sqlite3-base.c]
  dir      counter added to the existing dir-only patch         [in: sqlite3-patch.c]
  dirroot  counter + volume-root flush after the dir flush      [in: sqlite3-patch.c]

Every edit is anchored; an anchor that does not match exactly once makes the
script exit non-zero without writing the output (fail closed).  Run:
    python e24_root_patch.py --variant dirroot --input sqlite3-patch.c --output sqlite3-dirroot.c
"""
import argparse
import sys

COUNTER_DEF = """\
/* Benchmark-visible hook counter: incremented once per successful
** winSyncDirectory() call.  A hook that never runs cannot make a namespace
** change durable, so the benchmarks read this to prove the hook fired
** (expected: one increment per rollback-journal transaction, from the
** journal's first xSync; a second increment per transaction appears under
** synchronous=EXTRA, whose journal deletion passes syncDir=1). */
SQLITE_API int sqlite3_win_dir_sync_count = 0;

"""

DOC_APPEND = """**
** After the parent-directory flush succeeds, and only when the containing
** directory is an absolute local path with a drive-letter prefix, the
** volume root ("X:\\") is flushed as well.  The measured NTFS behavior is
** that FlushFileBuffers() on a subdirectory handle issues the disk flush
** IRP but writes no metadata - the new file's MFT record and the directory
** index stay in memory - while FlushFileBuffers() on the volume root issues
** the same IRP and persists that metadata (block-command tracing in QEMU,
** E17; ETW flush traces on real Windows kernels, E21; per-commit cost,
** E23 - archived under docs/powercut/ in takomitkm/leveldb).  When the
** containing directory IS the volume root, the single flush above already
** covers it and no second flush is made.  Paths without a drive-letter
** prefix (UNC shares, relative names) keep only the parent-directory
** flush, and the volume-root flush carries the same error policy and the
** same MS-FSA caveat as the parent-directory flush above.
*/

"""

TAIL_ANCHOR = """    if( !osCloseHandle(hDir) && rc==SQLITE_OK ){
      rc = winLogError(SQLITE_IOERR_DIR_FSYNC, osGetLastError(),
                       "winSyncDirectory", zPath);
    }
  }
  sqlite3_free(zDir);
  return rc;
}
"""

COUNTER_TAIL = """    if( !osCloseHandle(hDir) && rc==SQLITE_OK ){
      rc = winLogError(SQLITE_IOERR_DIR_FSYNC, osGetLastError(),
                       "winSyncDirectory", zPath);
    }
  }
  if( rc==SQLITE_OK ){
    sqlite3_win_dir_sync_count++;
  }
  sqlite3_free(zDir);
  return rc;
}
"""

ROOT_TAIL = """    if( !osCloseHandle(hDir) && rc==SQLITE_OK ){
      rc = winLogError(SQLITE_IOERR_DIR_FSYNC, osGetLastError(),
                       "winSyncDirectory", zPath);
    }
  }
  if( rc==SQLITE_OK ){
    sqlite3_win_dir_sync_count++;
    if( zDir[0]!=L'.' && zDir[1]==L':' && zDir[2]!=0 && zDir[3]!=0 ){
      /* The containing directory is not the volume root itself: flush the
      ** volume root "X:\\" too.  Same handle shape and same error policy
      ** as the parent-directory flush above. */
      WCHAR zRoot[4];
      zRoot[0] = zDir[0];
      zRoot[1] = L':';
      zRoot[2] = L'\\\\';
      zRoot[3] = 0;
      hDir = osCreateFileW(zRoot,
                           GENERIC_WRITE,
                           FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
                           NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS,
                           NULL);
      if( hDir==INVALID_HANDLE_VALUE ){
        rc = winLogError(SQLITE_IOERR_DIR_FSYNC, osGetLastError(),
                         "winSyncVolumeRoot", zPath);
      }else{
        if( !osFlushFileBuffers(hDir) ){
          rc = winLogError(SQLITE_IOERR_DIR_FSYNC, osGetLastError(),
                           "winSyncVolumeRoot", zPath);
        }
        if( !osCloseHandle(hDir) && rc==SQLITE_OK ){
          rc = winLogError(SQLITE_IOERR_DIR_FSYNC, osGetLastError(),
                           "winSyncVolumeRoot", zPath);
        }
      }
    }
  }
  sqlite3_free(zDir);
  return rc;
}
"""

DIR_ANCHOR = "static int winSyncDirectory(const char *zPath){"
BASE_ANCHOR = """static int winDelete(
  sqlite3_vfs *pVfs,          /* Not used on win32 */
"""
DOC_ANCHOR = """** Whether filesystem-specific errors indicating an unsupported
** directory flush should be treated differently is a policy question
** for review.
*/
"""


def replace_once(text, old, new, what):
    n = text.count(old)
    if n != 1:
        sys.exit(f'FAIL: anchor {what!r} matched {n} times (expected exactly 1)')
    return text.replace(old, new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', choices=['base', 'dir', 'dirroot'], required=True)
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    a = ap.parse_args()

    with open(a.input, encoding='utf-8', newline='') as f:
        text = f.read()

    if a.variant == 'base':
        text = replace_once(text, BASE_ANCHOR, COUNTER_DEF + BASE_ANCHOR,
                            'base winDelete definition')
    else:
        text = replace_once(text, DIR_ANCHOR, COUNTER_DEF + DIR_ANCHOR,
                            'winSyncDirectory definition')
        text = replace_once(text, TAIL_ANCHOR, COUNTER_TAIL, 'dir counter tail')
        if a.variant == 'dirroot':
            text = replace_once(text, DOC_ANCHOR, DOC_APPEND, 'doc paragraph')
            text = replace_once(text, COUNTER_TAIL, ROOT_TAIL, 'root flush tail')

    with open(a.output, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    print(f'OK: {a.variant} -> {a.output} ({len(text)} chars)')


if __name__ == '__main__':
    main()
