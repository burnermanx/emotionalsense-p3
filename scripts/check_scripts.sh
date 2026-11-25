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

# --- RecompLauncher pad hint (RL1): `auto` starts from RECOMPLAUNCHER_PAD before a controller is seen; a named
# style wins over it; no hint = the PlayStation default. The first "buttons: style" line of a short run tells.
check_pad_hint() {   # hint option expected-style(1 playstation, 2 xbox)
    log=$(mktemp)
    RECOMPLAUNCHER_PAD=$1 EMOTIONALSENSE_BUTTONS=$2 EMOTIONALSENSE_CONFIRM=circle SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$tmp" --frames 20 --renderer null >/dev/null 2>"$log" || true
    got=$(grep -m1 '^buttons: style [0-9]*,' "$log" | sed 's/buttons: style \([0-9]*\),.*/\1/')
    rm -f "$log"
    [ "$got" = "$3" ] || { echo "FAIL: RECOMPLAUNCHER_PAD='$1' buttons=$2 gave style '$got', expected $3" >&2; return 1; }
    echo "OK: RECOMPLAUNCHER_PAD='$1' with buttons=$2 starts as style $3"
}
check_pad_hint xbox auto 2
check_pad_hint playstation auto 1
check_pad_hint "" auto 1
check_pad_hint xbox playstation 1
check_pad_hint bogus auto 1

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

# --- Respect speed (SP, round 35). A fresh copy of the save per run (the list remembers the last song).
fresh_data() {   # prints a new data dir (disc/module linked, ms copied), the caller removes it
    d=$(mktemp -d)
    ln -s "$data/disc" "$d/disc"; ln -s "$data/module" "$d/module"; cp -a "$data/ms" "$d/ms"
    case $(cd "$d" && pwd -P) in */work/data) echo "refusing to run on work/data" >&2; return 1 ;; esac
    echo "$d"
}
# Buttons: 18 L, 20 R, 40 R and 3 R from the start speed S (read from the log: the save in use may hold any speed): L stops at 1.0,
# R at 7.0, and the refused presses are not steps. Wanted: min(18, S-1.0) down steps, then min(63, 7.0-low) up steps.
check_speed_keys() {
    log=$(mktemp); sdata=$(fresh_data)
    EMOTIONALSENSE_SPEED=respect EMOTIONALSENSE_POKE="${J}+0x7FC:0x42C80000" \
    EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/speed_keys.script" EMOTIONALSENSE_POLL=scene \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$sdata" --frames 3400 --renderer null >/dev/null 2>"$log" || true
    s=$(sed -n 's/.*start \([0-9]*\)\.\([0-9]\).*/\1\2/p' "$log" | head -1); ok=1; [ -n "$s" ] || { s=25; ok=0; }
    nd=$(( s - 10 )); [ "$nd" -gt 18 ] && nd=18
    low=$(( s - nd )); nu=$(( 70 - low )); [ "$nu" -gt 63 ] && nu=63
    [ "$(grep -c 'speed: down ->' "$log")" = "$nd" ] || ok=0
    [ "$(grep -c 'speed: up ->' "$log")" = "$nu" ] || ok=0
    [ "$nd" -lt 1 ] || grep 'speed: down ->' "$log" | tail -1 | grep -q "down -> $(( (low) / 10 )).$(( low % 10 )) " || ok=0
    t20=$(( low + 20 )); [ "$nu" -lt 20 ] || grep 'speed: up ->' "$log" | sed -n 20p | grep -q "up -> $(( t20 / 10 )).$(( t20 % 10 )) " || ok=0
    top=$(( low + nu )); grep 'speed: up ->' "$log" | tail -1 | grep -q "up -> $(( top / 10 )).$(( top % 10 )) " || ok=0
    if grep -q 'down -> 0\.\|up -> 7\.[1-9]' "$log"; then ok=0; fi
    if [ "$ok" != 1 ]; then echo "FAIL: speed buttons (start $s: wanted $nd down, $nu up)" >&2; grep 'speed:' "$log" | head -5 >&2; rm -rf "$log" "$sdata"; return 1; fi
    rm -rf "$log" "$sdata"
    echo "OK: speed buttons from $(( s / 10 )).$(( s % 10 )): $nd L steps (floor 1.0), $nu R steps (ceiling 7.0), the rest refused"
}
check_speed_keys

