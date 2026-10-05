"""
백그라운드 음악 서비스 (안드로이드 포그라운드 서비스)

- 화면 앱이 꺼져 있거나 화면이 잠겨 있어도 음악 재생 / 다음 곡 전환을 계속한다.
- 알림창에 이전 / 재생·일시정지 / 다음 / 닫기 버튼이 뜬다.
- 화면 앱(main.py)은 core.rpc() 로 이 서비스에 명령을 보낸다.
"""
import os
import threading
import time
import traceback

CHANNEL_ID = "mymusic_playback"
NOTIF_ID = 4242  # p4a 기본 알림("Player")과 다른 번호 → startForeground 로 교체되어 기본 알림은 사라짐


class Notifier:
    def __init__(self, brain, data_dir, on_close):
        from jnius import autoclass, cast
        self.autoclass = autoclass
        self.brain = brain
        self.data_dir = data_dir
        self.on_close = on_close
        self.lock = threading.Lock()
        self.last_key = None
        self.started = False

        PythonService = autoclass("org.kivy.android.PythonService")
        self.svc = PythonService.mService
        self.pkg = self.svc.getPackageName()
        Context = autoclass("android.content.Context")
        self.nm = cast("android.app.NotificationManager",
                       self.svc.getSystemService(Context.NOTIFICATION_SERVICE))
        self.sdk = autoclass("android.os.Build$VERSION").SDK_INT
        self.Builder = autoclass("android.app.Notification$Builder")
        self.PendingIntent = autoclass("android.app.PendingIntent")
        self.Intent = autoclass("android.content.Intent")
        self.R = autoclass("android.R$drawable")

        if self.sdk >= 26:
            NChannel = autoclass("android.app.NotificationChannel")
            NManager = autoclass("android.app.NotificationManager")
            ch = NChannel(CHANNEL_ID, "음악 재생", NManager.IMPORTANCE_LOW)
            try:
                ch.setShowBadge(False)
            except Exception:
                pass
            self.nm.createNotificationChannel(ch)

        self.art_cache = {}   # 썸네일 url -> Bitmap
        self.art_loading = set()
        self.session = None
        try:  # 삼성/안드로이드 "미디어" 패널과 잠금화면에 제대로 뜨게 하는 미디어 세션
            MediaSession = autoclass("android.media.session.MediaSession")
            self.session = MediaSession(self.svc, "MyMusic")
            self.session.setActive(True)
            self.PS = autoclass("android.media.session.PlaybackState")
            self.PSB = autoclass("android.media.session.PlaybackState$Builder")
            self.MMB = autoclass("android.media.MediaMetadata$Builder")
        except Exception:
            self.session = None
            self._log("media session fail:\n" + traceback.format_exc())

        self.A_PREV = self.pkg + ".MUSIC_PREV"
        self.A_TOGGLE = self.pkg + ".MUSIC_TOGGLE"
        self.A_NEXT = self.pkg + ".MUSIC_NEXT"
        self.A_CLOSE = self.pkg + ".MUSIC_CLOSE"

        # 알림 버튼 → 브로드캐스트 → 여기서 받아서 처리
        try:
            from android.broadcast import BroadcastReceiver
            self.receiver = BroadcastReceiver(
                self.on_broadcast, actions=[self.A_PREV, self.A_TOGGLE, self.A_NEXT, self.A_CLOSE])
            self.receiver.start()
        except Exception:
            self.receiver = None
            self._log("receiver fail:\n" + traceback.format_exc())
            self.brain.set_status("⚠ 알림 버튼 연결 실패 (재생은 정상)")

    def _log(self, msg):
        try:
            from core import log
            log(self.data_dir, msg)
        except Exception:
            pass

    def on_broadcast(self, context, intent):
        try:
            a = intent.getAction()
            if a == self.A_TOGGLE:
                self.brain.cmd_toggle()
            elif a == self.A_NEXT:
                self.brain.cmd_next()
            elif a == self.A_PREV:
                self.brain.cmd_prev()
            elif a == self.A_CLOSE:
                self.on_close()
        except Exception:
            self._log("broadcast error:\n" + traceback.format_exc())

    def _flags(self):
        f = self.PendingIntent.FLAG_UPDATE_CURRENT
        if self.sdk >= 23:
            f |= self.PendingIntent.FLAG_IMMUTABLE
        return f

    def _action_pi(self, action, code):
        i = self.Intent(action)
        i.setPackage(self.pkg)
        return self.PendingIntent.getBroadcast(self.svc, code, i, self._flags())

    def _open_app_pi(self):
        li = self.svc.getPackageManager().getLaunchIntentForPackage(self.pkg)
        li.setFlags(self.Intent.FLAG_ACTIVITY_NEW_TASK | self.Intent.FLAG_ACTIVITY_SINGLE_TOP)
        return self.PendingIntent.getActivity(self.svc, 0, li, self._flags())

    def _update_session(self, snap, playing, bmp):
        if self.session is None:
            return
        try:
            md = self.MMB()
            md.putString("android.media.metadata.TITLE", snap["title"] or "My Music")
            md.putString("android.media.metadata.ARTIST", snap["channel"] or "")
            if snap.get("dur"):
                md.putLong("android.media.metadata.DURATION", int(snap["dur"]))
            if bmp is not None:
                md.putBitmap("android.media.metadata.ALBUM_ART", bmp)
            self.session.setMetadata(md.build())
            PS = self.PS
            st = snap["state"]
            code = PS.STATE_PLAYING if playing else (
                PS.STATE_BUFFERING if st == "loading" else (PS.STATE_PAUSED if snap["title"] else PS.STATE_NONE))
            acts = (PS.ACTION_PLAY | PS.ACTION_PAUSE | PS.ACTION_PLAY_PAUSE |
                    PS.ACTION_SKIP_TO_NEXT | PS.ACTION_SKIP_TO_PREVIOUS)
            b = self.PSB()
            b.setActions(acts)
            b.setState(code, int(snap.get("pos") or 0), 1.0 if playing else 0.0)
            self.session.setPlaybackState(b.build())
        except Exception:
            self._log("session update error:\n" + traceback.format_exc())

    def _build(self, snap, title, text, playing, bmp):
        b = self.Builder(self.svc, CHANNEL_ID) if self.sdk >= 26 else self.Builder(self.svc)
        icon = self.svc.getApplicationInfo().icon or self.R.ic_media_play
        b.setSmallIcon(icon)
        b.setContentTitle(title)
        b.setContentText(text)
        b.setContentIntent(self._open_app_pi())
        b.setOngoing(True)
        b.setOnlyAlertOnce(True)
        b.setShowWhen(False)
        if bmp is not None:
            try:
                b.setLargeIcon(bmp)
            except Exception:
                pass
        try:
            b.setVisibility(1)  # 잠금화면에도 표시
        except Exception:
            pass
        b.addAction(self.R.ic_media_previous, "이전", self._action_pi(self.A_PREV, 11))
        b.addAction(self.R.ic_media_pause if playing else self.R.ic_media_play,
                    "일시정지" if playing else "재생", self._action_pi(self.A_TOGGLE, 12))
        b.addAction(self.R.ic_media_next, "다음", self._action_pi(self.A_NEXT, 13))
        b.addAction(self.R.ic_menu_close_clear_cancel, "닫기", self._action_pi(self.A_CLOSE, 14))
        try:
            style = self.autoclass("android.app.Notification$MediaStyle")()
            if self.session is not None:
                style.setMediaSession(self.session.getSessionToken())
            try:
                style.setShowActionsInCompactView([0, 1, 2])
            except Exception:
                pass
            b.setStyle(style)
        except Exception:
            self._log("style fail:\n" + traceback.format_exc())
        return b.build()

    def _fetch_art(self, url):
        """썸네일을 뒤에서 받아와 Bitmap 으로 만든 뒤 알림을 다시 그림"""
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=10).read()
            BF = self.autoclass("android.graphics.BitmapFactory")
            bmp = BF.decodeByteArray(data, 0, len(data))
            if bmp is not None:
                self.art_cache[url] = bmp
        except Exception:
            self.art_cache[url] = None
            self._log("art fetch fail:\n" + traceback.format_exc())
        finally:
            self.art_loading.discard(url)
        self.refresh(force=True)

    def refresh(self, force=False):
        try:
            s = self.brain.snapshot(self.brain.rev)
            state = s["state"]
            title = s["title"] or "My Music"
            if state == "loading":
                text = s["status"] or "불러오는 중..."
            elif s["title"]:
                text = s["channel"] or ""
            else:
                text = s["status"] or "재생할 곡을 골라 주세요"
            playing = state == "playing"
            url = s.get("thumb") or ""
            bmp = self.art_cache.get(url) if url else None
            if url and url not in self.art_cache and url not in self.art_loading:
                self.art_loading.add(url)
                threading.Thread(target=self._fetch_art, args=(url,), daemon=True).start()
            key = (state, title, text, bmp is not None)
            with self.lock:
                if key == self.last_key and not force:
                    return
                self.last_key = key
                self._update_session(s, playing, bmp)
                n = self._build(s, title, text, playing, bmp)
                # 항상 startForeground 로 올려서 p4a 기본 알림("Player")을 우리 알림으로 교체
                self.svc.startForeground(NOTIF_ID, n)
                self.started = True
        except Exception as e:
            err = traceback.format_exc()
            self._log("notify error:\n" + err)
            try:
                self.brain.set_status(f"⚠ 알림 오류: {type(e).__name__} {str(e)[:120]}")
            except Exception:
                pass

    def remove(self):
        try:
            self.svc.stopForeground(True)
        except Exception:
            pass
        try:
            self.nm.cancel(NOTIF_ID)
        except Exception:
            pass
        try:
            if self.session is not None:
                self.session.setActive(False)
                self.session.release()
        except Exception:
            pass


