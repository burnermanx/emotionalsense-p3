# sceMpeg / PSMF: the video path

The game's videos (opening, logos, menu background, song-select preview and the
songs' BGA) are PSMF: MPEG-PS with H.264 video and ATRAC3+ audio. The HLE in
`game/hle_djmax.c` implements the sequence the game uses and decodes with
libavcodec (`game/avc_dec.c`, `game/at3_dec.c`).

## The sequence the game calls

```
RingbufferPut -> GetAtracAu -> AtracDecode -> GetAvcAu ->
AvcDecodeYCbCr -> AvcCsc (destination in guest memory)
```

## Ring buffer

The guest's ring buffer holds **2048-byte** packets (`PS_PKT`). The HLE does not
fill the ring: it calls the callback the game itself registered and demuxes what
arrived.

`hle_MpegRingbufferConstruct` writes the struct at `rb+0..40`:

| Offset | Field |
|---|---|
| +0 | total packets |
| +4 | read pointer |
| +8 | write pointer |
| +12 | free packets (`rb_free_pkts()`) |
| +16 | packet size (2048) |
| +20 | data address |
| +24 | callback |
| +28 | callback argument |
| +32 | end of the data |
| +36, +40 | zeroed |

`hle_MpegRingbufferPut`:

- limits the request by `available` and by `rb_free_pkts()`;
- calls the callback with `a0 = ptr`, `a1 = chunk`, `a2 = arg` and reads `v0` =
  packets added (`< 0` = EOF, `0` = stop);
- does not cross the end of the ring in one call (the write pointer does not wrap
  in the middle);
- demuxes every packet delivered and adds `g_raw_demuxed += 2048`.

`rb_free_pkts()` returns the real space, with three rules that have already cost
bugs:

| Rule | Reason |
|---|---|
| `used = held / PS_PKT` with **floor** | the game's end-of-stream test is exact equality `AvailableSize == total_packets`; partial bytes left the count one packet short forever |
| above 1 MB it returns 0 | this is what paces the feeder in the opening |
| above 1 MB, if the game has **never** read MPEG audio (`!g_atrac_read`), it discards the oldest half of the audio queue | the BGA has audio that gameplay never consumes; without this the ring fills up and the video dies |

`g_atrac_read` is set in `hle_MpegGetAtracAu` and is **not** cleared in
`mpeg_reset_streams`: being sticky between streams is correct for this game.

## Stream size and read-ahead

- `hle_MpegQueryStreamSize` reads BE32 at `buf+0x0C` into `g_stream_size`.
- `hle_MpegQueryStreamOffset` reads BE32 at `buf+0x08` (default `0x800`).
- The demux stops at `g_raw_demuxed >= g_stream_size`, discarding the reader's
  64 KB read-ahead beyond the declared end (which injected the PSMF header of the
  next video).
- `hle_MpegRegistStream` type 0 (video) clears `g_raw_demuxed` and `g_sig_have`.

## PSMF loop

The game repeats a small PSMF by feeding the same stream again through the
callback. The HLE detects this by **content**: it stores the first whole 2048-byte
packet and, after the end of the stream, looks for that signature in every packet
delivered. When it appears, `g_raw_demuxed = 0`.

| Rule | Value |
|---|---|
| Signature | first whole packet, 2048 bytes |
| Size limit | `g_stream_size <= 256 * 1024` |
| PTS | not reset by force; `ts_interp_next` restarts when the base changes |

The 256 KB limit is essential: the PLAY MODE SELECT background (163840 bytes)
restarts; the song-select preview (2,267,136) and the BGA (17,803,264) are
one-shot. Without the limit the preview also looped and the game never left
scene 2.

## Demux

`demux_pack` only accepts a packet that starts with `00 00 01 BA`; the PES start at
`14 + (p[13] & 7)`.

| Stream id | Content | Handling |
|---|---|---|
| `0xE0` | H.264 video | PTS/DTS and payload to the video FIFO |
| `0xBD` | audio (substream) | skips 4 bytes of substream header; only queued if `g_have_aud_stream` |

`g_have_aud_stream` is set by `sceMpegRegistStream` type 1. The BGA's audio is not
read in gameplay; queueing it fills the ring and kills the video.

Each audio AU is `0F D0` + 6 bytes of header + **744 bytes** of ATRAC3+ (132 kbps
stereo). The HLE resynchronises on `0F D0` and delivers a whole frame.

## Video AU

`hle_MpegGetAvcAu` assembles an Annex-B AU:

1. Finds the start: prefers an AUD (NAL type 9); if there is none, accepts any
   start code.
2. Finds the end, in this order:
   - next AUD;
   - next PES boundary with a known PTS (`g_vts.e[1].off`);
   - next SPS (7) or AUD;
   - EOF (`g_rb_eof`);
   - declared stream already demuxed;
   - `n >= 16384` or `rb_free_pkts() == 0` (avoids a circular deadlock).