# --- V4b / V4c (round 36b). The start value is read from the log ("speed: respect, start S"), not assumed: the save in use may hold any speed.
tenths_of() { sed -n 's/.*start \([0-9]*\)\.\([0-9]\).*/\1\2/p' "$1" | head -1; }
fmt_t() { echo "$(( $1 / 10 )).$(( $1 % 10 ))"; }
# The EFFECTOR menu (the list's triangle): its SPEED slot steps by 0.1 in Respect (2 up, 2 down), the game's 0.5 stays in Original.
check_effector() {
    log=$(mktemp); sdata=$(fresh_data)
    EMOTIONALSENSE_SPEED=respect EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/effector_speed.script" \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy "$game" --data "$sdata" --frames 2700 --renderer null >/dev/null 2>"$log" || true
    s=$(tenths_of "$log"); ok=1
    [ -n "$s" ] || ok=0
    want="up -> $(fmt_t $((s + 1))) up -> $(fmt_t $((s + 2))) down -> $(fmt_t $((s + 1))) down -> $(fmt_t "$s")"
    got=$(grep -o 'effector [a-z]* -> [0-9.]*' "$log" | sed 's/effector //' | tr '\n' ' ' | sed 's/ $//')
    [ "$got" = "$(echo $want)" ] || ok=0
    grep -q "the list set the game's index" "$log" && ok=0
    rm -f "$log"
    EMOTIONALSENSE_SPEED=original EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/effector_speed.script" \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy "$game" --data "$sdata" --frames 2700 --renderer null >/dev/null 2>"$log" || true
    grep -q 'speed: effector' "$log" && ok=0
    rm -rf "$log" "$sdata"
    [ "$ok" = 1 ] || { echo "FAIL: effector speed (wanted: $want; got: $got)" >&2; return 1; }
    echo "OK: effector: up, up, down, down = $want (start $(fmt_t "$s")); Original untouched"
}
check_effector
# R and L (and LT / RT) during the song, in each of the five styles: the first five presses (R L R R L) give S+1 S S+1 S+2 S+1.
# LT / RT: 4T and 6T are L / R (two more steps); with triggers_2t=stick they are the analog stick in 3.2T and 4.2T (no step).
# 6.2T too: exactly 5 (LT/RT are the stick, never a step; 14 runs measured in round 36c, none with a sixth).
check_speed_play() {   # style
    log=$(mktemp); sdata=$(fresh_data)
    EMOTIONALSENSE_SPEED=respect EMOTIONALSENSE_POKE="${J}+0x7FC:0x42C80000" EMOTIONALSENSE_CONFIRM=circle \
    EMOTIONALSENSE_SCRIPT="@$root/scripts/speed_play_$1.script" SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$sdata" --frames 3300 --renderer null >/dev/null 2>"$log" || true
    s=$(tenths_of "$log"); ok=1; [ -n "$s" ] || ok=0
    want="up -> $(fmt_t $((s + 1))) down -> $(fmt_t "$s") up -> $(fmt_t $((s + 1))) up -> $(fmt_t $((s + 2))) down -> $(fmt_t $((s + 1)))"
    got=$(grep -o 'speed: \(up\|down\) -> [0-9.]*' "$log" | sed 's/speed: //' | head -5 | tr '\n' ' ' | sed 's/ $//')
    [ "$got" = "$(echo $want)" ] || ok=0
    n=$(grep -c 'speed: \(up\|down\) ->' "$log" || true)
    case $1 in 4t|6t) [ "$n" = 7 ] || ok=0 ;; 32t|42t) [ "$n" = 5 ] || ok=0 ;; *) [ "$n" = 5 ] || ok=0 ;; esac
    last=$(grep -o 'speed: \(up\|down\) -> [0-9]*\.[0-9]' "$log" | tail -1 | sed 's/.*-> //; s/\.//')
    saved=$(sed -n 's/.*"tenths": \([0-9]*\).*/\1/p' "$sdata/es_speed.json" 2>/dev/null | head -1)
    [ -n "$saved" ] && [ "$saved" = "$last" ] || ok=0    # the value of the last step is the shared one (list, pause, effector, next song)
    rm -rf "$log" "$sdata"
    [ "$ok" = 1 ] || { echo "FAIL: speed in play, $1 (wanted: $want; got: $got; $n steps, saved ${saved:-none} / last ${last:-none})" >&2; return 1; }
    echo "OK: R/L in play, $1: $want, $n steps in all, saved $(fmt_t "$saved")"
}
for m in 4t 6t 32t 42t 62t; do check_speed_play $m; done

