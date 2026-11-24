# Respect speed (Auto Velocity) and the play mode

`EMOTIONALSENSE_SPEED=respect|original` (`--speed`, launcher "Speed system", default Respect). Found in round 35 by
polling the lane through the scene machine and reading the recompiled code; all offsets are for the module as loaded.

## What the game does

| field | meaning |
|---|---|
| `0x130E62` (u8) | the chosen speed as an **index**: speed = 0.5 x index, 1 = 0.5x ... 14 = 7.0x (struct `0x130E58`, +10) |
| `0x130E5C` (u8) | **play mode**: 0 CLASSIC and the other styles, 1 FREE STYLE, 2 MISSION (struct `0x130E58`, +4) |
| judge `+0x55C` | lane object (judge = scene machine `0xC8B30` -> scene `+0` -> `+0xC`) |
| lane `+0x1940` / `+0x1944` | target / current speed (floats). The current one eases to the target 0.05 per vblank |
| lane `+0x1930` | ticks per beat, 192 on every chart measured (`+0x1934` = ticks per measure, 768) |
| lane `+0x12B0` | **y of the judgment line (199.0)**, not a tempo: the plan's hypothesis "BPM" was wrong (it reads 199.0 on all 40 songs) |
| judge `+0x20` | the song's **current tempo** (float, BPM): written by the sound player (`0x771B0`), at the start and by the chart's tempo events |
| chart = `*(judge+0)` | header: `+0` 192, `+4` 4, `+8` 768, `+0xC` the **first tempo** (float, e.g. 114.0), `+0x10` another float (e.g. 113.684, not used here) |

* `0x1B40C(lane, snap)`: index -> target (`switch (index-1)`, table `0xBB148`); `snap` = 1 also copies it to the current speed. It always
  first copies the old target into the current speed. Called with `snap` = 1 from `0x20578` (judge initialisation) and `0x210CC`
  (restart), with 0 from the two routines below.
* `0x17E90` / `0x17F04`: index +1 / -1 (refused at 7.0 / 0.5), then `0x1B40C(lane, 0)` and `0x1DF44`. Callers: R and L in play
  (`0x1FBF4`, `0x1FC90`; they refuse while a Fever runs and in the two mission-only states read through `0x53C7C`), the Fever's
  speed steps (up at `0x255B8` / `0x255E8`, down at `0x24564`, `0x24578`, `0x2517C`, `0x2519C`, `0x25314`, `0x25334`).
* The scroll: the span of the lane is (ticks per measure + ticks per beat) / speed ticks (`0x18D00` and the three scrollers
  `0x18EC0`, `0x19900`, `0x19B90`), so the **distance between two beats is proportional to the speed** and a note falls at
  BPM/60 x K x speed pixels a second. The same speed number is faster on a faster song.
* `0x1DF44`: the speed sprite (`lane+0x1A00`): it plays 3 frames per **.0 / .5** value (0.5 = frames 4-6, 1.0 = 7-9 ... 7.0 =
  43-45, jump table `0xBB180`); the 60 values in between jump to `0x1E0F0`, which hides it. So only .0 and .5 have art.
  It is drawn top left, about 80 x 14 pixels of the 480x272 picture plus an underline (x 0..92, y 5..28), appears on the change,
  stays about 45 vblanks and fades in 15.
* `0x1DDB0` (called every vblank from the easing `0x18AC0`): when `0x53C7C(0x1349A4, 0)` says 0xF (a mission state, seen as `0xF` at
  `0x134B48` in a mission) it keeps the speed between 1.0 and 5.0; with 0x10 it limits R/L. Zero effect outside missions.
* **Persistence**: R / L in the **song list** write the index into `SD.BIN` (one byte at `0x3BA8`, measured by diffing the save); R / L in play
  do not. The list reloads the saved index when the style is changed (SELECT), not when the list is entered.

## Respect

