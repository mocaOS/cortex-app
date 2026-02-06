const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const STATE_DIR = path.join(process.env.HOME, '.openclaw/skills/library/state');
const CREDENTIALS_PATH = path.join(STATE_DIR, 'credentials.json');
const TRACKING_PATH = path.join(STATE_DIR, 'uploaded_files.json');
const MEMORY_DIRS = ['/home/tobias/clawd/memory'];
const COLLECTION_NAME = 'OpenClaw';

async function main() {
    console.log("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━");
    console.log("📚 OpenClaw Library Sync (Node.js)");
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

    // Headers
    const headers = { 'X-API-Key': api_key };

    // Find/Create Collection
    console.log("🔍 Finding OpenClaw collection by name...");
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
        console.log("   📚 Creating OpenClaw collection...");
        const res = await fetch(`${base_url}/api/collections`, {
            method: 'POST',
            headers: { ...headers, 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: COLLECTION_NAME, description: "Memory files synced from OpenClaw agent" })
        });
        const data = await res.json();
        if (!data.id) {
            console.error("❌ Failed to create collection:", JSON.stringify(data));
            process.exit(1);
        }
        collectionId = data.id;
        console.log(`   ✅ Created OpenClaw collection: ${collectionId}`);
    } else {
        console.log(`   ✅ Found OpenClaw collection: ${collectionId}`);
    }

    // Update credentials with confirmed ID
    creds.collection_id = collectionId;
    fs.writeFileSync(CREDENTIALS_PATH, JSON.stringify(creds, null, 2));

    // Scan Files
    console.log("\nscan: Scanning for new files...");
    const toUpload = [];
    const skipped = []; // Just count
    
    for (const dir of MEMORY_DIRS) {
        if (!fs.existsSync(dir)) continue;
        const files = fs.readdirSync(dir);
        for (const file of files) {
            if (!file.match(/\.(md|txt|json)$/)) continue;
            const filePath = path.join(dir, file);
            const content = fs.readFileSync(filePath);
            const hash = crypto.createHash('sha256').update(content).digest('hex');
            
            const tracked = tracking.files[filePath];
            if (tracked && tracked.hash === hash && tracked.status === 'completed') {
                skipped.push(file);
                continue;
            }
            
            toUpload.push({ filePath, file, hash });
        }
    }

    console.log(`   Found ${toUpload.length} files to upload, ${skipped.length} skipped.\n`);

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

            // URL Params!
            const url = new URL(`${base_url}/api/upload`);
            url.searchParams.append('collection_id', collectionId);
            url.searchParams.append('start_processing', 'false'); // Bulk mode

            const res = await fetch(url, {
                method: 'POST',
                headers: headers, // Do NOT set Content-Type, let fetch set boundary
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
        // Save as pending
    } else {
        console.log(`   📊 Task ID: ${taskId}. Waiting for completion...`);
        
        // Wait loop
        let attempts = 0;
        let finalStatus = 'processing';
        while (attempts < 60) {
            await new Promise(r => setTimeout(r, 5000));
            attempts++;
            
            const tRes = await fetch(`${base_url}/api/tasks/${taskId}`, { headers });
            const tData = await tRes.json();
            
            const status = (tData.status || '').toLowerCase();
            const progress = tData.progress_percent || 0;
            
            if (attempts % 3 === 0) console.log(`   Progress: ${progress}% - ${tData.message || ''}`);
            
            if (status === 'completed') {
                console.log("\n   ✅ All documents processed successfully!");
                finalStatus = 'completed';
                break;
            } else if (status === 'failed') {
                console.log(`\n   ❌ Processing failed: ${tData.error}`);
                finalStatus = 'failed';
                break;
            }
        }
    }

    // Update Tracking
    console.log("\n📝 Updating tracking records...");
    const now = new Date().toISOString();
    for (const item of uploaded) {
        tracking.files[item.filePath] = {
            hash: item.hash,
            document_id: item.docId,
            uploaded_at: now,
            status: 'completed' // Assume completed if batch succeeded
        };
    }
    tracking.last_sync = now;
    fs.writeFileSync(TRACKING_PATH, JSON.stringify(tracking, null, 2));

    console.log("📊 Sync complete!");
}

main().catch(console.error);