# Scroll speed in the captured picture: the same Auto Velocity number gives the same speed on a 114 and a 196
# BPM song; Original gives the PSP proportion (the tempo ratio). Needs the Vulkan renderer and python3 with PIL;
# skipped (and said so) when either is missing.
#
# Q1 (round 37): the measure no longer depends on the user's save. A save is *built here*: a data folder with an
# empty ms/ makes the game ask for a player name, scripts/dump/profile.script (the texture-dump bootstrap) answers it and the game writes a
# brand-new SD.BIN (always the same bytes: default skin, speed 2.5 after the poke below, song list on its first
# song, nothing played). Every measure runs on a copy of that, with the Respect speed set by es_speed.json
# (2.5) and the Original speed byte poked to 2.5 (index 5). Rebuilt on every run, never stored in the repository.
make_speed_base() {   # builds $tmp/speedbase/ms once
    [ -d "$tmp/speedbase/ms" ] && return 0
    mkdir -p "$tmp/speedbase/ms"
    ln -s "$data/disc" "$tmp/speedbase/disc"; ln -s "$data/module" "$tmp/speedbase/module"
    EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/dump/profile.script" \
    EMOTIONALSENSE_CLOCK=virtual EMOTIONALSENSE_UNCAP=1 SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$tmp/speedbase" --frames 2700 --renderer null >/dev/null 2>&1 || true
    ls "$tmp"/speedbase/ms/PSP/SAVEDATA/*/SD.BIN >/dev/null 2>&1 || { echo "FAIL: the profile route did not create a save (scripts/dump/profile.script)" >&2; return 1; }
}
speed_data() {   # prints a new data dir: disc/module linked, the fresh save copied, Respect speed 2.5
    d=$(mktemp -d)
    ln -s "$data/disc" "$d/disc"; ln -s "$data/module" "$d/module"; cp -a "$tmp/speedbase/ms" "$d/ms"
    printf '{\n "version": 1,\n "tenths": 25\n}\n' > "$d/es_speed.json"
    case $(cd "$d" && pwd -P) in */work/data) echo "refusing to run on work/data" >&2; return 1 ;; esac
    echo "$d"
}
measure_song() {   # script mode -> prints "px_per_vblank tempo"
    sdata=$(speed_data); log=$(mktemp); shots=$(mktemp -d)
    pokes="${J}+0x7FC:0x42C80000"
    [ "$2" = original ] && pokes="$pokes,0x130E62:5:1"    # the game's own speed index: 2.5
    export EMOTIONALSENSE_SPEED=$2 EMOTIONALSENSE_POKE="$pokes" EMOTIONALSENSE_CONFIRM=circle \
           EMOTIONALSENSE_CLOCK=virtual EMOTIONALSENSE_UNCAP=1 SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy
    EMOTIONALSENSE_SCRIPT="@$root/scripts/$1.script" EMOTIONALSENSE_POLL=scene \
        "$game" --data "$sdata" --frames 3300 --renderer vulkan >/dev/null 2>"$log" || true
    s3=$(grep '0x0805E874 = 0x00000003' "$log" | head -1 | sed 's/poll\[\([0-9]*\)\].*/\1/')
    tempo=$(grep -o 'chart tempo [0-9.]*' "$log" | head -1 | sed 's/chart tempo //')
    a=$((s3 + 500)); b=$((a + 1000))
    list=$(python3 -c "print(','.join(str(x) for x in range($a, $b + 1, 2)))")
    EMOTIONALSENSE_SCRIPT="@$root/scripts/$1.script" EMOTIONALSENSE_SHOT="$list" EMOTIONALSENSE_SHOT_DIR="$shots" \
        "$game" --data "$sdata" --frames $((b + 5)) --renderer vulkan --internal-scale 2 >/dev/null 2>"$log" || true
    px=$(python3 -I "$root/scripts/measure_speed.py" "$shots" $a $b 2 | sed -n 's/^px_per_vblank \([0-9.]*\).*/\1/p')
    unset EMOTIONALSENSE_SPEED EMOTIONALSENSE_POKE EMOTIONALSENSE_CLOCK EMOTIONALSENSE_UNCAP
    rm -rf "$sdata" "$log" "$shots"
    echo "$px ${tempo:-0}"
}
if python3 -c "import PIL" 2>/dev/null && [ "${ES_CHECK_NO_VULKAN:-0}" != 1 ]; then
    make_speed_base
    set -- $(measure_song speed_a respect); ra=$1; ta=$2
    set -- $(measure_song speed_b respect); rb=$1; tb=$2
    set -- $(measure_song speed_a original); oa=$1
    set -- $(measure_song speed_b original); ob=$1
    if python3 - "$ra" "$rb" "$oa" "$ob" "$ta" "$tb" <<'PY'
