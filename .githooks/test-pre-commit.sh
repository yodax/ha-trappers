#!/bin/bash
# test-pre-commit.sh — proves .githooks/pre-commit actually blocks, rather than
# assuming it does.
#
# WHY THIS EXISTS: the hook's first version used a grep pipeline that errored out under
# ugrep (this machine's `grep`) and, with `|| true` swallowing the error, exited 0 on a
# staged diff containing a live corporate email address. It looked installed and correct
# and guarded nothing. A leak guard is only worth having if something checks it.
#
# Every string below is synthetic. The identity-specific patterns the real hook also
# loads are deliberately NOT in this repo (see the hook's header), so this script tests
# the *loading mechanism* with a throwaway pattern file and a canary string instead —
# which proves the private half works without publishing any of it.
#
# Run from the repo root:  .githooks/test-pre-commit.sh
# Exits 0 only if every case behaves as expected.
set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel) || exit 1
HOOK="$REPO_ROOT/.githooks/pre-commit"
[ -x "$HOOK" ] || { echo "FAIL: $HOOK is not executable"; exit 1; }

WORK=$(mktemp -d) || { echo "FAIL: could not create a scratch directory"; exit 1; }
[ -d "$WORK" ] || { echo "FAIL: scratch directory missing"; exit 1; }
trap 'rm -rf "$WORK"' EXIT

pass=0
fail=0
case_n=0

# Each case gets a FRESH scratch repo. An earlier draft reused one, and state left over
# from prior cases (a deleted HEAD plus a still-populated index) made a later `git commit`
# find nothing to commit — which the assertion then read as "the hook blocked it". A test
# harness that reports the wrong reason is the same fail-open trap the hook itself fell
# into; isolation is cheaper than interpreting the residue.
fresh_repo() {
  case_n=$((case_n + 1))
  SCRATCH="$WORK/case$case_n"
  git init -q -b main "$SCRATCH" || {
    echo "FAIL: could not create scratch repo (harness broken, not a hook verdict)"
    exit 1
  }
  git -C "$SCRATCH" config user.name "test"
  git -C "$SCRATCH" config user.email "test@example.com"
  git -C "$SCRATCH" config core.hooksPath "$REPO_ROOT/.githooks"
}

# Default: no identity pattern file, so only the generic half is exercised. Individual
# cases override TRAPPERS_LEAK_PATTERNS where they need the private half.
export TRAPPERS_LEAK_PATTERNS="$WORK/no-such-pattern-file.txt"

# _commit <content> → 0 if the commit went through, 1 if the hook blocked it.
# Output of the attempt is left in $LAST_OUT for assertions that inspect it.
_commit() {
  fresh_repo
  local path="${PROBE_PATH:-probe.txt}"
  mkdir -p "$SCRATCH/$(dirname "$path")"
  printf '%s\n' "$1" > "$SCRATCH/$path"
  git -C "$SCRATCH" add "$path"
  local rc=0
  LAST_OUT=$(git -C "$SCRATCH" commit -m "probe" 2>&1) || rc=1
  # A commit that produced no new commit is a harness bug, not a hook verdict.
  if [ "$rc" -eq 0 ] && ! git -C "$SCRATCH" rev-parse --verify -q HEAD >/dev/null; then
    echo "HARNESS BUG: commit reported success but created no commit:"
    printf '%s\n' "$LAST_OUT" | sed 's/^/    /'
    return 2
  fi
  return "$rc"
}

# _commit_msg <message> → 0 if the commit went through, 1 if a hook blocked it.
# Content is benign; only the message varies, so this isolates the commit-msg hook.
_commit_msg() {
  fresh_repo
  printf 'nothing to see here\n' > "$SCRATCH/probe.txt"
  git -C "$SCRATCH" add probe.txt
  local rc=0
  LAST_OUT=$(git -C "$SCRATCH" commit -m "$1" 2>&1) || rc=1
  if [ "$rc" -eq 0 ] && ! git -C "$SCRATCH" rev-parse --verify -q HEAD >/dev/null; then
    echo "HARNESS BUG: commit reported success but created no commit:"
    printf '%s\n' "$LAST_OUT" | sed 's/^/    /'
    return 2
  fi
  return "$rc"
}

