/* emotionalsense -- H.264/AVC access-unit decoder (libavcodec) with a built-in
 * YUV420 -> ABGR8888 converter, for the game's sceMpeg video. */
#ifndef ES_AVC_DEC_H
#define ES_AVC_DEC_H

#include <stdint.h>

typedef struct avc_dec avc_dec;

avc_dec *avc_open(void);

/* Feed one Annex-B access unit. Returns 1 if a picture is now available
 * (the latest one is kept), 0 if the decoder produced none, <0 on error. */
int avc_decode(avc_dec *d, const uint8_t *au, int size);

/* Drop decoder state (seek / flush). Keeps the last picture. */
void avc_flush(avc_dec *d);

/* Convert the region (x, y, w, h) of the latest picture to ABGR8888 (R in the
 * low byte, as the PSP's 8888 framebuffer) into dst, `stride_px` pixels per row.
 * Returns 0, or -1 if there is no picture yet. */
int avc_convert(avc_dec *d, uint32_t *dst, int stride_px, int x, int y, int w, int h);

int avc_width(const avc_dec *d);
int avc_height(const avc_dec *d);

void avc_close(avc_dec *d);

#endif