import sys
ra, rb, oa, ob, ta, tb = (float(x or 0) for x in sys.argv[1:7])
if min(ra, rb, oa, ob, ta, tb) <= 0:
    sys.exit("measurement failed: %s" % sys.argv[1:])
r = rb / ra; o = ob / oa; want = tb / ta
print("   respect: %.4f vs %.4f px/vblank (B/A %.4f, tempo %g / %g); original: %.4f vs %.4f (B/A %.3f, tempo ratio %.3f)"
      % (ra, rb, r, ta, tb, oa, ob, o, want))
sys.exit(0 if abs(r - 1) <= 0.01 and abs(o / want - 1) <= 0.01 else 1)
PY
    then echo "OK: speed: Respect keeps the scroll speed across songs (+-1 %), Original keeps the PSP's tempo proportion (+-1 %)"
    else echo "FAIL: speed in px/s" >&2; exit 1; fi
else
    echo "SKIP: speed in px/s (needs python3 with PIL and a Vulkan renderer)"
fi

# --- MISSION is always Original (R). Every option is Respect; the mission route hammers the lanes with the life
# held and the MAX window wide, and the game's auto-fever flag forced to 0 (as check_fever does): in FreeStyle the
# host's Respect starts the Fever by itself (check_fever above), in the mission it must wait for CROSS; no speed
# song start is logged (the game's index stands); no Respect record is written.
check_mission() {
    log=$(mktemp); sdata=$(fresh_data)
    EMOTIONALSENSE_FEVER=respect EMOTIONALSENSE_SCORE=respect EMOTIONALSENSE_SPEED=respect \
    EMOTIONALSENSE_POKE="$fever_pokes" EMOTIONALSENSE_POLL="${J}+0x564*+0x13A:b,${J}+0x564*+0x13B:b,0x130E5C:b" \
    EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/mission_fever.script" \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$sdata" --frames 5000 --renderer null >/dev/null 2>"$log" || true
    ok=1
    grep -q '0x00130E5C = 0x00000002' "$log" || ok=0                       # the play mode byte says MISSION
    full=$(grep '= 0x00000001' "$log" | grep -v '0x00130E5C' | head -1 | sed 's/poll\[\([0-9]*\)\].*/\1/')
    run=$(grep '= 0x00000001' "$log" | grep -v '0x00130E5C' | sed -n 2p | sed 's/poll\[\([0-9]*\)\].*/\1/')
    [ -n "$full" ] && [ -n "$run" ] && [ $((run - full)) -ge 10 ] || ok=0 # gauge full, the Fever waited for CROSS
    ! grep -q 'speed: song start' "$log" || ok=0
    [ ! -f "$sdata/es_scores.json" ] || ok=0
    if [ "$ok" != 1 ]; then echo "FAIL: mission is Original (full=$full run=$run)" >&2; grep -E 'poll\[|speed:|score:' "$log" | head -12 >&2; rm -rf "$log" "$sdata"; return 1; fi
    rm -rf "$log" "$sdata"
    echo "OK: mission with Respect options: Fever waited for CROSS (gauge full at vblank $full, started $run), game's speed, no record"
}
check_mission

