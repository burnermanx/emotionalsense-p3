"""The launcher window: four screens drawn on one Tk Canvas.

The look is es_theme (original art, see L2.6). Every screen is composed as a
Pillow image and shown on the Canvas; Tk supplies the window, the keyboard, the
timer and the file dialogs. Because a frame is only an image, screenshots and
tests need no display (`python3 es_gui.py --shots DIR`).

Screens: SETUP (choose the ISO), LOADING (prepare), PLAY, OPTIONS.
Keyboard: arrows move / change, Enter = OK, Esc = back. Gamepad (es_pad):
d-pad / stick move, and the south and east buttons are OK and Back according to
the "confirm button" option (circle or cross, auto by language). Titles of screens keep
their English stylised names in every language, like the game's own menus;
everything else is translated (es_i18n).
"""

from __future__ import annotations

import base64
import io
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import es_i18n  # noqa: E402
import es_theme as th  # noqa: E402
from es_i18n import t  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

W, H = th.W, th.H
PAD_POLL_MS = 8          # pad polled ~120 Hz: a press is seen well inside one 60 Hz frame
SCALES = ["auto", "1", "2", "3", "4"]
RENDERERS = ["auto", "vulkan", "software"]
KEY_TRIMS = ["default", "0", "256", "512", "1024"]


def short_path(p: Path, width_px: int, ui=None, size: int = 19) -> str:
    """A path that fits `width_px`: the home folder becomes ~ (Linux/macOS), and
    when it is still too long the middle is replaced by an ellipsis, keeping
    the beginning and the last two folders."""
    s = str(p)
    home = str(Path.home())
    if not sys.platform.startswith("win") and s.startswith(home):
        s = "~" + s[len(home):]
    f = th.font_for(es_i18n.language(), size) if ui is not None else None
    fits = (lambda x: f.getlength(x) <= width_px) if f else (lambda x: len(x) * size * 0.55 <= width_px)  # noqa: E731
    if fits(s):
        return s
    parts = s.replace("\\", "/").split("/")
    sep = "\\" if "\\" in s else "/"
    tail = sep.join(parts[-2:])
    head = parts[0] or sep
    for n in range(len(parts) - 3, 0, -1):
        cand = sep.join(parts[:n]) + sep + "..." + sep + tail
        if fits(cand):
            return cand
    return head + sep + "..." + sep + tail


def open_folder(p: Path) -> None:
    """Show a folder in the system file manager."""
    p.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(str(p))                                   # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        import es_env
        subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env=es_env.child_env())


