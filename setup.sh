#!/bin/bash

# --- 設定變數 ---
SCRIPT_NAME="sync_notes.py"
CONFIG_NAME="ignore_strings.json"
# 修改安裝目錄為使用者指定位置
INSTALL_DIR="$HOME/note-sync"
SERVICE_NAME="notes-sync"
PYTHON_BIN=$(which python3)
TARGET_REPO="$HOME/ming-note"

# 輸出顏色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== 部署/更新筆記自動整理服務 ===${NC}"

# 1. 停止服務 (因為我們要移動設定檔，最好先停)
if systemctl --user is-active --quiet $SERVICE_NAME; then
    echo -e "${YELLOW}[*] 停止現有服務...${NC}"
    systemctl --user stop $SERVICE_NAME
fi

# 2. 安裝依賴
pip3 install watchdog --break-system-packages 2>/dev/null || pip3 install watchdog

# 3. 部署/檢查檔案
# 既然主程式已在目標位置，這裡僅確保目錄存在與處理設定檔
# echo -e "[*] 更新檔案至 $INSTALL_DIR..."  <-- 移除多餘 log
mkdir -p "$INSTALL_DIR"
# cp -f "$SCRIPT_NAME" "$INSTALL_DIR/"    <-- 移除導致報錯的複製指令

# 定義目標 Config 路徑，方便後續引用
TARGET_CONFIG="$INSTALL_DIR/$CONFIG_NAME"

# 處理 Ignore Config
if [ -f "$CONFIG_NAME" ]; then
    # 情況 A: 當前目錄有提供 Config 檔案 -> 更新到目標目錄
    echo -e "[*] 檢測到本地 $CONFIG_NAME，正在更新..."
    cp -f "$CONFIG_NAME" "$TARGET_CONFIG"
elif [ ! -f "$TARGET_CONFIG" ]; then
    # 情況 B: 當前無 Config 且 目標目錄也沒有 -> 建立預設值
    echo -e "${YELLOW}[!] 目標缺設定檔，建立預設 $CONFIG_NAME...${NC}"
    cat <<EOF > "$TARGET_CONFIG"
[
    "是否同意這個分析？",
    "請確認以上理解是否正確。",
    "Do you agree with this analysis?"
]
EOF
else
    # 情況 C: 目標目錄已有 Config，且當前目錄無意覆蓋 -> 什麼都不做，保持原狀
    : 
fi

# 4. 建立 Systemd Service
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"
SERVICE_FILE="$SYSTEMD_DIR/$SERVICE_NAME.service"

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Gemini Brain Auto-Organizer Service
After=network.target

[Service]
Type=simple
# 設定 WorkingDirectory 為安裝目錄，確保 Python 能直接存取同層級的 json
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYTHON_BIN $INSTALL_DIR/$SCRIPT_NAME
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF

# 5. 啟動
systemctl --user daemon-reload
systemctl --user enable $SERVICE_NAME
systemctl --user restart $SERVICE_NAME

sleep 1
if systemctl --user is-active --quiet $SERVICE_NAME; then
    echo -e "${GREEN}=== 部署成功！ ===${NC}"
    echo "安裝位置: $INSTALL_DIR"
    echo "設定檔案: $INSTALL_DIR/$CONFIG_NAME"
    echo "狀態記錄: $INSTALL_DIR/sync_state.json"
    echo "Logs: journalctl --user -u $SERVICE_NAME -f"
else
    echo -e "${RED}=== 啟動失敗 ===${NC}"
    systemctl --user status $SERVICE_NAME --no-pager
fi