# --- 2T triggers (T, round 36). The style byte (options struct 0x130E58 +5: 0 4T, 1 6T, 2 3.2T, 3 4.2T, 4 6.2T) holds in the song; 4.2T
# gets LT/RT (and E/R) hammered with no lane button, and the judge's weight sum (judge+0x814) tells whether the side notes were hit:
# triggers_2t=stick must beat lr by far (lr: LT/RT are L/R, the speed buttons). In 4T a trigger is L/R whatever the option says.
check_style_byte() {
    log=$(mktemp); sdata=$(fresh_data)
    EMOTIONALSENSE_POLL="scene,0x130E5D:b" EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/mode_62t.script" \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy "$game" --data "$sdata" --frames 2000 --renderer null >/dev/null 2>"$log" || true
    ok=1; grep -q '0x00130E5D = 0x00000004' "$log" || ok=0
    s3=$(grep '0x0805E874 = 0x00000003' "$log" | head -1 | sed 's/poll\[\([0-9]*\)\].*/\1/')
    sb=$(grep '0x00130E5D = 0x00000004' "$log" | head -1 | sed 's/poll\[\([0-9]*\)\].*/\1/')
    [ -n "$s3" ] && [ -n "$sb" ] && [ "$sb" -le "$s3" ] || ok=0
    rm -rf "$log" "$sdata"
    if [ "$ok" != 1 ]; then echo "FAIL: style byte 0x130E5D in 6.2T" >&2; return 1; fi
    echo "OK: style byte 0x130E5D = 4 (6.2T) before the song starts"
}
check_style_byte
weight_of() {   # script option [pokes-extra]: prints the judge's weight sum at the end of the run
    log=$(mktemp); sdata=$(fresh_data)
    EMOTIONALSENSE_TRIGGERS_2T=$2 EMOTIONALSENSE_POKE="${J}+0x7FC:0x42C80000,${J}+0x560*+0x10:0x7f:1" \
    EMOTIONALSENSE_POLL="${J}+0x814" EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/$1.script" \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy "$game" --data "$sdata" --frames 3300 --renderer null >/dev/null 2>"$log" || true
    v=$(grep 'poll\[' "$log" | tail -1 | sed 's/.*= 0x\([0-9A-Fa-f]*\) .*/\1/')
    rm -rf "$log" "$sdata"
    echo $(( 0x${v:-0} ))
}
w_stick=$(weight_of triggers_2t stick); w_lr=$(weight_of triggers_2t lr); w_keys=$(weight_of triggers_2t_keys lr)
if [ "$w_stick" -gt $((w_lr + 60)) ] && [ "$w_keys" -gt $((w_lr + 60)) ]; then
    echo "OK: 4.2T side notes: LT/RT as the stick judged weight $w_stick, as L/R $w_lr; E/R keys $w_keys"
