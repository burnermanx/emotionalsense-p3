# Song unlock table

The game starts with every song unlocked by a CWCheat code built into the host.
The original code was at the wrong address and in the wrong format, and that
corrupted the list assembled for the song-select screen -- it was the cause of
"extra songs" and of the freeze when opening the list. This document records the
measured layout.

## CWCheat address translation

| System | Module base |
|---|---|
| PSP / PPSSPP | `0x08804000` |
| This build | `0` |

CWCheat addresses are offsets from `0x08800000`, so:

```
our_address = cheat_address - 0x4000
```

Example: the "All Music" code points at `0x00130ED1`, **not** `0x00134ED1`. All the
other codes (`0x001389A4..0x001389C0`) have the same `+0x4000` error.

## Code type `0x8`

```
0x8aaaaaaa nnnnssss
0x000000vv
```

is a fill with a step: `nnnn` one-byte writes, with step `ssss`, value `vv`. "All
Music" is **1500 one-byte writes with step 10** at `0x00130ED1`. The `0x0A` that
showed up as a "safe value" is the step, not the value.

## Table layout

| Item | Value |
|---|---|
| Base | `0x00130EC8` |
| Size | 100 songs x 150 bytes |
| Per song | 5 styles x 3 difficulties x 10 bytes |
| Record | `0x130EC8 + song*150 + style*30 + difficulty*10` |
| Total records | 1500 |
| Obfuscation | bytes 6..9 XORed with `0x8B1C65AD` (little-endian: `AD 65 1C 8B`) |
| State byte | byte 9, key `0x8B` |

Default locked record:

```
7d b0 15 8d 70 fe ad 65 1c 8b
```

Byte 9 is `0x8B`; `0x8B ^ 0x8B = 0x00` (locked). `0x130ED1` is exactly byte 9 of
the first record.

### Unlock state

The **2 high bits** of the decoded byte 9 are the state:

| Decoded (`b & 0xC0`) | State | Meaning |
|---|---|---|
| `0x00` | 0 | locked |
| `0x40` | 1 | unlocked (the reading did not confirm whether it shows the NEW mark) |
| `0x80` | 2 | unlocked |

Measured examples:

| Raw byte | Decoded | Reading |
|---|---|---|
| `0x8B` | `0x00` | locked |
| `0xCB` | `0x40` | state 1 |
| `0x0A` | `0x81` | state 2 + bit 0 of another field |

The current code writes state 2 (`b | 0x80`) and preserves the 6 low bits, which
belong to another field. Only records in state 0 are touched, so charts the user
already unlocked by playing are not altered.

### Functions that read the state

| Address | Role |
|---|---|
| `0x53144` | unlock test: decodes the record **in place**, reads the 2 high bits of byte 9 and re-encodes |
| `0x551C4` | caller: `!= 0` = the song appears in the list; song `0x64` = RANDOM, always appears |
| `0x55168` | caller: `== 1` |

The loop that builds the list is at `0x56980..0x569E0`, with base `0x130E68 + 0x60`.

## The assembled list

| Item | Address |
|---|---|
| List object | `0x134B88` |
| Count byte | `0x135045` |

The old plan treated `0x134858..0x135043` as the list (12-byte records: song id,
flag, index; `ff ff 00...` = empty) and `0x135044..0x1354AD` as another structure.
**That address was wrong**: the object starts at `0x134B88`, and the count byte is
at `0x135045`, inside what the plan called "another structure".

### Why 1500 contiguous bytes corrupt the list

The old loop ORed `0x02` into 1500 contiguous bytes from `0x00134ED1` to
`0x001354AD`, every frame. That range covers:

- the list object (`0x134B88..`), including the count at `0x135045`;
- empty records (`ff ff 00...`), which the OR turns into false entries -- this is
  the "extra songs";
- state bytes and UV/position floats of the neighbouring structure.

With the list corrupted every frame, the FreeStyle list build hangs in scene 2
(threads 262169/262174 stopped on an event flag, with the message `controlID has
waited 500 ticks`). The hang happens **before** a song is chosen.

## Measured variants

Route `/tmp/fs42c.script` (FreeStyle 4T), save from the development data folder,
`--renderer null`:

| Variant | 4T list | Scene 3 | Hung |
|---|---|---|---|
| no unlock | 2/8 | 1898 | no |
| old repo (OR `0x02`, `0x134ED1`, 1500 contiguous) | -- | never | yes, in scene 2 |
| contiguous OR `0x02`/`0x0A` at `0x130ED1` | 2/8 | 1898 | no (and unlocks nothing) |
| **OR `0x40` at `0x130ED1`, 1500 x step 10** | **2/41** | 1898 | no |