def main():
    data_dir = os.environ.get("PYTHON_SERVICE_ARGUMENT") or ""
    if not data_dir or not os.path.isdir(data_dir):
        data_dir = os.environ.get("ANDROID_PRIVATE") or "."
    import core
    core.log(data_dir, "service start, yt-dlp dir=" + data_dir)

    holder = {}

    def on_change():
        n = holder.get("notifier")
        if n:
            n.refresh()

    engine = core.make_engine(True)
    brain = core.Brain(data_dir, engine, None, on_change=on_change)

    def load_yt():  # yt-dlp 는 불러오는 데 오래 걸려서 뒤에서 따로 (서비스는 먼저 열어 둠)
        try:
            brain.yt = core.load_ytdlp(os.path.join(data_dir, "ytdlp"))
        except Exception:
            core.log(data_dir, "yt-dlp load fail:\n" + traceback.format_exc())
        brain.yt_ready.set()
        core.log(data_dir, "yt-dlp loaded: %s" % (brain.yt is not None))
    threading.Thread(target=load_yt, daemon=True).start()

    def shutdown():
        core.log(data_dir, "service shutdown")
        try:
            brain.cmd_stop()
        except Exception:
            pass
        try:
            os.remove(os.path.join(data_dir, "service.json"))
        except Exception:
            pass
        n = holder.get("notifier")
        if n:
            n.remove()
        try:
            from jnius import autoclass
            autoclass("org.kivy.android.PythonService").mService.stopSelf()
        except Exception:
            pass
        threading.Timer(2.0, lambda: os._exit(0)).start()

    core.CommandServer(brain, data_dir, lambda: threading.Timer(0.3, shutdown).start())
    core.log(data_dir, "service ready")

    try:
        notifier = Notifier(brain, data_dir, shutdown)
        holder["notifier"] = notifier
        notifier.refresh(force=True)
    except Exception as e:
        core.log(data_dir, "notifier init fail:\n" + traceback.format_exc())
        brain.set_status(f"⚠ 알림 만들기 실패: {type(e).__name__} (재생은 정상)")

    while True:
        time.sleep(30)


# p4a 가 이 파일을 진입점으로 실행하므로 바로 시작한다
try:
    main()
except Exception:
    try:
        import core
        core.log(os.environ.get("PYTHON_SERVICE_ARGUMENT") or ".", "service crashed:\n" + traceback.format_exc())
    except Exception:
        pass
    raise
