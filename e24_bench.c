/*
** e24_bench.c - commit-shaped cost benchmark for the Windows dir-sync patch.
**
** Runs N transactions in rollback-journal (DELETE) mode, each
** BEGIN IMMEDIATE / INSERT / COMMIT, so every commit creates a journal
** (whose first xSync fires winSyncDirectory via WINFILE_DIRSYNC) and under
** synchronous=EXTRA also deletes it with syncDir=1.  Prints per-transaction
** wall time, TPS, and the winSyncDirectory hook counter, and exits 3 when
** the counter is below the expected minimum (hook-dead guard: a hook that
** never runs cannot make a namespace change durable).
**
** Usage: e24_bench <db-path> <txns> [warmup] [FULL|EXTRA] [expect-min-count]
*/
#include "sqlite3.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

extern int sqlite3_win_dir_sync_count;

static void xexec(sqlite3 *db, const char *sql, const char *ctx){
  char *em = 0;
  if( sqlite3_exec(db, sql, 0, 0, &em)!=SQLITE_OK ){
    fprintf(stderr, "exec failed (%s): %s: %s\n", ctx, sql, em?em:"?");
    exit(2);
  }
}

int main(int argc, char **argv){
  const char *zDb;
  sqlite3 *db = 0;
  char sql[256];
  char filler[201];
  LARGE_INTEGER freq, st, now;
  int nTxn, nWarm, expect, i, before, after;
  double ms;

  if( argc<3 ){
    fprintf(stderr, "usage: e24_bench <db> <txns> [warmup] [FULL|EXTRA] [expect-min]\n");
    return 1;
  }
  zDb = argv[1];
  nTxn = atoi(argv[2]);
  nWarm = argc>3 ? atoi(argv[3]) : 50;
  {
    const char *mode = argc>4 ? argv[4] : "FULL";
    int expectArg = argc>5 ? atoi(argv[5]) : 0;
    expect = expectArg;
    memset(filler, 'x', 200);
    filler[200] = 0;

    DeleteFileA(zDb);
    if( sqlite3_open(zDb, &db)!=SQLITE_OK ){
      fprintf(stderr, "open failed: %s\n", sqlite3_errmsg(db));
      return 1;
    }
    sprintf(sql, "PRAGMA synchronous=%s;", mode);
    xexec(db, sql, "sync pragma");
    xexec(db, "PRAGMA journal_mode=DELETE;", "journal mode");
    xexec(db, "CREATE TABLE t(a INTEGER PRIMARY KEY, b TEXT);", "schema");

    /* warmup, not measured; the hook counter is reset afterwards */
    before = sqlite3_win_dir_sync_count;
    for(i=0; i<nWarm; i++){
      xexec(db, "BEGIN IMMEDIATE;", "begin(w)");
      sprintf(sql, "INSERT INTO t VALUES(%d,'%s');", i, filler);
      xexec(db, sql, "insert(w)");
      xexec(db, "COMMIT;", "commit(w)");
    }
    sqlite3_win_dir_sync_count = before;

    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&st);
    for(i=0; i<nTxn; i++){
      xexec(db, "BEGIN IMMEDIATE;", "begin");
      sprintf(sql, "INSERT INTO t VALUES(%d,'%s');", nWarm + i, filler);
      xexec(db, sql, "insert");
      xexec(db, "COMMIT;", "commit");
    }
    QueryPerformanceCounter(&now);
    after = sqlite3_win_dir_sync_count;
    ms = (double)(now.QuadPart - st.QuadPart) * 1000.0 / (double)freq.QuadPart;
    printf("%s: %.1f ms total | %.3f ms/txn | %.1f TPS | dir_sync_count=%d (expected >= %d)\n",
           zDb, ms, ms/(double)nTxn, (double)nTxn*1000.0/ms, after, expect);
    sqlite3_close(db);
    {
      char zJrnl[1024];
      sprintf(zJrnl, "%s-journal", zDb);
      DeleteFileA(zJrnl);
    }
    DeleteFileA(zDb);
    if( after < expect ){
      fprintf(stderr, "HOOK DEAD: dir_sync_count=%d expected>=%d\n", after, expect);
      return 3;
    }
    return 0;
  }
}
