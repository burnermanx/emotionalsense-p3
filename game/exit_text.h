/* The texts of the exit confirmation, rasterised (scripts/gen_exit_text.py). */
#ifndef ES_EXIT_TEXT_H
#define ES_EXIT_TEXT_H

#define ES_EXIT_LANGS 5

typedef struct {
    int w, h;                       /* mask pixels: 2 per 480x272 unit */
    const unsigned char *a;         /* h rows of w bytes, coverage 0..255 */
} es_exit_text;

/* [language][0 prompt, 1 yes, 2 no]; languages in the order of the codes below. */
extern const es_exit_text es_exit_texts[ES_EXIT_LANGS][3];
extern const char *const es_exit_lang_codes[ES_EXIT_LANGS];

#endif