`2/41` = the 40 songs + RANDOM, which is what the user asked for ("there must be
40 selectable songs"). The assembled list has 41 entries with ids `0x00..0x27` +
`0x64`.

Other variations tried in the unlock round, all hanging in scene 2: OR `0x02` with
a scene-change guard (once per scene), OR `0x02` every frame, OR `0x0A` (bits 1 and
3) with a guard and every frame, a conditional OR `0x02` (`if (v & 0xFC)`), and a
UMD delay only on the check's LBAs (reverted; it was not the cause).

What was **not** tested (according to the plan): state 2 instead of 1 on screen,
the other styles, the user's save and the gallery/video/gear/icon codes.

## Styles and modes

Inside FreeStyle, SELECT cycles **4T -> 6T -> 3.2T -> 4.2T -> 6.2T** and back. 6T
was always there; the old claim that "6T is not reachable" was a misreading of a
route that only pressed SELECT twice. 6.2T only shows up when item 108 is
unlocked.

`PLAY MODE SELECT` has six items when everything is unlocked: `CLASSIC 4T`,
`CLASSIC 6T`, `EASY 3.2T`, `NORMAL 4.2T`, `HARD MODE 6.2T` and `Free Style`.

## Current implementation

In `game/host.c`:

```c
#define SONG_TABLE 0x00130EC8u
#define SONG_KEY9  0x8Bu
#define ITEM_BITS  0x001349A4u   /* 271 ownership bits */
#define ITEM_KEYS  0x00138E00u   /* key of each word, read from the module */

for (uint32_t k = 0; k < 1500; k++) {
    uint32_t a = SONG_TABLE + k * 10 + 9;
    uint8_t  b = psp_read8(a) ^ SONG_KEY9;
    if ((b & 0xC0) == 0) psp_write8(a, (uint8_t)((b | 0x80) ^ SONG_KEY9));
}
for (uint32_t w = 0; w < 9; w++) {
    uint32_t a = ITEM_BITS + w * 4, key = psp_read32(ITEM_KEYS + w * 4);
    uint32_t have = psp_read32(a) ^ key;
    uint32_t want = w == 8 ? 0x7FFFu : 0xFFFFFFFFu;
    if ((have | want) != have) psp_write32(a, (have | want) ^ key);
}
```

4.2T is not here: it is unlocked by a wrap on the player level
(`game/guest_patches.c`, `0x547F0`).

- It runs every frame, in the main loop, holding the baton with the guest threads
  parked. That matters because `0x53144` decodes the record in place and
  re-encodes it right after.
- `EMOTIONALSENSE_UNLOCK=0` turns it off (on by default).
- **Persistence:** the table is part of the saved data. The unlock ends up in
  `SD.BIN` at the next save; warn whoever tests.

## PLAY MODE SELECT: the real rule of the modes

`0x00134960` is **not** a mode bitmap. It is the `[5 modes][3 stages]` table of
`{song id, difficulty}`, initialised with `FF 00` at `0x55720` and read at
`0x556CC`. The previous round read the first `u16` as if it were a mode field and
did `0x17 | 0x20 = 0x37`, which swaps the song id of the first record for one that
does not exist. 4.2T seemed to have been unlocked by that, but the save used
already had a high enough level.

The rule is in two identical places, `0x27C78` (PLAY MODE SELECT) and `0x2D780`
(the FreeStyle style submenu):

```
if byte 0x130E6C == 2: nothing locked
else:
  level = 0x547F0(0x130E68 + 68)
  level < 2 -> locks [4]
  level < 5 -> locks [5]        (4.2T)
  [6] = !0x5A2D8(0x135058, 4, 0)  (6.2T hidden without item 108)
```

The level comes from the profile at `0x130EAC` (file `0x44`): word 0 XOR
`0xC34F931B` = bits 0-3 title, **4-10 level**, 11-31 experience; word 1 XOR
`0x7914AC38` = bits 6+ play count.

Since the level cannot be written into the save (it shares the word with the title
and the experience), the unlock intercepts only the two queries, with a wrap on
`0x547F0` that returns 5 when `ra` is `0x27CBC` or `0x2D7B0`. 6.2T comes for free,
because it is item 108 of the bitmap below.

Consequence for anyone who ran the previous version: the first id of the table may
be at `0x28..0x3F`. The unlock clears the `0x20` bit in that case. Ids `0..7` that
became `0x20..0x27` are indistinguishable from real ids `32..39` and stay as they
are -- the possible damage is the first song of a mode.

4.2T has its own list of 10 songs, and so does 6.2T -- they are not the 41 of
FreeStyle. This holds for any save; it is not an effect of the unlock.

## Items: icons, crew, gear, notes, gallery, videos and 6.2T

Everything the game counts as "owned" is a bit in a 9-word bitmap at `0x001349A4`
(file `0x3B3C`), 271 items. Word 0 is XORed with the key at `0x138E00` (measured
`0xA462B832`, but read from RAM: do not hard-code it); words 1-8 have key 0
(`0x138E04..0x138E20`). The ownership test is `0x53634(base, id)`, and
`0x5A2D8(type, index)` maps through the table `0x138328 + 202*type + 2*index`
(count at byte `+200`). The next two words of the bitmap (`+36`, `+72`) are other
marks (probably "new" and "seen") and are not touched.

| type | ids | count | what it is |
|---|---|---|---|
| 0 | 0-61 | 62 | Studio CREW (62 entries, includes Basic Male/Female); the LOUNGE's DJ ICON also shows 62 (see below) |
| 1 | 62-87 | 26 | not identified (no visited screen queries it) |
| 2 | 88-97 | 10 | GEAR |
| 3 | 98-107 | 10 | NOTE |
| 4 | 108 | 1 | 6.2T mode |
| 5 | 109-188 | 80 | IMAGE GALLERY |
| 6 | 189-228 | 40 | VIDEO |
| 7 | 229-267 | 39 | not identified (no visited screen queries it) |
| 8 | 268-270 | 3 | extra videos |

Checked on screen with a level-4 save: DJ ICON `01/62`, IMAGE GALLERY `01/80`,
VIDEO `01/43` (40 + the 3 extras; the plan's estimate of 41 counted only one of
them), and `HARD MODE 6.2T` shows in the menu with `6.2T STAGE 1` and 10 songs. The
`SD.BIN` stays byte for byte the same after opening the menus: the unlock write is
in RAM and the file only changes when the game saves (see below).

### The unlock goes into the save (user's decision: option a)

The game writes the save (caller `0x287C`, mode 1, 15320 bytes) when leaving the
result of a finished song, and what is in RAM at that moment, with the unlock, goes
into `SD.BIN` along with it. After that `EMOTIONALSENSE_UNLOCK=0` (and
`_UNLOCK_ITEMS=0`) does not lock again: the bits are already in the file. The
user's decision: accept this and document it; do not restore the original state on
save. Anyone who wants to keep the save clean backs up `SD.BIN` first or runs with
`EMOTIONALSENSE_UNLOCK=0` from the start. It is in the README.

Checked in round 17, counting entries on screen with the unlock on and with
`EMOTIONALSENSE_UNLOCK_ITEMS=0` (level-6 save): STUDIO > CREW lists 62 named
entries (before: only Basic, NB Yellow and Old had a name, the rest was `?`), and
queries type 0; GEAR lists 10 (before: only Max3) and queries type 2; NOTE lists 10
and queries type 3; the LOUNGE's VIDEO goes from 10 to 43 and queries types 5, 6
and 8. Types 1 and 7 were not queried in STUDIO, LOUNGE (PLAY DATA, VIDEO,
GALLERY), mode select, FreeStyle, the result or on entering MISSION; they stay with
no identified screen.

CREW and DJ ICON are two different screens with the same count. STUDIO > CREW
(title `STUDIO`, categories CREW/GEAR/NOTE/EFFECTOR) lists 62 crews with a name and
a skill (Super52 "Anti Break 6", Juggernaut "AUTO FEVER"), and it was type 0 as
measured in the caller. DJ ICON is the "Select your DJ ICON  01/62" screen opened
with triangle in the LOUNGE, with 62 portraits. The type the DJ ICON queries was
**not measured**; 62 = 62 and the two turning on together with the unlock suggest
the same type 0 (the DJ icon would be the crew's portrait), but that is inference.
Round 17 counted the entries on the STUDIO screen, not on the icons one.

The crew has a gameplay effect (Anti Break 2/3, for example). Unlocking does not
change the chosen crew -- the default stays basic -- but it makes all of them
selectable. Whoever wants only songs and modes can use
`EMOTIONALSENSE_UNLOCK_ITEMS=0`.

## Corrections to old claims

| Claim | Status |
|---|---|
| "Bit `0x02` is the unlock; `0x08` may be 4.2T" | Wrong. Those were bytes of the assembled list, not of the table. There is no "4.2T bit" there. |
| "4T = 11, 4.2T = 10, 6T/3.2T = 19 (total 40)" | Coincidence. Each style builds the list from the charts unlocked for that style; with everything unlocked 4T shows all 40. |
| "`0xCB` corrupts a table and aborts with an indirect call to `0x05040504`" | `0xCB` is the right value for state 1. The abort came from writing it into 1500 contiguous bytes in the wrong place. |
| "The user's freeze is when confirming the song" | May be the same defect: their AppImage made the same wrong writes every frame. |
| List at `0x134858` | Wrong; the object is at `0x134B88` and the count at `0x135045`. |
| "4.2T in PLAY MODE SELECT comes from the song table" | Wrong, but the correction was also wrong: `0x134960` is the `[mode][stage]` table, not a bitmap. 4.2T depends on the player's level. |
| "Bit `0x20` of the `u16` at `0x134960` unlocks 4.2T" | Wrong. That `u16` is `{song id, difficulty}` of the first stage; adding `0x20` swaps the song for a nonexistent one. |
| "6T is not reachable in FreeStyle" | Wrong; SELECT goes through 4T, 6T, 3.2T, 4.2T and 6.2T. |

## Allowed scope

Songs, all items (icons, crew, gear, notes, gallery, videos) and the two modes the
game locks, 4.2T and 6.2T. No life, score, combo, autoplay, fever, ranking or
male-role cheats.

The gallery/video/gear/icon codes are **no longer used**: the item bitmap replaced
the CWCheat, which had the same `-0x4000` error and possibly the same code-type
error.
