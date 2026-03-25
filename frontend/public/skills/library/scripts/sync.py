#!/usr/bin/env python3
"""Cortex Library Sync Script - Single file upload with immediate processing.

Reads base_url from credentials (no hardcoded URLs).
Uses URL query parameters for upload API.
"""
import os
import json
import hashlib
import requests
import glob

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
        print(f"📚 Creating {COLLECTION_NAME} collection...")
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
        print(f"❌ Error finding/creating collection: {e}")
        return None


def upload_file(filepath, api_base, api_key, collection_id):
    """Upload a single file with immediate processing using URL query parameters."""
    # CRITICAL: collection_id and start_processing are URL query parameters
    url = f"{api_base}/api/upload"
    params = {"collection_id": collection_id, "start_processing": "true"}
    headers = {"X-API-Key": api_key}

    print(f"   📄 {os.path.basename(filepath)}")
    try:
        with open(filepath, 'rb') as f:
            files = {'file': (os.path.basename(filepath), f)}
            response = requests.post(url, params=params, headers=headers, files=files)

        if response.status_code in [200, 201]:
            result = response.json()
            doc_id = result.get('document_id', result.get('doc_id'))
            print(f"      ✅ {doc_id}")
            return doc_id
        else:
            print(f"      ❌ Failed: {response.text}")
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
    print("📚 Cortex Library Sync")
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

    changes_count = 0
    skip_count = 0

    print("📤 Uploading new files...")
    for filepath in files_to_sync:
        try:
            current_hash = calculate_sha256(filepath)
            stored_info = tracking["files"].get(filepath)
            if stored_info and stored_info.get("hash") == current_hash:
                skip_count += 1
                continue

            doc_id = upload_file(filepath, api_base, api_key, collection_id)
            if doc_id:
                tracking["files"][filepath] = {
                    "hash": current_hash,
                    "document_id": doc_id,
                    "uploaded_at": str(os.path.getmtime(filepath)),
                    "status": "processing"
                }
                # Save tracking immediately after each upload
                save_json(TRACKING_FILE, tracking)
                changes_count += 1

        except Exception as e:
            print(f"   Error processing {filepath}: {e}")

    tracking["last_sync"] = __import__('datetime').datetime.utcnow().isoformat() + "Z"
    save_json(TRACKING_FILE, tracking)

    print(f"\n📊 Sync complete! Uploaded: {changes_count}, Skipped: {skip_count}")


if __name__ == "__main__":
    main()
