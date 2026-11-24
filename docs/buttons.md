# Button symbols and the 2T triggers (round 36)

Two options that are about the controller, not about the game's rules.

## Button symbols: `button_style` (`EMOTIONALSENSE_BUTTONS`, `--buttons`)

`auto` (default) | `playstation` | `xbox` | `steamdeck` | `switch`.

| Style | cross (bottom) | circle (right) | square (left) | triangle (top) | letters |
|---|---|---|---|---|---|
| playstation | cross | circle | square | triangle | the game's own symbols |
| xbox | A | B | X | Y | A green, B red, X blue, Y yellow |
| steamdeck | A | B | X | Y | white |
| switch | B | A | Y | X | white |

The game draws PlayStation symbols as sprites inside UI atlases. The renderer repaints the **uploaded copy** of those textures (nothing is
written anywhere) following rules that are numbers only (`k_glyph_rules` in `game/hle_djmax.c`: texture key, size, square, the symbol it shows,
`rot`); the letters A, B, X, Y are rasterised by code (`glyph_paint` in `psprecomp/src/render_vulkan.c`, strokes in a unit square on the glyph's
own dark disc, in the glyph's rim, nothing from a font or from the game). `psprecomp` knows nothing about DJMAX: the host gives it the rules and,
per original symbol, a look (`psp_glyph_map`: a letter and a colour, or the symbol to show). The texture cache keys carry the map's id, so a change
of style (or of the confirm swap) re-uploads only the affected textures. Only the Vulkan renderer does this; the software renderer shows the
PlayStation symbols (the option's hint says so).

### Composition with the confirm button

A prompt shows the button that runs its action. With "cross confirms" the menus swap circle and cross on the sceCtrl value (outside a song only), so the
game's "circle = OK" prompt is run by the physical cross: its disc shows the **cross position's** symbol of the style (A on Xbox, B on Switch).
`game/button_style.h` is that table (`bs_resolve(style, swap, symbol)`), `launcher/es_buttons.py` the launcher's copy, and both have a test of all
combinations (`game/tests/test_button_style.c`, `launcher/tests/test_buttons.py`: 5 styles x 2 confirmations). The map is recomputed every vblank
(`djmax_buttons_tick`) from the style and from "is the swap active now" (a menu, not a song).

### Positional input

SDL's game controller maps by *label* on a Nintendo pad (its A is the right button). The game and the launcher set
`SDL_GAMECONTROLLER_USE_BUTTON_LABELS=0` before the init (a value in the environment wins), so the bottom button is always the PSP cross, which is
what the table above (and the Switch row's B A Y X) assumes.

### Auto

Follows the controller that last pressed something (the window opens up to four): `SDL_GameControllerGetType` (Xbox 360 / One -> xbox, Switch Pro and
Joy-Con -> switch, PS3 / PS4 / PS5 -> playstation, anything else -> xbox), the Steam Deck by its own ids (`28de:1205`) or, for Steam's virtual pad
(`28de:11ff`, which SDL reports as an Xbox 360), by `SteamDeck=1`. Windows (XInput) is xbox. The keyboard never changes it; with no pad yet it is PlayStation.
Under Steam Input every pad is a "virtual Xbox 360"; the real type reaches the game only when the SDL in use reads the file named by Steam's
`SteamVirtualGamepadInfo` environment variable (SDL 3 and sdl2-compat on it; the AppImage and the Windows kit carry SDL 2.30, whose support was **not
verified**). `EMOTIONALSENSE_TRACE_PAD=1` logs each controller opened (name, SDL type, vendor:product, resulting style): that is the tool to settle it on a Deck.
The launcher's pad (`es_pad.py`) uses the same classification (`es_buttons.classify_pad`, tested) and drives the footer and the hints of the Options.

### Button pills: SELECT, START and the shoulder buttons (round 36b)

Some prompts are not discs but small **pills with a word** in them. The renderer keeps the pill (shape, rim, gradient) and replaces only the word:
it erases the word's box (each row is blended between the two texels just outside the box, so a black pill and a gradient face both stay as they are)
and draws the new label from geometry (`pill_paint` in `psprecomp/src/render_vulkan.c`: bars, rounded squares, plus, minus and four stroked characters;
nothing from any maker's art or font). The rules (`k_glyph_rules`, symbols `E` SELECT, `l` and `r` the shoulders, `S` START) hold the **text box** inside the pill;
the table is `bs_pill_look` in `game/button_style.h` (and `launcher/es_buttons.py`, both tested), independent of the confirm button:

| Style | START | SELECT | left shoulder | right shoulder |
|---|---|---|---|---|
| playstation | the game's word | the game's word | L1 | R1 |
| xbox | Menu icon (three bars) | View icon (two overlapping squares) | LB | RB |
| steamdeck | Menu icon | View icon | L1 | R1 |
| switch | + | - | L | R |

Icons are white; the shoulder text keeps the dark of the game's own label. PlayStation is **no longer byte for byte the 0.1 look**: its shoulder pills read L1 / R1
(the game's art says LB / RB, which is the Xbox name); its SELECT and START are untouched. The map id is never 0 now (`djmax_buttons_tick`).

What the catalogue found (`find_glyphs.py pills <dump/all>`, 268 textures of the 20 routes):

| texture key (size) | pills | seen in |
|---|---|---|
| `a8fb042a8b1754db` (128x32) | SELECT (word box x 8, y 4, 32x7) | FreeStyle list footer "SELECT Change Trax" (not in CLASSIC stage lists) |
| `3ddbb8a994b8c554` (256x256) | left / right shoulder pills (word boxes x 20 / 138, y 5, 15x9) | list header "LB X 3.5 RB", pause menu |
| `48fb00810c9356e7`, `bf62771857f976e0` (256x128) | the same two pills, shifted by (-1, +2) | list header variants |

Left as the game draws them (reported, not changed): the title's "PRESS START BUTTON" (a sentence of body text, so START has no pill on any screen of the 20 routes: its
Menu / plus looks exist in the table and the renderer, with no rule yet); the tiny plain "L" and "R" beside the arrows of the lounge's gallery and PLAY DATA
pickers (textures `5cdd108a66d9f92d`, `66c61a264efc44a6`, `92a108fbcd642f11`: letters about 4x8 texels in a quad of their own, there is no room for "L1" / "LB" inside the quad);
the letter atlas `94c452fd2aaf0a7e` has three 16-texel button discs (cross, circle, triangle) that no captured screen draws (unreached, uncatalogued).
`find_glyphs.py pillscreens <dir> --dump <dump/all>` lists the original words still visible in 1x captures.

### The catalogue (`scripts/find_glyphs.py`)

Template matching of the four disc symbols over the texture dump of every route in `scripts/dump/` (276 textures) found **27 discs in 10 textures**:

| texture key (size) | discs | seen in |
|---|---|---|
| `0fba947c8ba1f2b9` (512x256) | circle, cross | scene 1 (title, PLAY MODE SELECT) |
| `3e86e26d5d754018` (128x128) | circle, cross, square, triangle in a row | scene 9 (LOUNGE) |
| `43176e9e394d6759` (512x256) | circle (RESULT "OK") | scene 4 |
| `665bed80d6445bb0` (256x256) | circle, cross ("Cancel / OK" bar) | scenes 2 and 6 |
| `66d76c818fc78d8a` (256x256) | circle, cross, text stored turned a quarter CCW (Yes / No) | scene 1 |
| `6df8e6862296785d` (128x64) | circle, cross (YES NO / CANCEL OK) | scenes 2, 4, 6 |
| `93b1446194f40cc3` (256x256) | circle, cross, square, triangle (Studio, Cancel, OK, Effector) | scene 2 |
| `838fa5df9271e5a5` (256x256) | cross, circle, square, 2 triangles (Cancel, OK, SP.Mission) | scene 6 |
| `92a108fbcd642f11` (128x64) | cross, circle, triangle in a column | scene 9 |
| `3537fbb8378a7b0e` (256x256) | cross, circle, text stored turned a quarter CCW | scene 1 (option) |

No disc is drawn in a song (the lanes have none). Checked on captures at 1x (`find_glyphs.py screens`, threshold 0.78): 20 routes sampled every 30 vblanks (3700 frames),
the PlayStation style shows 3506 disc hits; the same routes in the Xbox style with "cross confirms" show **none**. Nine routes (modes, menu, option, lounge, list, studio,
result, FreeStyle 4T and CLASSIC 6T song select; every 45 vblanks) in Xbox with "circle confirms", and in Steam Deck and Switch with both confirmations: none either; in
PlayStation with "cross confirms" the pairs are swapped (circle and cross trade places). Not seen on any capture: the two textures stored turned (`66d76...`, `3537f...`), whose
letters are turned by `rot` from the direction of their stored text, so their orientation is unverified.

`find_glyphs.py catalog <dump/all>` prints the table above from a dump; `find_glyphs.py screens <dir> --dump <dump/all>` lists the discs still visible in 1x captures.

## Triggers in the .2T modes: `triggers_2t` (`EMOTIONALSENSE_TRIGGERS_2T`, `--triggers-2t`)

`stick` (default) | `lr`. In 3.2T, 4.2T and 6.2T songs the lateral notes are the PSP analog stick pushed left or right.

* With `stick`, **only while such a song is being played**, LT = stick X 0 (left), RT = X 255 (right), both = centre; a physical stick outside its dead zone
  wins; LB / RB are L / R always. In menus, 4T and 6T LT / RT are L / R as before. With `lr` LT / RT are L / R everywhere (the 0.1 behaviour).
* The keyboard, always: **E** = stick left, **R** = stick right, both = centre (Q / W stay L / R).
* The game keeps the current style in the options struct (`0x130E58`) at **`0x130E5D`**: 0 4T, 1 6T, 2 3.2T, 3 4.2T, 4 6.2T. Proof: RAM of five songs, one per style (CLASSIC 4T and 6T, EASY 3.2T,
  NORMAL 4.2T, HARD 6.2T), diffed over `0x130000..0x138000`: that byte is 0, 1, 2, 3, 4 (the only byte of the struct that differs this way); polled in the FreeStyle list it goes 0 -> 1 -> 2
  with SELECT (4T -> 6T -> 3.2T) and in the PLAY MODE SELECT routes it is 3 (4.2T) and 4 (6.2T) before the song starts and while it plays.
* The psprecomp layer has no DJMAX knowledge: `psp_ctrl_set_ex(buttons, ax, ay, triggers, stick_moved)` delivers the triggers apart and
  `psp_ctrl_set_trigger_hook(fn)` asks the host "triggers as the stick now?" on every sceCtrl read (`triggers_as_stick_now` in `game/buttons.c`: the option,
  `djmax_playing()` and the style byte in 2..4), in both the SDL and the XInput paths. The E / R keys are handled in the window layer.
* Tests (`scripts/check_scripts.sh`; test script masks `0x10000` LT, `0x20000` RT, `0x40000` E, `0x80000` R): in 4.2T with LT / RT hammered and no lane button the
  judge's weight sum (judge `+0x814`) is 206 with `stick` and 6 with `lr` (where the 180 speed steps show LT / RT being L / R); the E / R keys reach 106 and 206; in 4T LT is L whatever the option says (15 speed steps).
