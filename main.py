"""
🎵 My Music Player — Android 버전 (Kivy)

- 폰에서: APK로 빌드해서 설치 (README.md 참고)
- PC에서 UI 미리보기: pip install kivy yt-dlp python-vlc pillow  →  python main.py
  (PC에서는 VLC 프로그램이 설치되어 있으면 소리도 나와요)

한글이 네모(□)로 보이면 이 파일 옆에 한글 폰트를 font.ttf 라는 이름으로 넣고 다시 빌드하세요.
(예: 나눔고딕 NanumGothic.ttf → font.ttf)
"""
import json
import os
import random
import shutil
import sys
import threading
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from types import SimpleNamespace

try:  # 안드로이드에서 https 인증서 인식용
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

from kivy.app import App
from kivy.clock import Clock, mainthread
from kivy.core.text import LabelBase
from kivy.core.window import Window
from kivy.graphics import Color, Ellipse, Line, Rectangle, RoundedRectangle, Triangle
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import AsyncImage
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import get_color_from_hex as C
from kivy.utils import platform

ON_ANDROID = platform == "android"
YTDLP_UPDATE_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
yt_dlp = None  # build() 에서 로드

# ───────────── 한글 폰트 ─────────────
def find_font():
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, n) for n in ("font.ttf", "font.otf", "font.ttc")]
    cands += [
        "/system/fonts/NotoSansCJK-Regular.ttc", "/system/fonts/NotoSansKR-Regular.otf",
        "/system/fonts/NotoSansKR-Regular.ttf", "/system/fonts/NanumGothic.ttf",
        "/system/fonts/DroidSansFallback.ttf", "/system/fonts/DroidSansFallbackFull.ttf",
        "C:/Windows/Fonts/malgun.ttf", "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for p in cands:
        if os.path.exists(p):
            return p
    return None


_font = find_font()
if _font:
    LabelBase.register(name="Roboto", fn_regular=_font, fn_bold=_font)

# ───────────── 테마 ─────────────
THEMES = {
    "퍼플": dict(bg="#101014", card="#1b1b22", card_hover="#25252e", accent="#8b5cf6",
               accent_dim="#2f2552", text="#f1f1f5", text_dim="#9a9aa8", input="#14141a"),
    "오션 블루": dict(bg="#0b1220", card="#131c2e", card_hover="#1c2740", accent="#3b82f6",
                  accent_dim="#1b2f57", text="#eef3fb", text_dim="#8fa0bd", input="#0e1626"),
    "민트": dict(bg="#0d1512", card="#15211c", card_hover="#1d2e27", accent="#10b981",
               accent_dim="#164236", text="#ecf7f2", text_dim="#8fb0a4", input="#0f1a16"),
    "벚꽃 핑크": dict(bg="#160f14", card="#231820", card_hover="#2e1f29", accent="#ec4899",
                  accent_dim="#4a1f38", text="#fbeef5", text_dim="#b79aab", input="#1a1117"),
    "선셋": dict(bg="#14100c", card="#211a14", card_hover="#2c231a", accent="#f97316",
               accent_dim="#4a2a12", text="#fbf1e8", text_dim="#b3a08c", input="#19130e"),
    "사이버": dict(bg="#07090d", card="#0f141b", card_hover="#17202b", accent="#22d3ee",
                 accent_dim="#0f3a44", text="#e6faff", text_dim="#7f93a8", input="#0a0e14"),
    "그레이": dict(bg="#0e0e10", card="#18181b", card_hover="#222226", accent="#71717a",
                 accent_dim="#2a2a30", text="#f4f4f5", text_dim="#9a9aa3", input="#121214"),
    "라이트": dict(bg="#eef0f6", card="#ffffff", card_hover="#e6e9f2", accent="#7c3aed",
                 accent_dim="#e6dcfb", text="#1f2430", text_dim="#6b7280", input="#f1f3f9"),
}
DEFAULT_THEME = "퍼플"
CREDIT = "제작자 : 러브 ♥"
T = SimpleNamespace(**THEMES[DEFAULT_THEME])
WHITE = (1, 1, 1, 1)


def fmt_time(sec):
    if not sec or sec < 0:
        return "0:00"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@dataclass
class Track:
    title: str
    url: str
    channel: str = ""
    duration: int = 0
    thumb: str = ""


def tracks_from_info(info):
    entries = info.get("entries") or [info]
    tracks = []
    for e in entries:
        if not e:
            continue
        vid = e.get("id")
        url = e.get("webpage_url") or e.get("url") or ""
        if not url.startswith("http"):
            if not vid:
                continue
            url = f"https://www.youtube.com/watch?v={vid}"
        thumb = e.get("thumbnail") or ""
        if "youtube.com" in url or "youtu.be" in url:
            thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg" if vid else thumb
        tracks.append(Track(
            title=e.get("title") or "제목 없음", url=url,
            channel=e.get("channel") or e.get("uploader") or "",
            duration=int(e.get("duration") or 0), thumb=thumb))
    return tracks


# ───────────── yt-dlp 로드 (업데이트본 우선) ─────────────
def load_ytdlp(update_dir):
    global yt_dlp
    upd = os.path.join(update_dir, "yt-dlp")
    if os.path.isfile(upd):
        sys.path.insert(0, upd)
        try:
            import yt_dlp as mod
            yt_dlp = mod
            return
        except Exception:
            sys.path.remove(upd)
            for k in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
                del sys.modules[k]
    try:
        import yt_dlp as mod
        yt_dlp = mod
    except Exception:
        yt_dlp = None


def ytdlp_version():
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:
        return "?"


# ───────────── 재생 엔진 ─────────────
class AndroidEngine:
    """안드로이드 내장 MediaPlayer (pyjnius)"""

    def __init__(self):
        from jnius import autoclass
        self._MP = autoclass("android.media.MediaPlayer")
        self._Uri = autoclass("android.net.Uri")
        self._Map = autoclass("java.util.HashMap")
        self._ctx = autoclass("org.kivy.android.PythonActivity").mActivity
        self.mp = None
        self.user_paused = False
        self._lock = threading.Lock()

    def load(self, url, headers, still_valid):
        """블로킹 호출 — 반드시 작업 스레드에서."""
        mp = self._MP()
        try:
            mp.setAudioStreamType(3)  # STREAM_MUSIC
            try:
                mp.setWakeMode(self._ctx, 1)  # PARTIAL_WAKE_LOCK
            except Exception:
                pass
            if str(url).startswith(("http://", "https://")):
                hm = self._Map()
                for k, v in (headers or {}).items():
                    if str(k).lower() != "accept-encoding":
                        hm.put(str(k), str(v))
                mp.setDataSource(self._ctx, self._Uri.parse(url), hm)
            else:  # 내려받은 로컬 파일
                mp.setDataSource(str(url))
            mp.prepare()
        except Exception:
            self._release(mp)
            raise
        if not still_valid():
            self._release(mp)
            return False
        with self._lock:
            old, self.mp = self.mp, mp
            self.user_paused = False
        self._release(old)
        mp.start()
        return True

    @staticmethod
    def _release(mp):
        if mp is not None:
            try:
                mp.release()
            except Exception:
                pass

    def pause(self):
        try:
            if self.mp:
                self.mp.pause()
                self.user_paused = True
        except Exception:
            pass

    def resume(self):
        try:
            if self.mp:
                self.mp.start()
                self.user_paused = False
        except Exception:
            pass

    def seek(self, ms):
        try:
            if self.mp:
                self.mp.seekTo(int(ms))
        except Exception:
            pass

    def position(self):
        try:
            return max(self.mp.getCurrentPosition(), 0) if self.mp else 0
        except Exception:
            return 0

    def duration(self):
        try:
            return max(self.mp.getDuration(), 0) if self.mp else 0
        except Exception:
            return 0

    def stop(self):
        with self._lock:
            old, self.mp = self.mp, None
        self._release(old)

    def state(self):
        mp = self.mp
        if not mp:
            return "idle"
        try:
            if mp.isPlaying():
                return "playing"
            if self.user_paused:
                return "paused"
            dur, pos = mp.getDuration(), mp.getCurrentPosition()
            if dur > 0 and pos >= dur - 1500:
                return "ended"
            return "playing"  # 버퍼링 중
        except Exception:
            return "error"


class VlcEngine:
    """PC 테스트용 (python-vlc)"""

    def __init__(self):
        import vlc
        self.vlc = vlc
        self.inst = vlc.Instance("--no-video", "--quiet")
        self.p = self.inst.media_player_new()

    def load(self, url, headers, still_valid):
        if not still_valid():
            return False
        if os.path.exists(str(url)):
            self.p.set_media(self.inst.media_new_path(url))
        else:
            self.p.set_media(self.inst.media_new(url))
        self.p.play()
        return True

    def pause(self):
        self.p.set_pause(1)

    def resume(self):
        self.p.set_pause(0)

    def seek(self, ms):
        self.p.set_time(int(ms))

    def position(self):
        return max(self.p.get_time(), 0)

    def duration(self):
        return max(self.p.get_length(), 0)

    def stop(self):
        self.p.stop()

    def state(self):
        s, V = self.p.get_state(), self.vlc.State
        if s == V.Ended:
            return "ended"
        if s == V.Error:
            return "error"
        if s == V.Paused:
            return "paused"
        return "playing"


class NullEngine:
    def load(self, *a, **k):
        raise RuntimeError("재생 엔진이 없어요 (PC 테스트: pip install python-vlc + VLC 설치)")

    def pause(self): pass
    def resume(self): pass
    def seek(self, ms): pass
    def position(self): return 0
    def duration(self): return 0
    def stop(self): pass
    def state(self): return "idle"


def make_engine():
    try:
        return AndroidEngine() if ON_ANDROID else VlcEngine()
    except Exception as e:
        print("engine error:", e)
        return NullEngine()


# ───────────── 커스텀 위젯 ─────────────
def lbl(text="", size=14, color=None, bold=False, halign="left", lines=1, **kw):
    opts = dict(font_size=sp(size), color=color or C(T.text), bold=bold, halign=halign, valign="middle")
    if lines == 1:
        opts.update(shorten=True, shorten_from="right")
    else:
        opts.update(max_lines=lines)
    opts.update(kw)
    w = Label(text=text, **opts)
    w.bind(size=lambda inst, s: setattr(inst, "text_size", s))
    return w


class Card(BoxLayout):
    """둥근 배경 박스"""

    def __init__(self, color, radius=14, **kw):
        super().__init__(**kw)
        self.bg = color
        with self.canvas.before:
            self._c = Color(*self._shade())
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._upd, size=self._upd)

    def _shade(self):
        return self.bg

    def _upd(self, *a):
        self._r.pos, self._r.size = self.pos, self.size
        self._c.rgba = self._shade()


