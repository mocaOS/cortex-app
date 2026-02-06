#!/usr/bin/env python3
import os
import json
import hashlib
import requests
import glob

# Config
CREDENTIALS_FILE = os.path.expanduser("~/.openclaw/skills/library/state/credentials.json")
TRACKING_FILE = os.path.expanduser("~/.openclaw/skills/library/state/uploaded_files.json")
MEMORY_DIR = "/home/tobias/clawd/memory"
LONG_TERM_MEMORY = "/home/tobias/clawd/MEMORY.md"
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

def upload_file(filepath, api_key, collection_id):
    url = f"{API_BASE}/api/upload"
    headers = {"X-API-Key": api_key}
    
    print(f"Uploading {os.path.basename(filepath)}...")
    try:
        with open(filepath, 'rb') as f:
            files = {
                'file': (os.path.basename(filepath), f),
                'collection_id': (None, collection_id),
                'start_processing': (None, 'true')
            }
            response = requests.post(url, headers=headers, files=files)
            
        if response.status_code in [200, 201]:
            result = response.json()
            print(f"✅ Success: {result.get('document_id', 'unknown id')}")
            return result.get('document_id')
        else:
            print(f"❌ Failed: {response.text}")
            return None
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return None

def main():
    # Load credentials
    creds = load_json(CREDENTIALS_FILE)
    api_key = creds.get("api_key")
    collection_id = creds.get("collection_id")

    if not api_key or not collection_id:
        print("⚠️ Missing API key or Collection ID in credentials.json")
        return

    # Load tracking
    tracking = load_json(TRACKING_FILE)
    if not tracking:
        tracking = {"files": {}, "last_sync": None}
    
    files_to_sync = []
    
    # Check long term memory
    if os.path.exists(LONG_TERM_MEMORY):
        files_to_sync.append(LONG_TERM_MEMORY)
        
    # Check daily memories
    if os.path.exists(MEMORY_DIR):
        files_to_sync.extend(glob.glob(os.path.join(MEMORY_DIR, "*.md")))

    changes_count = 0
    
    for filepath in files_to_sync:
        try:
            current_hash = calculate_sha256(filepath)
            filename = os.path.basename(filepath)
            
            # Check if needs sync
            stored_info = tracking["files"].get(filepath)
            if stored_info and stored_info.get("hash") == current_hash:
                # print(f"Skipping {filename} (unchanged)")
                continue
                
            # Upload
            doc_id = upload_file(filepath, api_key, collection_id)
            if doc_id:
                tracking["files"][filepath] = {
                    "hash": current_hash,
                    "document_id": doc_id,
                    "uploaded_at": str(os.path.getmtime(filepath))
                }
                changes_count += 1
                
        except Exception as e:
            print(f"Error processing {filepath}: {e}")

    if changes_count > 0:
        print(f"Synced {changes_count} files.")
        save_json(TRACKING_FILE, tracking)
    else:
        print("No changes to sync.")

if __name__ == "__main__":
    main()
