[app]
title = My Music
package.name = mymusic
package.domain = org.mymusic
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,otf,ttc
version = 1.0

# yt-dlp 는 순수 파이썬이라 그대로 포함돼요
requirements = python3,kivy==2.3.0,pyjnius,android,yt-dlp,certifi,openssl,sqlite3

orientation = portrait
fullscreen = 0

android.permissions = INTERNET,WAKE_LOCK,FOREGROUND_SERVICE
android.api = 34
android.minapi = 24
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = True

[buildozer]
log_level = 2
warn_on_root = 1
