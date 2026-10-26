# 영차

**운영 플랫폼 이전 상태:** Google Cloud 테스트 배포가 성공했습니다.
사용자가 실행한 배포 스크립트에서 실제 DB 쓰기/읽기와 공개 HTTPS 검사를
통과했습니다. 테스트 주소는 https://yeongcha-test-t7e4i53n5q-du.a.run.app 입니다.
기존 Railway 데이터는 사용자가 모두 테스트용이라고 확인해 가져오기를 생략합니다.
카카오 로그인 설정과 사용자의 신규 계정 접속까지 확인했습니다.
운영자 권한 연결과 임시 지정 변수 제거, 카페·판매자·메뉴 등록 및 기본 주문 동작은 사용자 확인을 마쳤습니다. 최종 검수는 계속 진행합니다.
[이전 계획](docs/GOOGLE_CLOUD_MIGRATION.md)과 [실행 기록](docs/GOOGLE_CLOUD_PROGRESS.md)을
참고하세요.

사무실 단체 커피 주문을 링크로 취합하는 서비스입니다. 카카오 로그인·권한, 주문 취합·전송, 판매자 수락·거절·메뉴 완료를 구현했습니다. 사용자 승인으로 **7단계 판매자 반복 소리·테스트·PC 화면 알림과 총무 사이트 알림**을 구현·Google 배포했고, 사용자가 최소화 상태를 포함한 실제 소리 동작을 확인했습니다. [7단계 기록](docs/PHASE_7.md)을 참고하세요.

**현재 배포 상태:** Google Cloud에서 6단계 주문 처리를 사용자가 확인했습니다.
추가 요청에 따라 판매자의 **메뉴 완료**, 총무의 영속적인 사이트 완료 알림,
카페 선택 화면의 **주문하기** 버튼을 구현하고 Google 배포·기본 검사까지 통과했습니다.
후속 주소/전화번호 연결 표시와 7단계는 Google 배포를 마쳤습니다.
메뉴 완료된 주문서의 링크 복사 숨김도 사용자가 확인했습니다.
8단계에서는 실제 Google 백업 복구·자료 검사·임시 자원 정리를 마쳤으며,
비용 점검은 사용자 지시로 미루고 개인정보 처리와 정상 서비스 준비를 진행 중입니다.
사이트 내 문의 접수·운영자 답변과 서버 자동 처리 상태 검사의 Google 배포·기본 검사를 마쳤습니다.
회원 탈퇴·카카오 연결 해제 재시도, 문의 답변 완료 후 1년 삭제와 운영자 전용 개인정보
안내 초안을 반영했습니다. 어드민 키 등록과 실제 카카오 회원 조회도 사용자가 확인했습니다.
실제 회원 탈퇴·카카오 연결 해제 시험과 개인정보 안내 공개는 아직입니다.
추가 요청으로 [운영자 회원 관리](docs/MEMBER_MANAGEMENT.md)를 구현해 기존 가입
회원과 이후 탈퇴 이력을 표로 조회합니다. 이 후속 기능의 Google 반영은 아직입니다.
[회원 탈퇴 적용 순서](docs/MEMBERSHIP_WITHDRAWAL.md)를 참고하세요. [8단계 기록](docs/PHASE_8.md)을 참고하세요.
사용자가 폐기 시간 고정 안내 요청을 취소해 주문서 안내는 기존 방식으로 유지합니다. [완료 처리 기록](docs/ORDER_COMPLETION.md)을 참고하세요.
일반 배포는 인증된 Cloud Shell에서 다음 명령을 사용합니다. 초기 배포 스크립트는
카카오 설정 이후 재사용하지 않습니다.

```bash
git -C ~/yeongcha pull --ff-only && python3 ~/yeongcha/scripts/gcp_update_site.py
```

## 구성

- Python 3.12 / Django 5.2 LTS.
- django-allauth의 카카오 인증 코드 로그인, 세션 인증, 역할별 서버 권한 검사.
- PostgreSQL 17: 로컬 개발에서는 Docker Compose로 실행합니다.
- HTML·CSS 기반 한국어 반응형 화면. Noto Sans KR을 사이트에서 직접 제공하고, 글자 크기는 기존 대비 1pt 키웠습니다. [글꼴·굵기 기준](docs/TYPOGRAPHY.md)을 참고하세요. 사진·JavaScript 프레임워크는 사용하지 않습니다.
- 흰 배경에 빨간색 포인트를 사용하고 `color-scheme: only light`로 밝은 테마를 지정합니다. 삼성 인터넷의 웹페이지 강제 다크 모드처럼 사이트 설정을 무시하는 경우까지 보장하지는 않습니다. [브라우저별 한계와 확인 방법](docs/LIGHT_THEME.md)을 참고하세요. 사용법을 브랜드 로고 바로 아래에 먼저 보여 주고 기존 소개 화면을 그 아래에 배치합니다.
- `uv.lock`으로 Python 의존성을 고정하고, PostgreSQL 컨테이너 이미지도 digest로 고정합니다.

