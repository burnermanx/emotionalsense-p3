/* The composition of the button style with the confirm-with-cross swap: every prompt shows the
 * letter (or symbol) of the physical button that runs its action. 5 styles x 2 confirmations. */
#include "../button_style.h"
#include <stdio.h>

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL: " __VA_ARGS__); printf("\n"); } } while (0)

int main(void) {
    /* letter of each game symbol: [style][swap][sym], 0 = a symbol, not a letter */
    static const char want[5][2][4] = {
        /* playstation */ { {0, 0, 0, 0}, {0, 0, 0, 0} },
        /* xbox        */ { {'A', 'B', 'x', 'Y'}, {'B', 'A', 'x', 'Y'} },
        /* steamdeck   */ { {'A', 'B', 'x', 'Y'}, {'B', 'A', 'x', 'Y'} },
        /* switch      */ { {'B', 'A', 'Y', 'x'}, {'A', 'B', 'Y', 'x'} },
    };
    static const char psym[2][4] = { {'x', 'o', 's', 't'}, {'o', 'x', 's', 't'} };
    int n = 0;
    for (int style = BS_PLAYSTATION; style <= BS_SWITCH; style++)
        for (int swap = 0; swap < 2; swap++)          /* circle confirms, cross confirms */
            for (int sym = 0; sym < 4; sym++) {
                bs_look l = bs_resolve(style, swap, sym);
                n++;
                CHECK(l.letter == want[style - 1][swap][sym], "style %d swap %d sym %d: letter %c", style, swap, sym, l.letter ? l.letter : '-');
                if (style == BS_PLAYSTATION) CHECK(l.shows == psym[swap][sym], "ps swap %d sym %d shows %c", swap, sym, l.shows);
                if (style == BS_XBOX) CHECK(l.rgb != 0 && l.rgb != 0xFFFFFF, "xbox letters are coloured");
                if (style == BS_STEAMDECK || style == BS_SWITCH) CHECK(l.rgb == 0xFFFFFF, "white letters");
            }
    /* the four xbox colours are distinct and follow the physical button, with or without the swap */
    CHECK(bs_resolve(BS_XBOX, 0, 0).rgb == 0x6CC04A && bs_resolve(BS_XBOX, 1, 1).rgb == 0x6CC04A, "A is green wherever it comes from");
    CHECK(bs_resolve(BS_XBOX, 0, 1).rgb == 0xE8453C, "B is red");
    /* the auto style (0) is the PlayStation one */
    CHECK(bs_resolve(BS_AUTO, 0, 0).shows == 'x', "auto = playstation");
    printf("button style table: %d combinations checked, %d failures\n", n, fails);
    return fails ? 1 : 0;
}
