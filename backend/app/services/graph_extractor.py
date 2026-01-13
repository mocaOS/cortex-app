"""GraphRAG entity and relationship extraction service using LLM."""

import logging
from typing import Optional, List
import json
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI

from app.config import get_settings
from app.models import Entity, Relationship, ExtractionResult

logger = logging.getLogger(__name__)

# Thread pool for running synchronous LLM calls without blocking the event loop
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="graph_extractor")


# System prompt for entity and relationship extraction (works with any model)
EXTRACTION_SYSTEM_PROMPT = """You are an expert knowledge graph builder. Your task is to extract entities and relationships from text to build a knowledge graph.

## Entity Types to Extract:
- **Person**: Named individuals
- **Organization**: Companies, institutions, groups
- **Location**: Places, regions, addresses
- **Concept**: Abstract ideas, theories, methodologies
- **Technology**: Software, tools, platforms, programming languages
- **Event**: Named events, conferences, incidents
- **Product**: Named products or services
- **Document**: Referenced documents, papers, standards

## Relationship Types to Use:
- **WORKS_FOR**: Person works for Organization
- **LOCATED_IN**: Entity is located in Location
- **USES**: Entity uses Technology/Product
- **RELATED_TO**: General relationship between entities
- **PART_OF**: Entity is part of another entity
- **CREATED_BY**: Entity was created by Person/Organization
- **IMPLEMENTS**: Technology implements Concept
- **MENTIONS**: Document mentions Entity
- **DEPENDS_ON**: Entity depends on another entity
- **IS_A**: Entity is a type of another entity (taxonomy)
- **HAS_PROPERTY**: Entity has a specific property

## Guidelines:
1. Extract only entities that are explicitly mentioned or strongly implied
2. Use consistent naming (e.g., always "Neo4j" not "neo4j" or "Neo4J")
3. Create relationships only between extracted entities
4. Provide brief, contextual descriptions
5. Focus on the most important entities and relationships
6. Avoid extracting overly generic terms unless they're central concepts

IMPORTANT: You MUST respond with ONLY valid JSON, no other text before or after."""


EXTRACTION_USER_PROMPT = """Extract entities and relationships from the following text.

Text:
{text}

Respond with ONLY a JSON object in this exact format (no markdown, no explanation):
{{"entities": [{{"name": "Entity Name", "type": "EntityType", "description": "Brief description"}}], "relationships": [{{"source": "Source Entity", "target": "Target Entity", "relationship_type": "RELATIONSHIP_TYPE", "description": "How they relate"}}]}}"""


QUERY_ENTITY_PROMPT = """Extract entity names from the following question. Focus on specific named entities like people, organizations, technologies, concepts, places, etc.

Question: {query}

Respond with ONLY a JSON object in this exact format (no markdown, no explanation):
{{"entities": ["entity1", "entity2"]}}"""


