/* button_style.h -- which symbol (or letter) each face button shows, per button style and per
 * confirm-button setting. Pure table, no state: host code and the unit test both use it. */
#ifndef ES_BUTTON_STYLE_H
#define ES_BUTTON_STYLE_H

#include <stdint.h>

enum { BS_AUTO = 0, BS_PLAYSTATION, BS_XBOX, BS_STEAMDECK, BS_SWITCH };

/* Symbols as the game draws them (and as the PSP names the physical positions):
 * 0 cross (bottom), 1 circle (right), 2 square (left), 3 triangle (top). */
typedef struct { char letter; char shows; uint32_t rgb; } bs_look;

/* What a prompt that the game draws as symbol `sym` must show.
 *   swap  the confirm-with-cross swap is active right now (a menu, not a song): the game's circle
 *         is then executed by the physical cross button and the other way round, so the prompt
 *         shows the symbol of the PHYSICAL button that does that action.
 *   letter 0 = keep the symbol shown in `shows` ('x' 'o' 's' 't'); else 'A' 'B' 'x' (the letter X) 'Y'. */
static inline bs_look bs_resolve(int style, int swap, int sym) {
    static const char SYM[4] = { 'x', 'o', 's', 't' };
    bs_look r = { 0, 0, 0 };
    int phys = sym;
    if (swap && sym < 2) phys = 1 - sym;                       /* circle <-> cross */
    if (style == BS_PLAYSTATION || style < BS_PLAYSTATION || style > BS_SWITCH) {
        r.shows = SYM[phys];
        return r;
    }
    /* the face button at that position: xbox/deck A B X Y by position, switch B A Y X */
    static const char XB[4] = { 'A', 'B', 'x', 'Y' };
    static const char SW[4] = { 'B', 'A', 'Y', 'x' };
    r.letter = style == BS_SWITCH ? SW[phys] : XB[phys];
    if (style == BS_XBOX) {
        static const uint32_t COL[4] = { 0x6CC04A, 0xE8453C, 0x3F8CF2, 0xF2C230 };   /* green, red, blue, yellow */
        r.rgb = COL[phys];
    } else {
        r.rgb = 0xFFFFFF;                                       /* white */
    }
    return r;
}

#endif
