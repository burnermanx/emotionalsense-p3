/* buttons.c -- the button style (round 36, B) and the 2T triggers (T).
 *
 *  B  The game draws PlayStation symbols. The host repaints the uploaded copy of the textures
 *     that hold them (rules in hle_djmax.c) as the style says: xbox / steamdeck / switch letters,
 *     composed with the confirm-with-cross swap (button_style.h). `auto` follows the controller
 *     that last pressed something (psp_window_pad_kind), keeps the current style when only the
 *     keyboard is used, and starts as PlayStation.
 *  T  triggers_2t = stick: during a 3.2T/4.2T/6.2T song the triggers are the PSP stick's X axis
 *     (psprecomp does the mapping, this file answers "now?"). */

#include <psprecomp/hle.h>
#include <psprecomp/mem.h>
#include <psprecomp/render.h>
#include <psprecomp/window.h>
#include "psprecomp/log.h"

#include <stdint.h>
#include <string.h>

#include "button_style.h"

int djmax_playing(void);                 /* guest_patches.c */
int djmax_swap_now(void);                /* hle_djmax.c */
int djmax_confirm_cross(void);
void djmax_install_glyph_rules(void);

int djmax_button_style = BS_AUTO;        /* the option */
int djmax_triggers_stick = 1;            /* the option: 1 stick (default), 0 lr */

/* ---- T ---- */
#define STYLE_BYTE 0x00130E5Du           /* options struct 0x130E58 +5: 0 4T, 1 6T, 2 3.2T, 3 4.2T, 4 6.2T */

static int triggers_as_stick_now(void) {
    if (!djmax_triggers_stick || !djmax_playing()) return 0;
    uint8_t st = psp_read8(STYLE_BYTE);
    return st >= 2 && st <= 4;
}

/* ---- B ---- */
static int g_auto_style = BS_PLAYSTATION;    /* what `auto` resolved to last */
static int g_cur_id = -1;

int djmax_button_effective_style(void) {
    if (djmax_button_style != BS_AUTO) return djmax_button_style;
    switch (psp_window_pad_kind()) {
    case 1: g_auto_style = BS_XBOX; break;
    case 2: g_auto_style = BS_PLAYSTATION; break;
    case 3: g_auto_style = BS_SWITCH; break;
    case 4: g_auto_style = BS_STEAMDECK; break;
    default: break;                          /* keyboard / no pad: keep the current one */
    }
    return g_auto_style;
}

/* For the host's own overlays (the exit dialog): the letter of the button that runs the confirm
 * (what_confirm = 1) or the cancel action, 0 + *shows for a PlayStation symbol. */
bs_look djmax_button_for_action(int confirm) {
    const int cross = djmax_confirm_cross();
    /* the physical position that confirms: circle position unless confirm-with-cross */
    const int phys_sym = confirm ? (cross ? 0 : 1) : (cross ? 1 : 0);
    return bs_resolve(djmax_button_effective_style(), 0, phys_sym);
}

/* Every vblank: the style or the swap changed -> a new glyph map. */
void djmax_buttons_tick(void) {
    const int style = djmax_button_effective_style();
    const int swap = djmax_swap_now();
    const int id = (style == BS_PLAYSTATION && !swap) ? 0 : style * 2 + swap + 1;
    if (id == g_cur_id) return;
    if (g_cur_id < 0 && id == 0) { g_cur_id = 0; return; }
    psp_glyph_map m;
    memset(&m, 0, sizeof m);
    m.id = (uint32_t)id;
    for (int sym = 0; sym < 4; sym++) {
        bs_look l = bs_resolve(style, swap, sym);
        m.look[sym].letter = l.letter;
        m.look[sym].shows = l.shows;
        m.look[sym].rgb = l.rgb;
    }
    psp_render_set_glyph_map(id ? &m : NULL);
    psp_log("buttons: style %d, confirm swap %d (map %d)\n", style, swap, id);
    g_cur_id = id;
}

void djmax_buttons_init(void) {
    djmax_install_glyph_rules();
    psp_ctrl_set_trigger_hook(triggers_as_stick_now);
}