# msg_should_block <name> <message> [expected marker]
msg_should_block() {
  local expected="${3:-$MESSAGE_BLOCK}"
  local rc=0
  _commit_msg "$2" || rc=$?
  case "$rc" in
    0) echo "FAIL: commit-msg did NOT block: $1"; fail=$((fail + 1)) ;;
    1) if printf '%s' "$LAST_OUT" | grep -qF "$expected"; then
         echo "ok:   blocked $1"; pass=$((pass + 1))
       else
         echo "FAIL: commit failed but not via the leak check: $1"
         printf '%s\n' "$LAST_OUT" | sed 's/^/    /'; fail=$((fail + 1))
       fi ;;
    *) echo "FAIL: harness error on: $1"; fail=$((fail + 1)) ;;
  esac
}

msg_should_pass() {
  local rc=0
  _commit_msg "$2" || rc=$?
  case "$rc" in
    0) echo "ok:   allowed $1"; pass=$((pass + 1)) ;;
    1) echo "FAIL: commit-msg blocked a clean message: $1"
       printf '%s\n' "$LAST_OUT" | sed 's/^/    /'; fail=$((fail + 1)) ;;
    *) echo "FAIL: harness error on: $1"; fail=$((fail + 1)) ;;
  esac
}

# A commit can fail for reasons that have nothing to do with the content under
# test — a broken scratch repo, a git that would not run, or the OTHER hook
# objecting to something else. Counting any failure as "blocked" is the same
# fail-open the hook itself once had, one level up.
#
# So a block counts only if the scanner named the gate that fired. Matching a
# bare "leak check:" is not enough and was actively wrong: that prefix is also
# printed on the SUCCESS path ("generic patterns only — no identity pattern
# file"), which every case in this suite triggers, so the marker discriminated
# nothing. Even "leak check: BLOCKED" is too loose — a commit-msg refusal would
# satisfy a content assertion. Hence the scope.
CONTENT_BLOCK="leak check: BLOCKED (staged changes)"
MESSAGE_BLOCK="leak check: BLOCKED (commit message)"
# The two abort paths refuse for a different, legitimate reason and say so in
# their own words. They get explicit markers rather than the default being
# loosened to accommodate them.
ABORT_BAD_REGEX="is not a valid regex"
ABORT_EMPTY="exists but defines no patterns"

# should_block <name> <content> [expected marker]
should_block() {
  local expected="${3:-$CONTENT_BLOCK}"
  local rc=0
  _commit "$2" || rc=$?
  case "$rc" in
    0) echo "FAIL: hook did NOT block: $1"; fail=$((fail + 1)) ;;
    1) if printf '%s' "$LAST_OUT" | grep -qF "$expected"; then
         echo "ok:   blocked $1"; pass=$((pass + 1))
       else
         echo "FAIL: commit failed but not via the leak check: $1"
         printf '%s\n' "$LAST_OUT" | sed 's/^/    /'; fail=$((fail + 1))
       fi ;;
    *) echo "FAIL: harness error on: $1"; fail=$((fail + 1)) ;;
  esac
}

should_pass() {
  local rc=0
  _commit "$2" || rc=$?
  case "$rc" in
    0) echo "ok:   allowed $1"; pass=$((pass + 1)) ;;
    1) echo "FAIL: hook blocked a clean commit: $1"
       printf '%s\n' "$LAST_OUT" | sed 's/^/    /'; fail=$((fail + 1)) ;;
    *) echo "FAIL: harness error on: $1"; fail=$((fail + 1)) ;;
  esac
}

