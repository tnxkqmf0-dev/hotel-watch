# hotel-watch — 후쿠오카 숙소 공실 알리미

楽天トラベル 공식 공실검색 API로 아래 5개 호텔을 20분마다 확인하고, 조건에 맞는 방이 **새로** 나오면 텔레그램으로 알려줍니다. 11/3이 지나면 자동으로 멈춥니다.

| 호텔 | 楽天 번호 | 감시 객실 (트윈·20㎡↑·최소객실 제외) |
|---|---|---|
| 니시테츠 크룸 하카타 기온 | 184730 | 小上がりツイン 21.8㎡ |
| 더 블라섬 하카타 프리미어 | 172876 | 디럭스트윈 30 / NAGOMI 31 / 프리미엄 44 / 스위트 54㎡ |
| 미쓰이 가든 후쿠오카 기온 | 172316 | 스탠다드트윈 23.9 / 주니어스위트트윈 54.7㎡ |
| 마이스테이즈 후쿠오카 텐진 | 72791 | 스탠다드트윈 22 / 디럭스트윈 24㎡ |
| 리치몬드 텐진 니시도리 | 171975 | 모데레이트트윈 25.5㎡ |

- 일정: 11/11 ~ 11/14, 어른 2명·1실 (만 2세는 각 호텔 모두 添い寝 무료 범위)
- 전체 일정 공실 + 1박 단위 공실을 따로 알림 (1박씩 쪼개 잡는 옵션용)
- 매일 오전 9시쯤 "감시 중" 생존 신고 1회

## 세팅 (폰으로 가능)

1. **楽天 API 키**: https://webservice.rakuten.co.jp/app/create 에서 앱 생성 → `Application ID`, `Access Key` 복사
2. **텔레그램 봇**: @BotFather → `/newbot` → 토큰 복사. 만든 봇에게 아무 말이나 한 번 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates` 열어서 `"chat":{"id":숫자}` 의 숫자가 chat ID
3. **GitHub Secrets** (레포 → Settings → Secrets and variables → Actions → New repository secret)
   - `RAKUTEN_APP_ID`, `RAKUTEN_ACCESS_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
4. **테스트**: Actions 탭 → hotel-watch → Run workflow (mode=test) → 텔레그램에 "✅ 테스트 성공" 오면 끝

## 조건 바꾸기
`config.json`만 고치면 됩니다. 예) 가격 상한 `max_total_per_night_yen: 40000`, 트리플도 허용하려면 `exclude_keywords`에서 `トリプル` 삭제 후 해당 호텔 `rooms`에 추가.

## 참고
- 楽天에 올라온 재고만 봅니다(아고다·부킹 재고와 별개). 알림 오면 楽天에서 바로 잡는 게 제일 빠름.
- private 레포면 Actions 무료 한도(월 2,000분) 안에서 돌아가도록 20분 간격으로 잡았습니다. public 레포면 `*/10`으로 줄여도 무료.
