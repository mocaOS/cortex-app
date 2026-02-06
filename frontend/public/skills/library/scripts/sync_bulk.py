#!/usr/bin/env python3
import os
import json
import hashlib
import time
import requests
import glob
import sys

# Config
CREDENTIALS_FILE = os.path.expanduser("~/.openclaw/skills/library/state/credentials.json")
TRACKING_FILE = os.path.expanduser("~/.openclaw/skills/library/state/uploaded_files.json")
MEMORY_DIRS = [
    "/home/tobias/clawd/memory",
    "/home/tobias/clawd/conversations",
    "/home/tobias/.openclaw/memory"
]
API_BASE = "https://library.moca.qwellco.de"

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

def ensure_collection(api_key, collection_id_from_file):
    if collection_id_from_file:
        return collection_id_from_file
    
    print("🔍 Looking for OpenClaw collection...")
    headers = {"X-API-Key": api_key}
    
    try:
        r = requests.get(f"{API_BASE}/api/collections", headers=headers)
        r.raise_for_status()
        collections = r.json().get("collections", [])
        
        for col in collections:
            if col.get("name") == "OpenClaw":
                return col.get("id")
        
        # Create if not found
        print("📚 Creating OpenClaw collection...")
        r = requests.post(f"{API_BASE}/api/collections", headers=headers, json={
            "name": "OpenClaw",
            "description": "Memory files synced from OpenClaw agent"
        })
        r.raise_for_status()
        new_id = r.json().get("id")
        
        # Save credentials
        creds = load_json(CREDENTIALS_FILE)
        creds["collection_id"] = new_id
        save_json(CREDENTIALS_FILE, creds)
        
        return new_id
    except Exception as e:
        print(f"❌ Error finding/creating collection: {e}")
        return None

def upload_file_no_process(filepath, api_key, collection_id):
    url = f"{API_BASE}/api/upload"
    headers = {"X-API-Key": api_key}
    
    try:
        with open(filepath, 'rb') as f:
            files = {
                'file': (os.path.basename(filepath), f),
                'collection_id': (None, collection_id),
                'start_processing': (None, 'false')
            }
            response = requests.post(url, headers=headers, files=files)
            
        if response.status_code in [200, 201]:
            result = response.json()
            doc_id = result.get('document_id') or result.get('doc_id')
            return doc_id
        else:
            print(f"  ❌ Upload failed: {response.text}")
            return None
    except Exception as e:
        print(f"  ❌ Error uploading {filepath}: {e}")
        return None

def main():
    # Load credentials
    creds = load_json(CREDENTIALS_FILE)
    api_key = creds.get("api_key")
    
    if not api_key:
        print("❌ No API key configured.")
        return

    collection_id = ensure_collection(api_key, creds.get("collection_id"))
    if not collection_id:
        return

    # Load tracking
    tracking = load_json(TRACKING_FILE)
    if "files" not in tracking:
        tracking["files"] = {}

    files_to_sync = []
    
    # Scan directories
    for directory in MEMORY_DIRS:
        if os.path.isdir(directory):
            for file in os.listdir(directory):
                if file.endswith(('.md', '.txt')):
                    files_to_sync.append(os.path.join(directory, file))

    # Add main MEMORY.md if exists
    main_memory = "/home/tobias/clawd/MEMORY.md"
    if os.path.exists(main_memory):
        files_to_sync.append(main_memory)

    uploaded_files = [] # Tuples of (path, hash, doc_id)
    skip_count = 0
    upload_count = 0

    print("📤 Phase 1: Uploading new files...")
    
    for filepath in files_to_sync:
        try:
            current_hash = calculate_sha256(filepath)
            filename = os.path.basename(filepath)
            
            # Check if needs sync
            stored_info = tracking["files"].get(filepath)
            if stored_info and stored_info.get("hash") == current_hash:
                skip_count += 1
                continue
            
            print(f"  📄 {filename}")
            doc_id = upload_file_no_process(filepath, api_key, collection_id)
            
            if doc_id:
                uploaded_files.append((filepath, current_hash, doc_id))
                upload_count += 1
                
        except Exception as e:
            print(f"Error checking {filepath}: {e}")

    print(f"\n📦 Uploaded {upload_count} files, {skip_count} already synced")

    # Phase 2: Batch Process
    if upload_count > 0:
        print("\n🔄 Phase 2: Triggering batch processing...")
        headers = {"X-API-Key": api_key}
        
        try:
            r = requests.post(f"{API_BASE}/api/documents/process-pending", headers=headers)
            r_json = r.json()
            task_id = r_json.get("task_id")
            pending_count = r_json.get("pending_count", 0)
            
            if not task_id:
                print(f"  ⚠️ Could not start processing: {r.text}")
                # Save as pending
                timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                for fpath, fhash, fdoc in uploaded_files:
                    tracking["files"][fpath] = {
                        "hash": fhash,
                        "document_id": fdoc,
                        "uploaded_at": timestamp,
                        "status": "pending"
                    }
                    tracking["last_sync"] = timestamp
                save_json(TRACKING_FILE, tracking)
                return

            print(f"  📊 Processing {pending_count} documents (Task: {task_id})")
            
            # Phase 3: Wait
            print("\n⏳ Phase 3: Waiting for processing to complete...")
            max_attempts = 120
            final_status = "processing"
            
            for attempt in range(max_attempts):
                r = requests.get(f"{API_BASE}/api/tasks/{task_id}", headers=headers)
                task_data = r.json()
                status = task_data.get("status", "").lower()
                progress = task_data.get("progress_percent", 0)
                msg = task_data.get("message", "")
                
                if attempt % 3 == 0:
                    print(f"  Progress: {progress}% - {msg}")
                
                if status == "completed":
                    print("\n  ✅ All documents processed successfully!")
                    final_status = "completed"
                    break
                elif status == "failed":
                    print(f"\n  ❌ Processing failed: {task_data.get('error')}")
                    final_status = "failed"
                    break
                
                time.sleep(5)
            
            # Phase 4: Update Tracking
            print("\n📝 Updating tracking records...")
            timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            
            for fpath, fhash, fdoc in uploaded_files:
                doc_status = final_status
                # If completed, double check doc status (optional, skipping for speed/simplicity or assume success)
                tracking["files"][fpath] = {
                    "hash": fhash,
                    "document_id": fdoc,
                    "uploaded_at": timestamp,
                    "status": doc_status
                }
                tracking["last_sync"] = timestamp
            
            save_json(TRACKING_FILE, tracking)

        except Exception as e:
            print(f"❌ Error during batch processing: {e}")

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("📊 Sync complete!")
    print(f"  📤 Uploaded: {upload_count} files")
    print(f"  ⏭️ Skipped: {skip_count} files")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    main()
