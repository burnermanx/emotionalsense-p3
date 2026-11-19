/* emotionalsense -- ATRAC3 / ATRAC3+ block decoder (libavcodec). */

#include "at3_dec.h"

#include <libavcodec/avcodec.h>
#include <libavutil/channel_layout.h>
#include <libavutil/mem.h>

#include <stdlib.h>
#include <string.h>

struct at3_dec {
    AVCodecContext *ctx;
    AVPacket *pkt;
    AVFrame *frm;
    int channels;
};

at3_dec *at3_open(int codec_atrac3p, int channels, int sample_rate, int block_align,
                  const uint8_t *extradata, int extradata_size) {
    const AVCodec *c = avcodec_find_decoder(codec_atrac3p ? AV_CODEC_ID_ATRAC3P : AV_CODEC_ID_ATRAC3);
    if (!c || channels < 1 || channels > 2) return NULL;
    at3_dec *d = calloc(1, sizeof *d);
    if (!d) return NULL;
    d->channels = channels;
    d->ctx = avcodec_alloc_context3(c);
    d->pkt = av_packet_alloc();
    d->frm = av_frame_alloc();
    if (!d->ctx || !d->pkt || !d->frm) { at3_close(d); return NULL; }
    d->ctx->sample_rate = sample_rate;
    d->ctx->block_align = block_align;
    av_channel_layout_default(&d->ctx->ch_layout, channels);
    if (extradata && extradata_size > 0) {
        d->ctx->extradata = av_mallocz((size_t)extradata_size + AV_INPUT_BUFFER_PADDING_SIZE);
        if (d->ctx->extradata) {
            memcpy(d->ctx->extradata, extradata, (size_t)extradata_size);
            d->ctx->extradata_size = extradata_size;
        }
    }
    if (avcodec_open2(d->ctx, c, NULL) < 0) { at3_close(d); return NULL; }
    return d;
}

static int16_t to_s16(float f) {
    int v = (int)(f * 32768.0f);
    return (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
}

int at3_decode(at3_dec *d, const uint8_t *block, int size, int16_t *out, int out_cap_frames) {
    if (!d) return 0;
    if (av_new_packet(d->pkt, size) < 0) return 0;
    memcpy(d->pkt->data, block, (size_t)size);
    int rc = avcodec_send_packet(d->ctx, d->pkt);
    av_packet_unref(d->pkt);
    if (rc < 0) return 0;
    if (avcodec_receive_frame(d->ctx, d->frm) < 0) return 0;

    int n = d->frm->nb_samples;
    if (n > out_cap_frames) n = out_cap_frames;
    const int ch = d->channels;
    for (int c = 0; c < ch; c++) {
        const uint8_t *src = d->frm->extended_data[c];
        if (d->frm->format == AV_SAMPLE_FMT_FLTP) {
            const float *f = (const float *)src;
            for (int i = 0; i < n; i++) out[i * ch + c] = to_s16(f[i]);
        } else if (d->frm->format == AV_SAMPLE_FMT_S16P) {
            const int16_t *s = (const int16_t *)src;
            for (int i = 0; i < n; i++) out[i * ch + c] = s[i];
        } else {
            for (int i = 0; i < n; i++) out[i * ch + c] = 0;
        }
    }
    av_frame_unref(d->frm);
    return n;
}

void at3_close(at3_dec *d) {
    if (!d) return;
    if (d->ctx) avcodec_free_context(&d->ctx);
    if (d->pkt) av_packet_free(&d->pkt);
    if (d->frm) av_frame_free(&d->frm);
    free(d);
}
