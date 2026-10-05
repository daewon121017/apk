"""
공용 재생 로직 — 앱 화면(main.py)과 백그라운드 서비스(service.py)가 같이 쓴다.
kivy 에 의존하지 않는다.

- Brain         : 대기열 / 다음 곡 / 셔플 / 반복 / 다운로드 / 미리받기를 전부 책임지는 두뇌
- 엔진          : AndroidEngine(안드로이드 MediaPlayer), VlcEngine(PC 테스트), NullEngine
- CommandServer : 화면 앱이 서비스에 명령을 보내는 통로 (127.0.0.1 로컬 TCP)
"""
import json
import os
import random
import secrets
import socket
import sys
import threading
import time
from dataclasses import asdict, dataclass

try:  # 안드로이드에서 https 인증서 인식용
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass


def log_tail(data_dir, n=3):
    try:
        with open(os.path.join(data_dir, "service.log"), "r", encoding="utf-8") as f:
            return " | ".join(f.read().strip().splitlines()[-n:])
    except Exception:
        return ""


def log(data_dir, msg):
    """service.log 에 한 줄 남김 (문제 생겼을 때 원인 찾기용)"""
    try:
        p = os.path.join(data_dir, "service.log")
        if os.path.exists(p) and os.path.getsize(p) > 200_000:
            os.remove(p)
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


@dataclass
class Track:
    title: str
    url: str
    channel: str = ""
    duration: int = 0
    thumb: str = ""


def track_from_dict(d):
    keys = Track.__dataclass_fields__.keys()
    return Track(**{k: v for k, v in d.items() if k in keys})


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


# ───────────── yt-dlp 로드 (앱 안에서 업데이트한 판 우선) ─────────────
def load_ytdlp(update_dir):
    upd = os.path.join(update_dir, "yt-dlp")
    if os.path.isfile(upd):
        sys.path.insert(0, upd)
        try:
            import yt_dlp as mod
            return mod
        except Exception:
            sys.path.remove(upd)
            for k in [k for k in sys.modules if k == "yt_dlp" or k.startswith("yt_dlp.")]:
                del sys.modules[k]
    try:
        import yt_dlp as mod
        return mod
    except Exception:
        return None


def ytdlp_version():
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:
        return "?"


# ───────────── 재생 엔진 ─────────────
class AndroidEngine:
    """안드로이드 내장 MediaPlayer (pyjnius). 화면 앱/서비스 어디서든 쓸 수 있다."""

    def __init__(self):
        from jnius import autoclass
        self._MP = autoclass("android.media.MediaPlayer")
        self._Uri = autoclass("android.net.Uri")
        self._Map = autoclass("java.util.HashMap")
        if os.environ.get("PYTHON_SERVICE_ARGUMENT") is not None:  # 서비스 프로세스
            self._ctx = autoclass("org.kivy.android.PythonService").mService
        else:
            self._ctx = autoclass("org.kivy.android.PythonActivity").mActivity
        self.mp = None
        self.user_paused = False
        self.completed = False
        self._volume = 1.0
        self._lock = threading.Lock()
        self._listener = self._make_listener()  # 만든 스레드(메인)에서 미리 — 다른 스레드에선 자바 클래스 찾기가 막힘

    def _make_listener(self):
        try:
            from jnius import PythonJavaClass, java_method
            eng = self

            class Done(PythonJavaClass):
                __javainterfaces__ = ["android/media/MediaPlayer$OnCompletionListener"]

                @java_method("(Landroid/media/MediaPlayer;)V")
                def onCompletion(self, mp):
                    eng.completed = True

            return Done()
        except Exception:
            return None

    def load(self, source, headers, still_valid):
        """블로킹 호출 — 반드시 작업 스레드에서."""
        mp = self._MP()
        try:
            mp.setAudioStreamType(3)  # STREAM_MUSIC
            try:
                mp.setWakeMode(self._ctx, 1)  # PARTIAL_WAKE_LOCK (화면 꺼져도 CPU 유지)
            except Exception:
                pass
            if str(source).startswith(("http://", "https://")):
                hm = self._Map()
                for k, v in (headers or {}).items():
                    if str(k).lower() != "accept-encoding":
                        hm.put(str(k), str(v))
                mp.setDataSource(self._ctx, self._Uri.parse(source), hm)
            else:  # 내려받은 로컬 파일
                mp.setDataSource(str(source))
            mp.prepare()
        except Exception:
            self._release(mp)
            raise
        if not still_valid():
            self._release(mp)
            return False
        if self._listener is not None:
            try:
                mp.setOnCompletionListener(self._listener)
            except Exception:
                pass
        try:
            mp.setVolume(self._volume, self._volume)
        except Exception:
            pass
        with self._lock:
            old, self.mp = self.mp, mp
            self.user_paused = False
            self.completed = False
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

    def set_volume(self, v):  # 0~100 (플레이어 자체 볼륨, 기기 볼륨과 별개)
        self._volume = max(0.0, min(1.0, float(v) / 100.0))
        try:
            if self.mp:
                self.mp.setVolume(self._volume, self._volume)
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
            self.completed = False
        self._release(old)

    def state(self):
        mp = self.mp
        if not mp:
            return "idle"
        if self.completed:
            return "ended"
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

    def load(self, source, headers, still_valid):
        if not still_valid():
            return False
        if os.path.exists(str(source)):
            self.p.set_media(self.inst.media_new_path(source))
        else:
            self.p.set_media(self.inst.media_new(source))
        self.p.play()
        return True

    def set_volume(self, v):
        self.p.audio_set_volume(int(v))

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

    def set_volume(self, v): pass
    def pause(self): pass
    def resume(self): pass
    def seek(self, ms): pass
    def position(self): return 0
    def duration(self): return 0
    def stop(self): pass
    def state(self): return "idle"