echo "── generic patterns (shipped in the hook) ──"
should_block "RFC1918 192.168 address"  'the box lives at 192.168.8.60'
should_block "RFC1918 10.x address"     'coordinator at 10.4.2.9'
should_block "RFC1918 172.16 address"   'gateway 172.20.0.1'
should_block "loopback address"         'bind to 127.0.0.1:8123'
should_block "pct exec"                 'run pct exec 100 -- docker restart ha'
should_block "hypervisor name"          'copy it onto the Proxmox host first'
should_block "homelab ssh alias"        'ssh pve then restart'
should_block "homelab storage path"     'config at /tank/docker/homeassistant'
should_block "a real JWT" \
  'token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpYXQiOjE3ODAwMDAwMDB9.AbCdEfGhIjK'
should_block "IBAN"                     'ibanAccountNumber: NL91ABNA0417164300'

echo "── benign content must still commit ──"
should_pass  "placeholder email"        'employeeEmail: you@example.com'
should_pass  "sanitized API sample"     '{"balance": 1234.0, "employeeNumber": "..."}'
should_pass  "ordinary python"          'async def async_get_status(self) -> dict:'
should_pass  "public API host"          'BASE_URL = "https://api.trappers.net/api/"'
should_pass  "a version-looking number" 'MIN_HA_VERSION = "2026.3.0"'

echo "── identity pattern file (the private half) ──"
CANARY_FILE="$WORK/patterns.txt"
printf '# synthetic\nZZQQ-CANARY-[0-9]{4}\tsynthetic canary\n' > "$CANARY_FILE"

TRAPPERS_LEAK_PATTERNS="$CANARY_FILE" should_block "canary from pattern file" \
  'secret marker ZZQQ-CANARY-4711 here'
TRAPPERS_LEAK_PATTERNS="$CANARY_FILE" should_pass "non-matching line with file loaded" \
  'secret marker ZZQQ-CANARY-not-a-number here'

# A corrupt pattern file must abort, not silently check with fewer patterns — the same
# fail-open shape that made the first version of this hook worthless.
BAD_FILE="$WORK/bad-patterns.txt"
printf 'ZZQQ-[unclosed\tbroken regex\n' > "$BAD_FILE"
TRAPPERS_LEAK_PATTERNS="$BAD_FILE" should_block "corrupt pattern file aborts the commit" \
  'entirely harmless line' "$ABORT_BAD_REGEX"

# Missing pattern file: the generic half still runs (a contributor has no secrets of the
# maintainer's to leak), and the hook says so rather than implying full coverage.
if TRAPPERS_LEAK_PATTERNS="$WORK/definitely-absent.txt" _commit 'harmless' &&
   printf '%s\n' "$LAST_OUT" | grep -q "generic patterns only"; then
  echo "ok:   missing pattern file warns instead of implying full coverage"
  pass=$((pass + 1))
else
  echo "FAIL: missing pattern file did not warn; output was:"
  printf '%s\n' "${LAST_OUT:-<none>}" | sed 's/^/    /'
  fail=$((fail + 1))
fi

echo "── .githooks/ exemption is asymmetric ──"
# The hook and this file must be able to contain generic patterns — they define and
# exercise them. Without this exemption the gate blocks its own test fixtures, which is
# exactly what happened on the third commit attempt.
PROBE_PATH=".githooks/fixture.sh" should_pass "generic pattern inside .githooks/" \
  'should_block "LAN" "the box lives at 192.168.8.60"'
# But the exemption must NOT cover identity patterns: putting a real secret in the hook
# is the v2 mistake, and .githooks/ is precisely where it would land.
PROBE_PATH=".githooks/fixture.sh" TRAPPERS_LEAK_PATTERNS="$CANARY_FILE" \
  should_block "identity pattern inside .githooks/ is still caught" \
  'ZZQQ-CANARY-4711'
# And outside .githooks/, generic patterns still apply.
PROBE_PATH="custom_components/trappers/api.py" should_block \
  "generic pattern outside .githooks/ still blocked" 'HOST = "192.168.8.60"'

