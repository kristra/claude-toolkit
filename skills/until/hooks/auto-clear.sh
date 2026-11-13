#!/bin/sh
# auto-clear.sh: type "/clear" and a follow-up prompt into one iTerm session.
#
# PROTOTYPE. Not wired into the gate. Claude Code has no way for a hook or
# the model to run /clear, so this types it from outside, through iTerm's
# AppleScript `write text`, addressed to the session by its unique id (it
# does not need focus). Needs macOS automation permission for iTerm, once.
#
#   auto-clear.sh [--session ID] [--delay S] [--gap S] [--dry-run] PROMPT
#
#   --session ID  iTerm session id; default: $ITERM_SESSION_ID (the part
#                 after the colon is used, so "w0t2p0:UUID" is fine)
#   --delay S     wait before typing, so the turn has ended (default 2)
#   --gap S       wait between /clear and the prompt (default 3)
#   --dry-run     find the session and report, type nothing
#
# Refuses to type (exit 3) when a turn is running (spinner line such as
# "✽ Thinking… (42s · ..."). The prompt box cannot be read reliably (an empty
# box shows a dim suggestion), so it sends Ctrl-U before each line: a draft
# the user was typing in that tab is dropped, not submitted.
# Exit: 0 typed (or dry-run found it), 2 bad usage / session not found,
# 3 session busy.
SESSION=${ITERM_SESSION_ID:-}; DELAY=2; GAP=3; DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --session) SESSION=$2; shift 2 ;;
    --delay) DELAY=$2; shift 2 ;;
    --gap) GAP=$2; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --) shift; break ;;
    -*) echo "unknown option $1" >&2; exit 2 ;;
    *) break ;;
  esac
done
PROMPT=$*
SESSION=${SESSION##*:}
[ -n "$SESSION" ] || { echo "no iTerm session id (not in iTerm?)" >&2; exit 2; }
[ -n "$PROMPT" ] || [ "$DRY" = 1 ] || { echo "no follow-up prompt given" >&2; exit 2; }
case "$PROMPT" in *'
'*) echo "prompt must be one line" >&2; exit 2 ;; esac

sleep "$DELAY"

# $1 = action (tail | type), rest passed through. Prints the result.
iterm() {
  osascript - "$SESSION" "$@" <<'OSA'
on run argv
  set sid to item 1 of argv
  set act to item 2 of argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if (id of s) is sid then
            if act is "tail" then
              set c to contents of s
              if length of c > 1500 then set c to text -1500 thru -1 of c
              return c
            end if
            -- Ctrl-U first: drop whatever sits in the prompt box
            tell s to write text (ASCII character 21) newline no
            -- Text and Enter go separately: a long line arriving together
            -- with its newline is taken as a paste and not submitted.
            tell s to write text (item 3 of argv) newline no
            delay 0.5
            tell s to write text (ASCII character 13) newline no
            return name of s
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "__NOT_FOUND__"
end run
OSA
}

TAIL=$(iterm tail) || { echo "iTerm AppleScript failed" >&2; exit 2; }
case "$TAIL" in *__NOT_FOUND__*) echo "session not found: $SESSION" >&2; exit 2 ;; esac
# Running turn: spinner line "<glyph> Word…", with or without "(12s · ...".
if printf '%s\n' "$TAIL" | grep -Eq '^[^[:alnum:][:space:]] [[:alpha:]]+… *(\(|$)'; then
  echo "session busy (a turn is running)" >&2; exit 3
fi
if [ "$DRY" = 1 ]; then echo "idle, would type: /clear ; $PROMPT"; exit 0; fi

iterm type "/clear" >/dev/null || exit 2
sleep "$GAP"
NAME=$(iterm type "$PROMPT") || exit 2
echo "typed into: $NAME"