else echo "FAIL: 2T triggers (stick $w_stick, lr $w_lr, E/R $w_keys)" >&2; exit 1; fi
check_trigger_4t() {   # a trigger is L/R in 4T: the speed changes (15 steps from 2.5 down to 1.0, then refused)
    log=$(mktemp); sdata=$(fresh_data)
    EMOTIONALSENSE_TRIGGERS_2T=$1 EMOTIONALSENSE_POKE="${J}+0x7FC:0x42C80000" EMOTIONALSENSE_CONFIRM=circle \
    EMOTIONALSENSE_SCRIPT="@$root/scripts/triggers_4t.script" SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
        "$game" --data "$sdata" --frames 2300 --renderer null >/dev/null 2>"$log" || true
    n=$(grep -c 'speed: down ->' "$log" || true)
    rm -rf "$log" "$sdata"
    [ "$n" -ge 10 ] || { echo "FAIL: LT in 4T with triggers_2t=$1 changed the speed $n times" >&2; return 1; }
    echo "OK: LT in 4T (triggers_2t=$1) is L: $n speed steps"
}
check_trigger_4t stick
check_trigger_4t lr

# --- V4: the song list steps in tenths and the song starts at the chosen value.
check_speed_list() {
    log=$(mktemp); sdata=$(fresh_data)
    EMOTIONALSENSE_SPEED=respect EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/speed_list.script" \
        SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy "$game" --data "$sdata" --frames 2000 --renderer null >/dev/null 2>"$log" || true
    s=$(sed -n 's/.*start \([0-9]*\)\.\([0-9]\).*/\1\2/p' "$log" | head -1); ok=1; [ -n "$s" ] || { s=25; ok=0; }
    a=$(( s - 1 )); b=$(( s - 2 ))
    grep -q "speed: list down -> $(( a / 10 )).$(( a % 10 ))" "$log" || ok=0
    grep -q "speed: list down -> $(( b / 10 )).$(( b % 10 ))" "$log" || ok=0
    grep -q "speed: song start, $(( b / 10 )).$(( b % 10 )) ->" "$log" || ok=0
    rm -rf "$log" "$sdata"
    [ "$ok" = 1 ] || { echo "FAIL: song list speed in tenths (start $s)" >&2; return 1; }
    echo "OK: song list: L twice from $(( s / 10 )).$(( s % 10 )) = $(( b / 10 )).$(( b % 10 )), the song starts at it"
}
check_speed_list

# --- U1 (round 36c): the result screen's "new content" notices with everything unlocked. A throwaway profile (level 1, experience 300:
# the poke before the gameplay scene, "<") makes the song end in a level up to 2, which the game announces with: the box item (message
# 0xC here), "3.2T unlocked" (0x1D) and a special mission (0x12). With the unlock on the first two are not opened (the level up and the
# box reveal stay), the mission stays (not part of the unlock); with EMOTIONALSENSE_UNLOCK=0 (or SHOW_NOTICES=1) all three open as the
# game does, and the SD.BIN of a skipped and a shown run (same inputs, same copy of the save) is byte for byte the same.
check_unlock_notices() {
    prof=$(printf '0x%X' $(( ((300 << 11) | (1 << 4)) ^ 0xC34F931B )))
    d1=$(fresh_data); d2=$(fresh_data); d3=$(fresh_data); l1=$(mktemp); l2=$(mktemp); l3=$(mktemp)
    run_notices() {   # datadir log env
        env $3 EMOTIONALSENSE_TRACE_MSG=1 EMOTIONALSENSE_POKE="<0x130EAC:$prof,${J}+0x7FC:0x42C80000,${J}+0x560*+0x10:0x7f:1" \
        EMOTIONALSENSE_POLL=scene EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/unlock_notices.script" \
            SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy "$game" --data "$1" --frames 9800 --renderer null >/dev/null 2>"$2" || true
    }
    run_notices "$d1" "$l1" EMOTIONALSENSE_UNLOCK=1 &
    run_notices "$d2" "$l2" EMOTIONALSENSE_SHOW_NOTICES=1 &
    run_notices "$d3" "$l3" EMOTIONALSENSE_UNLOCK=0 &
    wait
    ok=1
    for id in 0xC 0x1D; do
        grep -q "msg: .* id=$id .* skipped" "$l1" || ok=0                    # unlock on: not opened
        grep -q "msg: .* id=$id .* shown" "$l2" || ok=0                      # SHOW_NOTICES: opened
        grep -q "msg: .* id=$id .* shown" "$l3" || ok=0                      # unlock off: opened as before
    done
    grep -q "msg: .* id=0x12 .* shown" "$l1" || ok=0                         # the special mission is not unlocked by us
    grep -q 'poll\[[0-9]*\]: 0x0805E874 = 0x0000000A' "$l1" || ok=0         # still goes through the level-up scene
    grep 'poll\[' "$l1" | grep -A1 '0x0805E874 = 0x0000000A' | grep -q '0x0805E874 = 0x00000002' || ok=0   # and back to the list
    sd1=$(md5sum "$d1"/ms/PSP/SAVEDATA/*/SD.BIN | cut -d' ' -f1); sd2=$(md5sum "$d2"/ms/PSP/SAVEDATA/*/SD.BIN | cut -d' ' -f1)
    [ "$sd1" = "$sd2" ] || ok=0
    if [ "$ok" != 1 ]; then echo "FAIL: unlock notices (SD.BIN $sd1 / $sd2)" >&2; grep -h 'msg:' "$l1" "$l2" "$l3" >&2; rm -rf "$d1" "$d2" "$d3" "$l1" "$l2" "$l3"; return 1; fi
    rm -rf "$d1" "$d2" "$d3" "$l1" "$l2" "$l3"
    echo "OK: unlock notices: box item and 3.2T notice skipped with the unlock on, shown with it off; special mission kept; SD.BIN identical"
}
check_unlock_notices

