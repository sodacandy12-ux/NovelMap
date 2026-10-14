#!/usr/bin/env bash
# 소설 지도 EC2 설치 스크립트 (Ubuntu 22.04 / 24.04 기준)
#
# 사용법 (프로젝트 폴더에서):
#   sudo DEMO_USER=demo DEMO_PASS='비밀번호' bash deploy/ec2_setup.sh
#
# 하는 일: 패키지 설치 → (메모리 부족 시) 스왑 → 파이썬 가상환경과 의존성 → 임베딩 모델 미리 받기
#          → systemd 서비스 등록 → nginx(80번, 비밀번호 보호) 연결
# 선택: ANTHROPIC_API_KEY=... 를 함께 주면 /etc/novelmap.env 에 저장(권한 600). 공개 시연에서는 권장하지 않음.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "[오류] sudo 로 실행하세요."; exit 1; fi
if [ -z "${DEMO_PASS:-}" ]; then echo "[오류] DEMO_PASS(접속 비밀번호)를 지정하세요. 예) sudo DEMO_USER=demo DEMO_PASS='abc12345' bash deploy/ec2_setup.sh"; exit 1; fi
DEMO_USER="${DEMO_USER:-demo}"

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_USER="${SUDO_USER:-ubuntu}"
echo "[정보] 설치 위치: $APP_DIR / 실행 사용자: $APP_USER"
[ -f "$APP_DIR/app/main.py" ] || { echo "[오류] $APP_DIR 에 app/main.py 가 없습니다. 압축을 올바른 위치에 풀었는지 확인하세요."; exit 1; }

echo "== 1/6 패키지 설치 =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-venv python3-pip nginx apache2-utils curl

echo "== 2/6 스왑 확인 =="
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 6000 ] && ! swapon --show | grep -q .; then
  echo "[정보] 메모리 ${MEM_MB}MB → 2GB 스왑 파일을 만듭니다."
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== 3/6 파이썬 환경 (5~10분 걸릴 수 있음) =="
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
sudo -u "$APP_USER" bash -c "
  set -e
  cd '$APP_DIR'
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
  .venv/bin/pip install -r requirements.txt
"

echo "== 4/6 임베딩 모델(bge-m3, 약 2GB) 미리 받기 =="
sudo -u "$APP_USER" bash -c "cd '$APP_DIR' && .venv/bin/python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3'); print('모델 준비 완료')\""

echo "== 5/6 서비스 등록 =="
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  umask 077
  printf 'ANTHROPIC_API_KEY=%s\n' "$ANTHROPIC_API_KEY" > /etc/novelmap.env
  chmod 600 /etc/novelmap.env
  echo "[주의] 서버에 API 키를 저장했습니다. 이 주소가 공개돼 있으면 접속자가 키로 분석을 실행할 수 있습니다."
fi
cat > /etc/systemd/system/novelmap.service <<EOF
[Unit]
Description=Novel Map
After=network.target

[Service]
User=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=-/etc/novelmap.env
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable novelmap >/dev/null
systemctl restart novelmap

echo "== 6/6 nginx (80번 포트, 아이디/비밀번호 보호) =="
htpasswd -bc /etc/nginx/.htpasswd "$DEMO_USER" "$DEMO_PASS" >/dev/null
cat > /etc/nginx/sites-available/novelmap <<'EOF'
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 5m;
    auth_basic "Novel Map";
    auth_basic_user_file /etc/nginx/.htpasswd;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
EOF
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/novelmap /etc/nginx/sites-enabled/novelmap
nginx -t
systemctl enable nginx >/dev/null
systemctl restart nginx

echo "서버가 시작되기를 기다리는 중 (최대 60초)..."
for i in $(seq 1 30); do
  curl -s -m 3 -o /dev/null http://127.0.0.1:8000/novels && break
  sleep 2
done
TOKEN=$(curl -s -m 3 -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)
IP=$(curl -s -m 3 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4 || true)
echo
if curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/novels | grep -q 200; then
  echo "[완료] 서버가 정상 동작합니다."
else
  echo "[경고] 서버 응답이 없습니다. 로그 확인: sudo journalctl -u novelmap -n 50 --no-pager"
fi
echo "접속 주소: http://${IP:-<EC2 퍼블릭 IP>}   (아이디: $DEMO_USER / 비밀번호: 방금 지정한 값)"