## 처음 실행하기

필수 도구는 Python 3.12, uv 0.12 이상, 실행 중인 Docker Engine과 Docker Compose v2입니다. 클라우드 작업은 기존 `/workspace/coffegroup` 체크아웃에서 진행합니다. 별도 Git worktree를 만들 필요가 없습니다.

```bash
cd /workspace/coffegroup
bash scripts/setup_dev.sh
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

다른 컴퓨터에서는 첫 줄을 실제 저장소 위치로 바꿉니다. 기본 웹 포트는 8000, PostgreSQL 포트는 5432입니다. DB 포트는 개발 머신의 loopback에만 공개합니다. 같은 포트의 기존 DB가 있다면 기존 서비스를 임의로 중지하지 말고 Compose의 로컬 포트와 `.env`의 `DATABASE_URL`을 함께 조정하세요.

설치 스크립트는 로컬 전용 무작위 비밀값이 포함된 `.env`를 최초 한 번만 만들고, 기존 파일은 덮어쓰지 않습니다. `.env`를 출력하거나 Git에 추가하지 마세요. `.env.example`은 설명용 예시이며 그대로 운영에 사용하면 안 됩니다. DB 비밀번호는 첫 볼륨 초기화에 사용되므로 `.env`만 바꾸면 기존 DB 비밀번호가 바뀌지는 않습니다.

스크립트는 잠금파일 기준 설치, DB 준비 대기, 마이그레이션, Django 설정 검사, 실제 DB 쓰기·읽기 검증을 수행합니다. 반복 실행해도 기존 비밀값과 DB 볼륨을 유지합니다. 검증 명령의 임시 테이블은 트랜잭션 종료 시 자동 제거됩니다.

## 이후 다시 실행하기

Docker 컨테이너와 웹 프로세스는 새 작업에서 다시 시작해야 할 수 있습니다.

```bash
cd /workspace/coffegroup
docker compose up -d --wait db
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py check_database
.venv/bin/python manage.py runserver 127.0.0.1:8000
```

의존성이 없거나 변경되었으면 `bash scripts/setup_dev.sh`를 실행합니다. 웹 서버는 `Ctrl+C`, DB는 `docker compose stop db`로 중지합니다. DB 데이터는 명명된 볼륨에 남습니다. `docker compose down -v`는 데이터를 삭제하므로 일반 종료 명령으로 사용하지 마세요.

주문방 자동 폐기와 전송 후 5분 미수락 거절을 위해 별도 터미널에서 `.venv/bin/python manage.py run_deadlines`도 실행합니다. 이 프로세스는 5초 간격으로 처리하며 새 클라우드 작업에서는 다시 시작해야 합니다. 현재 기한을 한 번만 처리하려면 `--once`를 사용합니다. Google Cloud Run에서는 `scripts/start_web.sh`가 웹과 기한 처리를 함께 감독하고, 최소 인스턴스 1개와 상시 CPU 설정을 유지합니다.

## 상태 확인

| 경로 | 의미 |
| --- | --- |
| `/` | 한국어 메인 화면. 카카오 설정 완료 전에는 로그인 버튼을 비활성화합니다. |
| `/accounts/entry/organizer/` | 총무 로그인 입구. 가입한 회원의 기본 역할입니다. |
| `/accounts/entry/seller/` | 판매자 로그인 입구. 운영자가 연결한 카페 계정만 이용합니다. |
| `/accounts/entry/operator/` | 운영자 로그인 입구. 지정된 운영자만 이용합니다. |
| `/accounts/profile/` | 상단 이름을 눌러 접근하는 본인 개인정보·주문 이력·연결코드. |
| `/cafes/<id>/manage/` | 운영자의 카페 정보·메뉴·옵션 관리. |
| `/orders/<id>/` | 총무 본인 또는 운영자만 조회하는 고정 메뉴판 주문서. |
| `/join/<token>/` | 가입 없이 고정 메뉴판에서 메뉴·옵션·수량 선택 및 제출. |
| `/accounts/withdraw/` | 카카오 재확인 후 회원 탈퇴. 추가 서버 키 등록 전에는 실행하지 않습니다. |
| `/accounts/operator/members/` | 운영자 전용 가입 회원·탈퇴 이력 조회와 검색. |
| `/accounts/privacy-preview/` | 운영자만 볼 수 있는 미게시 개인정보 안내 초안. |
| `/help/` | 본인 문의 접수·답변 조회. 비회원 문의는 작성한 브라우저에서 확인합니다. |
| `/help/manage/` | 운영자의 사용자 문의 목록·답변 처리. |
| `/health/live/` | 웹 프로세스 응답 확인. 정상 시 HTTP 200. DB 연결을 요구하지 않습니다. |
| `/health/ready/` | 실제 PostgreSQL 쿼리 확인. 정상 시 HTTP 200, 연결 실패 시 HTTP 503. 연결 상세·비밀값은 반환하지 않습니다. |

상태 경로는 운영 점검용입니다. 사용자 화면에 내부 DB 상태를 표시하지 않습니다. 1단계 준비 상태는 로그인·주문 기능이나 운영 배포 완료를 뜻하지 않습니다.

## 검사하기

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py check_database
.venv/bin/python manage.py test --noinput
.venv/bin/ruff check .
.venv/bin/ruff format --check .
```