# --- U1b (round 36d): "You have acquired a new HD / SS pattern" on the FIRST play of a chart (the play mark of (song, style, diff 1) is 0).
# The route plays song 18 (4T); the song has no such chart, so the test gives it one (poke of the chart-level byte 0x138E38 + 61 * song + 40
# + 3 * style + 1, memory only) and clears its play mark (poke "<": before the gameplay scene only; record 0x130EC8 + 150 * song + 10 *
# diff, the halfword XOR 0xB07F). Message 0x16 opens from 0x430DC: skipped with the unlock on, shown with UNLOCK=0 and with
# SHOW_NOTICES=1, and the SD.BIN of the skipped and the shown run is the same.
# The special mission's notice (0x12) needs no skip: the game only raises it for a mission THIS song unlocked (0x5C190 skips every
# mission whose bit in the word 0x134A30, XOR the key 0x9D321C55, is set), so with all 30 missions unlocked there is no window at all.
check_first_play_notice() {
    d1=$(fresh_data); d2=$(fresh_data); d3=$(fresh_data); d4=$(fresh_data); l1=$(mktemp); l2=$(mktemp); l3=$(mktemp); l4=$(mktemp)
    allm=$(printf '0x%X' $(( 0x3FFFFFFF ^ 0x9D321C55 )))
    run_first() {   # datadir log extra-poke env
        env $4 EMOTIONALSENSE_TRACE_MSG=1 EMOTIONALSENSE_POKE="0x1392AB:3:1,<0x13195E:0xB07F:2,$3${J}+0x7FC:0x42C80000,${J}+0x560*+0x10:0x7f:1" \
        EMOTIONALSENSE_POLL=scene EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_SCRIPT="@$root/scripts/unlock_notices.script" \
            SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy "$game" --data "$1" --frames 9800 --renderer null >/dev/null 2>"$2" || true
    }
    run_first "$d1" "$l1" "" EMOTIONALSENSE_UNLOCK=1 &
    run_first "$d2" "$l2" "" EMOTIONALSENSE_SHOW_NOTICES=1 &
    run_first "$d3" "$l3" "" EMOTIONALSENSE_UNLOCK=0 &
    run_first "$d4" "$l4" "<0x134A30:$allm," EMOTIONALSENSE_UNLOCK=1 &
    wait
    ok=1
    grep -q "msg: .* id=0x16 .* skipped" "$l1" || ok=0
    grep -q "msg: .* id=0x16 .* shown" "$l2" || ok=0
    grep -q "msg: .* id=0x16 .* shown" "$l3" || ok=0
    grep -q "msg: .* id=0x12 .* shown" "$l1" || ok=0               # a locked mission unlocked by this song: the notice stays
    ! grep -q "msg: .* id=0x12 " "$l4" || ok=0                     # every mission already unlocked: the game raises no notice
    grep 'poll\[' "$l1" | grep -A1 '0x0805E874 = 0x0000000A' | grep -q '0x0805E874 = 0x00000002' || ok=0
    sd1=$(md5sum "$d1"/ms/PSP/SAVEDATA/*/SD.BIN | cut -d' ' -f1); sd2=$(md5sum "$d2"/ms/PSP/SAVEDATA/*/SD.BIN | cut -d' ' -f1)
    [ "$sd1" = "$sd2" ] || ok=0
    if [ "$ok" != 1 ]; then echo "FAIL: first-play pattern notice (SD.BIN $sd1 / $sd2)" >&2; grep -h 'msg:' "$l1" "$l2" "$l3" "$l4" >&2; rm -rf "$d1" "$d2" "$d3" "$d4" "$l1" "$l2" "$l3" "$l4"; return 1; fi
    rm -rf "$d1" "$d2" "$d3" "$d4" "$l1" "$l2" "$l3" "$l4"
    echo "OK: first-play pattern notice skipped with the unlock on, shown with it off; locked mission notice kept, none with all missions unlocked; SD.BIN identical"
}
check_first_play_notice