def lighten(c, k=0.12):
    return (min(c[0] + k, 1), min(c[1] + k, 1), min(c[2] + k, 1), c[3])


class PillButton(ButtonBehavior, Card):
    def __init__(self, text, bg, fg, fs=13, radius=10, **kw):
        super().__init__(color=bg, radius=radius, **kw)
        self.label = lbl(text, fs, fg, bold=True, halign="center")
        self.add_widget(self.label)
        self.bind(state=self._upd)

    def _shade(self):
        return lighten(self.bg) if self.state == "down" else self.bg

    def set_colors(self, bg, fg):
        self.bg = bg
        self.label.color = fg
        self._upd()


class TapBox(ButtonBehavior, BoxLayout):
    pass


class IconButton(ButtonBehavior, Widget):
    """글꼴 없이 도형으로 그리는 아이콘 버튼"""

    def __init__(self, icon, fg, bg=None, **kw):
        super().__init__(**kw)
        self.icon, self.fg, self.bg = icon, fg, bg
        self.bind(pos=self.redraw, size=self.redraw, state=self.redraw, disabled=self.redraw)
        self.redraw()

    def set_icon(self, icon):
        if icon != self.icon:
            self.icon = icon
            self.redraw()

    def redraw(self, *a):
        self.canvas.clear()
        x, y = self.pos
        w, h = self.size
        cx, cy = x + w / 2, y + h / 2
        s = min(w, h)
        k = s * (0.22 if self.bg else 0.26)
        with self.canvas:
            if self.bg:
                Color(*(lighten(self.bg) if self.state == "down" else self.bg))
                Ellipse(pos=(cx - s / 2, cy - s / 2), size=(s, s))
            Color(*self.fg)
            ic = self.icon
            if ic == "play":
                Triangle(points=[cx - 0.8 * k, cy - 1.2 * k, cx - 0.8 * k, cy + 1.2 * k, cx + 1.2 * k, cy])
            elif ic == "pause":
                Rectangle(pos=(cx - 0.95 * k, cy - k), size=(0.65 * k, 2 * k))
                Rectangle(pos=(cx + 0.3 * k, cy - k), size=(0.65 * k, 2 * k))
            elif ic == "next":
                Triangle(points=[cx - k, cy - k, cx - k, cy + k, cx + 0.6 * k, cy])
                Rectangle(pos=(cx + 0.7 * k, cy - k), size=(0.35 * k, 2 * k))
            elif ic == "prev":
                Triangle(points=[cx + k, cy - k, cx + k, cy + k, cx - 0.6 * k, cy])
                Rectangle(pos=(cx - 1.05 * k, cy - k), size=(0.35 * k, 2 * k))
            elif ic == "up":
                Triangle(points=[cx - k, cy - 0.6 * k, cx + k, cy - 0.6 * k, cx, cy + 0.8 * k])
            elif ic == "down":
                Triangle(points=[cx - k, cy + 0.6 * k, cx + k, cy + 0.6 * k, cx, cy - 0.8 * k])
            elif ic == "close":
                Line(points=[cx - 0.8 * k, cy - 0.8 * k, cx + 0.8 * k, cy + 0.8 * k], width=dp(1.6))
                Line(points=[cx - 0.8 * k, cy + 0.8 * k, cx + 0.8 * k, cy - 0.8 * k], width=dp(1.6))
            elif ic == "plus":
                Line(points=[cx - k, cy, cx + k, cy], width=dp(1.8))
                Line(points=[cx, cy - k, cx, cy + k], width=dp(1.8))