class Ui:
    """State and drawing, no Tk."""

    def __init__(self, data: Path, kit: Path, jobs: int = 4):
        import es_main
        import es_prepare
        self.es_main, self.es_prepare = es_main, es_prepare
        self.data, self.kit, self.jobs = data, kit, jobs
        import es_chd
        es_chd.configure(kit)
        self.cfg = es_main.load_config(data)
        es_i18n.set_language(self.cfg["language"])
        self.screen = "setup"
        self.sel = {"setup": 0, "play": 0, "options": 0, "loading": 0, "texpack": 0}
        self.iso: Optional[Path] = None
        self.iso_status = ("", "")          # (kind ok|err|"", text)
        self.message = ""
        self.progress = 0.0
        self.step = ""
        self.running: Optional[subprocess.Popen] = None
        self.asset_cache = None
        self.text_cache: dict = {}
        self.prev_img: Optional[Image.Image] = None
        self.trans_t0 = 0.0
        self.dirty = True
        self.cancel_flag = False
        self.tp_state = "confirm"           # texpack screen: confirm, working, done
        self.tp_text = ""

    # -- assets ---------------------------------------------------------------

    def assets(self) -> dict:
        if self.asset_cache is None:
            g = th.assets_dir() / "generated"
            try:
                self.asset_cache = {k: Image.open(g / f"{k}.png").convert("RGBA")
                                    for k in ("bg", "header", "band")}
            except OSError:
                self.asset_cache = th.render_assets()
        return self.asset_cache

    def text(self, s: str, size: int, color=th.WHITE, kind: str = "ui", lang: Optional[str] = None,
             **kw) -> Image.Image:
        lang = lang or es_i18n.language()
        key = (s, size, tuple(color), kind, lang, tuple(sorted(kw.items())))
        im = self.text_cache.get(key)
        if im is None:
            if "slant" not in kw and kind == "ui" and lang in ("ja", "ko"):
                kw["slant"] = 0.16               # the CJK faces are upright: slant them like the Latin italic
            im = th.text_image(s, th.font_for(lang, size, kind), color, **kw)
            self.text_cache[key] = im
        return im

    def wrap(self, s: str, size: int, width: int) -> List[str]:
        f = th.font_for(es_i18n.language(), size)
        lines, cur = [], ""
        cjk = es_i18n.language() in ("ja", "ko")
        tokens = list(s) if (cjk and " " not in s) else s.split(" ")
        for tok in tokens:
            trial = cur + ("" if cjk and " " not in s else (" " if cur else "")) + tok
            if f.getlength(trial) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = tok
        if cur:
            lines.append(cur)
        return lines

    # -- drawing --------------------------------------------------------------

    def _title(self, img, header: str, chrome: str, accent: str, accent_col):
        a = self.assets()
        img.alpha_composite(a["header"], (0, 26))
        img.alpha_composite(self.text(header, 24, th.WHITE, kind="title", slant=0.0), (22, 31))
        c = self.text(chrome, 66, kind="title", chrome=True)
        img.alpha_composite(c, (50, 78))
        img.alpha_composite(self.text(accent, 66, accent_col, kind="title", stroke=(0, 0, 0)),
                            (50 + c.width - 14, 78))

    def _panel(self, img, x, y, w, h, lines, size=19, color=th.WHITE, accent=th.CYAN):
        img.alpha_composite(th.render_panel(w, h, accent=accent), (x, y))
        yy = y + 12
        for ln, col in lines:
            img.alpha_composite(self.text(ln, size, col), (x + 20, yy))
            yy += size + 9

    def confirm_mode(self) -> str:
        """circle or cross, from the option (auto follows the language)."""
        return self.es_main.resolve_confirm(self.cfg.get("confirm", "auto"), es_i18n.language())

    def _footer(self, img, back=True, ok=True, move=True):
        d = ImageDraw.Draw(img)
        x = 56
        y = H - 40
        label = lambda s: self.text(s, 17, th.WHITE)  # noqa: E731
        cross_ok = self.confirm_mode() == "cross"      # ✕ confirms, ○ goes back

        def circle(x):
            d.ellipse([x, y, x + 20, y + 20], outline=th.WHITE, width=3)

        def cross(x):
            d.line([x + 2, y + 2, x + 18, y + 18], fill=th.WHITE, width=3)
            d.line([x + 18, y + 2, x + 2, y + 18], fill=th.WHITE, width=3)
        if ok:
            (cross if cross_ok else circle)(x)
            im = label(t("nav.ok") + "  Enter")
            img.alpha_composite(im, (x + 28, y - 3))
            x += 28 + im.width + 26
        if back:
            (circle if cross_ok else cross)(x)
            im = label(t("nav.back") + "  Esc")
            img.alpha_composite(im, (x + 28, y - 3))
            x += 28 + im.width + 26
        if move:
            for dy, pts in ((1, [(x + 10, y), (x + 1, y + 9), (x + 19, y + 9)]),
                            (0, [(x + 10, y + 20), (x + 1, y + 11), (x + 19, y + 11)])):
                d.polygon(pts, fill=th.WHITE)
            img.alpha_composite(label(t("nav.move")), (x + 28, y - 3))

    def _items(self, img, items, sel, y0=268, step=58, x=56, width=620):
        a = self.assets()
        for i, (label, enabled) in enumerate(items):
            y = y0 + i * step
            if i == sel:
                img.alpha_composite(a["band"], (x - 24, y - 16))
                col = th.WHITE if enabled else (170, 180, 175)
            else:
                col = th.DIM if enabled else (62, 68, 72)
            img.alpha_composite(self.text(label, 30, col), (x + 8, y - 2))

    def compose(self) -> Image.Image:
        img = self.assets()["bg"].copy()
        getattr(self, "draw_" + self.screen)(img)
        self._version_mark(img)
        return img

    def _version_mark(self, img):
        """Small and dim in the bottom-right corner of every screen."""
        im = self.text(t("main.version", version=self._version()), 14, th.DIM)
        img.alpha_composite(im, (W - 24 - im.width, H - 34))

    def draw_setup(self, img):
        self._title(img, "SETUP", "GAME ", "DISC", th.LIME)
        kind, status = self.iso_status
        lines = [(ln, th.WHITE) for ln in self.wrap(t("welcome.body"), 19, 800)]
        if self.es_prepare_ready():
            lines.append((t("welcome.have_game"), th.LIME))
        stat = status or t("welcome.no_iso")
        for ln in self.wrap(stat, 19, 800)[:2]:
            lines.append((ln, th.LIME if kind == "ok" else (255, 110, 110) if kind == "err" else th.DIM))
        self._panel(img, 56, 168, 848, 24 + len(lines) * 28, lines)
        items = [(t("welcome.browse"), True), (t("welcome.update"), False), (t("welcome.continue"), kind == "ok")]
        self._items(img, items, self.sel["setup"], y0=300 + max(0, len(lines) - 4) * 28)
        if self.sel["setup"] == 1:
            img.alpha_composite(self.text(t("welcome.update_soon"), 17, th.DIM), (56, H - 78))
        self._footer(img, back=False)

    def es_prepare_ready(self) -> bool:
        try:
            return self.es_prepare.Preparer(self.es_prepare.PrepareOptions(
                iso=Path("."), data_dir=self.data, kit_dir=self.kit)).is_prepared()
        except Exception:               # noqa: BLE001
            return False

    def draw_loading(self, img):
        self._title(img, "LOADING", "BUILD ", "%d%%" % int(self.progress * 100), th.CYAN)
        d = ImageDraw.Draw(img)
        x0, x1, y = 56, 904, 330
        d.rectangle([x0, y, x1, y + 6], fill=(30, 36, 40, 255))
        fx = x0 + int((x1 - x0) * self.progress)
        if fx > x0:
            bar = th.hgradient(fx - x0, 6, [(0, th.GREEN + (255,)), (1, th.CYAN + (255,))])
            img.alpha_composite(bar, (x0, y))
            glow = Image.new("RGBA", (fx - x0 + 20, 30), (0, 0, 0, 0))
            ImageDraw.Draw(glow).rectangle([10, 12, fx - x0 + 10, 18], fill=th.CYAN + (90,))
            from PIL import ImageFilter
            img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6)), (x0 - 10, y - 12))
        step = t("prepare.step." + self.step) if self.step else ""
        img.alpha_composite(self.text(step, 32, th.WHITE), (56, 270))
        lines = [(ln, th.WHITE) for ln in self.wrap(t("prepare.first_run"), 19, 800)]
        if self.message:
            lines += [(ln, (255, 110, 110)) for ln in self.wrap(self.message, 19, 800)]
        self._panel(img, 56, 372, 848, 24 + len(lines) * 28, lines)
        self._footer(img, ok=False, move=False)

    def draw_play(self, img):
        self._title(img, "PLAY", "EMOTIONALSENSE-", "P3", th.CYAN)
        save = self.es_prepare.save_file(self.data)
        lines = [(t("main.save_path", path=short_path(save.parent, 760, self)), th.WHITE)]
        if self.message:
            lines.append((self.message, th.LIME))
        self._panel(img, 56, 176, 848, 24 + len(lines) * 28, lines)
        items = [(t("main.play"), True), (t("main.options"), True), (t("main.save"), True),
                 (t("main.open_save"), True), (t("main.quit"), True)]
        self._items(img, items, self.sel["play"], y0=268 + max(0, len(lines) - 1) * 28, step=42)
        self._footer(img, back=False)

    def _version(self) -> str:
        try:
            return json.loads((self.kit / "manifest.json").read_text())["version_full"]
        except (OSError, ValueError, KeyError):
            import es_version                       # first run, no kit yet: the launcher's own
            return es_version.VERSION

    def options_rows(self):
        c = self.cfg
        on = lambda v: t("options.on") if v else t("options.off")  # noqa: E731
        lang = c["language"]
        rows = [
            ("language", t("options.language"), t("options.auto") if lang == "auto" else es_i18n.NAMES[lang]),
            ("scale", t("options.scale"), t("options.auto") if c["scale"] == "auto" else f"{c['scale']}x"),
            ("unlock", t("options.unlock"), on(c["unlock"])),
            ("unlock_items", t("options.items"), on(c["unlock_items"])),
            ("key_trim", t("options.key_trim"), t("options.auto") if c["key_trim"] == "default" else str(c["key_trim"])),
            ("confirm", t("options.confirm"), t("options.confirm." + c.get("confirm", "auto"))),
            ("start_mode", t("options.startmode"), t("options.startmode." + str(c.get("start_mode", "last")))),
            ("refresh", t("options.refresh"), t("options.refresh." + str(c.get("refresh", "native")))),
            ("fever", t("options.fever"), t("options.fever." + str(c.get("fever", "respect")))),
            ("score", t("options.score"), t("options.score." + str(c.get("score", "respect")))),
            ("bga_smooth", t("options.bga"), t("options.bga." + str(c.get("bga_smooth", "0")))),
            ("update", t("options.update"), self.update_label()),
            ("renderer", t("options.renderer"), t("options.auto") if c["renderer"] == "auto" else c["renderer"]),
            ("restore", t("options.restore"), ""),
        ]
        if self._tp().ENABLED:                        # experimental, hidden unless ES_TEXPACK_PROTO=1
            rows.insert(5, ("texpack", t("options.texpack"), self.texpack_label()))
        return rows

    def texpack_label(self) -> str:
        import es_texpack
        if not es_texpack.platform_key():
            return t("options.texpack.na")
        if not es_texpack.pack_built(self.data):
            return t("options.texpack.build")
        return t("options.texpack.on" if self.cfg.get("texpack") == "on" else "options.texpack.off")

    def update_label(self) -> str:
        n = self.es_prepare.update_status(self.data)
        if not n:
            return t("options.update.none")
        return t("options.update.on" if self.cfg.get("update", True) else "options.update.off", count=n)

    def draw_options(self, img):
        self._title(img, "OPTIONS", "GAME ", "OPTIONS", th.MAGENTA)
        a = self.assets()
        rows = self.options_rows()
        c_lang = self.cfg["language"] if self.cfg["language"] in es_i18n.LANGS else None
        y0 = 160
        step = min(32, 290 // len(rows))
        fs = 24 if step >= 30 else max(13, step - 4)         # many rows: smaller type, no overlap
        for i, (key, label, val) in enumerate(rows):
            y = y0 + i * step
            if i == self.sel["options"]:
                band = a["band"].resize((700, a["band"].height - 6))
                img.alpha_composite(band, (32, y - 14))
            col = th.WHITE if i == self.sel["options"] else th.DIM
            img.alpha_composite(self.text(label, fs, col), (56, y - 2))
            if val:
                v = self.text(val, fs, th.LIME if i == self.sel["options"] else th.DIM,
                              lang=c_lang if key == "language" else None)
                img.alpha_composite(v, (700 - v.width - 28, y - 2))
                if i == self.sel["options"]:
                    d = ImageDraw.Draw(img)
                    d.polygon([(700 - v.width - 52, y + 12), (700 - v.width - 40, y + 4), (700 - v.width - 40, y + 20)], fill=th.WHITE)
                    d.polygon([(710, y + 4), (710, y + 20), (722, y + 12)], fill=th.WHITE)
        key = rows[self.sel["options"]][0]
        hint = (t("options.hint_unlock") if key in ("unlock", "unlock_items")
                else t("options.hint_confirm") if key == "confirm"
                else t("options.hint_refresh") if key == "refresh"
                else t("options.hint_fever") if key == "fever"
                else t("options.hint_score") if key == "score"
                else t("options.hint_startmode") if key == "start_mode"
                else t("options.hint_bga") if key == "bga_smooth"
                else t("options.hint_texpack", size=self._tp().download_size_mb(), minutes=self._tp().ESTIMATE_MINUTES)
                if key == "texpack"
                else t("options.hint_update") if key == "update" else t("options.hint_save"))
        if self.message and key == "update":
            hint = self.message
        for j, ln in enumerate(self.wrap(hint, 17, 860)[:2]):
            img.alpha_composite(self.text(ln, 17, th.DIM), (56, 452 + j * 22))
        self._footer(img)

    def draw_texpack(self, img):
        self._title(img, "TEXTURES", "HI-RES ", "AI", th.MAGENTA)
        tp = self._tp()
        if self.tp_state == "confirm":
            lines = [(ln, th.WHITE) for ln in self.wrap(
                t("texpack.body", size=tp.download_size_mb(), minutes=tp.ESTIMATE_MINUTES), 19, 800)]
            lines += [(ln, th.DIM) for ln in self.wrap(t("texpack.credits"), 16, 800)]
            self._panel(img, 56, 168, 848, 24 + len(lines) * 28, lines, size=19)
            self._items(img, [(t("texpack.start"), True)], 0, y0=400)
            self._footer(img, move=False)
        else:
            step = t("texpack.step." + self.step) if self.tp_state == "working" and self.step else ""
            x0, x1, y = 56, 904, 330
            d = ImageDraw.Draw(img)
            d.rectangle([x0, y, x1, y + 6], fill=(30, 36, 40, 255))
            fx = x0 + int((x1 - x0) * self.progress)
            if fx > x0:
                img.alpha_composite(th.hgradient(fx - x0, 6, [(0, th.GREEN + (255,)), (1, th.CYAN + (255,))]), (x0, y))
            img.alpha_composite(self.text(step or "", 32, th.WHITE), (56, 270))
            msg = self.tp_text
            lines = [(ln, th.LIME if self.tp_state == "done" else (255, 110, 110))
                     for ln in self.wrap(msg, 19, 800)]
            if lines:
                self._panel(img, 56, 372, 848, 24 + len(lines) * 28, lines)
            self._footer(img, ok=self.tp_state in ("done", "failed"), back=True, move=False)

    def _tp(self):
        import es_texpack
        return es_texpack

    # -- transitions ----------------------------------------------------------

    def goto(self, screen: str):
        self.prev_img = self.compose()
        self.screen = screen
        self.trans_t0 = time.monotonic()
        self.message = "" if screen not in ("loading",) else self.message
        self.dirty = True

    def frame(self) -> Image.Image:
        img = self.compose()
        if self.prev_img is not None:
            tt = (time.monotonic() - self.trans_t0) / 0.22
            if tt >= 1:
                self.prev_img = None
            else:
                e = 1 - (1 - tt) ** 3
                out = self.prev_img.copy()
                moved = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                moved.alpha_composite(img, (int((1 - e) * 70), 0))
                moved.putalpha(moved.getchannel("A").point(lambda v: int(v * e)))
                out.alpha_composite(moved)
                self.dirty = True
                return out
        return img

    # -- input ----------------------------------------------------------------

    def key(self, k: str, host) -> None:
        """k in up/down/left/right/ok/back; host provides ask_iso/ask_dir/launch/quit."""
        s = self.screen
        self.dirty = True
        if s == "setup":
            n = 3
            if k == "up":
                self.sel[s] = (self.sel[s] - 1) % n
            elif k == "down":
                self.sel[s] = (self.sel[s] + 1) % n
            elif k == "ok":
                if self.sel[s] == 0:
                    p = host.ask_iso()
                    if p:
                        self.set_iso(Path(p))
                elif self.sel[s] == 2 and self.iso_status[0] == "ok":
                    host.start_prepare(self.iso)
                elif self.sel[s] == 2 and self.es_prepare_ready():
                    self.goto("play")
        elif s == "loading":
            if k == "back":
                self.cancel_flag = True
        elif s == "texpack":
            if self.tp_state == "confirm":
                if k == "back":
                    self.goto("options")
                elif k == "ok":
                    self.tp_state, self.tp_text = "working", ""
                    self.progress, self.step, self.cancel_flag = 0.0, "tools", False
                    host.start_texpack()
            elif self.tp_state == "working":
                if k == "back":
                    self.cancel_flag = True
            elif k in ("ok", "back"):
                self.goto("options")
        elif s == "play":
            n = 5
            if k == "up":
                self.sel[s] = (self.sel[s] - 1) % n
            elif k == "down":
                self.sel[s] = (self.sel[s] + 1) % n
            elif k == "ok":
                i = self.sel[s]
                if i == 0:
                    host.launch()
                elif i == 1:
                    self.goto("options")
                elif i == 2:
                    try:
                        self.message = t("main.backup_done", path=self.es_main.backup_save(self.data))
                    except OSError as e:
                        self.message = t("main.backup_failed", error=str(e)) if self.es_prepare.save_file(self.data).exists() else t("main.backup_none")
                elif i == 3:
                    try:
                        open_folder(self.es_prepare.save_file(self.data).parent)
                    except OSError as e:
                        self.message = t("main.open_failed", error=str(e))
                else:
                    host.quit()
        elif s == "options":
            rows = self.options_rows()
            if k == "back":
                self.goto("play")
            elif k == "up":
                self.sel[s] = (self.sel[s] - 1) % len(rows)
            elif k == "down":
                self.sel[s] = (self.sel[s] + 1) % len(rows)
            elif k in ("left", "right", "ok"):
                self.change(rows[self.sel[s]][0], -1 if k == "left" else 1, host)

    def change(self, key: str, d: int, host):
        c = self.cfg
        cyc = lambda lst, cur: lst[(lst.index(cur) + d) % len(lst)] if cur in lst else lst[0]  # noqa: E731
        if key == "language":
            c["language"] = cyc(["auto"] + es_i18n.LANGS, c["language"])
            es_i18n.set_language(c["language"])
            self.text_cache.clear()
            host.set_confirm(self.confirm_mode())
        elif key == "scale":
            c["scale"] = cyc(SCALES, str(c["scale"]))
        elif key in ("unlock", "unlock_items"):
            c[key] = not c[key]
        elif key == "key_trim":
            c["key_trim"] = cyc(KEY_TRIMS, str(c["key_trim"]))
        elif key == "texpack":
            import es_texpack
            self.message = ""
            if not es_texpack.platform_key():
                self.message = t("texpack.unsupported", arch=es_texpack.arch_name())
            elif not es_texpack.pack_built(self.data):
                if not self.es_prepare_ready():
                    self.message = t("texpack.not_ready")
                elif not es_texpack.vulkan_present():
                    self.message = t("texpack.no_vulkan")
                else:
                    self.tp_state, self.tp_text = "confirm", ""
                    self.goto("texpack")
                    return
            else:
                c["texpack"] = "off" if c.get("texpack") == "on" else "on"
        elif key == "confirm":
            c["confirm"] = cyc(list(self.es_main.CONFIRM_VALUES), c.get("confirm", "auto"))
            host.set_confirm(self.confirm_mode())
        elif key == "refresh":
            c["refresh"] = cyc(list(self.es_main.REFRESH_VALUES), str(c.get("refresh", "native")))
        elif key == "fever":
            c["fever"] = cyc(list(self.es_main.FEVER_VALUES), str(c.get("fever", "respect")))
        elif key == "score":
            c["score"] = cyc(list(self.es_main.SCORE_VALUES), str(c.get("score", "respect")))
        elif key == "start_mode":
            c["start_mode"] = cyc(list(self.es_main.START_MODE_VALUES), str(c.get("start_mode", "last")))
        elif key == "bga_smooth":
            c["bga_smooth"] = cyc(list(self.es_main.BGA_VALUES), str(c.get("bga_smooth", "0")))
        elif key == "update":
            from es_pipeline import PipelineError
            self.message = ""
            if d < 0:
                self.es_prepare.remove_update(self.data)
            else:
                p = host.ask_dir()
                if p:
                    try:
                        self.message = t("update.installed", count=self.es_prepare.install_update(self.data, Path(p)))
                    except PipelineError as e:
                        self.message = str(e)
        elif key == "renderer":
            c["renderer"] = cyc(RENDERERS, c["renderer"])
        elif key == "restore":
            lang = c["language"]
            c.update(self.es_main.CONFIG_DEFAULTS)
            c["language"] = lang
            host.set_confirm(self.confirm_mode())
        self.es_main.save_config(self.data, c)

    def set_iso(self, p: Path):
        from es_chd import ChdError
        from es_pipeline import GAME_ID, Iso9660, PipelineError, parse_sfo
        self.iso = p
        try:
            iso = Iso9660(p)
            try:
                entries = {e.path: e for e in iso.walk()}
                sfo_e = entries.get("PSP_GAME/PARAM.SFO")
                if not sfo_e or "PSP_GAME/SYSDIR/EBOOT.BIN" not in entries:
                    self.iso_status = ("err", t("welcome.not_psp"))
                    return
                sfo = parse_sfo(iso.read_file(sfo_e))
            finally:
                iso.close()
        except (OSError, PipelineError, struct_error) as e:
            msg = str(e)
            if isinstance(e, ChdError):                  # already a full, translated sentence
                self.iso_status = ("err", msg)
            else:
                self.iso_status = ("err", t("welcome.not_psp") if "ISO9660" in msg else t("welcome.unreadable", error=msg))
            return
        did, title = sfo.get("DISC_ID", "?"), sfo.get("TITLE", "?")
        if did != GAME_ID:
            self.iso_status = ("err", t("welcome.wrong_region", title=title, id=did))
        else:
            self.iso_status = ("ok", t("welcome.ok", title=title, id=did) + "  -  " + p.name)
            self.sel["setup"] = 2


import struct  # noqa: E402
struct_error = struct.error


def to_photo(tk, img: Image.Image):
    """An image with alpha (the window icon) as a Tk photo: PNG, base64."""
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1)
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


