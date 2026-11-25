# 이세계아이돌 멜론 누적 스트리밍 집계 사이트

곡별 누적 스트리밍 수를 `data/input.txt`에 적어 두면, **매일 한국 시간 00시**에 GitHub가 알아서

- 이세계아이돌 그룹 명의 38곡(Inst. 포함) 합계
- 고세구 솔로 앨범 《NO CAT, NO LIFE!》 3곡 합계 (따로)

를 계산해서 기록하고, 사이트 그래프를 새로 그립니다.

이세계아이돌 합계에는 **멜론 빌리언스 클럽(누적 10억 회)까지 남은 횟수와 예상 달성일**도 같이 보여 줘요.
예상 달성일은 최근 7일 동안 하루 평균 늘어난 양으로 계산해요.

> 멜론·가이섬이 자동 수집을 막고 있어서, 숫자를 읽어 오는 부분만 사람이 하고
> 합계·기록·그래프는 전부 자동으로 처리하는 방식이에요.

---

## 파일 구성

| 파일 | 하는 일 |
|---|---|
| `data/input.txt` | ✏️ **여기만 고치면 돼요.** 곡별 누적 스트리밍 수 입력 |
| `data/songs.json` | 곡별 멜론 링크 (사이트 곡 표에서 클릭하면 멜론이 열려요) |
| `data/goals.json` | 목표치 (빌리언스 10억 → 골드 50억 → 다이아 100억) |
| `data/history.json` | 날짜별 00시 기록 (자동으로 쌓임, 손대지 않기) |
| `scripts/aggregate.py` | 합계 계산 프로그램 |
| `.github/workflows/daily-total.yml` | 매일 00시 자동 실행 설정 |
| `docs/index.html` | 사이트 화면 |
| `docs/data.json` | 사이트가 읽는 숫자 (자동 생성) |

---

## 처음 한 번만 하는 설정

### 1. 파일 올리기
1. GitHub 저장소 화면에서 **Add file → Upload files** 클릭
2. 압축을 푼 폴더 **안의 내용물 전체**(`data`, `docs`, `scripts`, `.github`, `README.md`)를 드래그해서 놓기
3. 아래 **Commit changes** 클릭
4. ✅ 올린 뒤 저장소에 이 경로들이 다 보이는지 꼭 확인하세요 (지난번처럼 빠지면 실행이 안 돼요)
   - `.github/workflows/daily-total.yml`
   - `scripts/aggregate.py`
   - `data/input.txt`, `data/songs.json`, `data/goals.json`, `data/history.json`
   - `docs/index.html`, `docs/.nojekyll`

   > `.github` 폴더나 `.nojekyll`처럼 점(.)으로 시작하는 건 탐색기에서 안 보일 수 있어요.
   > 빠졌다면 **Add file → Create new file**에서 이름 칸에 경로를 통째로 입력하고 내용을 붙여넣으면 돼요.

5. 예전에 쓰던 `update.yml`이 있다면 지워 주세요 (두 개가 같이 돌면 헷갈려요).

### 2. 사이트 켜기 (GitHub Pages)
1. 저장소 **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **main**, 폴더: **/docs** → **Save**
4. 1~2분 뒤 `https://아이디.github.io/저장소이름/` 에서 사이트가 열려요.

### 3. 첫 기록 만들기
1. `data/input.txt` 에 숫자를 채우고 저장 (아래 "매일 할 일" 참고)
2. **Actions** 탭 → 왼쪽 **매일 00시 누적 합계** → **Run workflow**
3. "지금 숫자를 오늘 날짜 기록으로 바로 확정할까요?" **체크** → 초록 버튼 클릭
4. 초록불이 뜨면 사이트에 첫 숫자가 나와요. 다음 날 00시부터는 자동이에요.

---

## 매일 할 일 (숫자 입력)

1. 멜론에서 곡별 스트리밍 카드의 **누적 스트리밍 수**를 확인
2. 사이트 맨 아래 **"숫자 입력하러 가기 ✏️"** 클릭 (또는 저장소에서 `data/input.txt` → 연필 아이콘)
3. 숫자 칸만 바꾸기

   ```
   이세계아이돌 | RE : WIND | 98,123,456
   고세구 《NO CAT, NO LIFE!》 | Your Turn | 2,345,678회
   ```
   - 쉼표·띄어쓰기·'회' 붙여도 괜찮아요
   - 바뀌지 않은 곡은 **비워 두면** 어제 숫자를 이어서 써요

4. **Commit changes** → 사이트에 "✏️ 새로 입력된 숫자"로 바로 미리 보이고,
   **00시가 되면 그날 기록으로 확정**돼요.

### 곡 추가하기
`input.txt`에 줄을 하나 더 쓰면 끝이에요. 그룹 이름이 같은 곡끼리 합쳐져요.
새 그룹 이름(예: `릴파`)을 쓰면 사이트에 칸이 하나 더 생겨요.

---

## 알아두면 좋은 점

- **00시 실행이 몇 분~수십 분 늦을 수 있어요.** GitHub가 자정에 몰리는 작업을 순서대로 처리해서 그래요. 날짜는 한국 시간 기준으로 제대로 찍혀요.
- 00시 전에 숫자를 안 바꿨다면, 어제 숫자가 그대로 기록돼요 (사이트 곡 표에 "이전 숫자"로 표시).
- **Actions가 빨간불일 때**: 실행 기록을 눌러 "합계 계산" 단계를 열어 보세요. `input.txt` 몇 번째 줄이 문제인지 한국어로 알려줘요.
- 저장 단계에서 권한 오류(403)가 나면: **Settings → Actions → General → Workflow permissions → Read and write permissions** 선택 후 저장.
- 60일 동안 저장소에 아무 변화가 없으면 GitHub가 예약 실행을 멈춰요. 숫자를 꾸준히 입력하면 걱정 없어요.

## 내 컴퓨터에서 시험해 보기 (선택)
```
python scripts/aggregate.py --mode preview
```
