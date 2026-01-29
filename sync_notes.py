import os
import time
import shutil
import re
import json
import subprocess
import logging
import threading
from pathlib import Path
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# --- Configuration ---
HOME_DIR = Path.home()
SOURCE_ROOT = HOME_DIR / ".gemini/antigravity/brain"
TARGET_ROOT = HOME_DIR / "ming/ming-note"
DEST_DIR = TARGET_ROOT / "notes/develop"
DEST_IMG_DIR = DEST_DIR / "src"

# Time & Frequency Settings
FILE_SETTLE_DELAY = 60.0   # 檔案變更後等待秒數
CHECK_INTERVAL = 29*60.0      # 背景迴圈檢查頻率

# GitHub Config
GITHUB_REPO_BASE = "https://github.com/bmw-ece-ntust/ming-nfapi-debugger/blob"
GITHUB_BRANCH = "oai-debugging-tool-rapp"
LOCAL_REPO_PATH = "/home/ubuntu/ming/ming-nfapi-debugger"

# --- System Files Location ---
SCRIPT_DIR = Path(__file__).parent.resolve()
STATE_FILE = SCRIPT_DIR / "sync_state.json"
IGNORE_FILE = SCRIPT_DIR / "ignore_strings.json"

# Template
NOTE_TEMPLATE = """# {title}

**Created:** {created_date}
**Last Updated:** {updated_date}
**Tags:** #Development #Research
**Status:** ✅ Complete

---

## 📋 Overview

> **Summary:** {overview}

### Abstract

{abstract}

---

## 📝 Content

{content}
"""

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')

class SyncState:
    def __init__(self):
        self.state = {}
        self.load()

    def load(self):
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, 'r') as f:
                    self.state = json.load(f)
            except: self.state = {}

    def save(self):
        try:
            with open(STATE_FILE, 'w') as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            logging.error(f"Failed to save state file at {STATE_FILE}: {e}")

    def should_process(self, hash_id, mtime):
        return mtime > self.state.get(hash_id, 0)

    def update(self, hash_id, mtime):
        self.state[hash_id] = mtime
        self.save()