def frame_photo(tk, img: Image.Image, photo=None):
    """An opaque frame as a Tk photo through raw PPM: no PNG encode, no base64 (about 8x
    faster, ~3 ms for 960x540). With `photo`, that image is updated in place."""
    rgb = img.convert("RGB")
    data = b"P6 %d %d 255\n" % rgb.size + rgb.tobytes()
    if photo is None:
        return tk.PhotoImage(data=data, format="PPM")
    photo.configure(data=data, format="PPM")
    return photo


class App:
    def __init__(self, ui: Ui):
        import tkinter as tk
        from tkinter import filedialog
        self.tk, self.fd = tk, filedialog
        self.ui = ui
        self.root = tk.Tk()
        self.root.title(t("app.title"))
        self.root.resizable(False, False)
        try:                                   # the application icon (es_icon: drawn by code)
            import es_icon
            self.icon_photo = to_photo(tk, es_icon.render(128))
            self.root.iconphoto(True, self.icon_photo)
        except Exception:                      # noqa: BLE001  a missing font must not stop the launcher
            pass
        self.canvas = tk.Canvas(self.root, width=W, height=H, highlightthickness=0, bg="black")
        self.canvas.pack()
        self.item = self.canvas.create_image(0, 0, anchor="nw")
        self.photo = None
        self.q: queue.Queue = queue.Queue()
        self.thread: Optional[threading.Thread] = None
        self.modal = 0                         # >0 while a dialog is open: the pad must not drive the screen behind
        self._anim = False                     # a frame is already scheduled for the running transition
        names = {"Up": "up", "Down": "down", "Left": "left", "Right": "right", "Return": "ok",
                 "KP_Enter": "ok", "space": "ok", "Escape": "back", "BackSpace": "back"}
        for ks, nm in names.items():
            self.root.bind(f"<{ks}>", lambda e, nm=nm: self.press(nm))
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        import es_pad
        self.pad = es_pad.Pad(ui.kit)
        self.set_confirm(ui.confirm_mode())
        if ui.es_prepare_ready():
            ui.screen = "play"
        self.tick()
        if self.pad.available:
            self.pad_tick()

    def press(self, k: str):
        """A key (keyboard or pad): act now and redraw at once (idle callbacks coalesce a burst
        into one frame), not at the next timer tick."""
        self.ui.key(k, self)
        self.root.after_idle(self.redraw)

    def redraw(self):
        ui = self.ui
        if not ui.dirty:
            return
        ui.dirty = False
        t0 = time.perf_counter()
        self.photo = frame_photo(self.tk, ui.frame(), self.photo)
        self.canvas.itemconfig(self.item, image=self.photo)
        self.root.title(t("app.title"))
        if ui.prev_img is not None and not self._anim:
            # A transition is running: the next frame is due one 60 Hz period after this one
            # started (not 16 ms after it ended, which made it ~30 ms a frame).
            self._anim = True
            self.root.after(max(1, 16 - int((time.perf_counter() - t0) * 1000)), self._next_anim)

    def _next_anim(self):
        self._anim = False
        self.redraw()

    def set_confirm(self, mode: str):
        """The pad's south/east buttons follow the confirm setting; so does the footer."""
        self.pad.confirm_cross = mode == "cross"
        self.ui.dirty = True

    def pad_tick(self):
        keys = self.pad.poll()
        # While the game runs the pad belongs to it: the same presses must not
        # also drive (and re-launch from) the hidden launcher.
        if not self.ui.running and not self.modal:
            for k in keys:
                self.press(k)
        self.root.after(PAD_POLL_MS, self.pad_tick)

    def bring_back(self):
        """The game ended (its exit confirmation, or the window closed): show the
        launcher again in front, focused, so a pad can drive it at once."""
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
            self.root.focus_force()
        except Exception:                      # noqa: BLE001
            pass

    def _modal(self, fn):
        """A blocking system dialog: the pad is mute while it is open (the launcher timers
        keep running under it), and what is held when it closes must be released first."""
        self.modal += 1
        try:
            return fn()
        finally:
            self.modal -= 1
            self.pad.rearm()

    def ask_iso(self):
        return self._modal(lambda: self.fd.askopenfilename(
            title=t("welcome.browse"), filetypes=[("ISO / CHD", "*.iso *.ISO *.chd *.CHD *.cso"), ("*", "*")]))

    def ask_dir(self):
        return self._modal(lambda: self.fd.askdirectory(title=t("options.texture_pack")))

    def quit(self):
        if self.ui.running and self.ui.running.poll() is None:
            self.ui.running.terminate()
        self.root.destroy()

    def start_prepare(self, iso: Path):
        ui = self.ui
        ui.progress, ui.step, ui.message, ui.cancel_flag = 0.0, "verify", "", False
        ui.goto("loading")
        prep = ui.es_prepare.Preparer(
            ui.es_prepare.PrepareOptions(iso=iso, data_dir=ui.data, kit_dir=ui.kit, jobs=ui.jobs),
            log=lambda s: None,
            progress=lambda f, s: self.q.put(("p", f)),
            step=lambda s: self.q.put(("s", s)),
            cancelled=lambda: ui.cancel_flag)

        def work():
            try:
                prep.run()
                self.q.put(("done", None))
            except Exception as e:                 # noqa: BLE001
                self.q.put(("fail", str(e)))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def start_texpack(self):
        ui = self.ui
        import es_texpack
        prep = ui.es_prepare.Preparer(ui.es_prepare.PrepareOptions(iso=Path("."), data_dir=ui.data, kit_dir=ui.kit))
        job = es_texpack.Job(ui.data, prep.game_exe(), ui.cfg,
                             progress=lambda f: self.q.put(("tp", f)), step=lambda s: self.q.put(("ts", s)),
                             cancelled=lambda: ui.cancel_flag)

        def work():
            try:
                self.q.put(("tdone", job.run()))
            except es_texpack.Cancelled:
                self.q.put(("tfail", t("texpack.cancelled")))
            except es_texpack.TexPackError as e:
                msg = str(e)
                self.q.put(("tfail", t("texpack.no_net", error=msg) if "http" in msg.lower() else t("texpack.failed", error=msg)))
            except Exception as e:                 # noqa: BLE001
                self.q.put(("tfail", t("texpack.failed", error=str(e))))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def launch(self):
        ui = self.ui
        prep = ui.es_prepare.Preparer(ui.es_prepare.PrepareOptions(iso=Path("."), data_dir=ui.data, kit_dir=ui.kit))
        if not prep.is_prepared():
            ui.message = t("main.game_missing")
            return
        cmd, env = ui.es_main.game_command(prep.game_exe(), ui.data, ui.cfg)
        self.crashes_before = set(ui.es_main.crash_reports(ui.data))
        self.game_started = time.time()
        ui.running = ui.es_main.spawn_game(cmd, env, ui.data)
        ui.message = ""
        # Out of the way while the game runs (it may be full screen); tick() brings
        # the launcher back, focused, when the game process ends.
        try:
            self.root.withdraw()
        except Exception:                      # noqa: BLE001
            pass

    def tick(self):
        ui = self.ui
        try:
            while True:
                kind, v = self.q.get_nowait()
                if kind == "p":
                    ui.progress = v
                elif kind == "s":
                    ui.step = v
                elif kind == "tp":
                    ui.progress = v
                elif kind == "ts":
                    ui.step = v
                elif kind == "tdone":
                    ui.cfg["texpack"] = "on"
                    ui.es_main.save_config(ui.data, ui.cfg)
                    ui.tp_state, ui.tp_text = "done", t("texpack.done", count=v)
                elif kind == "tfail":
                    ui.tp_state, ui.tp_text = "failed", v
                elif kind == "done":
                    ui.message = ""
                    ui.goto("play")
                elif kind == "fail":
                    cancelled = ui.cancel_flag
                    ui.message = t("prepare.cancelled") if cancelled else t("prepare.failed", error=v)
                    ui.goto("setup")
                    ui.iso_status = ("err" if not cancelled else "", ui.message)
                ui.dirty = True
        except queue.Empty:
            pass
        if ui.running and ui.running.poll() is not None:
            code = ui.running.returncode
            ui.es_main.finish_game(ui.running)
            ui.running = None
            ui.message = t("main.game_ended", code=code) if code else ""
            ui.dirty = True
            self.pad.rearm()                   # held while the game had the pad: must be released first
            self.bring_back()
            self.check_crash(code)
            self.check_renderer()
        if not self._anim:                     # a running transition paints on its own 60 Hz schedule
            self.redraw()
        # 16 ms while a transition animates (60 Hz), relaxed otherwise: key presses redraw on
        # their own (press), the timer only serves the background thread and the game's end.
        # 8 ms while a worker thread reports progress (the queue is only read here), 16 ms in a
        # transition, relaxed on idle screens (a key press redraws on its own).
        working = ui.screen == "loading" or (ui.screen == "texpack" and ui.tp_state == "working")
        self.root.after(8 if working else 16 if ui.prev_img is not None else 60, self.tick)

    def check_crash(self, code):
        """A new report after the game ended: offer to open the folder, copy it or open an issue.
        A freeze report from a game that then ended normally is not a crash: no dialog."""
        ui = self.ui
        new = [p for p in ui.es_main.crash_reports(ui.data) if p not in getattr(self, "crashes_before", set())]
        if not new:
            return
        report = new[-1]
        if code == 0 and ui.es_main.crash_summary(report).get("kind") == "hang":
            return
        CrashDialog(self, report, code)

    def check_renderer(self):
        """The game started without Vulkan (it ran the slow software renderer, or stopped because
        --renderer vulkan allows no fallback): say so, with the report. A crash dialog, if one was
        just shown, comes first; this one waits behind it."""
        ui = self.ui
        found = ui.es_main.renderer_report(ui.data, getattr(self, "game_started", 0.0))
        if found:
            RendererDialog(self, found[0], found[1])

    def run(self):
        self.root.mainloop()