테스트는 **실제 PostgreSQL**의 별도 `test_coffegroup` DB를 생성·제거합니다. 개발 전용 DB 계정을 사용해야 하며, 테스트 DB 생성 권한이 필요합니다. 운영 DB 연결로 테스트를 실행하지 마세요. SQLite 대체 실행은 지원하지 않습니다.

## 설정과 운영 경계

설정은 `config/settings.py`에서 환경변수로 읽습니다. 로컬 `.env`보다 실제 프로세스 환경변수가 우선합니다.

| 변수 | 용도 |
| --- | --- |
| `DJANGO_SECRET_KEY` | Django의 비밀키. 실제 값을 직접 생성하며 저장소에 넣지 않습니다. |
| `DJANGO_DEBUG` | 로컬 개발에서는 `True`. 지정하지 않으면 `False`. |
| `DJANGO_ALLOWED_HOSTS` | 허용할 호스트 이름 목록. 운영 모드에서는 비어 있거나 `*`일 수 없습니다. |
| `DATABASE_URL` | PostgreSQL 접속 URL. 다른 DB로 자동 대체하지 않습니다. |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | 로컬 Compose가 DB를 초기화할 때 사용합니다. |
| `KAKAO_LOGIN_ENABLED` | 카카오 키·설정 준비 후 `True`. 기본 `False`. |
| `KAKAO_RESET_API_KEY`, `KAKAO_CLIENT_SECRET` | 같은 카카오 개발자 앱의 REST API 키와 로그인 Secret. `RESET`은 사용자가 정한 서버 변수 이름이며 실제 값은 카카오 REST API 키입니다. 안전한 환경변수로만 관리합니다. |

한 카카오 회원이 총무·판매자·운영자 권한을 함께 사용할 수 있습니다. 역할 선택만으로 판매자·운영자 권한이 생기지 않습니다. 운영자는 카페와 가입 계정의 연결 코드를 연결합니다. 최초 운영자는 본인 계정을 확인한 뒤 Railway의 초기 계정 지정 설정으로 연결합니다. 자세한 순서는 [카카오 설정 가이드](docs/KAKAO_SETUP.md)를 참고하세요.

로컬 개발 서버는 운영용으로 사용하지 않습니다. Docker·Gunicorn·WhiteNoise 기반 [Google 테스트 사이트](https://yeongcha-test-t7e4i53n5q-du.a.run.app)를 운영합니다. 사용자가 기존 Railway 프로젝트를 삭제했다고 확인했습니다. 카카오 설정 전에는 로그인 시도를 차단합니다. 실제 계정 로그인과 주문 기능 완료를 혼동하지 않습니다. 고가용성·백업 복구 검수·개인정보 정책을 포함한 파일럿 출시 준비는 별도 단계입니다. [테스트 배포 문서](docs/TEST_DEPLOYMENT.md)와 [2단계 결과](docs/PHASE_2.md)를 참고하세요.

## 개발 계획

[단계별 개발 계획](docs/DEVELOPMENT_PLAN.md)의 순서에 따라 진행합니다. 한 단계의 구현·검증 결과를 보고한 후 사용자 확인을 거쳐 다음 단계로 넘어갑니다.

[3단계 기록](docs/PHASE_3.md)에 메뉴판 보존·자동 폐기를, [4단계 기록](docs/PHASE_4.md)에 주문 제출·취합·마감을, [5단계 기록](docs/PHASE_5.md)에 최종 전송·주문 잠금·기한 처리와 검증을 정리했습니다.

## 운영자 표 관리

운영자 페이지에서 카페·판매자를 표로 관리하고 카페명 아래의 **메뉴·옵션 관리**로 들어갑니다. 메뉴·옵션 탭에서 여러 행을 붙여넣고 **변경사항 저장**으로 함께 저장할 수 있습니다. 오류 셀과 미저장 변경을 구분하며 기존 주문방의 메뉴판은 보존합니다. [사용법과 검증](docs/ADMIN_SHEETS.md)을 참고하세요.