class GraphExtractor:
    """Extract entities and relationships from text using LLM."""
    
    def __init__(self):
        self.settings = get_settings()
        self._client: Optional[OpenAI] = None
        
        if not self.settings.openai_api_key:
            logger.warning("OpenAI API key not configured - graph extraction will be disabled")
    
    @property
    def client(self) -> Optional[OpenAI]:
        """Lazy initialization of OpenAI client."""
        if self._client is None and self.settings.openai_api_key:
            self._client = OpenAI(
                api_key=self.settings.openai_api_key,
                base_url=self.settings.openai_api_base,
            )
        return self._client
    
    @property
    def is_available(self) -> bool:
        """Check if graph extraction is available."""
        return self.client is not None
    
    def _extract_json_from_response(self, content: str) -> dict:
        """
        Extract JSON from LLM response, handling various formats.
        Works with models that may include markdown or extra text.
        """
        if not content:
            return {}
        
        # Try direct JSON parse first
        try:
            return json.loads(content.strip())
        except json.JSONDecodeError:
            pass
        
        # Try to extract JSON from markdown code blocks
        json_patterns = [
            r'```json\s*([\s\S]*?)\s*```',  # ```json ... ```
            r'```\s*([\s\S]*?)\s*```',       # ``` ... ```
            r'\{[\s\S]*\}',                   # Raw JSON object
        ]
        
        for pattern in json_patterns:
            matches = re.findall(pattern, content, re.MULTILINE)
            for match in matches:
                try:
                    # Clean up the match
                    cleaned = match.strip()
                    if not cleaned.startswith('{'):
                        # Find the first { and last }
                        start = cleaned.find('{')
                        end = cleaned.rfind('}')
                        if start != -1 and end != -1:
                            cleaned = cleaned[start:end+1]
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    continue
        
        logger.warning(f"Could not extract JSON from response: {content[:200]}...")
        return {}
    
    def extract_from_text(self, text: str) -> ExtractionResult:
        """
        Extract entities and relationships from text using LLM.
        
        Args:
            text: The text to extract from
            
        Returns:
            ExtractionResult containing entities and relationships
        """
        if not self.is_available:
            logger.warning("Graph extraction unavailable - returning empty result")
            return ExtractionResult()
        
        try:
            # Make API call without response_format for broader model compatibility
            response = self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": EXTRACTION_USER_PROMPT.format(text=text)}
                ],
                temperature=0.1,  # Low temperature for consistent extraction
                max_tokens=2000,
            )
            
            # Parse the response (handles various formats)
            content = response.choices[0].message.content
            data = self._extract_json_from_response(content)
            
            if not data:
                logger.warning("No valid JSON extracted from LLM response")
                return ExtractionResult()
            
            # Validate and create models
            entities = []
            for e in data.get("entities", []):
                try:
                    name = e.get("name", "") if isinstance(e, dict) else str(e)
                    if isinstance(e, dict):
                        entities.append(Entity(
                            name=name.strip(),
                            type=e.get("type", "Concept").strip(),
                            description=e.get("description", "").strip()
                        ))
                    elif name.strip():
                        entities.append(Entity(
                            name=name.strip(),
                            type="Concept",
                            description=""
                        ))
                except Exception as ex:
                    logger.warning(f"Failed to parse entity {e}: {ex}")
            
            relationships = []
            # Create a set of entity names for validation
            entity_names = {e.name.lower() for e in entities}
            
            for r in data.get("relationships", []):
                try:
                    if not isinstance(r, dict):
                        continue
                    source = r.get("source", "").strip()
                    target = r.get("target", "").strip()
                    
                    # Only include relationships where both entities exist
                    if source.lower() in entity_names and target.lower() in entity_names:
                        relationships.append(Relationship(
                            source=source,
                            target=target,
                            relationship_type=r.get("relationship_type", "RELATED_TO").strip().upper().replace(" ", "_"),
                            description=r.get("description", "").strip()
                        ))
                    else:
                        logger.debug(f"Skipping relationship with unknown entities: {source} -> {target}")
                except Exception as ex:
                    logger.warning(f"Failed to parse relationship {r}: {ex}")
            
            result = ExtractionResult(entities=entities, relationships=relationships)
            logger.info(f"Extracted {len(entities)} entities and {len(relationships)} relationships")
            return result
            
        except Exception as e:
            logger.error(f"Error during graph extraction: {e}")
            return ExtractionResult()
    
    def extract_from_chunks(self, chunks: List[dict]) -> dict[str, ExtractionResult]:
        """
        Extract entities and relationships from multiple text chunks.
        
        Args:
            chunks: List of dicts with 'id' and 'content' keys
            
        Returns:
            Dict mapping chunk_id to ExtractionResult
        """
        results = {}
        
        for chunk in chunks:
            chunk_id = chunk.get("id", "")
            content = chunk.get("content", "")
            
            if content:
                results[chunk_id] = self.extract_from_text(content)
            else:
                results[chunk_id] = ExtractionResult()
        
        return results
    
    # =========================================================================
    # Async methods - run LLM calls in thread pool to avoid blocking event loop
    # =========================================================================
    
    async def extract_from_text_async(self, text: str) -> ExtractionResult:
        """
        Async version of extract_from_text that runs in a thread pool.
        Use this from async contexts to avoid blocking the event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, self.extract_from_text, text)
    
    async def extract_entities_from_query_async(self, query: str) -> List[str]:
        """
        Async version of extract_entities_from_query that runs in a thread pool.
        Use this from async contexts to avoid blocking the event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, self.extract_entities_from_query, query)
    
    def extract_entities_from_query(self, query: str) -> List[str]:
        """
        Extract entity names from a user query for graph lookup.
        
        Args:
            query: The user's question
            
        Returns:
            List of entity names mentioned in the query
        """
        if not self.is_available:
            return []
        
        try:
            response = self.client.chat.completions.create(
                model=self.settings.openai_model,
                messages=[
                    {"role": "system", "content": "You extract entity names from questions. Respond with ONLY valid JSON, no other text."},
                    {"role": "user", "content": QUERY_ENTITY_PROMPT.format(query=query)}
                ],
                temperature=0,
                max_tokens=500,
            )
            
            content = response.choices[0].message.content
            data = self._extract_json_from_response(content)
            entities = [str(e).strip() for e in data.get("entities", []) if e]
            
            logger.info(f"Extracted {len(entities)} entities from query: {entities}")
            return entities
            
        except Exception as e:
            logger.error(f"Error extracting entities from query: {e}")
            return []


# Singleton instance
_graph_extractor: Optional[GraphExtractor] = None


def get_graph_extractor() -> GraphExtractor:
    """Get the singleton GraphExtractor instance."""
    global _graph_extractor
    if _graph_extractor is None:
        _graph_extractor = GraphExtractor()
    return _graph_extractor
