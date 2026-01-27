#!/bin/bash

# --- 設定變數 ---
SCRIPT_NAME="sync_notes.py"
CONFIG_NAME="ignore_strings.json"
INSTALL_DIR=$(pwd)
SERVICE_NAME="notes-sync"
PYTHON_BIN=$(which python3)

# 輸出顏色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== 部署/更新筆記自動整理服務 ===${NC}"

# 1. 停止服務
if systemctl --user is-active --quiet $SERVICE_NAME; then
    echo -e "${YELLOW}[*] 停止現有服務...${NC}"
    systemctl --user stop $SERVICE_NAME
fi

# 2. 安裝依賴
pip3 install watchdog --break-system-packages 2>/dev/null || pip3 install watchdog

# 3. 檢查 Git 配置與環境 (解決 Status 128 錯誤)
echo -e "[*] 檢查 Git 環境配置..."
if [ ! -d ".git" ]; then
    echo -e "${YELLOW}[*] 初始化 Git 儲存庫...${NC}"
    git init
fi

# 設定全域使用者資訊以解決 Author identity unknown (Status 128)
git config --global user.email "minghunghsu.taiwan@gmail.com"
git config --global user.name "dong881"
git config --global --add safe.directory "$INSTALL_DIR"

# 4. 部署檔案
if [ ! -f "$SCRIPT_NAME" ]; then
    echo -e "${RED}[!] 錯誤: 找不到 $SCRIPT_NAME。請確保在腳本所在目錄執行。${NC}"
    exit 1
fi
echo -e "[*] 使用當前路徑作為部署目錄: $INSTALL_DIR"

# 處理 Config
TARGET_CONFIG="$INSTALL_DIR/$CONFIG_NAME"
if [ -f "$CONFIG_NAME" ]; then
    echo -e "[*] 檢測到本地 $CONFIG_NAME，正在更新..."
    cp -f "$CONFIG_NAME" "$TARGET_CONFIG"
elif [ ! -f "$TARGET_CONFIG" ]; then
    echo -e "${YELLOW}[!] 目標缺設定檔，建立預設 $CONFIG_NAME...${NC}"
    cat <<EOF > "$TARGET_CONFIG"
[
    "是否同意這個分析？",
    "請確認以上理解是否正確。",
    "Do you agree with this analysis?"
]
EOF
fi

# 5. 建立 Systemd Service
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"
SERVICE_FILE="$SYSTEMD_DIR/$SERVICE_NAME.service"

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Gemini Brain Auto-Organizer Service
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYTHON_BIN $INSTALL_DIR/$SCRIPT_NAME
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

# 6. 啟動
systemctl --user daemon-reload
systemctl --user enable $SERVICE_NAME
systemctl --user restart $SERVICE_NAME

sleep 1
if systemctl --user is-active --quiet $SERVICE_NAME; then
    echo -e "${GREEN}=== 部署成功！ ===${NC}"
    echo "安裝位置: $INSTALL_DIR"
    echo "設定檔案: $INSTALL_DIR/$CONFIG_NAME"
    echo "Logs: journalctl --user -u $SERVICE_NAME -f"
else
    echo -e "${RED}=== 啟動失敗 ===${NC}"
    echo -e "${YELLOW}提示: 請檢查 $INSTALL_DIR/$SCRIPT_NAME 是否具備執行權限或 Python 語法錯誤${NC}"
    systemctl --user status $SERVICE_NAME --no-pager
fi