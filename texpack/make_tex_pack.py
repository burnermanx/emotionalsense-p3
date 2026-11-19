#!/usr/bin/env python3
"""Build a 4x texture pack from a dump (EMOTIONALSENSE_TEX_DUMP).

    texpack/make_tex_pack.py <dump> <out> [--profile default|dat2] [--model texturedat2|texturedat2-onnx|realesrgan]

Every `<key>.png` in the dump becomes `<key>.png` in the pack, same name, so the
game finds it by content key with no index. The pack goes in `<data>/textures`
(or anywhere with `EMOTIONALSENSE_TEX_PACK=<dir>`).

Models
  texturedat2  4xTextureDAT2_otf (Phhofm/helaman, CC-BY-4.0, see
               THIRD_PARTY_NOTICES.md). PyTorch + spandrel, run from a venv:
                   <venv>/bin/python -I texpack/make_tex_pack.py ...
               Needs --weights <.safetensors>; the sha256 is checked against the
               value pinned below, so a wrong or tampered file is refused.
  realesrgan   realesrgan-x4plus through realesrgan-ncnn-vulkan, run in batch
               mode (one process per directory, not per texture). Vulkan only:
               works on AMD/Intel/NVIDIA and on the Steam Deck. Needs Pillow and
               numpy, nothing else.

Alpha, all models. A plain upscale leaves a dark halo on translucent edges
(the model sees black under the transparent part). The round-25 recipe
(premultiplied colour, alpha through the model, divide) was measured here and
is NOT enough: the model's colour and alpha disagree on the faint pixels and the
division draws a thin dark outline (still available as --alpha-mode premul).
Default `bleed`: the colour of transparent texels is filled in from the nearest
visible ones, that straight colour goes through the model, the alpha channel
goes through the same model as a grey picture, and the two are joined with no
division. Opaque textures skip the alpha pass.

Small or narrow textures (512x16...) get a reflected border of 8 px and the
size is padded to a multiple of 32 by reflection, then cropped after the model:
no edge artefacts. Textures bigger than --tile-max pixels run in overlapping
tiles whose margins are discarded, so there is no seam.

Nothing derived from the game is written next to this script: <out> is yours.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
NCNN_TOOL = os.path.join(ROOT, "work", "tools", "upscalers", "realesrgan-ncnn-vulkan")
NCNN_MODEL = "realesrgan-x4plus"
W2X_TOOL = os.path.join(ROOT, "work", "tools", "upscalers", "waifu2x-ncnn-vulkan-20250915-linux",
                        "waifu2x-ncnn-vulkan")
W2X_MODELS = "models-cunet"          # directory next to the tool
CLASSES_FILE = os.path.join(HERE, "tex_classes.txt")
MIX_INTERFACE = 0.5                  # interface: TextureDAT2 mixed this much with Lanczos

TEXTUREDAT2_SHA256 = "5851f91a4c95d803c919dd279a9353e32b212495cf54c5fbc868eed23b9f068b"
TEXTUREDAT2_URL = ("https://github.com/Phhofm/models/releases/download/"
                   "4xTextureDAT2_otf/4xTextureDAT2_otf.safetensors")

CONTEXT = 8          # reflected border added around every texture (source px)
MULTIPLE = 32        # the DAT window wants multiples of this
ALPHA_FLOOR = 4      # upscaled alpha below this (of 255) is treated as zero


# ---- alpha handling shared by both backends --------------------------------

def split_alpha(im):
    """-> (premultiplied RGB uint8 HxWx3, alpha uint8 HxW or None if opaque)."""
    rgba = np.asarray(im.convert("RGBA")).astype(np.float32)
    a = rgba[..., 3]
    pm = (rgba[..., :3] * (a[..., None] / 255.0) + 0.5).astype(np.uint8)
    return pm, (None if a.min() == 255 else a.astype(np.uint8))


def join_alpha(rgb_up, a_up):
    """rgb_up premultiplied HxWx3 uint8; a_up HxW float32 0..255 or None."""
    h, w = rgb_up.shape[:2]
    if a_up is None:
        return Image.fromarray(np.dstack([rgb_up, np.full((h, w), 255, np.uint8)]), "RGBA")
    a = np.clip(a_up, 0, 255)
    a[a < ALPHA_FLOOR] = 0
    rgb = np.where(a[..., None] > 0,
                   np.clip(rgb_up.astype(np.float32) * 255.0 / np.maximum(a[..., None], 1.0), 0, 255),
                   0)
    out = np.dstack([rgb, a]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def bleed_colour(im, iters=24):
    """-> (straight RGB uint8 HxWx3 with the colour of transparent texels filled in
    from their nearest visible neighbours, alpha uint8 HxW or None if opaque).
    Without this the model sees black (or garbage) under the transparent part and
    drags it into the edge: that is the dark halo."""
    rgba = np.asarray(im.convert("RGBA"))
    a = rgba[..., 3]
    col = rgba[..., :3].astype(np.float32)
    if a.min() == 255:
        return rgba[..., :3].copy(), None
    known = a > 0
    for _ in range(iters):
        if known.all():
            break
        k = np.pad(known, 1).astype(np.float32)
        c = np.pad(col * known[..., None], ((1, 1), (1, 1), (0, 0)))
        h, w = known.shape
        acc = np.zeros_like(col)
        cnt = np.zeros(known.shape, np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy or dx:
                    acc += c[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
                    cnt += k[1 + dy:1 + dy + h, 1 + dx:1 + dx + w]
        new = (~known) & (cnt > 0)
        col[new] = acc[new] / cnt[new][:, None]
        known = known | new
    if not known.all():                      # fully transparent texture: any colour will do
        col[~known] = 0
    return (col + 0.5).astype(np.uint8), a.copy()


def join_straight(rgb_up, a_up):
    """Straight colour (already bled) + upscaled alpha; no division anywhere."""
    h, w = rgb_up.shape[:2]
    if a_up is None:
        return Image.fromarray(np.dstack([rgb_up, np.full((h, w), 255, np.uint8)]), "RGBA")
    a = np.clip(a_up, 0, 255)
    a[a < ALPHA_FLOOR] = 0
    return Image.fromarray(np.dstack([rgb_up, a]).astype(np.uint8), "RGBA")


def grey3(a):
    return np.repeat(a[..., None], 3, axis=2)


def save_png(im, path):
    tmp = path + ".part"
    im.save(tmp, format="PNG")
    os.replace(tmp, path)


# ---- TextureDAT2 (PyTorch + spandrel) --------------------------------------

class DatBackend:
    name = "texturedat2"
    scale = 4

    def __init__(self, weights, fp16, tile_max, device):
        import torch
        from spandrel import ModelLoader
        self.torch = torch
        if not weights or not os.path.isfile(weights):
            sys.exit("texturedat2 needs --weights <4xTextureDAT2_otf.safetensors>\n"
                     "download: %s\nsha256: %s" % (TEXTUREDAT2_URL, TEXTUREDAT2_SHA256))
        h = hashlib.sha256()
        with open(weights, "rb") as f:
            for blk in iter(lambda: f.read(1 << 20), b""):
                h.update(blk)
        if h.hexdigest() != TEXTUREDAT2_SHA256:
            sys.exit("%s: sha256 %s does not match the pinned %s" %
                     (weights, h.hexdigest(), TEXTUREDAT2_SHA256))
        self.dev = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        desc = ModelLoader().load_from_file(weights)
        assert desc.scale == 4
        self.model = desc.model.eval().to(self.dev)
        self.tile_max = tile_max
        self.half = False
        self.fp16_mode = fp16
        self.fp16_note = ""

    # one model call on a uint8 HxWx3 array (already context-padded)
    def _forward(self, arr):
        torch = self.torch
        t = torch.from_numpy(arr).permute(2, 0, 1)[None].float().div(255).to(self.dev)
        if self.half:
            t = t.half()
        with torch.inference_mode():
            o = self.model(t)
        o = o.float().clamp(0, 1)[0].permute(1, 2, 0).cpu().numpy()
        return (o * 255 + 0.5).astype(np.uint8)

    def _run_padded(self, arr):
        """arr HxWx3 uint8. Reflect-pad context + multiple of 32, run, crop."""
        h, w = arr.shape[:2]
        ph = (-(h + 2 * CONTEXT)) % MULTIPLE
        pw = (-(w + 2 * CONTEXT)) % MULTIPLE
        p = np.pad(arr, ((CONTEXT, CONTEXT + ph), (CONTEXT, CONTEXT + pw), (0, 0)), mode="symmetric")
        out = self._forward(p)
        s = self.scale
        return out[CONTEXT * s:(CONTEXT + h) * s, CONTEXT * s:(CONTEXT + w) * s]

    def run(self, arr, tile=None):
        h, w = arr.shape[:2]
        tile = tile or self.tile_max
        if h * w <= tile * tile:
            return self._run_padded(arr)
        # overlapping tiles: tile core of `tile` px plus 16 px margins that are
        # thrown away; edge tiles are reflect-padded by _run_padded.
        s, m = self.scale, 16
        out = np.zeros((h * s, w * s, 3), np.uint8)
        for y0 in range(0, h, tile):
            for x0 in range(0, w, tile):
                y1, x1 = min(y0 + tile, h), min(x0 + tile, w)
                ya, yb = max(y0 - m, 0), min(y1 + m, h)
                xa, xb = max(x0 - m, 0), min(x1 + m, w)
                sub = self._run_padded(arr[ya:yb, xa:xb])
                out[y0 * s:y1 * s, x0 * s:x1 * s] = \
                    sub[(y0 - ya) * s:(y1 - ya) * s, (x0 - xa) * s:(x1 - xa) * s]
        return out

    def upscale_naive(self, im):
        """Straight (non-premultiplied) colour through the model, alpha bicubic: the
        old behaviour, kept only to make the before/after evidence."""
        rgba = im.convert("RGBA")
        rgb = self.run(np.asarray(rgba)[..., :3].copy())
        a = rgba.getchannel("A").resize((rgba.width * 4, rgba.height * 4), Image.BICUBIC)
        return Image.fromarray(np.dstack([rgb, np.asarray(a)]), "RGBA")

    alpha_mode = "bleed"

    def upscale(self, im):
        if self.alpha_mode == "bleed":
            col, a = bleed_colour(im)
            rgb = self.run(col)
        else:
            col, a = split_alpha(im)
            rgb = self.run(col)
        a_up = None
        if a is not None:
            a_up = self.run(grey3(a)).astype(np.float32).mean(axis=2)
        if self.alpha_mode == "bleed":
            return join_straight(rgb, a_up)
        return join_alpha(rgb, a_up)

    def choose_precision(self, samples):
        """fp16 only if it matches fp32 to < 1/255 mean on the samples."""
        torch = self.torch
        if self.fp16_mode == "off" or self.dev.type != "cuda":
            self.fp16_note = "fp32 (cpu)" if self.dev.type != "cuda" else "fp32 (forced)"
            return
        diffs = []
        try:
            for im in samples:
                pm, _ = split_alpha(im)
                self.half = False
                ref = self.run(pm).astype(np.float32)
                self.model.half()
                self.half = True
                got = self.run(pm).astype(np.float32)
                self.model.float()
                self.half = False
                diffs.append(float(np.abs(ref - got).mean()))
        except Exception as e:                       # fp16 not usable for this arch
            self.model.float()
            self.half = False
            self.fp16_note = "fp32 (fp16 failed: %s)" % type(e).__name__
            return
        worst = max(diffs) if diffs else 0.0
        if self.fp16_mode == "on" or worst < 1.0:
            self.model.half()
            self.half = True
            self.fp16_note = "fp16 (mean |diff| vs fp32 = %.3f/255)" % worst
        else:
            self.fp16_note = "fp32 (fp16 mean |diff| %.3f/255 >= 1)" % worst


# ---- TextureDAT2 through ONNX Runtime (prototype: no PyTorch) ----------------

class OnnxBackend(DatBackend):
    """Same padding/tiling/alpha code as DatBackend, model run by onnxruntime.

    The weights are the author's own export (4xTextureDAT2_otf_fp32_opset17.onnx,
    same licence); pin below. Providers are tried in order: CUDA, ROCm, DirectML,
    CPU. `pip install onnxruntime` (CPU), `onnxruntime-gpu`, `onnxruntime-directml`
    or `onnxruntime-rocm` pick the build for the machine."""
    name = "texturedat2-onnx"
    SHA256 = "1983ca762d159b33e029e36adfca3b90b7d934321f0d64ffb0a2ca692f682559"

    def __init__(self, weights, providers, tile_max):
        import onnxruntime as ort
        if hasattr(ort, "preload_dlls"):      # CUDA/cuDNN from the pip `nvidia-*` wheels
            try:
                ort.preload_dlls()
            except Exception:
                pass
        if not weights or not os.path.isfile(weights):
            sys.exit("texturedat2-onnx needs --weights <4xTextureDAT2_otf_fp32_opset17.onnx>\n"
                     "download: https://github.com/Phhofm/models/releases/download/4xTextureDAT2_otf/"
                     "4xTextureDAT2_otf_fp32_opset17.onnx\nsha256: %s" % self.SHA256)
        h = hashlib.sha256(open(weights, "rb").read()).hexdigest()
        if h != self.SHA256:
            sys.exit("%s: sha256 %s does not match the pinned %s" % (weights, h, self.SHA256))
        avail = ort.get_available_providers()
        want = providers.split(",") if providers else [
            p for p in ("CUDAExecutionProvider", "ROCMExecutionProvider",
                        "DmlExecutionProvider") if p in avail]
        want.append("CPUExecutionProvider")
        self.sess = ort.InferenceSession(weights, providers=want)
        self.provider = self.sess.get_providers()[0]
        self.tile_max = tile_max
        self.half = False
        self.fp16_note = "fp32 (onnx, %s)" % self.provider
        self.dev = type("D", (), {"type": "onnx:" + self.provider, "__str__": lambda s: s.type})()

    def _forward(self, arr):
        t = (arr.astype(np.float32).transpose(2, 0, 1)[None]) / 255.0
        o = self.sess.run(None, {self.sess.get_inputs()[0].name: t})[0]
        o = np.clip(o[0].transpose(1, 2, 0), 0, 1)
        return (o * 255 + 0.5).astype(np.uint8)

    def choose_precision(self, samples):
        pass


# ---- Real-ESRGAN (ncnn-vulkan, batch mode) ---------------------------------

def run_ncnn_batch(tool, model_args, indir, outdir, cancelled=lambda: False):
    """Batch over a directory whose images all have the SAME size, one worker thread
    per stage (-j 1:1:1). Mixing sizes in one batch corrupts some outputs (measured:
    56-73 of 102 textures differed from run to run); with same-size batches the default
    -j 1:2:2 still raced on 512x256 (18 of 23 differed between two runs, T6), and
    -j 1:1:1 is bit-identical."""
    groups = {}
    for n in sorted(os.listdir(indir)):
        with Image.open(os.path.join(indir, n)) as im:
            groups.setdefault(im.size, []).append(n)
    os.makedirs(outdir, exist_ok=True)
    rc = 0
    for size, names in groups.items():
        if cancelled():
            break
        sub_in = os.path.join(indir, "_%dx%d" % size)
        os.makedirs(sub_in)
        for n in names:
            os.replace(os.path.join(indir, n), os.path.join(sub_in, n))
        r = subprocess.run([tool, "-i", sub_in, "-o", outdir, "-s", "4", *model_args, "-j", "1:1:1", "-f", "png"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        rc = rc or r
        for n in names:
            os.replace(os.path.join(sub_in, n), os.path.join(indir, n))
        os.rmdir(sub_in)
    return rc


def ncnn_pack(args, names, todo):
    tool = args.tool
    if not os.access(tool, os.X_OK):
        sys.exit("%s not found; see work/tools/upscalers" % tool)
    stats = {"done": 0, "failed": []}
    work = tempfile.mkdtemp(prefix="texpack-", dir=os.path.abspath(args.tmp or os.path.dirname(os.path.abspath(args.out))))
    try:
        a_in, a_out = os.path.join(work, "rgb"), os.path.join(work, "rgb4")
        g_in, g_out = os.path.join(work, "alpha"), os.path.join(work, "alpha4")
        os.makedirs(a_in)
        os.makedirs(g_in)
        has_alpha = set()
        if args.alpha_mode == "naive":      # the tool's own alpha path (pre-fix behaviour)
            for n in todo:
                shutil.copy(os.path.join(args.dump, n), os.path.join(a_in, n))
            run_ncnn_batch(tool, ["-n", args.ncnn_model], a_in, a_out)
            for n in todo:
                try:
                    shutil.copy(os.path.join(a_out, n), os.path.join(args.out, n))
                    stats["done"] += 1
                except OSError:
                    stats["failed"].append(n)
            return stats
        prep = bleed_colour if args.alpha_mode == "bleed" else split_alpha
        for n in todo:
            pm, a = prep(Image.open(os.path.join(args.dump, n)))
            Image.fromarray(pm, "RGB").save(os.path.join(a_in, n))
            if a is not None:
                has_alpha.add(n)
                Image.fromarray(grey3(a), "RGB").save(os.path.join(g_in, n))
        run_ncnn_batch(tool, ["-n", args.ncnn_model], a_in, a_out)
        if has_alpha:
            run_ncnn_batch(tool, ["-n", args.ncnn_model], g_in, g_out)
        for i, n in enumerate(todo, 1):
            try:
                rgb = np.asarray(Image.open(os.path.join(a_out, n)).convert("RGB"))
                a_up = None
                if n in has_alpha:
                    a_up = np.asarray(Image.open(os.path.join(g_out, n)).convert("RGB")
                                      ).astype(np.float32).mean(axis=2)
                joiner = join_straight if args.alpha_mode == "bleed" else join_alpha
                save_png(joiner(rgb, a_up), os.path.join(args.out, n))
                stats["done"] += 1
            except (OSError, ValueError):
                stats["failed"].append(n)
                print("failed: %s" % n, file=sys.stderr)
            if i % 10 == 0 or i == len(todo):
                print("progress %d/%d" % (i, len(todo)), file=sys.stderr, flush=True)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return stats


# ---- profile "default": per-class method (T6) --------------------------------
#
#   track         TextureDAT2 100 %          (sharp edges for the notes and the HUD)
#   interface     TextureDAT2 50 % + Lanczos 50 %   (panels keep their lines, less "painted")
#   illustration  waifu2x cunet, noise 0     (covers, backgrounds: soft, no invented texture)
#
# All three use the same alpha treatment (bleed: straight colour of transparent texels
# filled in, alpha upscaled apart, joined without division).

def load_classes(path):
    out = {}
    if os.path.exists(path):
        for ln in open(path):
            p = ln.split("#")[0].split()
            if len(p) == 2:
                out[p[0]] = p[1]
    return out


def class_of(key, path, classes):
    """The listed class, or the classifier's feature heuristic for a key it does not know."""
    if key in classes:
        return classes[key]
    import classify_textures as ct
    return ct.classify(set(), *ct.features(path))