class RendererDialog:
    """"Vulkan did not start": open the folder, copy the report, open an issue. Nothing is sent."""

    def __init__(self, app: App, report: Path, result: str):
        tk = app.tk
        self.app, self.report, self.result = app, report, result
        self.top = tk.Toplevel(app.root)
        self.top.title(t("renderer.title"))
        self.top.resizable(False, False)
        self.top.transient(app.root)
        app.modal += 1
        self.top.bind("<Destroy>", lambda e: self._closed(e), add="+")
        body = t("renderer.body", file=str(report.name), folder=short_path(report.parent, 560))
        tk.Label(self.top, text=body, justify="left", wraplength=560, padx=18, pady=14).pack()
        tk.Label(self.top, text=t("main.version", version=app.ui._version()), fg="#888", font=("TkDefaultFont", 8)).pack(anchor="e", padx=10)
        row = tk.Frame(self.top)
        row.pack(pady=(0, 14))
        for label, fn in ((t("crash.open_folder"), self.open_folder), (t("crash.copy"), self.copy),
                          (t("crash.issue"), self.issue), (t("crash.close"), self.top.destroy)):
            tk.Button(row, text=label, command=fn, padx=8).pack(side="left", padx=6)
        self.status = tk.Label(self.top, text="", fg="#2a7")
        self.status.pack(pady=(0, 8))
        try:
            self.top.grab_set()
        except Exception:                      # noqa: BLE001
            pass

    def _closed(self, e):
        if e.widget is self.top and self.app.modal > 0:
            self.app.modal -= 1
            self.app.pad.rearm()

    def open_folder(self):
        try:
            open_folder(self.report.parent)
        except OSError as e:
            self.status.config(text=str(e))

    def copy(self):
        try:
            text = self.app.ui.es_main.scrub_paths(self.report.read_text(encoding="utf-8", errors="replace"))
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(text)
            self.status.config(text=t("crash.copied"))
        except OSError as e:
            self.status.config(text=str(e))

    def issue(self):
        import webbrowser
        webbrowser.open(self.app.ui.es_main.renderer_issue_url(self.report, self.result))


