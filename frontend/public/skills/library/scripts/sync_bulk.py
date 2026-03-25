#!/usr/bin/env python3
"""Cortex Library Bulk Sync Script - Upload all files first, then batch process.

Reads base_url from credentials (no hardcoded URLs).
Uses URL query parameters for upload API.
Handles QMD session files automatically.
"""
import os
import json
import hashlib
import time
import requests
import sys

# Config
CREDENTIALS_FILE = os.path.expanduser("~/.openclaw/skills/library/state/credentials.json")
TRACKING_FILE = os.path.expanduser("~/.openclaw/skills/library/state/uploaded_files.json")
MEMORY_DIRS = [
    os.path.expanduser("~/.openclaw/memory"),
    os.path.expanduser("~/.openclaw/conversations"),
]
COLLECTION_NAME = "OpenClaw"


def load_json(path):
    if not os.path.exists(path):
        return {}
    with open(path, 'r') as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def calculate_sha256(filepath):
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def ensure_collection(api_base, api_key):
    """Find or create the target collection."""
    headers = {"X-API-Key": api_key}

    try:
        r = requests.get(f"{api_base}/api/collections", headers=headers)
        r.raise_for_status()
        collections = r.json().get("collections", [])

        for col in collections:
            if col.get("name") == COLLECTION_NAME:
                return col.get("id")

        # Create if not found
        print(f"   📚 Creating {COLLECTION_NAME} collection...")
        r = requests.post(f"{api_base}/api/collections", headers=headers, json={
            "name": COLLECTION_NAME,
            "description": "Memory files synced from agent"
        })
        r.raise_for_status()
        new_id = r.json().get("id")

        # Save to credentials
        creds = load_json(CREDENTIALS_FILE)
        creds["collection_id"] = new_id
        save_json(CREDENTIALS_FILE, creds)

        return new_id
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None


def upload_file_no_process(filepath, api_base, api_key, collection_id):
    """Upload a file WITHOUT processing using URL query parameters."""
    # CRITICAL: collection_id and start_processing are URL query parameters
    url = f"{api_base}/api/upload"
    params = {"collection_id": collection_id, "start_processing": "false"}
    headers = {"X-API-Key": api_key}

    try:
        with open(filepath, 'rb') as f:
            files = {'file': (os.path.basename(filepath), f)}
            response = requests.post(url, params=params, headers=headers, files=files)

        if response.status_code in [200, 201]:
            result = response.json()
            return result.get('document_id') or result.get('doc_id')
        else:
            print(f"      ❌ Upload failed: {response.text}")
            return None
    except Exception as e:
        print(f"      ❌ Error: {e}")
        return None


def detect_qmd_dirs():
    """Detect QMD sessions directory if QMD is enabled."""
    try:
        config_path = os.path.expanduser("~/.openclaw/openclaw.json")
        if os.path.exists(config_path):
            config = load_json(config_path)
            if config.get("memory", {}).get("backend") == "qmd":
                agent_id = config.get("agents", {}).get("defaults", {}).get("id", "main")
                qmd_dir = os.path.expanduser(f"~/.openclaw/agents/{agent_id}/qmd/sessions")
                if os.path.isdir(qmd_dir):
                    return [qmd_dir]
    except Exception:
        pass
    return []


def main():
    # Load credentials
    creds = load_json(CREDENTIALS_FILE)
    api_key = creds.get("api_key")
    api_base = creds.get("base_url")

    if not api_key or not api_base:
        print("❌ Missing api_key or base_url in credentials.json")
        return

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("📚 Cortex Library Bulk Sync")
    print(f"   Base URL: {api_base}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    collection_id = ensure_collection(api_base, api_key)
    if not collection_id:
        return

    # Load tracking
    tracking = load_json(TRACKING_FILE)
    if "files" not in tracking:
        tracking["files"] = {}

    # Gather memory directories
    memory_dirs = MEMORY_DIRS + detect_qmd_dirs()

    files_to_sync = []
    for directory in memory_dirs:
        if os.path.isdir(directory):
            for file in os.listdir(directory):
                if file.endswith(('.md', '.txt', '.json')):
                    files_to_sync.append(os.path.join(directory, file))

    uploaded_files = []
    skip_count = 0
    upload_count = 0

    print("📤 Phase 1: Uploading new files...")

    for filepath in files_to_sync:
        try:
            current_hash = calculate_sha256(filepath)
            stored_info = tracking["files"].get(filepath)
            if stored_info and stored_info.get("hash") == current_hash:
                skip_count += 1
                continue

            print(f"   📄 {os.path.basename(filepath)}")
            doc_id = upload_file_no_process(filepath, api_base, api_key, collection_id)

            if doc_id:
                uploaded_files.append((filepath, current_hash, doc_id))
                upload_count += 1

                # Save tracking immediately after each upload
                tracking["files"][filepath] = {
                    "hash": current_hash,
                    "document_id": doc_id,
                    "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "uploaded"
                }
                save_json(TRACKING_FILE, tracking)

        except Exception as e:
            print(f"   Error checking {filepath}: {e}")

    print(f"\n📦 Uploaded {upload_count} files, {skip_count} already synced")

    # Phase 2: Batch Process
    if upload_count > 0:
        print("\n🔄 Phase 2: Triggering batch processing...")
        headers = {"X-API-Key": api_key}

        try:
            r = requests.post(f"{api_base}/api/documents/process-pending", headers=headers)
            r_json = r.json()
            task_id = r_json.get("task_id")
            pending_count = r_json.get("pending_count", 0)

            if not task_id:
                print(f"   ⚠️ Could not start processing: {r.text}")
            else:
                print(f"   📊 Processing {pending_count} documents (Task: {task_id})")

                # Phase 3: Wait
                print("\n⏳ Phase 3: Waiting for processing...")
                max_attempts = 120
                final_status = "processing"

                for attempt in range(max_attempts):
                    r = requests.get(f"{api_base}/api/tasks/{task_id}", headers=headers)
                    task_data = r.json()
                    status = task_data.get("status", "").lower()
                    progress = task_data.get("progress_percent", 0)

                    if attempt % 6 == 0:
                        print(f"   Progress: {progress}%")

                    if status == "completed":
                        print("\n   ✅ All documents processed!")
                        final_status = "completed"
                        break
                    elif status == "failed":
                        print(f"\n   ❌ Processing failed: {task_data.get('error')}")
                        final_status = "failed"
                        break

                    time.sleep(5)

                # Update tracking with final status
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                for fpath, fhash, fdoc in uploaded_files:
                    tracking["files"][fpath]["status"] = final_status
                tracking["last_sync"] = timestamp
                save_json(TRACKING_FILE, tracking)

        except Exception as e:
            print(f"❌ Error during batch processing: {e}")

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("📊 Sync complete!")
    print(f"   📤 Uploaded: {upload_count} files")
    print(f"   ⏭️  Skipped: {skip_count} files")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    main()