def lanczos_up(col, a):
    """(straight bled colour uint8 HxWx3, alpha uint8 HxW|None) -> (rgb uint8 4x, alpha float32 4x|None)."""
    h, w = col.shape[:2]
    rgb = np.asarray(Image.fromarray(col, "RGB").resize((w * 4, h * 4), Image.LANCZOS))
    a_up = None if a is None else np.asarray(
        Image.fromarray(a, "L").resize((w * 4, h * 4), Image.LANCZOS)).astype(np.float32)
    return rgb, a_up


def mix_straight(m1, m2, w):
    """w of method 1, 1-w of method 2; each is (rgb uint8, alpha float|None)."""
    rgb = m1[0].astype(np.float32) * w + m2[0].astype(np.float32) * (1 - w)
    a = None if m1[1] is None else m1[1] * w + m2[1] * (1 - w)
    return (rgb + 0.5).astype(np.uint8), a


def ncnn_items(tool, model_args, items, tmp, cancelled=lambda: False):
    """items {name: (bled colour, alpha|None)} -> {name: (rgb uint8 4x, alpha float32 4x|None)}
    through an ncnn-vulkan tool in same-size batches (colour, then the alphas as grey)."""
    c_in, c_out = os.path.join(tmp, "c_in"), os.path.join(tmp, "c_out")
    g_in, g_out = os.path.join(tmp, "g_in"), os.path.join(tmp, "g_out")
    os.makedirs(c_in)
    os.makedirs(g_in)
    has_alpha = set()
    for n, (col, a) in items.items():
        Image.fromarray(col, "RGB").save(os.path.join(c_in, n))
        if a is not None:
            has_alpha.add(n)
            Image.fromarray(grey3(a), "RGB").save(os.path.join(g_in, n))
    run_ncnn_batch(tool, model_args, c_in, c_out, cancelled)
    if has_alpha:
        run_ncnn_batch(tool, model_args, g_in, g_out, cancelled)
    res = {}
    for n in items:
        try:
            rgb = np.asarray(Image.open(os.path.join(c_out, n)).convert("RGB"))
            a_up = None
            if n in has_alpha:
                a_up = np.asarray(Image.open(os.path.join(g_out, n)).convert("RGB")
                                  ).astype(np.float32).mean(axis=2)
            res[n] = (rgb, a_up)
        except (OSError, ValueError):
            pass
    return res


