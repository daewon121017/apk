# My Music (Android)

## APK 만들기 — GitHub 에서 (내 컴퓨터에 설치할 것 없음, 무료)

1. github.com 로그인 -> 오른쪽 위 `+` -> **New repository** -> 이름 아무거나 -> **Create repository**
2. 만들어진 화면에서 **uploading an existing file** 클릭
3. 이 폴더 안의 파일 4개를 끌어다 놓고 **Commit changes**:
   `main.py`, `buildozer.spec`, `font.otf`, `README.md`
4. 저장소 화면에서 **Add file -> Create new file** 클릭
5. 파일 이름 칸에 `.github/workflows/build-apk.yml` 을 그대로 입력
   (`/` 를 치면 폴더가 자동으로 만들어져요)
6. 큰 입력창에 `build-apk.yml` 파일 내용을 전부 복사해서 붙여넣고 **Commit changes**
7. 위쪽 **Actions** 탭 -> `Build APK` 가 돌아간다 (처음엔 20~40분). 초록 체크가 뜰 때까지 기다리기
   (안 돌고 있으면 Build APK 클릭 -> **Run workflow**)
8. 완료된 실행을 클릭 -> 맨 아래 **Artifacts -> music-player-apk** 다운로드 (zip 안에 .apk)
9. apk 를 폰으로 옮겨서 설치 (설정에서 "출처를 알 수 없는 앱 설치 허용" 필요)

### 빌드가 빨간 X 로 실패하면
실행 결과 화면 -> `build` -> 빨간 줄이 있는 단계를 열어서 맨 아래 에러 몇 줄을 복사해서 알려주세요.

## PC 에서 UI 미리보기
```
pip install kivy yt-dlp python-vlc pillow
python main.py
```

## 알아둘 점
- 한글 폰트(`font.otf`, Noto Sans CJK KR 일부 / SIL OFL 라이선스)가 들어 있어서 한글이 네모로 안 나와요.
- 앱의 `yt-dlp↑` 버튼: 유튜브가 바뀌어 재생이 안 될 때 누르고, 앱을 완전히 껐다 켜면 최신판이 적용돼요.
- 뒤로가기를 누르면 앱이 꺼지지 않고 백그라운드로 가서 음악이 계속 나와요.
- 설정 -> 배터리에서 이 앱을 "제한 없음"으로 해두면 화면을 꺼도 덜 끊겨요.
