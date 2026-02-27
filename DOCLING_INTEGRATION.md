# Docling Integration - Document Processing

## Overview

Docling has been successfully integrated into the MOCA Knowledge Base backend to handle multi-format document processing. This integration replaces the previous PDF-only processing with support for a wide range of document formats.

## What is Docling?

Docling is an advanced document processing library that provides:
- Multi-format document parsing (PDF, DOCX, PPTX, XLSX, HTML, Markdown, images)
- Advanced PDF understanding with page layout, reading order, and table structure
- OCR support for scanned documents and images
- Unified document representation format
- Export to Markdown, HTML, and other formats

## Integration Details

### Backend Changes

#### 1. Requirements Updated (`backend/requirements.txt`)

```txt
# Document Processing - Docling handles PDF, DOCX, PPTX, XLSX, HTML, MD, images
docling==2.15.0
```

**Note:** Version 2.15.0 was selected for stability. The library was also updated to use:
- `pydantic>=2.7.0` (upgraded from 2.6.1 for compatibility)
- `pydantic-settings>=2.3.0` (updated for compatibility)

#### 2. Document Processor Service (`backend/app/services/document_processor.py`)

The `DocumentProcessor` class now uses Docling's `DocumentConverter`:

```python
from docling.document_converter import DocumentConverter

class DocumentProcessor:
    def __init__(self):
        # Initialize Docling converter for multi-format support
        self.docling_converter = DocumentConverter()
```

#### 3. Supported Formats (`_is_supported_format` method)

The system now supports the following document formats:

**Documents:**
- `.pdf` - PDF documents
- `.docx` - Microsoft Word documents
- `.pptx` - Microsoft PowerPoint presentations
- `.xlsx` - Microsoft Excel spreadsheets

**Web & Markup:**
- `.html`, `.htm` - HTML documents
- `.md`, `.markdown` - Markdown files

**Text:**
- `.txt` - Plain text files
- `.csv` - CSV data files

**Images (with OCR support):**
- `.png` - PNG images
- `.tiff`, `.tif` - TIFF images
- `.jpeg`, `.jpg` - JPEG images
- `.bmp` - Bitmap images

**Other formats:**
- `.xml` - XML documents
- `.json` - JSON files

### Processing Pipeline

1. **Upload** - Files are uploaded via `/api/upload`
2. **Validation** - File type is checked against supported formats
3. **Conversion** - Docling converts the document to Markdown
4. **Chunking** - The Markdown content is split into chunks
5. **Embedding** - Embeddings are generated for each chunk
6. **GraphRAG** - Entities and relationships are extracted
7. **Storage** - Everything is stored in Neo4j

## Testing

### Test DOCX Upload

```bash
# Create a test DOCX file (inside container)
docker exec moca-backend python -c "
from docx import Document
doc = Document()
doc.add_heading('Test Document for Docling', 0)
doc.add_paragraph('This is a test DOCX file to verify Docling integration.')
doc.save('/tmp/test.docx')
"

# Copy to host
docker cp moca-backend:/tmp/test.docx /tmp/test.docx

# Upload to knowledge base
curl -X POST "http://localhost:8000/api/upload?start_processing=true" \
  -H "X-API-Key: moca_admin_39080adac97a3aeb54e5984536a73a23" \
  -F "file=@/tmp/test.docx"

# Check processing status
curl -s "http://localhost:8000/api/documents" \
  -H "X-API-Key: moca_admin_39080adac97a3aeb54e5984536a73a23" | python3 -m json.tool
```

### Verify Docling Installation

```bash
# Check Docling is properly installed
docker exec moca-backend python -c "
from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat

print('Supported formats:', [fmt.value for fmt in InputFormat])
converter = DocumentConverter()
print('✓ Docling initialized successfully')
"
```

### Search Processed Documents

```bash
# Search for content in processed documents
curl -X POST "http://localhost:8000/api/search" \
  -H "X-API-Key: moca_admin_39080adac97a3aeb54e5984536a73a23" \
  -H "Content-Type: application/json" \
  -d '{"query": "your search query", "top_k": 5}'
```

## Configuration

### Environment Variables

No additional environment variables are required for Docling. The existing configuration works:

- `UPLOAD_DIR` - Directory for uploaded files (default: `/app/uploads`)
- `ENABLE_GRAPH_EXTRACTION` - Enable/disable GraphRAG extraction
- `CHUNK_BY` - Chunking method ("sentence" or "word")
- `SENTENCES_PER_CHUNK` - Sentences per chunk when using sentence-based chunking

### Docker Configuration