class CrashDialog:
    """"The game closed unexpectedly": four buttons, nothing is sent automatically."""

    def __init__(self, app: App, report: Path, code):
        tk = app.tk
        self.app, self.report = app, report
        self.top = tk.Toplevel(app.root)
        self.top.title(t("crash.title"))
        self.top.resizable(False, False)
        self.top.transient(app.root)
        app.modal += 1                                   # the pad must not drive the launcher behind
        self.top.bind("<Destroy>", lambda e: self._closed(e), add="+")
        body = t("crash.body", file=str(report.name), folder=short_path(report.parent, 560))
        tk.Label(self.top, text=body, justify="left", wraplength=560, padx=18, pady=14).pack()
        tk.Label(self.top, text=t("main.version", version=app.ui._version()), fg="#888", font=("TkDefaultFont", 8)).pack(anchor="e", padx=10)
        row = tk.Frame(self.top)
        row.pack(pady=(0, 14))
        for label, fn in ((t("crash.open_folder"), self.open_folder), (t("crash.copy"), self.copy),
                          (t("crash.issue"), self.issue), (t("crash.close"), self.top.destroy)):
            tk.Button(row, text=label, command=fn, padx=8).pack(side="left", padx=6)
        self.status = tk.Label(self.top, text="", fg="#2a7")
        self.status.pack(pady=(0, 8))
        try:
            self.top.grab_set()
        except Exception:                      # noqa: BLE001
            pass

    def _closed(self, e):
        if e.widget is self.top and self.app.modal > 0:
            self.app.modal -= 1
            self.app.pad.rearm()

    def open_folder(self):
        try:
            open_folder(self.report.parent)
        except OSError as e:
            self.status.config(text=str(e))

    def copy(self):
        try:
            text = self.app.ui.es_main.scrub_paths(self.report.read_text(encoding="utf-8", errors="replace"))
            self.app.root.clipboard_clear()
            self.app.root.clipboard_append(text)
            self.status.config(text=t("crash.copied"))
        except OSError as e:
            self.status.config(text=str(e))

    def issue(self):
        import webbrowser
        webbrowser.open(self.app.ui.es_main.issue_url(self.report))