def make_engine(on_android):
    try:
        return AndroidEngine() if on_android else VlcEngine()
    except Exception as e:
        print("engine error:", e)
        return NullEngine()


# ───────────── 두뇌 ─────────────
class Brain:
    CACHE_KEEP = 20

    def __init__(self, data_dir, engine, ytdlp, on_change=None):
        self.data_dir = data_dir
        self.cache_dir = os.path.join(data_dir, "cache")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.engine = engine
        self.yt = ytdlp
        self.yt_ready = threading.Event()
        if ytdlp is not None:
            self.yt_ready.set()
        self.on_change = on_change or (lambda: None)
        self.lock = threading.RLock()
        self.items = []
        self.current = -1
        self.state = "idle"  # idle / loading / playing / paused
        self.token = 0
        self.shuffle = False
        self.repeat = 0  # 0 끔, 1 전체, 2 한곡
        self.volume = 100
        self.status = ""
        self.rev = 1  # 곡 목록이 바뀔 때마다 증가
        self._paths = {}
        self._dl_locks = {}
        self._prefetching = set()
        self._next_pick = None
        self._load_saved()
        try:
            self.engine.set_volume(self.volume)
        except Exception:
            pass
        threading.Thread(target=self._loop, daemon=True).start()

    # ── 저장 / 복원 ──
    def _state_path(self):
        return os.path.join(self.data_dir, "player.json")

    def _load_saved(self):
        data = None
        for name in ("player.json", "queue.json"):  # queue.json = 예전 버전이 쓰던 파일
            try:
                with open(os.path.join(self.data_dir, name), "r", encoding="utf-8") as f:
                    data = json.load(f)
                break
            except Exception:
                continue
        if data is None:
            return
        raw = data if isinstance(data, list) else data.get("items", [])
        try:
            self.items = [track_from_dict(d) for d in raw]
        except Exception:
            self.items = []
        if isinstance(data, dict):
            self.volume = int(data.get("volume", 100))
            self.shuffle = bool(data.get("shuffle", False))
            self.repeat = int(data.get("repeat", 0))

    def _save(self):
        try:
            data = {"items": [asdict(t) for t in self.items], "volume": self.volume,
                    "shuffle": self.shuffle, "repeat": self.repeat}
            tmp = self._state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self._state_path())
        except Exception:
            pass

    def _changed(self):
        try:
            self.on_change()
        except Exception:
            pass

    def _items_changed(self):
        self.rev += 1
        self._next_pick = None
        self._save()

    def set_status(self, text):
        self.status = text

    # ── 화면/알림용 상태 ──
    def snapshot(self, known_rev=-1):
        with self.lock:
            cur = self.items[self.current] if 0 <= self.current < len(self.items) else None
            state = self.state
            snap = dict(rev=self.rev, state=state, current=self.current, shuffle=self.shuffle,
                        repeat=self.repeat, volume=self.volume, status=self.status,
                        title=cur.title if cur else "", channel=cur.channel if cur else "",
                        thumb=cur.thumb if cur else "", n=len(self.items), pos=0, dur=0)
            if known_rev != self.rev:
                snap["items"] = [asdict(t) for t in self.items]
        if cur is not None and state in ("playing", "paused"):
            snap["pos"] = self.engine.position()
            snap["dur"] = self.engine.duration() or cur.duration * 1000
        elif cur is not None:
            snap["dur"] = cur.duration * 1000
        return snap

    ALLOWED = {"snap", "add", "play", "toggle", "next", "prev", "seek", "move", "remove",
               "clear", "shuffle", "repeat", "volume", "stop", "quit"}

    def handle(self, msg):
        """화면 앱(또는 같은 프로세스의 UI)이 보낸 명령 처리 → 최신 상태 반환"""
        cmd = msg.get("cmd")
        args = msg.get("args") or {}
        if cmd in self.ALLOWED and cmd not in ("snap", "quit"):
            try:
                getattr(self, "cmd_" + cmd)(**args)
            except Exception as e:
                self.set_status(f"⚠ {type(e).__name__}: {str(e)[:150]}")
        return self.snapshot(msg.get("rev", -1))

    # ── 명령 ──
    def cmd_add(self, tracks, play=False):
        new = [track_from_dict(t) for t in tracks]
        if not new:
            self.set_status("⚠ 추가할 곡이 없어요")
            return
        with self.lock:
            first = len(self.items)
            self.items.extend(new)
            self._items_changed()
            autoplay = play or (self.current == -1 and self.state == "idle")
        self.set_status(f"추가됨: {new[0].title[:30]}" + (f" 외 {len(new) - 1}곡" if len(new) > 1 else ""))
        if autoplay:
            self.cmd_play(first)
        else:
            self._changed()

    def cmd_play(self, i):
        with self.lock:
            if not (0 <= i < len(self.items)):
                return
            self.engine.stop()
            self.current = i
            self.token += 1
            token = self.token
            self.state = "loading"
            track = self.items[i]
        self.set_status("불러오는 중...")
        self._changed()
        threading.Thread(target=self._resolve_and_play, args=(token, track), daemon=True).start()

    def cmd_toggle(self):
        with self.lock:
            st = self.state
        if st == "loading":
            return
        if st == "playing":
            self.engine.pause()
            with self.lock:
                self.state = "paused"
        elif st == "paused":
            self.engine.resume()
            with self.lock:
                self.state = "playing"
        elif self.items:
            self.cmd_play(max(self.current, 0))
            return
        self._changed()

    def cmd_stop(self):
        with self.lock:
            self.token += 1
            self.engine.stop()
            self.state = "idle"
        self._changed()

    def cmd_next(self, auto=False):
        with self.lock:
            n = len(self.items)
            if n == 0:
                return
            target = None
            if self.shuffle and n > 1:
                pick = self._next_pick
                if pick is None or not (0 <= pick < n) or pick == self.current:
                    pick = random.choice([i for i in range(n) if i != self.current])
                self._next_pick = None
                target = pick
            else:
                nxt = self.current + 1
                if nxt >= n:
                    nxt = 0 if (self.repeat == 1 or not auto) else None
                target = nxt
        if target is None:
            self.cmd_stop()
            with self.lock:
                self.current = -1
            self.set_status("대기열 재생이 끝났어요")
            self._changed()
            return
        self.cmd_play(target)

    def cmd_prev(self):
        with self.lock:
            n = len(self.items)
            st = self.state
        if n == 0:
            return
        if st in ("playing", "paused") and self.engine.position() > 3000:
            self.engine.seek(0)
            return
        self.cmd_play((self.current - 1) % n)

    def cmd_seek(self, f):
        with self.lock:
            if self.state not in ("playing", "paused"):
                return
            cur = self.items[self.current] if 0 <= self.current < len(self.items) else None
        dur = self.engine.duration() or (cur.duration * 1000 if cur else 0)
        if dur > 0:
            self.engine.seek(max(0.0, min(1.0, float(f))) * dur)

    def cmd_move(self, i, d):
        with self.lock:
            j = i + d
            if not (0 <= i < len(self.items) and 0 <= j < len(self.items)):
                return
            self.items[i], self.items[j] = self.items[j], self.items[i]
            if self.current == i:
                self.current = j
            elif self.current == j:
                self.current = i
            self._items_changed()
        self._changed()

    def cmd_remove(self, i):
        replay = None
        with self.lock:
            if not (0 <= i < len(self.items)):
                return
            self.items.pop(i)
            if i < self.current:
                self.current -= 1
            elif i == self.current:
                self.token += 1
                self.engine.stop()
                self.state = "idle"
                self.current = -1
                if i < len(self.items):
                    replay = i
            self._items_changed()
        if replay is not None:
            self.cmd_play(replay)
        else:
            self._changed()

    def cmd_clear(self):
        with self.lock:
            self.token += 1
            self.engine.stop()
            self.state = "idle"
            self.items.clear()
            self.current = -1
            self._items_changed()
        self.set_status("대기열을 비웠어요")
        self._changed()

    def cmd_shuffle(self):
        with self.lock:
            self.shuffle = not self.shuffle
            self._next_pick = None
        self._save()
        self._changed()

    def cmd_repeat(self):
        with self.lock:
            self.repeat = (self.repeat + 1) % 3
        self._save()
        self._changed()

    def cmd_volume(self, v):
        self.volume = max(0, min(100, int(v)))
        try:
            self.engine.set_volume(self.volume)
        except Exception:
            pass
        self._save()

    # ── 다운로드 / 재생 ──
    def _dl_lock(self, key):
        with self.lock:
            return self._dl_locks.setdefault(key, threading.Lock())

    def _download(self, track, token=None):
        """yt-dlp 로 오디오를 내려받아 로컬 파일 경로를 돌려줌 (같은 곡은 한 번만)"""
        if self.yt is None:
            self.yt_ready.wait(60)  # 서비스가 yt-dlp 를 아직 불러오는 중일 수 있음
        if self.yt is None:
            raise RuntimeError("yt-dlp 를 불러오지 못했어요")
        key = track.url
        with self._dl_lock(key):
            p = self._paths.get(key)
            if p and os.path.exists(p):
                return p
            last = [-10]

            def hook(d):
                if token is not None and token != self.token:
                    raise RuntimeError("cancelled")  # 다른 곡을 눌렀으면 다운로드 중단
                if token is not None and d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    if total:
                        pct = int(d.get("downloaded_bytes", 0) * 100 / total)
                        if pct - last[0] >= 10:
                            last[0] = pct
                            self.set_status(f"불러오는 중... {pct}%")

            opts = {"format": "bestaudio[ext=m4a]/bestaudio/best", "quiet": True, "no_warnings": True,
                    "noplaylist": True, "cachedir": False, "socket_timeout": 20, "retries": 5,
                    "outtmpl": os.path.join(self.cache_dir, "%(id)s.%(ext)s"),
                    "progress_hooks": [hook]}
            with self.yt.YoutubeDL(opts) as y:
                info = y.extract_info(track.url, download=True)
                path = y.prepare_filename(info)
            if not os.path.exists(path):
                raise RuntimeError("내려받은 파일을 찾지 못했어요")
            dur = info.get("duration")
            if dur and not track.duration:
                track.duration = int(dur)
                with self.lock:
                    self._items_changed()
            self._paths[key] = path
            return path

    def _resolve_and_play(self, token, track):
        try:
            path = self._download(track, token)
            if token != self.token:
                return
            os.utime(path, None)
            ok = self.engine.load(path, {}, lambda: token == self.token)
            if not ok:
                return
            with self.lock:
                if token != self.token:
                    return
                self.state = "playing"
            self.set_status(f"재생 중: {track.title[:40]}")
            self._changed()
            self._trim_cache()
            self._prefetch_next()
        except Exception as e:
            if token == self.token:
                self._on_play_error(token, f"{type(e).__name__}: {str(e)[:200]}")

    def _on_play_error(self, token, msg):
        with self.lock:
            if token != self.token:
                return
            self.state = "idle"
            has_next = self.current + 1 < len(self.items)
        self.set_status(f"⚠ 재생 실패 - {msg}"[:230])
        self._changed()
        if has_next:
            def later():
                time.sleep(1.5)
                if token == self.token:
                    self.cmd_next(auto=True)
            threading.Thread(target=later, daemon=True).start()

    def _prefetch_next(self):
        """다음 곡을 미리 받아 둠 → 화면이 꺼져 있어도 곡 전환이 끊기지 않음"""
        with self.lock:
            n = len(self.items)
            if n < 2 and self.repeat != 1:
                return
            if self.repeat == 2:
                return
            if self.shuffle and n > 1:
                pick = random.choice([i for i in range(n) if i != self.current])
                self._next_pick = pick
            else:
                pick = self.current + 1
                if pick >= n:
                    if self.repeat != 1:
                        return
                    pick = 0
            if not (0 <= pick < n) or pick == self.current:
                return
            track = self.items[pick]
            key = track.url
            cached = self._paths.get(key)
            if (cached and os.path.exists(cached)) or key in self._prefetching:
                return
            self._prefetching.add(key)

        def run():
            try:
                self._download(track, None)
            except Exception as e:
                log(self.data_dir, f"prefetch fail: {e}")
            finally:
                with self.lock:
                    self._prefetching.discard(key)

        threading.Thread(target=run, daemon=True).start()

    def _trim_cache(self):
        """내려받은 음악 파일이 쌓이지 않게 오래된 것부터 지움"""
        try:
            files = [os.path.join(self.cache_dir, f) for f in os.listdir(self.cache_dir)]
            files = [f for f in files if os.path.isfile(f) and not f.endswith(".part")]
            files.sort(key=os.path.getmtime, reverse=True)
            for f in files[self.CACHE_KEEP:]:
                try:
                    os.remove(f)
                except Exception:
                    pass
        except Exception:
            pass

    # ── 감시 루프: 곡이 끝났는지 지켜보고 다음 곡으로 (화면과 무관하게 계속 돈다) ──
    def _loop(self):
        while True:
            time.sleep(0.4)
            try:
                self._tick()
            except Exception as e:
                log(self.data_dir, f"tick error: {e}")

    def _tick(self):
        with self.lock:
            st = self.state
            token = self.token
        if st not in ("playing", "paused"):
            return
        es = self.engine.state()
        if es == "ended":
            with self.lock:
                if token != self.token:
                    return
                self.state = "idle"
                repeat_one = self.repeat == 2
                cur = self.current
            if repeat_one:
                self.cmd_play(cur)
            else:
                self.cmd_next(auto=True)
        elif es == "error":
            with self.lock:
                if token != self.token:
                    return
            self._on_play_error(token, "재생 중 오류가 발생했어요")
        elif es in ("playing", "paused") and es != st:
            with self.lock:
                if token == self.token and self.state in ("playing", "paused"):
                    self.state = es
            self._changed()