The speed is an exact number of tenths `v` (10..70). The lane target is `v / 10 x 100 / T`, with `T` the chart's first tempo
(`chart+0xC`, read when the sound player starts, `0x77124`: at the judge's initialisation the header still holds a placeholder).
The game's own tempo scaling stays, so the AV 2.0 of a 200 BPM song and of a 100 BPM one scroll alike, and a tempo change inside a song
(judge `+0x20` follows the chart: 118 -> 236 -> 118 and 150 -> 75 -> 150 were measured) changes the scroll in the game's proportion.

* R / L (`speed.c`, wraps of `0x17E90` / `0x17F04`): `v` +-1, refused (return 0, "no step") at 7.0 / 1.0; the current speed snaps to the old
  target and eases to the new one like the game; the byte `0x130E62` is set to `floor(v / 5)` (at least 2): always a valid index, so the
  save and the Original mode never see anything odd. `0x1DF44` is not called: the label takes its place.
* The Fever with Original Fever mode adds **+0.5** on top of `v` at x4 and x5 and the end of the Fever takes back exactly that
  (refused above 7.0, like the game at 7.0). With Fever Respect the Fever's steps are skipped as before.
* The list (round 36): R and L step `v` by **0.1**. The game's own handler (`0x30938`) still runs (its sound, its 0.5 step of the index);
  the host sees the press through the button test `0x6D234` (wrapped; only the list's two return sites `0x30DF0` = L and `0x30EA4` = R count),
  and a tick later takes the step of 0.1 instead and writes back the valid index `floor(v / 5)`. A style change (SELECT) reloads the
  saved index of that style: the host takes it as the new `v` (index x 0.5). In play the exact value shows in the label.
* Where the game draws the speed outside the song (round 36): the header pill "LB [X 2.5] RB" (`0x2D320`, updated on entry and on a press, frame
  `32 + index`), the badge "SPEED 2.5" at the bottom left (`0x2D618`) and the pause menu's pill (`0x265E0`, same pill art). The art has a frame per
  .0 / .5 only, so the overlay (`es_speed_ui_overlay`) paints over the digits and draws the speed in tenths: it blends, row by row, the
  pixels at two clean columns beside the digits (nothing of the art is stored), and writes `AV 2.3` in the pill (the pill's box is as wide as
  its text, so its bounds are found on its bright top edge), `AV` over the badge's caption and `2.3` over its digits. Rectangles (480x272 units):
  list pill digits x 377..410 around the box's middle, y 15..27 (top edge row 15, bounds searched from x 391); badge digits x 11..38, y 214..229,
  caption x 12..38, y 205..211; pause pill y 141..153 (top edge row 141, searched from x 134). Each patch checks the sprite's own edge pixels first,
  so nothing is drawn where the sprite is not on screen. The list sprites are only redrawn on entry and on a press, so they count as visible from the
  first update after the list is entered until the scene changes; the pause pill counts as visible 20 vblanks after the song's sound player
  stopped (`djmax_song_paused`: a started song whose flag is 0: pause, game over, the mission's failed menu).
* `<data>/es_speed.json` (`{"tenths": 23}`) remembers `v` between songs and sessions; without it `v` is the game's index x 0.5 (at
  least 1.0). Written atomically when the value changed (list: next vblank; play: half a second after the last press) and at the end of a run.
* The label `AV 2.3` is drawn by the host over the presented frame (`es_speed_overlay`, glyphs rasterised from Noto Sans CJK Bold, nothing of
  the game's art), at the rectangle and for the time of the game's sprite; it scales with the window and `--internal-scale`.
  `EMOTIONALSENSE_SHOT_OVERLAY=1` puts it into the captures (also with the software renderer: the overlay is drawn by the window).

## MISSION always plays Original

`djmax_mission_active()` (`guest_patches.c`) is `0x130E5C == 2`, read live while no match runs and frozen from the moment the match scene (3)
is entered. Writers: `0x273B4` (style select, 0), `0x279C4` (FREE STYLE, 1), `0x3B400` (the mission list, 2). In a mission: the host does not
force the auto-fever flag nor skip the Fever's speed steps, the speed wraps pass through (the game's index, 0.5x..7.0x, the game's sprite),
the result screen shows the game's score and no Respect record is written. Back out of a mission, the Respect speed `v` is put back into the
byte. Other options (the button style, the 2T triggers) do not change rules and stay.

## Not covered

* The list's own click sound for R / L is the game's (it plays at each press, 0.1 steps included).
* A chart whose first tempo cannot be read (outside 30..1000) keeps the game's speed and logs it.
* Nothing in the records, rankings or unlocks reads the speed (the only readers are the lane code above).

## The EFFECTOR menu and the shoulder buttons in play (round 36b)

* The FreeStyle list's triangle opens the EFFECTOR slots (SPEED, then three effects). Its first slot is the same speed byte, but it is not the 0x30938 handler
  of the list's L / R: the menu's handler (`0x31028` and the pieces after it) steps its cursor and asks `0x59AC4(menu, 0)` for the new index, then stores it with
  `sb v0, 10($s6)` (`$s6` = `0x130E58`) at **`0x31300`** (Up; the call returns to `0x312F0`) and **`0x31198`** (Down; returns to `0x31188`). It is outside the
  `0x17E90` / `0x17F04` hook, which only runs in a song. Found with `EMOTIONALSENSE_WATCH_W=0x130E62 EMOTIONALSENSE_WATCH_ALL=1` (every write, with the guest call chain
  `0x2F240` <- `0x166FC` <- `0x13E54`; before, the watch only reported a write whose previous value differed from the previous report, which hid the effector's).
* Respect wraps `0x59AC4` (`speed.c`): at those two return sites it takes a step of **0.1** (1.0 to 7.0, the same `g_v` as L / R, the list, the pause menu and the song) and returns the valid index
  `floor(v / 5)` so the game stores that; the slot's badge (`0x2D618`, redrawn right after the store) and the header pill already show `AV x.y` through the list's overlay.
  Original does not touch it (steps of 0.5). Measured: 3.5 -> 3.6 -> 3.7 -> 3.6 -> 3.5 on the badge and the pill, and the song that follows starts at the shared value.
* R and L **in play** are the `0x17E90` / `0x17F04` hook of round 35 (callers `0x1FBF4` / `0x1FC90`); the value they leave is the shared one (`es_speed.json`, then the list, the pause menu and the
  effector). Measured with R L R R L and then LT, RT: 4T and 6T: 5 steps for the shoulders (S+1, S, S+1, S+2, S+1) and 2 more for the triggers (they are L / R there); 3.2T and 4.2T with `triggers_2t=stick`:
  5 steps, the triggers are the stick and change nothing; 6.2T: the 5 shoulder steps plus one step up from RT (the game's own handling of the pushed stick; LT did nothing).