# --- B: button style. The PLAY MODE SELECT footer (No / Yes) is repainted: Xbox letters are green (A) and red (B); the PlayStation
# footer has no such colours. Needs PIL and Vulkan (skipped otherwise).
if python3 -c "import PIL" 2>/dev/null && [ "${ES_CHECK_NO_VULKAN:-0}" != 1 ]; then
    footer_colors() {   # style -> "green red" pixel counts of the footer, 480x272 capture at vblank 1000 of the modes route
        sdata=$(fresh_data); shots=$(mktemp -d)
        EMOTIONALSENSE_BUTTONS=$1 EMOTIONALSENSE_CONFIRM=circle EMOTIONALSENSE_CLOCK=virtual EMOTIONALSENSE_SHOT=1000 EMOTIONALSENSE_SHOT_DIR="$shots" \
        EMOTIONALSENSE_SCRIPT="@$root/scripts/dump/modes.script" SDL_AUDIODRIVER=dummy SDL_VIDEODRIVER=dummy \
            "$game" --data "$sdata" --frames 1010 --renderer vulkan --internal-scale 1 >/dev/null 2>&1 || true
        python3 -I - "$shots/frame_1000.ppm" <<'PY'
import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("RGB").crop((170, 245, 330, 272))
g = r = 0
px = im.load()
for y in range(im.height):
    for x in range(im.width):
        R, G, B = px[x, y]
        if G > 140 and R < 120 and B < 100: g += 1
        if R > 190 and G < 90 and B < 90: r += 1
print(g, r)
PY
        rm -rf "$sdata" "$shots"
    }
    set -- $(footer_colors playstation); psg=$1; psr=$2
    set -- $(footer_colors xbox); xg=${1:-0}; xr=${2:-0}
    if [ "${xg:-0}" -ge 8 ] && [ "${xr:-0}" -ge 8 ] && [ "${psg:-0}" -lt 3 ] && [ "${psr:-0}" -lt 3 ]; then
        echo "OK: button style: Xbox footer has green $xg / red $xr pixels (A, B), PlayStation $psg / $psr"
    else echo "FAIL: button style footer (xbox $xg/$xr, playstation $psg/$psr)" >&2; exit 1; fi
else
    echo "SKIP: button style capture (needs python3 with PIL and a Vulkan renderer)"
fi