def smoke(data: Path, kit: Path) -> int:
    """Open the real window for a moment and press keys: proves the Tk path
    (PhotoImage, bindings, timer) works. Used by the release checks."""
    app = App(Ui(data, kit))
    seq = ["Down", "Up", "Down", "Return", "Escape"]
    def press(i=0):
        if i < len(seq):
            app.root.event_generate(f"<{seq[i]}>")
            app.root.after(150, press, i + 1)
        else:
            app.root.after(200, app.root.destroy)
    app.root.after(300, press)
    app.run()
    print("gui smoke ok: screen =", app.ui.screen)
    return 0


def offer_migration(data: Path) -> None:
    """Data of an older version in its old folder: offer to COPY it into the portable
    folder (never move or delete). Declining is remembered."""
    import es_portable
    marker = data / ".migration-declined"
    old = es_portable.find_legacy(data)
    if not old or marker.exists():
        return
    import tkinter
    from tkinter import messagebox
    root = tkinter.Tk()
    root.withdraw()
    try:
        es_i18n.set_language(Ui(data, Path("/nonexistent-es-kit")).cfg["language"])
        if messagebox.askyesno(t("app.title") + " " + Ui(data, Path("/nonexistent-es-kit"))._version(), t("portable.migrate_ask", path=str(old), dest=str(data))):
            es_portable.migrate(old, data, log=lambda s: None)
        else:
            marker.write_text("declined\n")
    finally:
        root.destroy()