3. Copies into `g_au_buf`, sets `g_au_valid`, consumes from the FIFO and fills the
   `SceMpegAu` (`pts` high/low, `dts` high/low, 0, ES size).

`hle_MpegAvcDecodeYCbCr` decodes the pending AU; `hle_MpegAvcDecodeDetail` returns
the decoder's width/height (480x272 in the normal case); `hle_MpegAvcCsc` converts
to RGBA in the guest buffer.

## A/V sync (PTS)

The PES header carries a timestamp once per GOP in the video (~16 frames) and every
2-3 blocks in the audio. Handing that value to every intermediate AU made both of
the player's clocks move in steps, and the recovery loop read the step as lag.

| Function | Role |
|---|---|
| `ts_take` | timestamp of the last PES entry at or before the offset; discards the older ones |
| `ts_interp_next` | the first AU of the PES gets the PES's PTS; each following AU adds one period, without rounding the period at every step |

| Stream | Period per AU |
|---|---|
| Video | `3003` ticks (90000 / 29.97) |
| Audio | `2048 * 90000 / 44100` = 4179.59... |

The state clears when the PTS base changes and in `mpeg_reset_streams()` (which
covers `sceMpegFlushAllStream`). The DTS is interpolated along with it.

The game's player compares `a - v` with the period of one frame (`p = 3003`) at
`0x7C330`; above `4p` it enters the recovery loop. With the interpolation, in
frames 303-480 the difference stayed between -34892 and -752 ticks (-11.6 to
-0.25 frame), with no reading above `4p`.

## Why the video goes straight to guest memory

`sceMpegAvcCsc(mpeg, source, range, stride, dest)` has a contract of writing to the
address the game passes. The HLE does exactly that: `avc_convert` writes RGBA to
`psp_mem_ptr(dest)`, with `stride` in pixels (default 512) and the `range`
rectangle (x, y, w, h; w/h <= 64 are multiplied by 16).

Consequences:

- The renderer **never sees** the decoding: it only executes GE display lists. In
  the opening the destination is the framebuffer; in gameplay the BGA goes to a
  512x512 8888 texture at `0x04154000`, which the GE draws as ~14 sprites of
  34x272.
- Raising the internal resolution requires drawing the decoded frame as a texture
  in the scaled target; touching the renderer is not enough.
- The capture reads VRAM before the current frame is presented, so in a scene with
  moving video the captured image can differ between runs even with deterministic
  state.

## Open problem: the second song stops at the 18 MB BGA

| Datum | Value |
|---|---|
| Size of the second song's BGA | 18,456,576 bytes |
| Where it stops | PTS 903813 (round 11, frame ~13183) / PTS 906816 (round 12, frame 9241) |
| Frames decoded | ~38 |
| `GetAvcAu` | stops being called |
| Ring state at frame 9212 | `vid=1102611 aud=0 raw=2621440 wp=64` |
| Immediate cause | the feeder stops because the video ring is full |
| BGA audio | empty (`aud=0`) |

The audio of the second song's BGA is empty, while the first one has a soundtrack.
The player waits for the audio clock. The sync function `0x7C330` is called until
frame 8575 (last record `ai=3 ap=235534`, `vi=0 vp=243153`, difference 7619 ticks
< `4p` = 12012); from the start of the second song it is no longer called,
because gameplay uses another sync path.

The first song works: BGA of 17,803,264 bytes, last AU at 11756/11764 with PTS
10,336,236 (113.8 s), zero `GetAvcAu` NO_DATA.

The next step suggested in the notes: separate gameplay sync from menu sync and
**do not wait for audio when the BGA has no soundtrack**.

## Diagnostic variables

| Variable | What it prints |
|---|---|
| `EMOTIONALSENSE_TRACE_AV=1` | audio/video firmware calls and a hexdump of the first bytes of the buffers (`AtracSetDataAndGetID`, `MpegQueryStreamOffset`, `AvcDecodeYCbCr`, `AvcCsc`, `AtracDecode`) |
| `EMOTIONALSENSE_TRACE_MPEG=1` | `Create`/`Delete`, `RingbufferConstruct`/`Put`, demuxed packets (first 80), `GetAvcAu`/`GetAtracAu` with PTS, audio discards, `FlushAllStream` |

## History of fixes

| Fix | Effect |
|---|---|
| `used = held / PS_PKT` with floor instead of ceil | the end-of-stream test started to close |
| `g_stream_size` captured in `QueryStreamSize` | discards the read-ahead beyond the end |
| PTS per AU (`ts_interp_next`) | the step that triggered the recovery is gone |
| BGA audio only queued if `g_have_aud_stream` | the video no longer dies from a full ring |
| discard of the old half of the audio when `!g_atrac_read` | same, for a BGA whose soundtrack is not read |
| loop by whole-packet signature + 256 KB limit | the menu background restarts; the preview and the BGA do not |
| 16-byte signature with PTS reset (discarded attempt) | restarted too early and trapped the game in scene 2 |
