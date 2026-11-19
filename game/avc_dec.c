/* emotionalsense -- H.264/AVC access-unit decoder (libavcodec). */

#include "avc_dec.h"

#include <libavcodec/avcodec.h>
#include <libavutil/imgutils.h>
#include <libavutil/mem.h>

#include <stdlib.h>
#include <string.h>

struct avc_dec {
    AVCodecContext *ctx;
    AVPacket *pkt;
    AVFrame *frm;     /* scratch for receive_frame */
    AVFrame *last;    /* latest picture */
    int have;
};

avc_dec *avc_open(void) {
    const AVCodec *c = avcodec_find_decoder(AV_CODEC_ID_H264);
    if (!c) return NULL;
    avc_dec *d = calloc(1, sizeof *d);
    if (!d) return NULL;
    d->ctx = avcodec_alloc_context3(c);
    d->pkt = av_packet_alloc();
    d->frm = av_frame_alloc();
    d->last = av_frame_alloc();
    if (!d->ctx || !d->pkt || !d->frm || !d->last) { avc_close(d); return NULL; }
    d->ctx->flags |= AV_CODEC_FLAG_LOW_DELAY;       /* one picture per AU, no reorder wait */
    d->ctx->thread_count = 1;
    if (avcodec_open2(d->ctx, c, NULL) < 0) { avc_close(d); return NULL; }
    return d;
}

int avc_decode(avc_dec *d, const uint8_t *au, int size) {
    if (!d || size <= 0) return -1;
    if (av_new_packet(d->pkt, size) < 0) return -1;
    memcpy(d->pkt->data, au, (size_t)size);
    int rc = avcodec_send_packet(d->ctx, d->pkt);
    av_packet_unref(d->pkt);
    if (rc < 0) return -1;
    int got = 0;
    while (avcodec_receive_frame(d->ctx, d->frm) >= 0) {
        av_frame_unref(d->last);
        av_frame_move_ref(d->last, d->frm);
        d->have = got = 1;
    }
    return got;
}

void avc_flush(avc_dec *d) {
    if (d && d->ctx) avcodec_flush_buffers(d->ctx);
}

static uint8_t clamp8(int v) { return (uint8_t)(v < 0 ? 0 : v > 255 ? 255 : v); }

int avc_convert(avc_dec *d, uint32_t *dst, int stride_px, int x0, int y0, int w, int h) {
    if (!d || !d->have || !dst) return -1;
    const AVFrame *f = d->last;
    const int fw = f->width, fh = f->height;
    const int planar420 = f->format == AV_PIX_FMT_YUV420P || f->format == AV_PIX_FMT_YUVJ420P;
    const int full = f->format == AV_PIX_FMT_YUVJ420P;
    for (int j = 0; j < h; j++) {
        int sy = y0 + j;
        uint32_t *row = dst + (size_t)j * (size_t)stride_px;
        for (int i = 0; i < w; i++) {
            int sx = x0 + i;
            if (!planar420 || sx < 0 || sy < 0 || sx >= fw || sy >= fh) { row[i] = 0xFF000000u; continue; }
            int Y = f->data[0][sy * f->linesize[0] + sx];
            int U = f->data[1][(sy >> 1) * f->linesize[1] + (sx >> 1)] - 128;
            int V = f->data[2][(sy >> 1) * f->linesize[2] + (sx >> 1)] - 128;
            int r, g, b;
            if (full) {
                r = Y + ((91881 * V) >> 16);
                g = Y - ((22554 * U + 46802 * V) >> 16);
                b = Y + ((116130 * U) >> 16);
            } else {                                   /* BT.601 limited range */
                int c = (Y - 16) * 76309;
                r = (c + 104597 * V + 32768) >> 16;
                g = (c - 25675 * U - 53279 * V + 32768) >> 16;
                b = (c + 132201 * U + 32768) >> 16;
            }
            row[i] = 0xFF000000u | ((uint32_t)clamp8(b) << 16) | ((uint32_t)clamp8(g) << 8) | clamp8(r);
        }
    }
    return 0;
}

int avc_width(const avc_dec *d)  { return d && d->have ? d->last->width : 0; }
int avc_height(const avc_dec *d) { return d && d->have ? d->last->height : 0; }

void avc_close(avc_dec *d) {
    if (!d) return;
    if (d->ctx) avcodec_free_context(&d->ctx);
    if (d->pkt) av_packet_free(&d->pkt);
    if (d->frm) av_frame_free(&d->frm);
    if (d->last) av_frame_free(&d->last);
    free(d);
}
