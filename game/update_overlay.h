/* The optional 1.02 data update, as a read-only overlay on DATA.TPA. */
#ifndef ES_UPDATE_OVERLAY_H
#define ES_UPDATE_OVERLAY_H
#include <stddef.h>

/* If <data>/update/DATA.TPA exists, make (or reuse) <data>/update/DATA.combined.TPA:
 * the disc's DATA.TPA with the entries of the update (same name hash) replaced.
 * The disc image and the extracted disc folder are never touched. Returns 1 and
 * fills `combined` when the overlay is active, 0 when there is none, -1 on a
 * bad update file (a message is printed). */
int es_update_prepare(const char *data, char *combined, size_t cap);
#endif
