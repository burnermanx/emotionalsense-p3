#!/bin/sh
# Every repository route must still reach gameplay. Run with the null renderer:
# the software renderer is 52-72 ms/frame on the music select and would make
# this test slow and noisy.
set -eu
# The game prints nothing by default; every check below reads its log.
export EMOTIONALSENSE_LOG=1
root=$(cd "$(dirname "$0")/.." && pwd)
game=${EMOTIONALSENSE_GAME:-"$root/build/game/EmotionalSense-P3-game"}
data=${ES_CHECK_DATA:-"$root/work/data"}   # read-only source: linked/copied below, never run against

# The game rewrites SD.BIN while it runs, so the routes must never see the real
# save: the read-only parts are linked, ms/ is copied to a scratch directory.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT INT TERM
ln -s "$data/disc" "$tmp/disc"
ln -s "$data/module" "$tmp/module"
cp -a "$data/ms" "$tmp/ms"
# ES_CHECK_UPDATE=<DATA.TPA of the 1.02 update>: run every route with the update
# overlay active (the scene word moves with it, so the poll below is "scene").
if [ -n "${ES_CHECK_UPDATE:-}" ]; then mkdir "$tmp/update"; cp "$ES_CHECK_UPDATE" "$tmp/update/DATA.TPA"; fi
# Guard: the directory the game runs on must be the scratch copy, never work/data.
case $(cd "$tmp" && pwd -P) in */work/data) echo "refusing to run on work/data" >&2; exit 1 ;; esac

# check <script> <frames> [scene] [confirm]: the route must reach <scene>
# (default 3, gameplay); with confirm=cross the game runs in cross-confirm mode.
check() {
    script=$1
    frames=$2
    scene=${3:-3}
    confirm=${4:-circle}
    log=$(mktemp)
    EMOTIONALSENSE_CONFIRM=$confirm \
    EMOTIONALSENSE_SCRIPT="@$script" EMOTIONALSENSE_POLL=scene \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$tmp" --frames "$frames" --renderer null \
        >/dev/null 2>"$log" || true
    want=$(printf '0x0805E874 = 0x%08X' "$scene")
    if ! grep -q "$want" "$log"; then
        echo "FAIL: $(basename "$script") ($confirm) did not reach scene $scene" >&2
        grep "poll\[" "$log" | tail -5 >&2 || true
        rm -f "$log"
        return 1
    fi
    rm -f "$log"
    echo "OK: $(basename "$script") ($confirm) reaches scene $scene"
}

check "$root/scripts/gameplay_fast.script" 1400
check "$root/scripts/screens_fast.script" 1900
# The unlock fix must leave the song list usable: 4T and 4.2T both enter a
# song that the original save had locked.
check "$root/scripts/freestyle_4t.script" 2300
check "$root/scripts/freestyle_select.script" 2300
# 4.2T from PLAY MODE SELECT, not from the style key in the list.
check "$root/scripts/mode_42t.script" 2400
check "$root/scripts/mode_62t.script" 2400

# The same routes in cross-confirm mode, with circle and cross swapped in the
# menu presses (0x2000 <-> 0x4000). They prove the menu swap lands on the same
# screens; a circle-confirm script under --confirm cross must NOT get there
# (checked by hand). LOUNGE is scene 9.
check "$root/scripts/gameplay_fast_cross.script" 1400 3 cross
check "$root/scripts/screens_fast_cross.script" 1900 3 cross
check "$root/scripts/freestyle_4t_cross.script" 2300 3 cross
check "$root/scripts/freestyle_select_cross.script" 2300 3 cross
check "$root/scripts/mode_42t_cross.script" 2400 3 cross
check "$root/scripts/mode_62t_cross.script" 2400 3 cross
check "$root/scripts/lounge_cross.script" 1900 9 cross
check "$root/scripts/lounge.script" 1900 9

# --- Fever mode (FV). The route holds the life, widens the MAX window so the gauge
# fills, never presses CROSS (the Fever button) and forces the game's own auto-fever
# flag (F+0x148) to 0, so only the host's Respect mode can start the Fever. The poll
# is the Fever-running byte (F+0x13B) through the scene machine, no heap address.
J='*0xC8B30+0*+0xC*'
fever_pokes="${J}+0x7FC:0x42C80000,${J}+0x560*+0x10:0x7f:1,${J}+0x564*+0x148:0:1"
check_fever() {   # mode expectation(1 = Fever must start, 0 = must not)
    log=$(mktemp)
    EMOTIONALSENSE_FEVER=$1 EMOTIONALSENSE_POKE="$fever_pokes" EMOTIONALSENSE_POLL="${J}+0x564*+0x13B:b" \
    EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/fever_lanes.script" \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$tmp" --frames 5700 --renderer null >/dev/null 2>"$log" || true
    if grep -q '= 0x00000001' "$log"; then got=1; else got=0; fi
    rm -f "$log"
    if [ "$got" != "$2" ]; then echo "FAIL: fever mode $1 (Fever started: $got, expected $2)" >&2; return 1; fi
    echo "OK: fever $1 ($([ "$2" = 1 ] && echo starts by itself || echo waits for the button))"
}
check_fever respect 1
check_fever original 0

