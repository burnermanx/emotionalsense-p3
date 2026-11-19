# Fever (DJMAX Portable 3, ULKS46236): what the game does and what EmotionalSense changes

Found in round 27 by diffing RAM, write/read watches and reading the recompiled
code. All offsets are for the module as loaded (1.00 and the 1.02 update share
the same code); heap objects are reached through the scene machine, never by a
fixed heap address.

## Path to the objects

    0xC8B30           pointer to the scene machine (scene word at +4)
    *0xC8B30 + 0      the current scene object (class pointer at +0)
    scene + 0xC       judge object, gameplay scene only (class 0xCA9B4)
    judge + 0x55C     lane object        + 0x1940 target speed, + 0x1944 current speed (floats, "X 2.5")
    judge + 0x560     rules object       + 0x10 = tightest judgment window, signed byte
    judge + 0x564     Fever object "F"
    judge + 0x7EC     key configuration  + 0xB = id of the Fever button, id 10 -> CROSS (0x4000)
    judge + 0x7FC     life (float, 100.0 = full)

## The Fever object F

| offset | meaning |
|---|---|
| +0x136 (u8) | gauge in percent (raw counter at +0x138, u16, capped per level at +0x80+24*level) |
| +0x13A | gauge full, waiting for the Fever button |
| +0x13B | Fever running (about 560 vblanks, 9.3 s) |
| +0x135 | level index: x2 = 1 ... x5 = 4 (the COMBO x2..x5 art on the lane sides) |
| +0x134 | highest level index (4, plus what the crew skills add; capped at 6) |
| +0x148 | **auto fever**: set from the crew skills (`0x5B48C`, judge `0x20118`) |
| +0x13C / +0x13D | "I sped the lane up" markers for level index 3 and 5 |

Flow: a MAX 100% hit (judgment 12) calls `0x25358`, which fills the gauge. At
100 % it sets +0x13A and, if +0x148 is set, calls the activation `0x254FC` at once.
Without auto fever the judge's per-frame update (`0x20724`) asks the input
object whether the button of id `config[0xB]` is down and calls `0x254FC` then;
`0x254FC` does nothing unless +0x13A is set. Activating level index 3 and 5
calls `0x17E90` (the routine R uses to speed the lane up by 0.5) from `0x255B8` /
`0x255E8` and the end of the Fever scrolls back (`0x17F04`) -- that is the
"faster at x4/x5".

Measured (cross-checked in `scripts/check_scripts.sh`): with the auto flag
forced to 0 and no CROSS pressed the gauge fills and the Fever never starts;
with CROSS it starts; with the flag at 1 it starts in the same vblank the gauge
fills. At level index 3 the target speed goes 2.5 -> 3.0 and the current speed
eases to 3.0 (0.05 per vblank); at the end it goes back.

## Modes

* **Original**: nothing is touched (the wrap only passes through).
* **Respect** (default): `host.c` holds F+0x148 at 1 while the gameplay scene is
  current, and `guest_patches.c` answers the two Fever call sites of `0x17E90`
  with "no step taken", so the lane keeps the speed the player chose. R and L
  still change the speed as always.

## What it does to the save

Nothing is written specially: no variable of the records, the ranking or an
unlock condition reads F+0x148 or the skipped speed step (the only readers are
the activation above and the initialisation). Respect changes *play*, not what is
saved: an automatic Fever can only add Fever points compared with a player who
forgets the button, and a slower lane at x4/x5 changes how hard the notes are to
read, so scores, ranks and anything gated on them can differ from a manual run.
That is the point of the option; use Original for PSP-faithful records.