echo
echo "── a '++' content line is content, not a diff header ──"
# A staged line reading "++ x" renders as "+++ x" in the diff. Treating any "+++ "
# line as the file header let an identity canary on such a line through unchecked.
CANARY_FILE2="$WORK/patterns2.txt"
printf '# synthetic\nZZQQ-CANARY-[0-9]{4}\tsynthetic canary\n' > "$CANARY_FILE2"
TRAPPERS_LEAK_PATTERNS="$CANARY_FILE2" \
  should_block "canary on a line beginning with ++" '++ ZZQQ-CANARY-1234'
TRAPPERS_LEAK_PATTERNS="$CANARY_FILE2" \
  should_block "canary on a line beginning with +++" '+++ ZZQQ-CANARY-1234'
should_pass  "an ordinary ++ line with nothing secret" '++ just a diff-looking line'

echo "── an existing but empty pattern file must not pass silently ──"
EMPTY_FILE="$WORK/empty-patterns.txt"
printf '# only comments, no patterns\n\n' > "$EMPTY_FILE"
TRAPPERS_LEAK_PATTERNS="$EMPTY_FILE" \
  should_block "empty pattern file aborts rather than running with no identity cover" \
  'perfectly ordinary content' "$ABORT_EMPTY"

echo "── commit messages are scanned too ──"
msg_should_pass  "an ordinary commit message" 'Fix the paging offset'
msg_should_block "a LAN address in the message" 'Deploy tested against 192.168.8.60'
msg_should_block "a homelab path in the message" 'copied into /tank/docker/homeassistant'
msg_should_block "an IBAN in the message" 'removed NL91ABNA0417164300 from the fixture'
TRAPPERS_LEAK_PATTERNS="$CANARY_FILE2" \
  msg_should_block "an identity canary in the message" 'redacted ZZQQ-CANARY-1234 from README'

echo "── a '#' line in a commit message is still published ──"
# git commit -m and -F use cleanup=whitespace, which does NOT strip comments.
msg_should_block "a commented-out LAN address in the message" '# staged on 192.168.8.60'
TRAPPERS_LEAK_PATTERNS="$CANARY_FILE2" \
  msg_should_block "a commented-out identity canary" '# ZZQQ-CANARY-1234'

echo "── a non-ASCII filename must still be scanned ──"
# With core.quotePath on, --name-only renders "café.txt" C-escaped; feeding that
# back as a pathspec matches nothing and yields an empty, unscanned diff.
PROBE_PATH='café.txt' should_block "LAN address in a non-ASCII filename" \
  'the box lives at 192.168.8.60'
unset PROBE_PATH

echo "── the harness itself must not count the wrong gate's refusal ──"
# Regression for a harness bug, not a hook bug: a clean-content commit whose
# MESSAGE is dirty fails, and a content assertion keyed on a bare "leak check:"
# counted that as a content block. Every case in this suite prints that prefix
# on the success path, so it discriminated nothing.
_harness_self_check() {
  fresh_repo
  printf 'perfectly benign content\n' > "$SCRATCH/probe.txt"
  git -C "$SCRATCH" add probe.txt
  local out
  out=$(git -C "$SCRATCH" commit -m 'staged on 192.168.8.60' 2>&1) && return 2
  # The commit must have failed on the MESSAGE...
  printf '%s' "$out" | grep -qF "$MESSAGE_BLOCK" || return 3
  # ...and must NOT satisfy a staged-content assertion.
  printf '%s' "$out" | grep -qF "$CONTENT_BLOCK" && return 4
  return 0
}
_harness_self_check
case "$?" in
  0) echo "ok:   a message block is not counted as a content block"; pass=$((pass + 1)) ;;
  2) echo "FAIL: the dirty message was not blocked at all"; fail=$((fail + 1)) ;;
  3) echo "FAIL: blocked, but not by the commit-msg gate"; fail=$((fail + 1)) ;;
  *) echo "FAIL: a message block still satisfies a content assertion"; fail=$((fail + 1)) ;;
esac

echo "pre-commit leak-guard: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
