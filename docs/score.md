# Respect scoring (display only)

`EMOTIONALSENSE_SCORE=respect|original` (`--score`, launcher "Scoring system", default Respect).

## What the game does

The judge object (scene machine at `0xC8B30` -> scene `+0` -> `+0xC`) keeps, for the song being played:

| field | meaning |
|---|---|
| `+0x814` (u32) | sum of the judgment weights: MAX 100 % = 100, 90 % = 90 ... 10 % = 10, judgment index 2 = 1, BREAK/MISS = 0 (function `0x200B8`) |
| `+0x810` (u16) | number of weighted judgments (the notes that count for the percentage; a chart with long notes has a larger combo) |
| `+0x808` / `+0x80C` | the game's own score and bonus (never touched) |

The result screen's `JUD. %` is `10 * sum / count` in tenths. `0x43ADC` draws the result numbers through `0x42064`
(the SCORE field is the call at `0x43C74`); `0x43CD4`..`0x43F70` compares the stage with the stored record
(`getScore 0x552C4(db, style, song, difficulty)`, `setScore 0x55300`); the song list panel (`0x31xxx`) asks `getScore` and draws
the value with `0x322E4`.

## Respect

`score = round(1000000 * sum / (100 * count))` in 64-bit integers (`(sum*10000 + count/2) / count`), never through the
rounded percentage. 100 % = exactly 1,000,000, 0 = 0. 87.8 % = about 878,000. 7 digits fit the result screen's SCORE field
(9 digits) and the list's HIGH SCORE field (7 digits).

The host samples `+0x814`/`+0x810` every vblank of the gameplay scene (stable pointer path, no heap address; samples that
cannot be real, such as the torn-down judge, are rejected) and uses the last one on the result screen.

- result screen: the number handed to `0x42064` at return address `0x43C7C` is replaced (the game's variable is not);
- song list: the value `getScore` returns to `0x319F8` is replaced by the Respect record (0 for a chart without one,
  e.g. played before this version);
- every cleared stage: at the result's `getScore` (`0x43F78`) the Respect record is updated if better and
  written atomically to `<data>/es_scores.json` (`"style:song:difficulty": score`). Written in both modes. Game over
  and abandoned songs never reach the result screen, so they write nothing.

The in-song HUD has no score in any mode (`0x1A600`, its number routine, is never called in 4T or 6.2T).

## Not covered

- "NEW RECORD" on the result screen follows the game's own record, in both modes.
- Other screens that read records through `getScore` (`0x4694C`, `0x4E560`: other result flavours and a list) show the
  game's value.
- The Fever mode does not enter the formula (only judgments do), but Respect Fever can hit more MAX notes than a player
  who forgets the button, so scores can differ from a manual run.
