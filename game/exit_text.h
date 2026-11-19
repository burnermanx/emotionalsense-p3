/* The texts of the exit confirmation, rasterised. */
#ifndef ES_EXIT_TEXT_H
#define ES_EXIT_TEXT_H

#define ES_EXIT_LANGS 5

typedef struct {
    int w, h;                       /* pixels */
    const unsigned char *bits;      /* h rows of (w+7)/8 bytes, MSB first */
} es_exit_text;

/* [language][0 prompt, 1 yes, 2 no]; languages in the order of the codes below. */
extern const es_exit_text es_exit_texts[ES_EXIT_LANGS][3];
extern const char *const es_exit_lang_codes[ES_EXIT_LANGS];

#endif
