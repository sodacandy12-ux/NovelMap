#!/usr/bin/env bash
# 소설 지도 EC2 업그레이드 스크립트: GitHub 최신 코드로 갱신하고 서비스를 다시 시작한다.
#
# 사용법 (서버의 /opt/novelmap 에서, ubuntu 사용자로):
#   bash deploy/ec2_upgrade.sh
#
# 하는 일: data 폴더 백업 → git 으로 최신 코드 받기(코드와 저장소에 포함된 샘플 데이터가 덮어써짐)
#          → 파이썬 패키지 갱신 → 서비스 재시작 → 응답 확인
# 유지되는 것: .env / /etc/novelmap.env(키), .venv, data/chroma, 서버에서 새로 만든(저장소에 없는) 파일
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_URL="${REPO_URL:-https://github.com/sodacandy12-ux/NovelMap.git}"
BRANCH="${BRANCH:-main}"
cd "$APP_DIR"

[ -f app/main.py ] || { echo "[오류] $APP_DIR 가 소설 지도 폴더가 아닙니다."; exit 1; }
command -v git >/dev/null || { echo "[오류] git 이 없습니다: sudo apt-get install -y git"; exit 1; }

echo "== 1/5 데이터 백업 =="
BK="$HOME/novelmap-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BK"
cp -a data "$BK/data"
echo "백업 위치: $BK"

echo "== 2/5 최신 코드 받기 ($BRANCH) =="
[ -d .git ] || git init -q          # tgz 로 올린 서버는 git 저장소가 아니므로 처음 한 번 초기화
git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"
git fetch --depth 1 origin "$BRANCH"
git reset --hard "origin/$BRANCH"
git log -1 --format="적용된 버전: %h %s (%ci)"

echo "== 3/5 파이썬 패키지 갱신 =="
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/pip install -q sentence-transformers      # requirements 에 빠져 있어도 임베딩 모델이 동작하도록

echo "== 4/5 서비스 재시작 =="
sudo systemctl restart novelmap

echo "== 5/5 응답 확인 (최대 60초) =="
OK=0
for i in $(seq 1 30); do
  if [ "$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/novels)" = "200" ]; then OK=1; break; fi
  sleep 2
done
if [ "$OK" = "1" ]; then
  echo "[완료] 업그레이드 성공. /info: $(curl -s -m 3 http://127.0.0.1:8000/info)"
  echo "브라우저에서 Ctrl+F5 로 새로고침해야 새 화면 코드가 반영됩니다."
else
  echo "[경고] 서버가 응답하지 않습니다: sudo journalctl -u novelmap -n 50 --no-pager"
  echo "되돌리려면 백업($BK/data)을 data 로 복사하고 git reset --hard <이전 커밋> 을 실행하세요."
  exit 1
fi