def run_gui(data: Path, kit: Path, jobs: int) -> int:
    try:
        offer_migration(data)
    except Exception:                          # noqa: BLE001  a failed offer must not stop the launcher
        pass
    App(Ui(data, kit, jobs)).run()
    return 0


def es_texpack_enabled() -> bool:
    import es_texpack
    return es_texpack.ENABLED


def shots(out: Path, data: Path, kit: Path, langs=None, confirm=None) -> None:
    """Write PNG screenshots of the four screens (no display needed)."""
    out.mkdir(parents=True, exist_ok=True)
    for lang in langs or ["en"]:
        ui = Ui(data, kit)
        ui.cfg["language"] = lang
        if confirm:
            ui.cfg["confirm"] = confirm
        es_i18n.set_language(lang)
        ui.text_cache.clear()
        ui.iso_status = ("ok", t("welcome.ok", title="DJMAX PORTABLE 3", id="ULKS46236") + "  -  game.iso")
        ui.sel["setup"] = 2
        ui.progress, ui.step = 0.42, "recomp"
        ui.message = ""
        for name in ("setup", "loading", "play", "options") + (("texpack",) if es_texpack_enabled() else ()):
            ui.screen = name
            ui.prev_img = None
            if name == "options":
                ui.sel["options"] = 2
            ui.compose().convert("RGB").save(out / f"{lang}-{name}{'-' + confirm if confirm else ''}.png")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", type=Path)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--langs", default="en")
    ap.add_argument("--confirm", default=None, choices=("auto", "circle", "cross"))
    ap.add_argument("--data", type=Path, default=Path("/nonexistent-es-data"))
    ap.add_argument("--kit", type=Path, default=Path("/nonexistent-es-kit"))
    a = ap.parse_args()
    if a.smoke:
        sys.exit(smoke(a.data, a.kit))
    shots(a.shots, a.data, a.kit, a.langs.split(","), a.confirm)
