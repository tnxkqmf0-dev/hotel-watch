# hotel-watch — 후쿠오카 숙소 공실 알리미

楽天トラベル 공식 공실검색 API로 후쿠오카 다다미방 공실을 매시간 확인해 텔레그램으로 보내줍니다. 11/3이 지나면 자동으로 멈춥니다.

감시 방식: **텐진역 반경 1.2km · 3성급 이상(대용 기준) · 3박 연박**
- 일본은 공식 성급이 없어 楽天 평점 3.8 이상·리뷰 30개 이상 호텔을 3성급 이상으로 간주 (호스텔·캡슐 제외)
- 그중 트윈·트리플·패밀리·다다미(和室/和洋室) 객실이 있는 호텔 목록을 하루 1번 갱신
- 매시간(밤 22~6시 제외) 11/11~11/14 3박 전체가 되는 공실만 확인해 현황 전송 (새로 풀린 방은 🚨/🆕)
- 흡연실·20㎡ 미만(객실명에 면적 표기된 경우) 제외. 설정은 `config.json`의 `area`

- 일정: 11/11 ~ 11/14, 어른 2명·1실 
- 아기(만 2세)는 대부분 添い寝 무료 범위라 어른 2명 기준으로 검색

## 세팅 (폰으로 가능)

1. **楽天 API 키**: https://webservice.rakuten.co.jp/app/create 에서 앱 생성 → `Application ID`, `Access Key` 복사
2. **텔레그램 봇**: @BotFather → `/newbot` → 토큰 복사. 만든 봇에게 아무 말이나 한 번 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates` 열어서 `"chat":{"id":숫자}` 의 숫자가 chat ID
3. **GitHub Secrets** (레포 → Settings → Secrets and variables → Actions → New repository secret)
   - `RAKUTEN_APP_ID`, `RAKUTEN_ACCESS_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
4. **테스트**: Actions 탭 → hotel-watch → Run workflow (mode=test) → 텔레그램에 "🔔 텔레그램 연결 OK"와 공실 현황이 오면 끝

## 조건 바꾸기
`config.json`만 고치면 됩니다. 예) 가격 상한 `max_total_per_night_yen: 40000`, 검색 범위 `area.radius_km`, 다다미 판정 키워드 `area.room_keywords`, 제외 키워드 `area.exclude_keywords`.

## 참고
- 楽天에 올라온 재고만 봅니다(아고다·부킹 재고와 별개). 알림 오면 楽天에서 바로 잡는 게 제일 빠름.
- private 레포면 Actions 무료 한도(월 2,000분) 안에서 돌아가도록 반복 없이 1회만 확인해요. public 레포면 1시간 간격(밤 22~6시 휴식) 자체 반복으로 계속 돌아요(무료). 실행 1건이 약 6시간 동안 "진행 중"으로 보이는 게 정상.