# --- Exit from the title (EX): No keeps the title, Yes exits with code 0.
check_exit() {
    log=$(mktemp)
    rc=0
    EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/exit_title.script" EMOTIONALSENSE_POLL=scene \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$tmp" --frames 4000 --renderer null >/dev/null 2>"$log" || rc=$?
    ok=1
    [ "$rc" = 0 ] || ok=0
    [ "$(grep -c 'exit: confirmation opened' "$log")" = 2 ] || ok=0
    grep -q 'exit: dialog closed, staying' "$log" || ok=0
    grep -q 'exit: yes, closing the game' "$log" || ok=0
    # still the title when it left: the scene word never moved past 1
    if grep 'poll\[' "$log" | grep -q '0x0805E874 = 0x0000000[2-9]'; then ok=0; fi
    if [ "$ok" != 1 ]; then echo "FAIL: exit from the title (rc=$rc)" >&2; grep -E 'exit:|poll\[' "$log" | tail -8 >&2; rm -f "$log"; return 1; fi
    rm -f "$log"
    echo "OK: exit_title.script: No stays on the title, Yes exits with code 0"
}
check_exit

# --- Respect scoring (SC), display only. Both routes play a whole song (about two
# minutes each on the null renderer) with the life held, then stay on the result
# screen. The host logs "score: result ... respect=N (weights W / C notes ...)".
#   score_max, respect:   every judgment MAX -> exactly 1000000 on the result screen, and
#                         es_scores.json holds it
#   score_max, original:  the game's own score is shown (no "shows Respect" line) and
#                         the Respect record is still written
#   score_low, respect:   N == round(1000000 * W / (100 * C)) in exact integers
check_score() {   # name route mode expectation(max|formula|original) pokes
    log=$(mktemp); sdata=$(mktemp -d)
    ln -s "$data/disc" "$sdata/disc"; ln -s "$data/module" "$sdata/module"; cp -a "$data/ms" "$sdata/ms"
    case $(cd "$sdata" && pwd -P) in */work/data) echo "refusing to run on work/data" >&2; return 1 ;; esac
    EMOTIONALSENSE_SCORE=$3 EMOTIONALSENSE_POKE="$5" \
    EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/$2.script" EMOTIONALSENSE_POLL=scene \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$sdata" --frames 8300 --renderer null >/dev/null 2>"$log" || true
    ok=1
    line=$(grep 'score: result style=' "$log" | tail -1)
    [ -n "$line" ] || ok=0
    n=$(printf '%s' "$line" | sed -n 's/.*respect=\([0-9]*\) .*/\1/p')
    w=$(printf '%s' "$line" | sed -n 's/.*weights \([0-9]*\) \/ .*/\1/p')
    c=$(printf '%s' "$line" | sed -n 's/.*\/ \([0-9]*\) notes.*/\1/p')
    shown=$(grep -c 'score: result shows Respect' "$log" || true)
    [ -f "$sdata/es_scores.json" ] && grep -q ": $n" "$sdata/es_scores.json" || ok=0
    case $4 in
        max)      [ "$n" = 1000000 ] && [ "$shown" = 1 ] || ok=0 ;;
        formula)  [ -n "$w" ] && [ "$c" -gt 0 ] && [ "$n" = $(( (w * 10000 + c / 2) / c )) ] && [ "$shown" = 1 ] || ok=0 ;;
        original) [ "$shown" = 0 ] || ok=0 ;;
    esac
    if [ "$ok" != 1 ]; then echo "FAIL: score $1 ($line)" >&2; rm -rf "$log" "$sdata"; return 1; fi
    rm -rf "$log" "$sdata"
    echo "OK: score $1: Respect $n from $w / $c"
}
life="${J}+0x7FC:0x42C80000"
check_score "max respect" score_max respect max "$life,${J}+0x560*+0x10:0x7f:1"
check_score "max original" score_max original original "$life,${J}+0x560*+0x10:0x7f:1"
check_score "low formula" score_low respect formula "$life"