def profile_pack(args, todo):
    classes = load_classes(args.classes)
    by_class = {"track": [], "interface": [], "illustration": []}
    for n in todo:
        by_class[class_of(n[:-4], os.path.join(args.dump, n), classes)].append(n)
    stats = {"done": 0, "failed": [], "classes": {k: len(v) for k, v in by_class.items()},
             "class_seconds": {}}
    total = len(todo)
    count = [0]

    cb = getattr(args, "progress", None)              # a host (the launcher) can pass these
    cancel = getattr(args, "cancelled", lambda: False)

    def tick():
        count[0] += 1
        if cb:
            cb(count[0], total)
        elif count[0] % 10 == 0 or count[0] == total:
            print("progress %d/%d" % (count[0], total), file=sys.stderr, flush=True)

    def put(n, rgb, a_up):
        save_png(join_straight(rgb, a_up), os.path.join(args.out, n))
        stats["done"] += 1
        tick()

    work = tempfile.mkdtemp(prefix="texpack-", dir=os.path.abspath(args.tmp or os.path.dirname(os.path.abspath(args.out))))
    try:
        # illustration: waifu2x cunet noise 0 (batched by size)
        t0 = time.time()
        if by_class["illustration"]:
            tool = args.w2x_tool
            if not os.access(tool, os.X_OK):
                sys.exit("%s not found (waifu2x-ncnn-vulkan)" % tool)
            items = {n: bleed_colour(Image.open(os.path.join(args.dump, n)))
                     for n in by_class["illustration"]}
            got = ncnn_items(tool, ["-n", "0", "-m", os.path.join(os.path.dirname(tool), args.w2x_models)],
                             items, os.path.join(work, "w2x"), cancel)
            for n in by_class["illustration"]:
                if cancel():
                    break
                if n in got:
                    put(n, *got[n])
                else:
                    stats["failed"].append(n)
                    print("failed: %s" % n, file=sys.stderr)
        stats["class_seconds"]["illustration"] = round(time.time() - t0, 1)

        # track + interface: TextureDAT2 (torch / onnx) or, for machines without a fast
        # DAT2 runtime, Real-ESRGAN through ncnn (--fast-ncnn); interface mixes with Lanczos
        t0 = time.time()
        ti = by_class["track"] + by_class["interface"]
        if args.fast_ncnn and ti:
            bled = {n: bleed_colour(Image.open(os.path.join(args.dump, n))) for n in ti}
            got = ncnn_items(args.tool, ["-n", args.ncnn_model], bled, os.path.join(work, "esr"), cancel)
            for n in ti:
                if cancel():
                    break
                if n not in got:
                    stats["failed"].append(n)
                    continue
                out = got[n]
                if n in by_class["interface"]:
                    out = mix_straight(out, lanczos_up(*bled[n]), MIX_INTERFACE)
                put(n, *out)
            stats["backend"] = "realesrgan-ncnn-vulkan (track, interface)"
        elif ti:
            if args.model == "texturedat2-onnx":
                be = OnnxBackend(args.weights, args.providers, args.tile_max)
            else:
                be = DatBackend(args.weights, args.fp16, args.tile_max, args.device)
            be.alpha_mode = "bleed"
            order = sorted(ti, key=lambda n: os.path.getsize(os.path.join(args.dump, n)))
            be.choose_precision([Image.open(os.path.join(args.dump, n))
                                 for n in (order[-1], order[len(order) // 2])])
            print("%s on %s, %s" % (be.name, be.dev, be.fp16_note), file=sys.stderr, flush=True)
            stats["backend"] = "%s %s %s (track, interface)" % (be.name, be.dev, be.fp16_note)
            for n in ti:
                try:
                    col, a = bleed_colour(Image.open(os.path.join(args.dump, n)))
                    dat = (be.run(col), None if a is None else be.run(grey3(a)).astype(np.float32).mean(axis=2))
                    if n in by_class["interface"]:
                        dat = mix_straight(dat, lanczos_up(col, a), MIX_INTERFACE)
                    put(n, *dat)
                except Exception as e:
                    stats["failed"].append(n)
                    print("failed: %s (%s: %s)" % (n, type(e).__name__, e), file=sys.stderr)
        stats["class_seconds"]["track+interface"] = round(time.time() - t0, 1)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return stats


# ---- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dump", help="directory written by EMOTIONALSENSE_TEX_DUMP")
    ap.add_argument("out", nargs="?", default=os.path.join(ROOT, "work", "data", "textures"))
    ap.add_argument("--model", default="texturedat2", choices=("texturedat2", "texturedat2-onnx", "realesrgan"))
    ap.add_argument("--weights", help="4xTextureDAT2_otf.safetensors (texturedat2)")
    ap.add_argument("--providers", help="onnxruntime providers, comma separated (default: best available)")
    ap.add_argument("--device", help="torch device (default: cuda if available, else cpu)")
    ap.add_argument("--fp16", default="auto", choices=("auto", "on", "off"))
    ap.add_argument("--tile-max", type=int, default=512, help="tile the model above this side (px)")
    ap.add_argument("--tool", default=NCNN_TOOL, help="realesrgan-ncnn-vulkan binary")
    ap.add_argument("--ncnn-model", default=NCNN_MODEL)
    ap.add_argument("--tmp", help="scratch directory (default: next to <out>)")
    ap.add_argument("--force", action="store_true", help="redo files that exist")
    ap.add_argument("--alpha-mode", default="bleed", choices=("bleed", "premul", "naive"),
                    help="bleed (default): colour of transparent texels filled from the visible ones, "
                         "alpha through the model; premul: round-25 recipe; naive: pre-fix, evidence only")
    ap.add_argument("--profile", default="default", choices=("default", "dat2"),
                    help="default: per class (track: TextureDAT2, interface: TextureDAT2 50%% + Lanczos, "
                         "illustration: waifu2x cunet); dat2: the whole pack with --model")
    ap.add_argument("--classes", default=CLASSES_FILE, help="`<key> <class>` list (default profile)")
    ap.add_argument("--w2x-tool", default=W2X_TOOL, help="waifu2x-ncnn-vulkan binary (default profile)")
    ap.add_argument("--w2x-models", default=W2X_MODELS, help="model directory next to the waifu2x tool")
    ap.add_argument("--fast-ncnn", action="store_true",
                    help="default profile: track and interface through Real-ESRGAN ncnn (no DAT2 runtime needed)")
    ap.add_argument("--stats", help="write a JSON report here")
    args = ap.parse_args()

    names = sorted(n for n in os.listdir(args.dump) if n.endswith(".png"))
    if not names:
        sys.exit("no PNGs in %s" % args.dump)
    os.makedirs(args.out, exist_ok=True)
    todo = [n for n in names if args.force or not os.path.exists(os.path.join(args.out, n))]
    skipped = len(names) - len(todo)
    started = time.time()
    report = {"model": args.model, "textures": len(names), "skipped_existing": skipped}

    if args.profile == "default":
        if args.model == "realesrgan":
            sys.exit("--model realesrgan is a whole-pack model: use --profile dat2, or --fast-ncnn")
        st = profile_pack(args, todo) if todo else {"done": 0, "failed": []}
        done, failed = st["done"], st["failed"]
        report.update({k: v for k, v in st.items() if k not in ("done", "failed")})
        report.setdefault("backend", "profile default")
    elif args.model == "realesrgan":
        st = ncnn_pack(args, names, todo) if todo else {"done": 0, "failed": []}
        done, failed = st["done"], st["failed"]
        report["backend"] = "realesrgan-ncnn-vulkan batch"
    else:
        done, failed = 0, []
        if todo:
            if args.model == "texturedat2-onnx":
                be = OnnxBackend(args.weights, args.providers, args.tile_max)
            else:
                be = DatBackend(args.weights, args.fp16, args.tile_max, args.device)
            probe = []
            by_size = sorted(todo, key=lambda n: os.path.getsize(os.path.join(args.dump, n)))
            for n in (by_size[-1], by_size[len(by_size) // 2]):
                probe.append(Image.open(os.path.join(args.dump, n)))
            alpha_n = next((n for n in todo
                            if split_alpha(Image.open(os.path.join(args.dump, n)))[1] is not None), None)
            if alpha_n:
                probe.append(Image.open(os.path.join(args.dump, alpha_n)))
            be.alpha_mode = args.alpha_mode
            be.choose_precision(probe)
            print("%s on %s, %s" % (be.name, be.dev, be.fp16_note), file=sys.stderr, flush=True)
            report["backend"] = "%s %s %s" % (be.name, be.dev, be.fp16_note)
            for i, n in enumerate(todo, 1):
                try:
                    im = Image.open(os.path.join(args.dump, n))
                    save_png(be.upscale_naive(im) if args.alpha_mode == "naive" else be.upscale(im),
                             os.path.join(args.out, n))
                    done += 1
                except Exception as e:                     # keep going, report at the end
                    failed.append(n)
                    print("failed: %s (%s: %s)" % (n, type(e).__name__, e), file=sys.stderr)
                if i % 10 == 0 or i == len(todo):
                    print("progress %d/%d" % (i, len(todo)), file=sys.stderr, flush=True)
            if be.name == "texturedat2" and be.dev.type == "cuda":
                report["peak_vram_mib"] = be.torch.cuda.max_memory_allocated() / 2**20

    total = sum(os.path.getsize(os.path.join(args.out, n))
                for n in os.listdir(args.out) if n.endswith(".png"))
    report.update(upscaled=done, failed=failed, seconds=round(time.time() - started, 1),
                  pack_mib=round(total / 2**20, 1))
    print("%d upscaled, %d already there, %d failed in %.0fs" %
          (done, skipped, len(failed), time.time() - started))
    print("%s: %.1f MiB" % (args.out, total / 2**20))
    if args.stats:
        json.dump(report, open(args.stats, "w"), indent=1)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