class BrainHandler(FileSystemEventHandler):
    def __init__(self):
        self.pending_hashes = set()
        self.lock = threading.Lock()
        self.processing_delay = FILE_SETTLE_DELAY 
        self.last_change_time = {}
        self.state_manager = SyncState()
        self.ignore_strings = self._load_ignore_list()

    def _load_ignore_list(self):
        if IGNORE_FILE.exists():
            try:
                with open(IGNORE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except: return []
        return []

    def scan_existing(self):
        logging.info(f"📂 Config Directory: {SCRIPT_DIR}")
        logging.info("🔍 Scanning existing sessions...")
        if not SOURCE_ROOT.exists(): return
        
        self.ignore_strings = self._load_ignore_list()
        count = 0
        skipped = 0
        for folder in SOURCE_ROOT.iterdir():
            if folder.is_dir():
                try:
                    mtime = folder.stat().st_mtime
                    max_file_mtime = mtime
                    for f in folder.glob("*"):
                        if f.stat().st_mtime > max_file_mtime:
                            max_file_mtime = f.stat().st_mtime
                    
                    if self.state_manager.should_process(folder.name, max_file_mtime):
                        if self.convert_hash_folder(folder.name, max_file_mtime):
                            count += 1
                        else:
                            skipped += 1
                except Exception as e:
                    logging.error(f"Error scanning {folder.name}: {e}")
        logging.info(f"✅ Scan complete. Processed: {count}, Skipped: {skipped}")

    def on_created(self, event): self._register_event(event)
    def on_modified(self, event): self._register_event(event)

    def _register_event(self, event):
        if event.is_directory: return
        path = Path(event.src_path)
        try:
            relative = path.relative_to(SOURCE_ROOT)
            if len(relative.parts) >= 2:
                hash_id = relative.parts[0]
                with self.lock:
                    self.pending_hashes.add(hash_id)
                    self.last_change_time[hash_id] = time.time()
        except ValueError: pass

    def process_pending(self):
        while True:
            time.sleep(CHECK_INTERVAL)
            with self.lock:
                now = time.time()
                to_process = []
                for hash_id in list(self.pending_hashes):
                    if now - self.last_change_time.get(hash_id, 0) > self.processing_delay:
                        to_process.append(hash_id)
                
                for hash_id in to_process:
                    self.pending_hashes.remove(hash_id)
                    mtime = self.last_change_time.pop(hash_id, now)
            
            for hash_id in to_process:
                self.convert_hash_folder(hash_id, timestamp=now)

    # --- Smart Summary Extraction ---
    def _clean_text(self, text):
        text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        text = re.sub(r'^(Summary|Abstract|Overview)[:\s-]*', '', text, flags=re.IGNORECASE)
        return text.strip()

    def _is_valid_sentence(self, text):
        text = text.strip()
        if len(text) < 15: return False                 
        if text.startswith("```"): return False         
        if text.startswith("#"): return False           
        if text.startswith("!["): return False          
        if text.count(';') > 3 or text.count('{') > 2: return False 
        if not re.search(r'[\u4e00-\u9fff a-zA-Z]', text): return False
        return True

    def _smart_extract(self, content_list, exclude_text=None):
        for content in content_list:
            if not content: continue
            paragraphs = re.split(r'\n\s*\n', content)
            for p in paragraphs:
                cleaned = self._clean_text(p)
                if self._is_valid_sentence(cleaned):
                    final_text = cleaned.replace('\n', ' ').strip()
                    if exclude_text and final_text == exclude_text:
                        continue
                    return final_text
        return "See content below."

    def convert_hash_folder(self, hash_id, timestamp=None):
        source_path = SOURCE_ROOT / hash_id
        if not source_path.exists(): return False

        # --- 1. Dynamic File Discovery & Grouping ---
        # 目的：將 'walkthrough.md', 'walkthrough.md.resolved' 等視為同一組，並取最大的檔案
        file_groups = {}
        
        for f in source_path.iterdir():
            if not f.is_file(): continue
            
            # Regex 解析: 抓取 .md 前面的名稱作為 Key (base_name)
            # 例如: architecture.md.resolved -> architecture
            match = re.match(r'^(.+?)\.md', f.name)
            if match:
                base_name = match.group(1)
                if base_name not in file_groups:
                    file_groups[base_name] = []
                file_groups[base_name].append(f)

        # 針對每一組，選出檔案大小 (st_size) 最大的那個路徑
        best_files_map = {}
        for base_name, paths in file_groups.items():
            # max key 使用檔案大小
            best_file = max(paths, key=lambda p: p.stat().st_size)
            best_files_map[base_name] = best_file

        # --- 2. Read Contents ---
        # 讀取所有選定檔案的內容存入 Dictionary
        contents = {}
        for base_name, path in best_files_map.items():
            contents[base_name] = self._read_file(path)

        # 取得關鍵檔案內容 (若無則為空字串)
        walkthrough_content = contents.get("walkthrough", "")
        impl_content = contents.get("implementation_plan", "")
        task_content = contents.get("task", "")

        # 準備 "其他檔案" 的列表 (排除已知的 key)
        known_keys = {"walkthrough", "implementation_plan", "task"}
        other_keys = sorted([k for k in contents.keys() if k not in known_keys])
        
        # 取得最後一個其他檔案的內容 (用於 Abstract fallback)
        last_other_content = contents[other_keys[-1]] if other_keys else ""

        # --- 3. Extract Metadata ---
        # Overview: 優先 Walkthrough -> Impl -> Task
        overview_text = self._smart_extract([walkthrough_content, impl_content, task_content])
        
        # Abstract: 優先 Impl -> Task -> 其他最後一個，排除 Overview
        abstract_text = self._smart_extract(
            [impl_content, task_content, last_other_content], 
            exclude_text=overview_text
        )

        # --- 4. Assemble Content Buffer ---
        content_buffer = []

        # (A) 固定結構: Implementation Plan
        if impl_content:
            content_buffer.append("\n### Implementation Plan\n")
            content_buffer.append(impl_content)

        # (B) 固定結構: Task List
        if task_content:
            content_buffer.append("\n### Task List\n")
            content_buffer.append(task_content)

        # (C) 動態結構: 所有其他檔案 (Architecture, Logs, etc.)
        for key in other_keys:
            # 將 key 轉為標題 (e.g., 'architecture' -> 'Architecture')
            title_str = key.replace("_", " ").title()
            content_buffer.append(f"\n### {title_str}\n")
            content_buffer.append(contents[key])

        # (D) 固定結構: Walkthrough (通常放最後或最前，這裡維持放最後)
        if walkthrough_content:
            content_buffer.append("\n### Walkthrough\n")
            content_buffer.append(walkthrough_content)

        full_raw_content = "\n".join(content_buffer)

        # --- 5. Title Extraction & Filtering ---
        title_match = re.search(r'^#\s+(.+)$', full_raw_content, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()
            # 移除內文中的標題行，避免重複
            full_raw_content = full_raw_content.replace(title_match.group(0), "")
        else:
            title = f"Note-{hash_id[:8]}"

        if re.match(r'^Note[\s-][0-9a-fA-F]+.*', title, re.IGNORECASE):
            logging.warning(f"⛔ Skipped default title note: {title}")
            if timestamp: self.state_manager.update(hash_id, timestamp)
            return False

        if len(full_raw_content.strip()) < 50:
            logging.warning(f"⛔ Skipped short/empty note: {title}")
            if timestamp: self.state_manager.update(hash_id, timestamp)
            return False

        # --- 6. Processing (Images, Links) ---
        # 注意: 這裡傳入 source_path，圖片處理需能應對
        processed_content = self._process_images(full_raw_content, source_path)
        processed_content = self._sanitize_content(processed_content)
        processed_content = self._convert_github_links(processed_content)

        creation_time = datetime.now()
        # 嘗試使用主要檔案的時間
        primary_file = best_files_map.get("walkthrough") or best_files_map.get("implementation_plan")
        if primary_file:
            creation_time = datetime.fromtimestamp(primary_file.stat().st_ctime)
        
        c_date_str = creation_time.strftime("%Y-%m-%d")
        u_date_str = datetime.now().strftime("%Y-%m-%d")

        if overview_text == "See content below.":
            overview_text = title
        if abstract_text == overview_text:
             abstract_text = "See details in Content section."

        final_note = NOTE_TEMPLATE.format(
            title=title,
            created_date=c_date_str,
            updated_date=u_date_str,
            overview=overview_text,
            abstract=abstract_text,
            content=processed_content
        )

        safe_title = re.sub(r'[\\/*?:"<>|]', "", title.replace("_-_", "-").replace("_", "-").replace(" ", "-"))
        safe_title = re.sub(r'-+', '-', safe_title)
        filename = f"{safe_title}.md"
        dest_file_path = DEST_DIR / filename

        try:
            DEST_DIR.mkdir(parents=True, exist_ok=True)
            with open(dest_file_path, 'w', encoding='utf-8') as f:
                f.write(final_note)

            logging.info(f"💾 Generated: {filename}")
            self._git_commit(dest_file_path)

            if timestamp:
                self.state_manager.update(hash_id, timestamp)
            return True

        except Exception as e:
            logging.error(f"Failed to save {filename}: {e}")
            return False

    def _read_file(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f: return f.read()
        except: return ""

    def _sanitize_content(self, text):
        for ignore_str in self.ignore_strings:
            text = text.replace(ignore_str, "")
        return text

    def _convert_github_links(self, text):
        target_prefix = f"file://{LOCAL_REPO_PATH}"
        replacement_base = f"{GITHUB_REPO_BASE}/{GITHUB_BRANCH}/"
        if target_prefix in text:
            text = text.replace(target_prefix, replacement_base)
        return text

    def _process_images(self, content, source_dir):
        img_pattern = r'!\[(.*?)\]\((.*?)\)'
        def replace_img(match):
            alt = match.group(1)
            src = match.group(2)
            if src.startswith("http") or src.startswith("https"):
                return match.group(0)
            img_name = Path(src).name
            img_source = source_dir / src
            if not img_source.exists(): img_source = source_dir / img_name
            if img_source.exists():
                DEST_IMG_DIR.mkdir(parents=True, exist_ok=True)
                target_path = DEST_IMG_DIR / img_name
                try:
                    shutil.copy2(img_source, target_path)
                    return f"![{alt}](./src/{img_name})"
                except: return match.group(0)
            else: return match.group(0)
        return re.sub(img_pattern, replace_img, content)

    def _git_commit(self, file_path):
        cwd = str(TARGET_ROOT)
        try:
            if not (TARGET_ROOT / ".git").exists(): return
            rel_path = file_path.relative_to(TARGET_ROOT)
            subprocess.run(["git", "add", str(rel_path)], cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if DEST_IMG_DIR.exists():
                img_rel = DEST_IMG_DIR.relative_to(TARGET_ROOT)
                subprocess.run(["git", "add", str(img_rel)], cwd=cwd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            status = subprocess.run(["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True)
            if status.stdout.strip():
                msg = f"new note: {file_path.stem}"
                subprocess.run(["git", "commit", "-m", msg], cwd=cwd, check=True, stdout=subprocess.DEVNULL)
                logging.info(f"🚀 Git Commit: {msg}")
            else:
                logging.info(f"⚡ No changes for {file_path.name}")
        except Exception as e:
            logging.error(f"Git Error: {e}")

if __name__ == "__main__":
    if not SOURCE_ROOT.exists():
        logging.error(f"Source not found: {SOURCE_ROOT}")
        exit(1)

    handler = BrainHandler()
    t = threading.Thread(target=handler.process_pending, daemon=True)
    t.start()
    
    handler.scan_existing()

    observer = Observer()
    observer.schedule(handler, str(SOURCE_ROOT), recursive=True)
    observer.start()

    logging.info(f"👀 Watching {SOURCE_ROOT}...")
    try:
        while True: 
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
