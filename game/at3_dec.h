/* emotionalsense -- ATRAC3 / ATRAC3+ block decoder (libavcodec). */
#ifndef ES_AT3_DEC_H
#define ES_AT3_DEC_H

#include <stdint.h>

typedef struct at3_dec at3_dec;

/* codec_atrac3p: 1 = ATRAC3+, 0 = ATRAC3. Returns NULL if libavcodec lacks it. */
at3_dec *at3_open(int codec_atrac3p, int channels, int sample_rate, int block_align,
                  const uint8_t *extradata, int extradata_size);

/* Decode one block_align-sized frame into interleaved int16 (channels per
 * sample). `out` must hold at least 2048 * channels samples. Returns the number
 * of sample frames produced (0 on error / no output yet). */
int at3_decode(at3_dec *d, const uint8_t *block, int size, int16_t *out, int out_cap_frames);

void at3_close(at3_dec *d);

#endif