class SeekSlider(Slider):
    def __init__(self, on_release_cb, **kw):
        super().__init__(**kw)
        self.cb = on_release_cb
        self.dragging = False

    def on_touch_down(self, touch):
        if self.collide_point(*touch.pos):
            self.dragging = True
        return super().on_touch_down(touch)

    def on_touch_up(self, touch):
        was = self.dragging
        self.dragging = False
        res = super().on_touch_up(touch)
        if was:
            self.cb(self.value)
        return res


# ───────────── 앱 ─────────────
class MusicApp(App):
    title = "My Music"

    def build(self):
        self.data_dir = self.user_data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.settings_path = os.path.join(self.data_dir, "settings.json")
        self.queue_path = os.path.join(self.data_dir, "queue.json")
        self.ytdlp_dir = os.path.join(self.data_dir, "ytdlp")
        load_ytdlp(self.ytdlp_dir)

        self.settings = self._load_json(self.settings_path, {})
        name = self.settings.get("theme")
        self.theme_name = name if name in THEMES else DEFAULT_THEME
        T.__dict__.update(THEMES[self.theme_name])

        self.engine = make_engine()
        self.items = []
        self.results = []
        self.current = -1
        self.state = "idle"  # idle / loading / playing / paused
        self.token = 0
        self.shuffle = False
        self.repeat = 0  # 0 끔, 1 전체, 2 한곡
        self.view = "queue"
        self.status_text = ""
        self.input_text = ""
        self._load_queue()

        if ON_ANDROID:
            Window.softinput_mode = "below_target"
        else:
            Window.size = (400, 780)
        Window.bind(on_keyboard=self.on_key)

        self.w_input = None
        self.root_box = BoxLayout(orientation="vertical")
        self.build_ui()
        if yt_dlp is None:
            self.set_status("⚠ yt-dlp 를 불러오지 못했어요")
        else:
            self.set_status(f"제목이나 URL을 입력하세요 (yt-dlp {ytdlp_version()})")
        Clock.schedule_interval(self.tick, 0.25)
        return self.root_box

    # ── 저장 ──
    @staticmethod
    def _load_json(path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _save_json(self, path, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception:
            pass

    def _load_queue(self):
        try:
            self.items = [Track(**d) for d in self._load_json(self.queue_path, [])]
        except Exception:
            self.items = []

    def save_queue(self):
        self._save_json(self.queue_path, [asdict(t) for t in self.items])

    # ── 시스템 이벤트 ──
    def on_pause(self):
        return True  # 백그라운드에서도 앱 유지(음악 계속)

    def on_resume(self):
        pass

    def on_stop(self):
        self.engine.stop()

    def on_key(self, window, key, *args):
        if key == 27 and ON_ANDROID:  # 뒤로가기 → 앱 종료 대신 백그라운드로
            try:
                from jnius import autoclass
                autoclass("org.kivy.android.PythonActivity").mActivity.moveTaskToBack(True)
                return True
            except Exception:
                return False
        return False

    # ── UI ──
    def build_ui(self):
        if getattr(self, "w_input", None) is not None:
            self.input_text = self.w_input.text
        root = self.root_box
        root.clear_widgets()
        Window.clearcolor = C(T.bg)
        root.padding = [dp(10), dp(10), dp(10), dp(6)]
        root.spacing = dp(8)

        # 헤더
        head = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(6))
        head.add_widget(lbl("My Music", 20, bold=True))
        upd = PillButton("yt-dlp↑", C(T.card_hover), C(T.text), fs=11, size_hint=(None, 1), width=dp(66))
        upd.bind(on_release=self.update_ytdlp)
        head.add_widget(upd)
        spin = Spinner(text=self.theme_name, values=list(THEMES), size_hint=(None, 1), width=dp(96),
                       font_size=sp(12), background_normal="", background_down="",
                       background_color=C(T.card_hover), color=C(T.text))
        spin.bind(text=lambda w, v: self.set_theme(v))
        head.add_widget(spin)
        root.add_widget(head)

        # 재생 카드
        card = Card(C(T.card), orientation="vertical", size_hint_y=None, height=dp(210),
                    padding=dp(12), spacing=dp(6))
        top = BoxLayout(size_hint_y=None, height=dp(72), spacing=dp(10))
        self.w_thumb = AsyncImage(size_hint=(None, 1), width=dp(128), fit_mode="cover")
        info = BoxLayout(orientation="vertical")
        self.w_title = lbl("", 15, bold=True, lines=2)
        self.w_channel = lbl("", 12, C(T.text_dim), size_hint_y=None, height=dp(22))
        info.add_widget(self.w_title)
        info.add_widget(self.w_channel)
        top.add_widget(self.w_thumb)
        top.add_widget(info)
        card.add_widget(top)

        seek_row = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(6))
        self.w_cur = lbl("0:00", 11, C(T.text_dim), size_hint_x=None, width=dp(40))
        self.w_seek = SeekSlider(self.on_seek, min=0, max=1, value=0, value_track=True,
                                 value_track_color=C(T.accent), cursor_size=(dp(18), dp(18)))
        self.w_total = lbl("0:00", 11, C(T.text_dim), size_hint_x=None, width=dp(40), halign="right")
        seek_row.add_widget(self.w_cur)
        seek_row.add_widget(self.w_seek)
        seek_row.add_widget(self.w_total)
        card.add_widget(seek_row)

        ctrl = BoxLayout(size_hint_y=None, height=dp(62), spacing=dp(8))
        self.btn_shuffle = PillButton("섞기", C(T.card_hover), C(T.text), fs=12,
                                      size_hint=(None, None), size=(dp(52), dp(36)), pos_hint={"center_y": .5})
        self.btn_shuffle.bind(on_release=self.toggle_shuffle)
        prev = IconButton("prev", C(T.text), C(T.card_hover), size_hint=(None, 1), width=dp(48))
        prev.bind(on_release=lambda *_: self.prev_track())
        self.w_play = IconButton("play", WHITE, C(T.accent), size_hint=(None, 1), width=dp(62))
        self.w_play.bind(on_release=lambda *_: self.toggle_play())
        nxt = IconButton("next", C(T.text), C(T.card_hover), size_hint=(None, 1), width=dp(48))
        nxt.bind(on_release=lambda *_: self.next_track())
        self.btn_repeat = PillButton("반복", C(T.card_hover), C(T.text), fs=12,
                                     size_hint=(None, None), size=(dp(52), dp(36)), pos_hint={"center_y": .5})
        self.btn_repeat.bind(on_release=self.toggle_repeat)
        ctrl.add_widget(Widget())
        for w in (self.btn_shuffle, prev, self.w_play, nxt, self.btn_repeat):
            ctrl.add_widget(w)
        ctrl.add_widget(Widget())
        card.add_widget(ctrl)
        root.add_widget(card)

        # 검색줄
        srow = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(6))
        self.w_input = TextInput(
            text=self.input_text, hint_text="노래 제목 또는 유튜브 URL", multiline=False, write_tab=False,
            font_size=sp(14), background_normal="", background_active="", background_color=C(T.input),
            foreground_color=C(T.text), hint_text_color=C(T.text_dim), cursor_color=C(T.accent),
            padding=[dp(12), dp(12), dp(12), dp(12)])
        self.w_input.bind(on_text_validate=self.submit)
        go = PillButton("검색", C(T.accent), WHITE, fs=14, size_hint=(None, 1), width=dp(64))
        go.bind(on_release=self.submit)
        srow.add_widget(self.w_input)
        srow.add_widget(go)
        root.add_widget(srow)

        # 탭
        tabs = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        self.tab_q = PillButton("대기열", C(T.card_hover), C(T.text), fs=13)
        self.tab_q.bind(on_release=lambda *_: self.set_view("queue"))
        self.tab_r = PillButton("검색 결과", C(T.card_hover), C(T.text), fs=13)
        self.tab_r.bind(on_release=lambda *_: self.set_view("results"))
        tabs.add_widget(self.tab_q)
        tabs.add_widget(self.tab_r)
        root.add_widget(tabs)

        # 목록
        sv = ScrollView(do_scroll_x=False, bar_width=dp(3), bar_color=C(T.accent))
        self.list_box = GridLayout(cols=1, size_hint_y=None, spacing=dp(6), padding=[0, 0, 0, dp(6)])
        self.list_box.bind(minimum_height=self.list_box.setter("height"))
        sv.add_widget(self.list_box)
        root.add_widget(sv)

        self.w_status = lbl(self.status_text, 11, C(T.text_dim), lines=2, size_hint_y=None, height=dp(34))
        root.add_widget(self.w_status)
        root.add_widget(lbl(CREDIT, 11, C(T.text_dim), halign="center", bold=True, size_hint_y=None, height=dp(20)))

        self.refresh_now()
        self.refresh_list()
        self.style_toggles()

    def set_theme(self, name):
        if name not in THEMES or name == self.theme_name:
            return
        self.theme_name = name
        T.__dict__.update(THEMES[name])
        self.settings["theme"] = name
        self._save_json(self.settings_path, self.settings)
        Clock.schedule_once(lambda dt: self.build_ui(), 0.05)

    def set_view(self, v):
        self.view = v
        self.refresh_list()

    def style_toggles(self):
        on = lambda flag: (C(T.accent), WHITE) if flag else (C(T.card_hover), C(T.text))
        self.btn_shuffle.set_colors(*on(self.shuffle))
        self.btn_repeat.set_colors(*on(self.repeat))
        self.btn_repeat.label.text = ("한곡" if self.repeat == 2 else "반복")

    @mainthread
    def set_status(self, text):
        self.status_text = text
        w = getattr(self, "w_status", None)
        if w is not None:
            w.text = text

    def refresh_now(self):
        if 0 <= self.current < len(self.items) and self.state != "idle":
            t = self.items[self.current]
            self.w_title.text, self.w_channel.text = t.title, t.channel
            self.w_thumb.source = t.thumb or ""
            self.w_total.text = fmt_time(t.duration)
        else:
            self.w_title.text, self.w_channel.text, self.w_thumb.source = "재생 중인 곡이 없어요", "", ""
            self.w_total.text = self.w_cur.text = "0:00"
            self.w_seek.value = 0
        self.w_play.set_icon("pause" if self.state == "playing" else "play")

    # ── 목록 ──
    def refresh_list(self):
        box = self.list_box
        box.clear_widgets()
        self.tab_q.label.text = f"대기열 ({len(self.items)})"
        sel = lambda a: (C(T.accent), WHITE) if a else (C(T.card_hover), C(T.text))
        self.tab_q.set_colors(*sel(self.view == "queue"))
        self.tab_r.set_colors(*sel(self.view == "results"))
        if self.view == "queue":
            if not self.items:
                box.add_widget(self._empty("대기열이 비어있어요\n검색해서 곡을 추가해 보세요"))
            for i, t in enumerate(self.items):
                box.add_widget(self.queue_row(i, t))
        else:
            if not self.results:
                box.add_widget(self._empty("위에서 노래를 검색해 보세요"))
            for t in self.results:
                box.add_widget(self.result_row(t))

    def _empty(self, text):
        w = lbl(text, 13, C(T.text_dim), halign="center", lines=3, size_hint_y=None, height=dp(110))
        return w

    def queue_row(self, i, t):
        cur = i == self.current
        row = Card(C(T.accent_dim) if cur else C(T.card), orientation="horizontal", size_hint_y=None,
                   height=dp(64), padding=[dp(6), dp(6)], spacing=dp(6))
        row.add_widget(AsyncImage(source=t.thumb or "", size_hint=(None, 1), width=dp(76), fit_mode="cover"))
        info = TapBox(orientation="vertical")
        info.bind(on_release=lambda *_, i=i: self.play_index(i))
        info.add_widget(lbl(t.title, 13, bold=True))
        info.add_widget(lbl(f"{t.channel} · {fmt_time(t.duration)}", 11, C(T.text_dim)))
        row.add_widget(info)
        last = len(self.items) - 1
        for icon, d in (("up", -1), ("down", 1)):
            ok = 0 <= i + d <= last
            b = IconButton(icon, C(T.text) if ok else C(T.text_dim), size_hint=(None, 1), width=dp(32))
            b.disabled = not ok
            b.bind(on_release=lambda *_, d=d, i=i: self.move(i, d))
            row.add_widget(b)
        x = IconButton("close", C(T.text_dim), size_hint=(None, 1), width=dp(32))
        x.bind(on_release=lambda *_, i=i: self.remove(i))
        row.add_widget(x)
        return row

    def result_row(self, t):
        row = Card(C(T.card), orientation="horizontal", size_hint_y=None, height=dp(64),
                   padding=[dp(6), dp(6)], spacing=dp(6))
        row.add_widget(AsyncImage(source=t.thumb or "", size_hint=(None, 1), width=dp(76), fit_mode="cover"))
        info = BoxLayout(orientation="vertical")
        info.add_widget(lbl(t.title, 13, bold=True))
        info.add_widget(lbl(f"{t.channel} · {fmt_time(t.duration)}", 11, C(T.text_dim)))
        row.add_widget(info)
        p = IconButton("play", WHITE, C(T.accent), size_hint=(None, 1), width=dp(40))
        p.bind(on_release=lambda *_, t=t: self.add_tracks([t], play=True))
        a = IconButton("plus", C(T.text), C(T.card_hover), size_hint=(None, 1), width=dp(40))
        a.bind(on_release=lambda *_, t=t: self.add_tracks([t]))
        row.add_widget(p)
        row.add_widget(a)
        return row

    # ── 검색 / 추가 ──
    def submit(self, *a):
        text = self.w_input.text.strip()
        if not text:
            return
        if yt_dlp is None:
            self.set_status("⚠ yt-dlp 가 없어요")
            return
        self.set_status("찾는 중...")
        threading.Thread(target=self._query, args=(text,), daemon=True).start()

    def _query(self, text):
        base = {"quiet": True, "no_warnings": True, "skip_download": True, "cachedir": False,
                "socket_timeout": 15}
        try:
            if text.startswith(("http://", "https://")):
                single = "v=" in text or "youtu.be/" in text
                opts = dict(base, extract_flat="in_playlist", noplaylist=single)
                with yt_dlp.YoutubeDL(opts) as y:
                    info = y.extract_info(text, download=False)
                self.on_added(tracks_from_info(info)[:200])
            else:
                with yt_dlp.YoutubeDL(dict(base, extract_flat=True)) as y:
                    info = y.extract_info(f"ytsearch8:{text}", download=False)
                self.on_results(tracks_from_info(info))
        except Exception as e:
            self.set_status(f"⚠ 오류: {str(e)[:120]}")

    @mainthread
    def on_results(self, tracks):
        self.results = tracks
        self.view = "results"
        self.refresh_list()
        self.set_status(f"{len(tracks)}개 찾았어요")

    @mainthread
    def on_added(self, tracks):
        self.add_tracks(tracks)

    def add_tracks(self, tracks, play=False):
        if not tracks:
            self.set_status("⚠ 추가할 곡이 없어요")
            return
        first_new = len(self.items)
        self.items.extend(tracks)
        self.save_queue()
        self.refresh_list()
        self.set_status(f"추가됨: {tracks[0].title[:30]}" + (f" 외 {len(tracks) - 1}곡" if len(tracks) > 1 else ""))
        if play or (self.state == "idle" and self.current == -1):
            self.play_index(first_new)

    def move(self, i, d):
        j = i + d
        if not (0 <= j < len(self.items)):
            return
        self.items[i], self.items[j] = self.items[j], self.items[i]
        if self.current == i:
            self.current = j
        elif self.current == j:
            self.current = i
        self.save_queue()
        self.refresh_list()

    def remove(self, i):
        self.items.pop(i)
        self.save_queue()
        if i < self.current:
            self.current -= 1
        elif i == self.current:
            self.stop()
            self.current = -1
            self.refresh_list()
            if i < len(self.items):
                self.play_index(i)
            return
        self.refresh_list()

    # ── 재생 ──
    def play_index(self, i):
        if not (0 <= i < len(self.items)):
            return
        if yt_dlp is None:
            self.set_status("⚠ yt-dlp 가 없어요")
            return
        self.engine.stop()
        self.current = i
        self.token += 1
        self.state = "loading"
        self.refresh_now()
        self.refresh_list()
        self.set_status("불러오는 중...")
        threading.Thread(target=self._resolve_and_play, args=(self.token, self.items[i]), daemon=True).start()

    def _trim_cache(self, keep_path, keep=15):
        """내려받은 음악 파일이 쌓이지 않게 오래된 것부터 지움"""
        try:
            d = os.path.join(self.data_dir, "cache")
            files = [os.path.join(d, f) for f in os.listdir(d)]
            files = [f for f in files if os.path.isfile(f) and f != keep_path]
            files.sort(key=os.path.getmtime, reverse=True)
            for f in files[keep - 1:]:
                try:
                    os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass

    def _resolve_and_play(self, token, track):
        cache = os.path.join(self.data_dir, "cache")
        os.makedirs(cache, exist_ok=True)
        last = [-10]

        def hook(d):
            if token != self.token:
                raise RuntimeError("cancelled")  # 다른 곡을 눌렀으면 다운로드 중단
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total:
                    pct = int(d.get("downloaded_bytes", 0) * 100 / total)
                    if pct - last[0] >= 10:
                        last[0] = pct
                        self.set_status(f"불러오는 중... {pct}%")

        opts = {"format": "bestaudio[ext=m4a]/bestaudio/best", "quiet": True, "no_warnings": True,
                "noplaylist": True, "cachedir": False, "socket_timeout": 20, "retries": 5,
                "outtmpl": os.path.join(cache, "%(id)s.%(ext)s"), "progress_hooks": [hook]}
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(track.url, download=True)
                path = y.prepare_filename(info)
            if not os.path.exists(path):
                raise RuntimeError("내려받은 파일을 찾지 못했어요")
            if token != self.token:
                return
            os.utime(path, None)
            ok = self.engine.load(path, {}, lambda: token == self.token)
            if ok:
                self.on_started(token, info.get("duration"))
                self._trim_cache(path)
        except Exception as e:
            if token == self.token:
                self.on_play_error(token, f"{type(e).__name__}: {str(e)[:200]}")

    @mainthread
    def on_started(self, token, dur):
        if token != self.token:
            return
        self.state = "playing"
        if dur and 0 <= self.current < len(self.items):
            self.items[self.current].duration = int(dur)
            self.save_queue()
        self.refresh_now()
        self.set_status(f"재생 중: {self.items[self.current].title[:40]}")

    @mainthread
    def on_play_error(self, token, msg):
        if token != self.token:
            return
        self.state = "idle"
        self.refresh_now()
        self.set_status(f"⚠ 재생 실패 - {msg}"[:230])
        if self.current + 1 < len(self.items):
            Clock.schedule_once(lambda dt: self.next_track(auto=True) if token == self.token else None, 1.5)

    def toggle_play(self):
        if self.state == "loading":
            return
        if self.state == "playing":
            self.engine.pause()
            self.state = "paused"
        elif self.state == "paused":
            self.engine.resume()
            self.state = "playing"
        elif self.items:
            self.play_index(max(self.current, 0))
        self.w_play.set_icon("pause" if self.state == "playing" else "play")

    def stop(self):
        self.token += 1
        self.engine.stop()
        self.state = "idle"
        self.refresh_now()

    def next_track(self, auto=False):
        if not self.items:
            return
        if self.shuffle and len(self.items) > 1:
            return self.play_index(random.choice([i for i in range(len(self.items)) if i != self.current]))
        nxt = self.current + 1
        if nxt >= len(self.items):
            if self.repeat == 1 or not auto:
                nxt = 0
            else:
                self.stop()
                self.current = -1
                self.refresh_list()
                self.set_status("대기열 재생이 끝났어요")
                return
        self.play_index(nxt)

    def prev_track(self):
        if not self.items:
            return
        if self.state in ("playing", "paused") and self.engine.position() > 3000:
            self.engine.seek(0)
            return
        self.play_index((self.current - 1) % len(self.items))

    def toggle_shuffle(self, *a):
        self.shuffle = not self.shuffle
        self.style_toggles()

    def toggle_repeat(self, *a):
        self.repeat = (self.repeat + 1) % 3
        self.style_toggles()

    def on_seek(self, value):
        if self.state not in ("playing", "paused"):
            return
        dur = self.engine.duration()
        if dur <= 0 and 0 <= self.current < len(self.items):
            dur = self.items[self.current].duration * 1000
        if dur > 0:
            self.engine.seek(value * dur)

    def tick(self, dt):
        if self.state not in ("playing", "paused"):
            return
        st = self.engine.state()
        if st == "ended":
            self.state = "idle"
            if self.repeat == 2:
                self.play_index(self.current)
            else:
                self.next_track(auto=True)
            return
        if st == "error":
            self.state = "idle"
            self.refresh_now()
            self.set_status("⚠ 재생 중 오류가 발생했어요")
            if self.current + 1 < len(self.items):
                self.next_track(auto=True)
            return
        pos, dur = self.engine.position(), self.engine.duration()
        if dur <= 0 and 0 <= self.current < len(self.items):
            dur = self.items[self.current].duration * 1000
        if dur > 0 and not self.w_seek.dragging:
            self.w_seek.value = min(pos / dur, 1)
            self.w_cur.text = fmt_time(pos / 1000)
            self.w_total.text = fmt_time(dur / 1000)
        self.w_play.set_icon("pause" if st == "playing" else "play")

    # ── yt-dlp 업데이트 ──
    def update_ytdlp(self, *a):
        self.set_status("yt-dlp 최신 버전 받는 중...")
        threading.Thread(target=self._do_update, daemon=True).start()

    def _do_update(self):
        try:
            os.makedirs(self.ytdlp_dir, exist_ok=True)
            dst = os.path.join(self.ytdlp_dir, "yt-dlp")
            tmp = dst + ".tmp"
            req = urllib.request.Request(YTDLP_UPDATE_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            if not zipfile.is_zipfile(tmp) or os.path.getsize(tmp) < 500_000:
                raise RuntimeError("받은 파일이 올바르지 않아요")
            os.replace(tmp, dst)
            self.set_status("✅ 업데이트 완료! 앱을 완전히 종료 후 다시 켜면 적용돼요")
        except Exception as e:
            self.set_status(f"⚠ 업데이트 실패: {str(e)[:100]}")


if __name__ == "__main__":
    MusicApp().run()
