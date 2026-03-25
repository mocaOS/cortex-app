const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const STATE_DIR = path.join(process.env.HOME, '.openclaw/skills/library/state');
const CREDENTIALS_PATH = path.join(STATE_DIR, 'credentials.json');
const TRACKING_PATH = path.join(STATE_DIR, 'uploaded_files.json');
const COLLECTION_NAME = 'OpenClaw';

// Default memory directories
const DEFAULT_MEMORY_DIRS = [
    path.join(process.env.HOME, '.openclaw/memory'),
    path.join(process.env.HOME, '.openclaw/conversations'),
];

function detectQmdDirs() {
    try {
        const configPath = path.join(process.env.HOME, '.openclaw/openclaw.json');
        if (fs.existsSync(configPath)) {
            const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
            if (config?.memory?.backend === 'qmd') {
                const agentId = config?.agents?.defaults?.id || 'main';
                const qmdDir = path.join(process.env.HOME, `.openclaw/agents/${agentId}/qmd/sessions`);
                if (fs.existsSync(qmdDir)) return [qmdDir];
            }
        }
    } catch (e) { /* ignore */ }
    return [];
}

async function main() {
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    console.log("📚 Cortex Library Sync (Node.js)");
    console.log(`   Target collection: ${COLLECTION_NAME}`);
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n");

    // Load Credentials
    if (!fs.existsSync(CREDENTIALS_PATH)) {
        console.error("❌ No credentials found.");
        process.exit(1);
    }
    const creds = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, 'utf8'));
    const { api_key, base_url } = creds;

    if (!api_key || !base_url) {
        console.error("❌ Missing api_key or base_url.");
        process.exit(1);
    }
    console.log(`   Base URL: ${base_url}\n`);

    // Load Tracking
    let tracking = { files: {}, last_sync: null };
    if (fs.existsSync(TRACKING_PATH)) {
        tracking = JSON.parse(fs.readFileSync(TRACKING_PATH, 'utf8'));
    }

    const headers = { 'X-API-Key': api_key };

    // Find/Create Collection
    console.log(`🔍 Finding ${COLLECTION_NAME} collection...`);
    let collectionId = null;
    try {
        const res = await fetch(`${base_url}/api/collections`, { headers });
        if (!res.ok) throw new Error(`API Error: ${res.status}`);
        const data = await res.json();
        const col = data.collections.find(c => c.name === COLLECTION_NAME);
        if (col) collectionId = col.id;
    } catch (e) {
        console.error("❌ Failed to fetch collections:", e.message);
        process.exit(1);
    }

    if (!collectionId) {
        console.log(`   📚 Creating ${COLLECTION_NAME} collection...`);
        const res = await fetch(`${base_url}/api/collections`, {
            method: 'POST',
            headers: { ...headers, 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: COLLECTION_NAME, description: "Memory files synced from agent" })
        });
        const data = await res.json();
        if (!data.id) {
            console.error("❌ Failed to create collection:", JSON.stringify(data));
            process.exit(1);
        }
        collectionId = data.id;
        console.log(`   ✅ Created: ${collectionId}`);
    } else {
        console.log(`   ✅ Found: ${collectionId}`);
    }

    // Update credentials
    creds.collection_id = collectionId;
    fs.writeFileSync(CREDENTIALS_PATH, JSON.stringify(creds, null, 2));

    // Gather memory directories
    const memoryDirs = [...DEFAULT_MEMORY_DIRS, ...detectQmdDirs()];

    // Scan Files
    console.log("\n📤 Uploading new files...");
    const toUpload = [];
    let skipped = 0;

    for (const dir of memoryDirs) {
        if (!fs.existsSync(dir)) continue;
        const files = fs.readdirSync(dir);
        for (const file of files) {
            if (!file.match(/\.(md|txt|json)$/)) continue;
            const filePath = path.join(dir, file);
            const content = fs.readFileSync(filePath);
            const hash = crypto.createHash('sha256').update(content).digest('hex');

            const tracked = tracking.files[filePath];
            if (tracked && tracked.hash === hash && tracked.status === 'completed') {
                skipped++;
                continue;
            }

            toUpload.push({ filePath, file, hash });
        }
    }

    console.log(`   Found ${toUpload.length} files to upload, ${skipped} skipped.\n`);

    if (toUpload.length === 0) {
        console.log("✅ Nothing new to sync.");
        return;
    }

    // Upload Phase
    console.log("📤 Phase 1: Uploading files...");
    const uploaded = [];

    for (const item of toUpload) {
        console.log(`   📄 ${item.file}`);
        try {
            const formData = new FormData();
            const fileBuffer = fs.readFileSync(item.filePath);
            const blob = new Blob([fileBuffer]);
            formData.append('file', blob, item.file);

            // CRITICAL: collection_id and start_processing are URL query parameters
            const url = new URL(`${base_url}/api/upload`);
            url.searchParams.append('collection_id', collectionId);
            url.searchParams.append('start_processing', 'false');

            const res = await fetch(url, {
                method: 'POST',
                headers: headers,
                body: formData
            });

            const data = await res.json();
            if (!data.document_id && !data.doc_id) {
                console.error(`      ❌ Upload failed: ${JSON.stringify(data)}`);
                continue;
            }

            const docId = data.document_id || data.doc_id;
            console.log(`      ✅ -> ${docId}`);
            uploaded.push({ ...item, docId, status: 'pending' });

            // Save tracking immediately after each upload
            tracking.files[item.filePath] = {
                hash: item.hash,
                document_id: docId,
                uploaded_at: new Date().toISOString(),
                status: 'uploaded'
            };
            fs.writeFileSync(TRACKING_PATH, JSON.stringify(tracking, null, 2));

        } catch (e) {
            console.error(`      ❌ Error: ${e.message}`);
        }
    }

    if (uploaded.length === 0) return;

    // Trigger Batch
    console.log("\n🔄 Phase 2: Triggering batch processing...");
    const procRes = await fetch(`${base_url}/api/documents/process-pending`, {
        method: 'POST',
        headers
    });
    const procData = await procRes.json();
    const taskId = procData.task_id;

    if (!taskId) {
        console.error("   ⚠️ Could not start batch processing:", JSON.stringify(procData));
    } else {
        console.log(`   📊 Task ID: ${taskId}. Waiting for completion...`);

        let finalStatus = 'processing';
        for (let attempts = 0; attempts < 60; attempts++) {
            await new Promise(r => setTimeout(r, 5000));

            const tRes = await fetch(`${base_url}/api/tasks/${taskId}`, { headers });
            const tData = await tRes.json();

            const status = (tData.status || '').toLowerCase();
            const progress = tData.progress_percent || 0;

            if (attempts % 3 === 0) console.log(`   Progress: ${progress}%`);

            if (status === 'completed') {
                console.log("\n   ✅ All documents processed!");
                finalStatus = 'completed';
                break;
            } else if (status === 'failed') {
                console.log(`\n   ❌ Processing failed: ${tData.error}`);
                finalStatus = 'failed';
                break;
            }
        }

        // Update tracking with final status
        const now = new Date().toISOString();
        for (const item of uploaded) {
            if (tracking.files[item.filePath]) {
                tracking.files[item.filePath].status = finalStatus;
            }
        }
        tracking.last_sync = now;
        fs.writeFileSync(TRACKING_PATH, JSON.stringify(tracking, null, 2));
    }

    console.log("\n📊 Sync complete!");
}

main().catch(console.error);