# ───────────── 화면 앱 ↔ 서비스 통로 (로컬 TCP) ─────────────
def _info_path(data_dir):
    return os.path.join(data_dir, "service.json")


class CommandServer:
    def __init__(self, brain, data_dir, on_quit):
        import socketserver
        outer = self
        self.secret = secrets.token_hex(8)
        self.on_quit = on_quit

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    line = self.rfile.readline(1 << 20)
                    msg = json.loads(line.decode("utf-8"))
                    if msg.get("secret") != outer.secret:
                        resp = {"error": "auth"}
                    else:
                        resp = brain.handle(msg)
                        if msg.get("cmd") == "quit":
                            outer.on_quit()
                    self.wfile.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
                except Exception as e:
                    log(data_dir, f"server error: {e}")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.srv = Server(("127.0.0.1", 0), Handler)
        port = self.srv.server_address[1]
        tmp = _info_path(data_dir) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"port": port, "secret": self.secret, "pid": os.getpid()}, f)
        os.replace(tmp, _info_path(data_dir))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


def rpc(data_dir, cmd, args=None, rev=-1, timeout=3.0):
    """서비스에 명령 하나 보내고 최신 상태를 받아온다. 연결 안 되면 예외."""
    with open(_info_path(data_dir), "r", encoding="utf-8") as f:
        info = json.load(f)
    msg = {"secret": info["secret"], "cmd": cmd, "args": args or {}, "rev": rev}
    s = socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout)
    try:
        s.settimeout(timeout)
        s.sendall((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    resp = json.loads(buf.decode("utf-8"))
    if "error" in resp:
        raise ConnectionError(resp["error"])
    return resp
