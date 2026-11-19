# Data update 1.02 (optional)

The Korean 1.02 update ships a `PSP/GAME/ULKS46236/DATA.TPA` with 6 entries that
replace 6 of the 92 entries of the disc's `DATA.TPA` (same name hash, different
content, 0 to 2 KB larger; one has the same size). There is no new executable
(`DATA.PSP` is empty), so no code is recompiled. The update's patcher `.exe` is
never run and the launcher refuses it.

## Use

    EmotionalSense-P3 --update <folder>     # a folder holding PSP/GAME/ULKS46236/DATA.TPA
    EmotionalSense-P3 --remove-update

or the "Data update 1.02" row of the launcher's options. The launcher checks the
header (magic 0x0004C0DA, entry count, size) and that at least one hash matches
the disc, then copies the file to `<data>/update/DATA.TPA`. The ISO and the
extracted disc folder are never modified; deleting `<data>/update/` undoes
everything. `EMOTIONALSENSE_UPDATE=0` turns it off for one run.

## How the overlay works

The top-level container is plain: a 12-byte header, 16-byte entries (hash, size,
offset, 0), then the data back to back. On start the game host writes
`<data>/update/DATA.combined.TPA` (once; it is rebuilt when either input is
newer): the disc's table with the 6 entries' size and offset replaced, followed
by the data. When the game opens `disc0:/PSP_GAME/INSDIR/DATA.TPA` it gets that
file, `sceIoGetstat` reports its size, and raw-sector reads of the container
come from it too -- except the container's first sector, which is all the
anti-copy check reads and which stays the disc's own. The log says
`data: update 1.02 overlay active`.

## What is known and what is not

The 6 entries are sub-packages (magic 0x0004C0DE) whose contents are encrypted
by the game's own cipher; their tables are identical in size to the disc's
(26/26/26/25/25/42 or 43 items), so what changed inside is not visible without
that cipher. `EMOTIONALSENSE_UPDATE_MASK=<hex bits by update entry>` applies only
some entries (debugging).

Measured with the 1.00 code: with the whole update the routes reach gameplay in
the same frames as without it. (An earlier note said entry `0x41A08A46` stalled
them. It did not: that entry is resident and 1392 bytes bigger, which moves the
heap by 0x580, and the scene machine object, hence the scene word the routes and
the play-state detection read, moved with it. The word is now found through the
pointer at guest address `0xC8B30` (object + 4). No "Data Install" is needed: the
game never opens a memory-stick install path on the way to gameplay.)