The backend Docker image includes Docling and all dependencies:

```dockerfile
# Install Python dependencies (including docling)
RUN pip install --no-cache-dir -r requirements.txt
```

## Performance Considerations

### Processing Speed

- **DOCX files**: Very fast (~0.1 seconds for typical documents)
- **PDF files**: Fast with advanced layout understanding
- **Images**: Slower due to OCR processing
- **Large documents**: Automatically handled with chunking

### Resource Usage

Docling uses:
- **CPU**: For document parsing and conversion
- **Memory**: Proportional to document size
- **Disk**: Temporary storage during processing

The backend uses a thread pool executor to run Docling conversions without blocking the async event loop, allowing multiple documents to be processed concurrently.

## Troubleshooting

### Common Issues

#### 1. "Unsupported file type: .docx"

**Cause:** Docling not installed or version mismatch

**Solution:**
```bash
# Rebuild backend with updated requirements
docker-compose build backend
docker-compose up -d
```

#### 2. ImportError: No module named 'docling'

**Cause:** Dependencies not installed in container

**Solution:**
```bash
# Rebuild the Docker image
docker-compose build --no-cache backend
docker-compose up -d
```

#### 3. Processing fails with no content extracted

**Cause:** Document might be corrupted or in an unsupported format

**Solution:**
- Check the document opens correctly in its native application
- Try converting to a different format (e.g., DOCX to PDF)
- Check backend logs: `docker logs moca-backend`

### Checking Logs

```bash
# View recent backend logs
docker logs moca-backend 2>&1 | tail -50

# Filter for Docling-related messages
docker logs moca-backend 2>&1 | grep -i docling

# Filter for document processing
docker logs moca-backend 2>&1 | grep "document\|processing\|converted"
```

## API Endpoints

### Upload Documents

```bash
POST /api/upload
```

**Parameters:**
- `file`: The document file (multipart form)
- `collection_id` (optional): Collection to add document to
- `start_processing` (optional): Start processing immediately (default: false)

**Response:**
```json
{
  "document_id": "uuid",
  "filename": "document.docx",
  "status": "pending",
  "message": "File uploaded. Call /api/documents/process-pending to start processing."
}
```

### Process Pending Documents

```bash
POST /api/documents/process-pending
```

**Response:**
```json
{
  "task_id": "task_uuid",
  "status": "pending",
  "pending_count": 5,
  "message": "Started processing 5 documents"
}
```

### Check Task Status

```bash
GET /api/tasks/{task_id}
```

**Response:**
```json
{
  "task_id": "task_uuid",
  "status": "completed",
  "progress_percent": 100.0,
  "result": {
    "processed": 5,
    "failed": 0
  }
}
```

## Architecture

```
┌─────────────────┐
│  Upload API     │
│  /api/upload    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ File Validation │
│ - Check format  │
│ - Store file    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Docling         │
│ Converter       │
│ - PDF → MD      │
│ - DOCX → MD     │
│ - PPTX → MD     │
│ - etc.          │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Chunking        │
│ - Split MD      │
│ - URL protection│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Embedding       │
│ - Generate      │
│   embeddings    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ GraphRAG        │
│ - Extract       │
│   entities      │
│ - Extract       │
│   relationships │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Neo4j Storage   │
│ - Chunks        │
│ - Entities      │
│ - Relationships │
└─────────────────┘
```

## Benefits

1. **Multi-format Support**: No longer limited to PDFs
2. **Better Quality**: Docling's advanced PDF parsing preserves structure
3. **OCR Support**: Can process scanned documents and images
4. **Unified Pipeline**: Same processing for all document types
5. **Future-proof**: Easy to add new formats as Docling supports them

## Maintenance

### Updating Docling

To update to a newer version of Docling:

1. Update `backend/requirements.txt`:
   ```txt
   docling==x.y.z  # New version
   ```

2. Rebuild and restart:
   ```bash
   docker-compose build backend
   docker-compose up -d
   ```

3. Verify:
   ```bash
   docker exec moca-backend python -c "import docling; print(docling.__version__)"
   ```

### Adding New Formats

New formats are automatically supported if Docling adds them. Just update the `_is_supported_format()` method in `document_processor.py` to include the new extension.

## References

- [Docling GitHub Repository](https://github.com/docling-project/docling)
- [Docling Documentation](https://docling-project.github.io/docling/)
- [Docling Technical Report](https://arxiv.org/abs/2408.09869)
- [Docling Release Notes](https://github.com/docling-project/docling/releases)

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review backend logs
3. Consult Docling documentation
4. Open an issue in the project repository