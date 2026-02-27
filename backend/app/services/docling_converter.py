"""Docling converter for PDF and DOCX files to Markdown.

This module provides converters that use the Docling library to convert
PDF and DOCX files to Markdown format, which is then converted to
Haystack Documents for further processing.
"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any, Union

from haystack import Document as HaystackDocument

logger = logging.getLogger(__name__)


class DoclingDocumentConverter:
    """Converter for PDF and DOCX files using Docling.

    Docling is a powerful document conversion library that converts
    PDF and DOCX files to structured markdown while preserving:
    - Document structure (headings, lists, tables)
    - Page divisions
    - Formatting information
    """

    def __init__(self):
        """Initialize the Docling converter."""
        self._converter = None
        self._docling_available = False
        self._init_error = None
        self._initialize()

    def _initialize(self):
        """Try to import and initialize docling."""
        try:
            from docling.datamodel.document_converter import DocumentConverter
            self._converter_class = DocumentConverter
            self._docling_available = True
            logger.info("Docling converter initialized successfully")
        except ImportError as e:
            self._docling_available = False
            self._init_error = str(e)
            logger.warning(f"Docling not available: {e}")

    def _get_converter(self):
        """Get or create the Docling document converter instance."""
        if not self._docling_available:
            return None

        if self._converter is None:
            try:
                self._converter = self._converter_class()
                logger.debug("Created Docling DocumentConverter instance")
            except Exception as e:
                logger.error(f"Failed to create Docling converter: {e}")
                self._docling_available = False
                return None

        return self._converter

    def run(
        self,
        sources: List[Path],
        meta: Optional[Union[Dict[str, Any], List[Optional[Dict[str, Any]]]]] = None
    ) -> Dict[str, List[HaystackDocument]]:
        """Convert document files to Haystack Documents.

        Args:
            sources: List of file paths to convert (.pdf, .docx)
            meta: Optional metadata to attach to documents. Can be a single dict
                  (applied to all) or a list of dicts (one per source).

        Returns:
            Dict with "documents" key containing list of HaystackDocument objects.
        """
        if not self._docling_available:
            raise ValueError(
                f"Docling is not available. Please install it with: pip install docling. "
                f"Import error: {self._init_error or 'unknown'}"
            )

        if not sources:
            return {"documents": []}

        converter = self._get_converter()
        if not converter:
            raise ValueError("Failed to initialize Docling converter")

        documents = []

        # Normalize metadata
        if meta is None:
            metas = [None] * len(sources)
        elif isinstance(meta, dict):
            metas = [meta] * len(sources)
        else:
            metas = list(meta)
            if len(metas) < len(sources):
                metas.extend([None] * (len(sources) - len(metas)))

        for source, doc_meta in zip(sources, metas):
            try:
                doc = self._convert_single_document(source, doc_meta, converter)
                if doc:
                    documents.append(doc)
            except Exception as e:
                logger.error(f"Failed to convert {source}: {e}")
                raise ValueError(f"Conversion failed for {source}: {e}")

        return {"documents": documents}

    def _convert_single_document(
        self,
        source: Path,
        meta: Optional[Dict[str, Any]],
        converter
    ) -> Optional[HaystackDocument]:
        """Convert a single document file to a Haystack Document."""
        source = Path(source)

        if not source.exists():
            raise FileNotFoundError(f"File not found: {source}")

        # Convert the document
        logger.debug(f"Converting {source} with Docling")

        conversion_result = converter.convert(str(source))

        if not conversion_result or not conversion_result.document:
            raise ValueError(f"Docling failed to extract content from {source}")

        # Export to markdown
        markdown_content = conversion_result.document.export_to_markdown()

        if not markdown_content or not markdown_content.strip():
            logger.warning(f"No content extracted from {source}")
            markdown_content = ""

        # Build metadata
        document_meta: Dict[str, Any] = {
            "file_path": str(source),
            "file_name": source.name,
            "source_id": str(source),
        }

        # Add page count if available from Docling
        if hasattr(conversion_result.document, 'pages') and conversion_result.document.pages:
            document_meta["page_count"] = len(conversion_result.document.pages)

        # Merge with provided metadata
        if meta:
            document_meta.update(meta)

        # Create Haystack document
        haystack_doc = HaystackDocument(
            content=markdown_content,
            meta=document_meta
        )

        logger.info(f"Successfully converted {source} to markdown ({len(markdown_content)} chars)")

        return haystack_doc


class DoclingPDFConverter(DoclingDocumentConverter):
    """Specialized converter for PDF files using Docling."""
    pass


class DoclingDOCXConverter(DoclingDocumentConverter):
    """Specialized converter for DOCX files using Docling."""
    pass
