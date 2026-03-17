"""GraphRAG entity and relationship extraction service using LLM.

High-quality knowledge graph extraction using XML-formatted prompts.
"""

import logging
from typing import Optional, List, Callable, Awaitable
import json
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI, AsyncOpenAI

from app.config import get_settings
from app.models import Entity, Relationship, ExtractionResult
from app.services.llm_config import get_llm_config, get_extraction_llm_config, is_turbo_mode_active

logger = logging.getLogger(__name__)

# Thread pool for running synchronous LLM calls - size matches concurrent_extractions setting
# This is used as fallback; prefer async methods which use AsyncOpenAI directly
_settings = get_settings()
_executor = ThreadPoolExecutor(
    max_workers=max(_settings.concurrent_extractions, 10),
    thread_name_prefix="graph_extractor"
)
logger.info(f"Graph extractor thread pool initialized with {max(_settings.concurrent_extractions, 10)} workers")


# =============================================================================
# Default Entity and Relationship Type Constraints
# =============================================================================

DEFAULT_ENTITY_TYPES = [
    "Person",
    "Organization", 
    "Location",
    "Concept",
    "Technology",
    "Event",
    "Product",
    "Document",
    "System",
    "Process",
]

DEFAULT_RELATION_TYPES = [
    "WORKS_FOR",
    "LOCATED_IN",
    "USES",
    "RELATED_TO",
    "PART_OF",
    "CREATED_BY",
    "IMPLEMENTS",
    "MENTIONS",
    "DEPENDS_ON",
    "IS_A",
    "HAS_PROPERTY",
    "FOUNDED_BY",
    "FEATURES",
    "CONTAINS",
    "INTERACTS_WITH",
]


# =============================================================================
# Graph Extraction Prompts (XML Format for Better Parsing)
# =============================================================================

EXTRACTION_SYSTEM_PROMPT = """You are an expert knowledge graph builder. Your task is to extract entities and their relationships from text to build a comprehensive knowledge graph.

# Goal
Given text (and optionally a document summary for context), identify all entities and their entity types, along with all relationships among the identified entities.

# Steps
1. Identify all entities in the text. For each identified entity, extract:
   - entity: Name of the entity, properly capitalized
   - entity_type: Type of the entity (use the provided entity types if specified)
   - entity_description: Comprehensive description of the entity based on the text

   Format each entity in XML tags as follows:
   <entity name="entity"><type>entity_type</type><description>entity_description</description></entity>

   Note: Generate additional entities from descriptions if they contain named entities needed for relationship mapping.

2. From the identified entities, identify all related entity pairs:
   - source_entity: name of the source entity
   - target_entity: name of the target entity
   - relation: relationship type (use the provided relation types if specified)
   - relationship_description: justification and context for the relationship
   - relationship_weight: strength score from 0-10 (10 = strongest/most direct)

   Format each relationship in XML tags as follows:
   <relationship><source>source_entity</source><target>target_entity</target><type>relation</type><description>relationship_description</description><weight>relationship_weight</weight></relationship>

3. Coverage Requirements:
   - Each entity should have at least one relationship when possible
   - Create intermediate entities if needed to establish relationships
   - Focus on the most significant and well-supported relationships
   - Use consistent naming for entities (e.g., always "Neo4j" not "neo4j")

IMPORTANT: Output ONLY the XML entities and relationships, no other text."""


EXTRACTION_USER_PROMPT = """Extract entities and relationships from the following text.

Entity Types: {entity_types}
Relation Types: {relation_types}

{context_section}

Text:
{text}

######################
Example Output (for reference):
<entity name="OpenAI"><type>Organization</type><description>OpenAI is an AI research and deployment company known for developing GPT models.</description></entity>
<entity name="GPT-4"><type>Technology</type><description>GPT-4 is a large language model developed by OpenAI.</description></entity>
<relationship><source>OpenAI</source><target>GPT-4</target><type>CREATED_BY</type><description>GPT-4 was developed by OpenAI.</description><weight>9</weight></relationship>

######################
Now extract entities and relationships from the text above:"""


# Document summary prompt for context generation
SUMMARY_PROMPT = """Generate a descriptive summary of the following document. The summary should:
- Be roughly 10% of the input document size
- Retain key points, entities, and relationships mentioned
- Provide context for entity extraction

Document:
{document}

Summary:"""


# Entity description enrichment prompt
ENTITY_DESCRIPTION_PROMPT = """Given the following information about an entity, generate a comprehensive description.

Document Context:
{document_summary}

Entity Information:
- Name: {entity_name}
- Type: {entity_type}
- Current Description: {entity_description}

Related Entities:
{relationships_txt}

Generate a comprehensive entity description that:
1. Opens with a clear definition identifying the entity's primary classification and function
2. Incorporates key data points from the document context
3. Emphasizes the entity's role within its broader context
4. Highlights critical relationships

Format Requirements:
- Length: 2-3 sentences
- Style: Technical and precise
- Tone: Objective and authoritative

Enhanced Description:"""


# Query entity extraction prompt
QUERY_ENTITY_PROMPT = """Extract entity names from the following question. Focus on specific named entities like people, organizations, technologies, concepts, places, etc.

Question: {query}

Output ONLY the XML format:
<entities>
<entity>entity_name_1</entity>
<entity>entity_name_2</entity>
</entities>"""


# =============================================================================
# Entity-Only Extraction Prompt (Phase A - per-document extraction)
# =============================================================================

ENTITY_EXTRACTION_SYSTEM_PROMPT = """You are an expert knowledge graph builder. Your task is to extract entities from text to build a comprehensive knowledge graph.

# Goal
Given text and a document summary for context, identify all important entities and their types.

# Steps
1. Identify all entities in the text. For each entity, extract:
   - entity: Name of the entity, properly capitalized
   - entity_type: Type of the entity (use the provided entity types)
   - entity_description: Comprehensive description of the entity based on the text

   Format each entity in XML tags as follows:
   <entity name="entity"><type>entity_type</type><description>entity_description</description></entity>

2. Coverage Requirements:
   - Extract ALL named entities (people, organizations, technologies, concepts, etc.)
   - Use consistent naming (e.g., always "Neo4j" not "neo4j")
   - Include entities that are referenced indirectly
   - Provide rich descriptions that capture the entity's role and context

IMPORTANT: Output ONLY the XML entities, no other text."""


ENTITY_EXTRACTION_USER_PROMPT = """Extract ALL named entities from the following document section. Be thorough - extract every person, organization, technology, concept, location, event, and any other named entity you can identify. Do not limit yourself to only the most important ones.

Entity Types: {entity_types}

Document Summary (for context):
{document_summary}

Text:
{text}

######################
Example Output:
<entity name="OpenAI"><type>Organization</type><description>OpenAI is an AI research and deployment company known for developing GPT models.</description></entity>
<entity name="GPT-4"><type>Technology</type><description>GPT-4 is a large language model developed by OpenAI.</description></entity>

######################
Now extract all entities from the text above:"""


# =============================================================================
# Relationship Analysis Prompt (Phase B - cross-document relationship discovery)
# =============================================================================

RELATIONSHIP_ANALYSIS_SYSTEM_PROMPT = """You are an expert knowledge graph analyst. Your task is to identify meaningful relationships between entities in a knowledge graph.

# Goal
Given a list of entities (with their types and descriptions), identify all meaningful relationships between them.

# Steps
1. Analyze the entities and their descriptions to find connections.
2. For each relationship found, extract:
   - source_entity: name of the source entity (MUST be from the provided list)
   - target_entity: name of the target entity (MUST be from the provided list)
   - relation: relationship type (use the provided relation types)
   - relationship_description: explanation of why these entities are related
   - relationship_weight: strength score from 0-10 (10 = strongest/most direct)

   Format each relationship in XML tags:
   <relationship><source>source_entity</source><target>target_entity</target><type>relation</type>
   <description>relationship_description</description><weight>relationship_weight</weight></relationship>

3. Requirements:
   - ONLY use entity names from the provided list
   - Focus on meaningful, well-supported relationships
   - Infer relationships from entity descriptions and context
   - Include both direct and indirect relationships
   - Prioritize relationships that connect different entity types

IMPORTANT: Output ONLY the XML relationships, no other text."""


RELATIONSHIP_ANALYSIS_USER_PROMPT = """Analyze the following entities and identify all meaningful relationships between them.

Relation Types: {relation_types}

{context_section}

=== Entities ===
{entity_list}

######################
Example Output:
<relationship><source>OpenAI</source><target>GPT-4</target><type>CREATED_BY</type>
<description>GPT-4 was developed by OpenAI as their flagship large language model.</description><weight>9</weight></relationship>

######################
Now identify all relationships between the entities listed above:"""


class GraphExtractor:
    """Extract entities and relationships from text using LLM prompts."""
    
    def __init__(self):
        self.settings = get_settings()
        self._client: Optional[OpenAI] = None
        self._async_client: Optional[AsyncOpenAI] = None
        self._extraction_client: Optional[OpenAI] = None
        self._async_extraction_client: Optional[AsyncOpenAI] = None
        self._async_embed_client: Optional[AsyncOpenAI] = None
        self._last_config_hash: Optional[str] = None  # Track config changes for turbo mode
        self._last_extraction_config_hash: Optional[str] = None
        self.entity_types = DEFAULT_ENTITY_TYPES
        self.relation_types = DEFAULT_RELATION_TYPES

        if not self.settings.openai_api_key:
            logger.warning("OpenAI API key not configured - graph extraction will be disabled")
    
    def _get_config_hash(self) -> str:
        """Get a hash of current LLM config to detect changes (e.g., turbo mode toggle)."""
        config = get_llm_config()
        return f"{config.base_url}:{config.api_key[:8] if config.api_key else 'none'}"

    def _get_extraction_config_hash(self) -> str:
        """Get a hash of current extraction LLM config to detect changes."""
        config = get_extraction_llm_config()
        return f"{config.base_url}:{config.api_key[:8] if config.api_key else 'none'}"

    def _reset_clients_if_config_changed(self):
        """Reset clients if the LLM configuration has changed (e.g., turbo mode toggled)."""
        current_hash = self._get_config_hash()
        if self._last_config_hash and self._last_config_hash != current_hash:
            logger.info("LLM configuration changed (turbo mode toggle), recreating clients")
            self._client = None
            self._async_client = None
        self._last_config_hash = current_hash

    def _reset_extraction_clients_if_config_changed(self):
        """Reset extraction clients if the extraction LLM config has changed."""
        current_hash = self._get_extraction_config_hash()
        if self._last_extraction_config_hash and self._last_extraction_config_hash != current_hash:
            logger.info("Extraction LLM configuration changed, recreating extraction clients")
            self._extraction_client = None
            self._async_extraction_client = None
        self._last_extraction_config_hash = current_hash
    
    @property
    def client(self) -> Optional[OpenAI]:
        """
        Lazy initialization of synchronous OpenAI client.
        Uses turbo mode URL when active, otherwise falls back to default settings.
        """
        self._reset_clients_if_config_changed()
        
        config = get_llm_config()
        if self._client is None and config.api_key:
            self._client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=120.0,
                max_retries=2,
            )
            if config.is_turbo:
                logger.info(f"Graph extractor using Turbo Mode: {config.base_url}")
        return self._client
    
    @property
    def async_client(self) -> Optional[AsyncOpenAI]:
        """
        Lazy initialization of async OpenAI client for concurrent processing.
        Uses turbo mode URL when active, otherwise falls back to default settings.
        """
        self._reset_clients_if_config_changed()
        
        config = get_llm_config()
        if self._async_client is None and config.api_key:
            self._async_client = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=120.0,
                max_retries=2,
            )
            if config.is_turbo:
                logger.info(f"Async graph extractor using Turbo Mode: {config.base_url}")
        return self._async_client
    
    @property
    def extraction_client(self) -> Optional[OpenAI]:
        """
        Lazy initialization of synchronous OpenAI client for extraction.
        Uses extraction-specific config (separate model/endpoint for entity extraction).
        """
        self._reset_extraction_clients_if_config_changed()

        config = get_extraction_llm_config()
        if self._extraction_client is None and config.api_key:
            self._extraction_client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=120.0,
                max_retries=2,
            )
            if config.is_turbo:
                logger.info(f"Extraction client using Turbo Mode: {config.base_url}")
            else:
                logger.info(f"Extraction client initialized: {config.base_url} / {config.model}")
        return self._extraction_client

    @property
    def async_extraction_client(self) -> Optional[AsyncOpenAI]:
        """
        Lazy initialization of async OpenAI client for extraction.
        Uses extraction-specific config (separate model/endpoint for entity extraction).
        """
        self._reset_extraction_clients_if_config_changed()

        config = get_extraction_llm_config()
        if self._async_extraction_client is None and config.api_key:
            self._async_extraction_client = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=120.0,
                max_retries=2,
            )
            if config.is_turbo:
                logger.info(f"Async extraction client using Turbo Mode: {config.base_url}")
        return self._async_extraction_client

    @property
    def extraction_model_name(self) -> str:
        """Get the current extraction model name."""
        config = get_extraction_llm_config()
        return config.model

    @property
    def current_model(self) -> str:
        """Get the current model to use (turbo model if active, otherwise default)."""
        config = get_llm_config()
        return config.model

    @property
    def is_available(self) -> bool:
        """Check if graph extraction is available."""
        return self.client is not None
    
    def _extract_response_content(self, response) -> Optional[str]:
        """Extract usable text content from an LLM response.

        Handles reasoning models (MiniMax-M2.1, DeepSeek-R1, etc.) that may
        put output in ``reasoning_content`` or wrap it in ``<think>`` tags
        instead of using the standard ``content`` field.
        """
        msg = response.choices[0].message
        content = msg.content

        if not content:
            content = getattr(msg, "reasoning_content", None) or getattr(msg, "refusal", None)

        if not content:
            logger.warning(
                f"LLM returned empty/None content "
                f"(model={self.current_model}, finish_reason={response.choices[0].finish_reason}, "
                f"has_tool_calls={bool(getattr(msg, 'tool_calls', None))}, "
                f"keys={[k for k in vars(msg) if not k.startswith('_')]})"
            )
            return None

        # Strip <think>…</think> blocks that reasoning models may prepend
        content = re.sub(r"<think>[\s\S]*?</think>\s*", "", content, flags=re.IGNORECASE).strip()

        return content or None

    def _extract_xml_entities(self, content: str) -> List[dict]:
        """
        Extract entities from XML-formatted LLM response.
        Handles format: <entity name="..."><type>...</type><description>...</description></entity>
        """
        entities = []
        
        # Pattern for XML entity format
        entity_pattern = r'<entity\s+name="([^"]+)"[^>]*>\s*<type>([^<]+)</type>\s*<description>([\s\S]*?)</description>\s*</entity>'
        
        matches = re.findall(entity_pattern, content, re.IGNORECASE | re.DOTALL)
        for name, etype, description in matches:
            entities.append({
                "name": name.strip(),
                "type": etype.strip(),
                "description": description.strip()
            })
        
        # Also try simpler format without description
        simple_pattern = r'<entity\s+name="([^"]+)"[^>]*>\s*<type>([^<]+)</type>\s*</entity>'
        simple_matches = re.findall(simple_pattern, content, re.IGNORECASE | re.DOTALL)
        existing_names = {e["name"].lower() for e in entities}
        for name, etype in simple_matches:
            if name.strip().lower() not in existing_names:
                entities.append({
                    "name": name.strip(),
                    "type": etype.strip(),
                    "description": ""
                })
        
        return entities
    
    def _extract_xml_relationships(self, content: str) -> List[dict]:
        """
        Extract relationships from XML-formatted LLM response.
        Handles format: <relationship><source>...</source><target>...</target><type>...</type><description>...</description><weight>...</weight></relationship>
        """
        relationships = []
        
        # Pattern for full XML relationship format
        rel_pattern = r'<relationship>\s*<source>([^<]+)</source>\s*<target>([^<]+)</target>\s*<type>([^<]+)</type>\s*<description>([^<]*)</description>\s*<weight>([^<]*)</weight>\s*</relationship>'
        
        matches = re.findall(rel_pattern, content, re.IGNORECASE | re.DOTALL)
        for source, target, rtype, description, weight in matches:
            try:
                weight_val = float(weight.strip()) if weight.strip() else 5.0
            except ValueError:
                weight_val = 5.0
            
            relationships.append({
                "source": source.strip(),
                "target": target.strip(),
                "relationship_type": rtype.strip().upper().replace(" ", "_"),
                "description": description.strip(),
                "weight": min(10.0, max(0.0, weight_val))
            })
        
        # Also try without weight
        simple_pattern = r'<relationship>\s*<source>([^<]+)</source>\s*<target>([^<]+)</target>\s*<type>([^<]+)</type>\s*<description>([^<]*)</description>\s*</relationship>'
        simple_matches = re.findall(simple_pattern, content, re.IGNORECASE | re.DOTALL)
        existing = {(r["source"].lower(), r["target"].lower(), r["relationship_type"]) for r in relationships}
        
        for source, target, rtype, description in simple_matches:
            key = (source.strip().lower(), target.strip().lower(), rtype.strip().upper().replace(" ", "_"))
            if key not in existing:
                relationships.append({
                    "source": source.strip(),
                    "target": target.strip(),
                    "relationship_type": rtype.strip().upper().replace(" ", "_"),
                    "description": description.strip(),
                    "weight": 5.0
                })
        
        return relationships
    
    def _extract_xml_entity_names(self, content: str) -> List[str]:
        """Extract entity names from XML format: <entities><entity>name</entity>...</entities>"""
        entities = []
        
        # Pattern for entity list format
        pattern = r'<entity>([^<]+)</entity>'
        matches = re.findall(pattern, content, re.IGNORECASE)
        entities = [m.strip() for m in matches if m.strip()]
        
        return entities
    
    def _extract_json_from_response(self, content: str) -> dict:
        """
        Extract JSON from LLM response, handling various formats.
        Fallback for models that may not follow XML format.
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
        
        logger.debug(f"Could not extract JSON from response, trying XML parsing...")
        return {}
    
    def extract_from_text(
        self, 
        text: str, 
        document_summary: Optional[str] = None,
        entity_types: Optional[List[str]] = None,
        relation_types: Optional[List[str]] = None
    ) -> ExtractionResult:
        """
        Extract entities and relationships from text using LLM prompts.
        
        Args:
            text: The text to extract from
            document_summary: Optional document summary for context
            entity_types: Optional list of entity types to constrain extraction
            relation_types: Optional list of relation types to constrain extraction
            
        Returns:
            ExtractionResult containing entities and relationships with weights
        """
        if not self.is_available:
            logger.warning("Graph extraction unavailable - returning empty result")
            return ExtractionResult()
        
        # Use provided types or defaults
        e_types = entity_types or self.entity_types
        r_types = relation_types or self.relation_types
        
        # Build context section if summary provided
        context_section = ""
        if document_summary:
            context_section = f"Document Summary (for context):\n{document_summary}\n"
        
        # Format the user prompt
        user_prompt = EXTRACTION_USER_PROMPT.format(
            entity_types=", ".join(e_types),
            relation_types=", ".join(r_types),
            context_section=context_section,
            text=text
        )
        
        try:
            response = self.client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=3000,
            )
            
            content = self._extract_response_content(response)
            if not content:
                return ExtractionResult()

            # Try XML parsing first
            xml_entities = self._extract_xml_entities(content)
            xml_relationships = self._extract_xml_relationships(content)
            
            # If XML parsing worked, use those results
            if xml_entities or xml_relationships:
                entities = []
                for e in xml_entities:
                    try:
                        entities.append(Entity(
                            name=e["name"],
                            type=e.get("type", "Concept"),
                            description=e.get("description", "")
                        ))
                    except Exception as ex:
                        logger.warning(f"Failed to parse entity {e}: {ex}")
                
                relationships = []
                entity_names = {e.name.lower() for e in entities}
                
                for r in xml_relationships:
                    try:
                        source = r.get("source", "").strip()
                        target = r.get("target", "").strip()
                        
                        # Only include relationships where both entities exist
                        if source.lower() in entity_names and target.lower() in entity_names:
                            relationships.append(Relationship(
                                source=source,
                                target=target,
                                relationship_type=r.get("relationship_type", "RELATED_TO"),
                                description=r.get("description", ""),
                                weight=r.get("weight", 5.0)
                            ))
                        else:
                            logger.debug(f"Skipping relationship with unknown entities: {source} -> {target}")
                    except Exception as ex:
                        logger.warning(f"Failed to parse relationship {r}: {ex}")
                
                result = ExtractionResult(entities=entities, relationships=relationships)
                logger.info(f"Extracted {len(entities)} entities and {len(relationships)} relationships (XML format)")
                return result
            
            # Fall back to JSON parsing
            data = self._extract_json_from_response(content)
            
            if not data:
                logger.warning("No valid extraction from LLM response")
                return ExtractionResult()
            
            # Validate and create models from JSON
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
            entity_names = {e.name.lower() for e in entities}
            
            for r in data.get("relationships", []):
                try:
                    if not isinstance(r, dict):
                        continue
                    source = r.get("source", "").strip()
                    target = r.get("target", "").strip()
                    
                    # Only include relationships where both entities exist
                    if source.lower() in entity_names and target.lower() in entity_names:
                        weight = 5.0
                        if "weight" in r:
                            try:
                                weight = float(r["weight"])
                            except (ValueError, TypeError):
                                pass
                        
                        relationships.append(Relationship(
                            source=source,
                            target=target,
                            relationship_type=r.get("relationship_type", "RELATED_TO").strip().upper().replace(" ", "_"),
                            description=r.get("description", "").strip(),
                            weight=min(10.0, max(0.0, weight))
                        ))
                    else:
                        logger.debug(f"Skipping relationship with unknown entities: {source} -> {target}")
                except Exception as ex:
                    logger.warning(f"Failed to parse relationship {r}: {ex}")
            
            result = ExtractionResult(entities=entities, relationships=relationships)
            logger.info(f"Extracted {len(entities)} entities and {len(relationships)} relationships (JSON format)")
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
    # Async methods - use AsyncOpenAI for true concurrent LLM calls
    # =========================================================================
    
    async def extract_from_text_async(
        self, 
        text: str, 
        document_summary: Optional[str] = None,
        entity_types: Optional[List[str]] = None,
        relation_types: Optional[List[str]] = None
    ) -> ExtractionResult:
        """
        Async version of extract_from_text using AsyncOpenAI for true concurrency.
        
        This method uses the async OpenAI client directly, allowing many LLM calls
        to run concurrently without thread pool bottlenecks.
        
        Args:
            text: The text to extract from
            document_summary: Optional document summary for context
            entity_types: Optional list of entity types to constrain extraction
            relation_types: Optional list of relation types to constrain extraction
        """
        if not self.async_client:
            logger.warning("Graph extraction unavailable - returning empty result")
            return ExtractionResult()
        
        # Use provided types or defaults
        e_types = entity_types or self.entity_types
        r_types = relation_types or self.relation_types
        
        # Build context section if summary provided
        context_section = ""
        if document_summary:
            context_section = f"Document Summary (for context):\n{document_summary}\n"
        
        # Format the user prompt
        user_prompt = EXTRACTION_USER_PROMPT.format(
            entity_types=", ".join(e_types),
            relation_types=", ".join(r_types),
            context_section=context_section,
            text=text
        )
        
        try:
            response = await self.async_client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
                max_tokens=3000,
            )
            
            content = self._extract_response_content(response)
            
            # Parse response (reuse existing parsing logic)
            xml_entities = self._extract_xml_entities(content)
            xml_relationships = self._extract_xml_relationships(content)
            
            if xml_entities or xml_relationships:
                entities = []
                for e in xml_entities:
                    try:
                        entities.append(Entity(
                            name=e["name"],
                            type=e.get("type", "Concept"),
                            description=e.get("description", "")
                        ))
                    except Exception as ex:
                        logger.warning(f"Failed to parse entity {e}: {ex}")
                
                relationships = []
                entity_names = {e.name.lower() for e in entities}
                
                for r in xml_relationships:
                    try:
                        source = r.get("source", "").strip()
                        target = r.get("target", "").strip()
                        
                        if source.lower() in entity_names and target.lower() in entity_names:
                            relationships.append(Relationship(
                                source=source,
                                target=target,
                                relationship_type=r.get("relationship_type", "RELATED_TO"),
                                description=r.get("description", ""),
                                weight=r.get("weight", 5.0)
                            ))
                    except Exception as ex:
                        logger.warning(f"Failed to parse relationship {r}: {ex}")
                
                return ExtractionResult(entities=entities, relationships=relationships)
            
            # Fall back to JSON parsing
            data = self._extract_json_from_response(content)
            
            if not data:
                return ExtractionResult()
            
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
            entity_names = {e.name.lower() for e in entities}
            
            for r in data.get("relationships", []):
                try:
                    if not isinstance(r, dict):
                        continue
                    source = r.get("source", "").strip()
                    target = r.get("target", "").strip()
                    
                    if source.lower() in entity_names and target.lower() in entity_names:
                        weight = 5.0
                        if "weight" in r:
                            try:
                                weight = float(r["weight"])
                            except (ValueError, TypeError):
                                pass
                        
                        relationships.append(Relationship(
                            source=source,
                            target=target,
                            relationship_type=r.get("relationship_type", "RELATED_TO").strip().upper().replace(" ", "_"),
                            description=r.get("description", "").strip(),
                            weight=min(10.0, max(0.0, weight))
                        ))
                except Exception as ex:
                    logger.warning(f"Failed to parse relationship {r}: {ex}")
            
            return ExtractionResult(entities=entities, relationships=relationships)
            
        except Exception as e:
            logger.error(f"Error during async graph extraction: {e}")
            return ExtractionResult()
    
    async def extract_entities_from_query_async(self, query: str) -> List[str]:
        """
        Async version of extract_entities_from_query using AsyncOpenAI.
        """
        if not self.async_client:
            return []
        
        try:
            response = await self.async_client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": "You extract entity names from questions. Respond with ONLY XML format as specified."},
                    {"role": "user", "content": QUERY_ENTITY_PROMPT.format(query=query)}
                ],
                temperature=0,
                max_tokens=500,
            )
            
            content = self._extract_response_content(response)
            if not content:
                return []
            
            # Try XML parsing first
            entities = self._extract_xml_entity_names(content)
            
            # Fall back to JSON parsing
            if not entities:
                data = self._extract_json_from_response(content)
                entities = [str(e).strip() for e in data.get("entities", []) if e]
            
            logger.info(f"Extracted {len(entities)} entities from query: {entities}")
            return entities
            
        except Exception as e:
            logger.error(f"Error extracting entities from query: {e}")
            return []
    
    async def generate_document_summary_async(self, document: str) -> str:
        """
        Async version of generate_document_summary using extraction client.
        """
        client = self.async_extraction_client or self.async_client
        if not client:
            return ""

        model = self.extraction_model_name

        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "You are a document summarization assistant."},
                    {"role": "user", "content": SUMMARY_PROMPT.format(document=document[:10000])}
                ],
                temperature=0.3,
                max_tokens=1000,
            )

            content = self._extract_response_content(response)
            if not content:
                return ""
            summary = content.strip()
            logger.info(f"Generated document summary: {len(summary)} chars")
            return summary

        except Exception as e:
            logger.error(f"Error generating document summary: {e}")
            return ""

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
                model=self.current_model,
                messages=[
                    {"role": "system", "content": "You extract entity names from questions. Respond with ONLY XML format as specified."},
                    {"role": "user", "content": QUERY_ENTITY_PROMPT.format(query=query)}
                ],
                temperature=0,
                max_tokens=500,
            )
            
            content = self._extract_response_content(response)
            if not content:
                return []

            # Try XML parsing first
            entities = self._extract_xml_entity_names(content)
            
            # Fall back to JSON parsing
            if not entities:
                data = self._extract_json_from_response(content)
                entities = [str(e).strip() for e in data.get("entities", []) if e]
            
            logger.info(f"Extracted {len(entities)} entities from query: {entities}")
            return entities
            
        except Exception as e:
            logger.error(f"Error extracting entities from query: {e}")
            return []
    
    def generate_document_summary(self, document: str) -> str:
        """
        Generate a document summary for context in extraction.
        Uses summary prompt.
        
        Args:
            document: The full document text
            
        Returns:
            Summary string (roughly 10% of input size)
        """
        if not self.is_available:
            return ""
        
        try:
            response = self.client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": "You are a document summarization assistant."},
                    {"role": "user", "content": SUMMARY_PROMPT.format(document=document[:10000])}
                ],
                temperature=0.3,
                max_tokens=1000,
            )
            
            content = self._extract_response_content(response)
            if not content:
                return ""
            summary = content.strip()
            logger.info(f"Generated document summary: {len(summary)} chars")
            return summary

        except Exception as e:
            logger.error(f"Error generating document summary: {e}")
            return ""

    def enrich_entity_description(
        self, 
        entity_name: str, 
        entity_type: str, 
        entity_description: str,
        document_summary: str,
        relationships: List[dict]
    ) -> str:
        """
        Enrich an entity description using prompt.
        
        Args:
            entity_name: Name of the entity
            entity_type: Type of the entity
            entity_description: Current description
            document_summary: Document context
            relationships: Related entity information
            
        Returns:
            Enhanced description string
        """
        if not self.is_available:
            return entity_description
        
        # Format relationships text
        rel_txt = "\n".join([
            f"- {r.get('source', '')} --[{r.get('type', '')}]--> {r.get('target', '')}: {r.get('description', '')}"
            for r in relationships[:10]
        ]) if relationships else "No relationships found."
        
        try:
            response = self.client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": "You are an entity description enrichment assistant."},
                    {"role": "user", "content": ENTITY_DESCRIPTION_PROMPT.format(
                        document_summary=document_summary or "No summary available.",
                        entity_name=entity_name,
                        entity_type=entity_type,
                        entity_description=entity_description or "No description available.",
                        relationships_txt=rel_txt
                    )}
                ],
                temperature=0.3,
                max_tokens=300,
            )

            content = self._extract_response_content(response)
            if not content:
                return entity_description
            enhanced = content.strip()
            logger.debug(f"Enhanced description for {entity_name}")
            return enhanced

        except Exception as e:
            logger.error(f"Error enriching entity description: {e}")
            return entity_description

    # =========================================================================
    # Community Summarization
    # =========================================================================
    
    def generate_community_summary(
        self,
        entities: List[dict],
        relationships: List[dict]
    ) -> dict:
        """
        Generate a summary and name for a community of related entities.
        
        Community summarization for improved RAG context.
        
        Args:
            entities: List of entity dicts with name, type, description
            relationships: List of relationship dicts within the community
            
        Returns:
            Dict with 'name' and 'summary' keys
        """
        if not self.is_available or not entities:
            return {"name": None, "summary": None}
        
        # Format entity information
        entity_info = "\n".join([
            f"- {e.get('name', 'Unknown')} ({e.get('type', 'Unknown')}): {e.get('description', '')[:100]}"
            for e in entities[:15]
        ])
        
        # Format relationship information
        rel_info = "\n".join([
            f"- {r.get('source', '')} --[{r.get('type', '')}]--> {r.get('target', '')}"
            for r in relationships[:20]
        ]) if relationships else "No explicit relationships."
        
        prompt = f"""Analyze this community of related entities from a knowledge graph.

=== Entities ===
{entity_info}

=== Relationships ===
{rel_info}

Generate a JSON object with:
- "name": A short descriptive name (3-5 words)
- "summary": A 2-3 sentence explanation of what connects these entities

Respond with ONLY the JSON object, no other text:
{{"name": "...", "summary": "..."}}"""
        
        try:
            response = self.client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": "You analyze knowledge graph communities and generate concise summaries. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=300,
            )

            raw_content = self._extract_response_content(response)
            if not raw_content:
                return {"name": f"Community ({len(entities)} entities)", "summary": ""}
            content = raw_content.strip()

            # Parse JSON response with multiple strategies
            import re

            # Strategy 1: Direct JSON parse
            try:
                result = json.loads(content)
                if "name" in result and "summary" in result:
                    logger.info(f"Generated community summary: {result.get('name', 'Unknown')}")
                    return result
            except json.JSONDecodeError:
                pass

            # Strategy 2: Extract JSON block from markdown code fence
            json_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_block_match:
                try:
                    result = json.loads(json_block_match.group(1))
                    if "name" in result:
                        logger.info(f"Generated community summary: {result.get('name', 'Unknown')}")
                        return result
                except json.JSONDecodeError:
                    pass
            
            # Strategy 3: Find any JSON object in the content
            json_match = re.search(r'\{[^{}]*"name"\s*:\s*"[^"]*"[^{}]*\}', content, re.DOTALL)
            if json_match:
                try:
                    result = json.loads(json_match.group())
                    logger.info(f"Generated community summary: {result.get('name', 'Unknown')}")
                    return result
                except json.JSONDecodeError:
                    pass
            
            # Strategy 4: Extract name and summary with regex patterns
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', content)
            summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', content)
            
            if name_match:
                logger.info(f"Generated community summary (regex): {name_match.group(1)}")
                return {
                    "name": name_match.group(1),
                    "summary": summary_match.group(1) if summary_match else content[:500]
                }
            
            # Fallback: use content as summary with generic name
            logger.warning("Could not parse community summary as JSON, using fallback")
            return {
                "name": f"Community ({len(entities)} entities)",
                "summary": content[:500] if content else None
            }
                
        except Exception as e:
            logger.error(f"Error generating community summary: {e}")
            return {"name": None, "summary": None}
    
    async def generate_community_summary_async(
        self,
        entities: List[dict],
        relationships: List[dict]
    ) -> dict:
        """Async version of generate_community_summary using AsyncOpenAI."""
        if not self.async_client or not entities:
            return {"name": None, "summary": None}
        
        # Format entity information
        entity_info = "\n".join([
            f"- {e.get('name', 'Unknown')} ({e.get('type', 'Unknown')}): {e.get('description', '')[:100]}"
            for e in entities[:15]
        ])
        
        # Format relationship information
        rel_info = "\n".join([
            f"- {r.get('source', '')} --[{r.get('type', '')}]--> {r.get('target', '')}"
            for r in relationships[:20]
        ]) if relationships else "No explicit relationships."
        
        prompt = f"""Analyze this community of related entities from a knowledge graph.

=== Entities ===
{entity_info}

=== Relationships ===
{rel_info}

Generate a JSON object with:
- "name": A short descriptive name (3-5 words)
- "summary": A 2-3 sentence explanation of what connects these entities

Respond with ONLY the JSON object, no other text:
{{"name": "...", "summary": "..."}}"""
        
        try:
            response = await self.async_client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": "You analyze knowledge graph communities and generate concise summaries. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=300,
            )

            raw_content = self._extract_response_content(response)
            if not raw_content:
                return {"name": f"Community ({len(entities)} entities)", "summary": ""}
            content = raw_content.strip()

            # Parse JSON response
            try:
                result = json.loads(content)
                if "name" in result and "summary" in result:
                    logger.info(f"Generated community summary: {result.get('name', 'Unknown')}")
                    return result
            except json.JSONDecodeError:
                pass

            # Try regex extraction
            import re
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', content)
            summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', content)
            
            if name_match:
                return {
                    "name": name_match.group(1),
                    "summary": summary_match.group(1) if summary_match else content[:500]
                }
            
            return {
                "name": f"Community ({len(entities)} entities)",
                "summary": content[:500] if content else None
            }
                
        except Exception as e:
            logger.error(f"Error generating community summary: {e}")
            return {"name": None, "summary": None}
    
    def generate_community_name(self, entities: List[dict]) -> str:
        """Generate a short name for a community based on its entities."""
        if not self.is_available or not entities:
            return f"Community ({len(entities)} entities)"
        
        # Get entity names for quick naming
        entity_names = [e.get("name", "") for e in entities[:10]]
        entity_types = list(set(e.get("type", "") for e in entities if e.get("type")))
        
        prompt = f"""Generate a short, descriptive name (3-5 words) for a community containing these entities:

Entities: {', '.join(entity_names)}
Types present: {', '.join(entity_types)}

Respond with ONLY the community name, nothing else."""
        
        try:
            response = self.client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": "You name knowledge graph communities. Respond with only the name."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=50,
            )

            content = self._extract_response_content(response)
            if not content:
                return f"Community ({len(entities)} entities)"
            name = content.strip().strip('"').strip("'")
            return name if name else f"Community ({len(entities)} entities)"

        except Exception as e:
            logger.error(f"Error generating community name: {e}")
            return f"Community ({len(entities)} entities)"

    # =========================================================================
    # Entity Embedding Generation (for Semantic Resolution)
    # =========================================================================
    
    def generate_entity_embedding(self, entity_name: str, entity_type: str, description: str) -> Optional[List[float]]:
        """
        Generate an embedding for an entity for semantic resolution.
        
        Combines entity name, type, and description for rich embedding.
        """
        if not self.settings.embed_api_key:
            return None

        # Create rich text for embedding
        text = f"{entity_name} ({entity_type}): {description}" if description else f"{entity_name} ({entity_type})"

        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self.settings.embed_api_key,
                base_url=self.settings.embed_api_base,
            )
            
            embed_kwargs = dict(model=self.settings.entity_embed_model, input=text)
            if self.settings.embedding_send_dimensions:
                embed_kwargs["dimensions"] = self.settings.embedding_dimension
            response = client.embeddings.create(**embed_kwargs)
            
            return response.data[0].embedding
            
        except Exception as e:
            logger.warning(f"Failed to generate entity embedding: {e}")
            return None
    
    async def generate_entity_embedding_async(
        self,
        entity_name: str,
        entity_type: str,
        description: str
    ) -> Optional[List[float]]:
        """Async version of generate_entity_embedding using AsyncOpenAI."""
        if not self.settings.embed_api_key:
            return None

        # Lazy-init dedicated async embedding client
        if self._async_embed_client is None:
            self._async_embed_client = AsyncOpenAI(
                api_key=self.settings.embed_api_key,
                base_url=self.settings.embed_api_base,
                timeout=120.0,
                max_retries=2,
            )

        # Create rich text for embedding
        text = f"{entity_name} ({entity_type}): {description}" if description else f"{entity_name} ({entity_type})"

        try:
            embed_kwargs = dict(model=self.settings.entity_embed_model, input=text)
            if self.settings.embedding_send_dimensions:
                embed_kwargs["dimensions"] = self.settings.embedding_dimension
            response = await self._async_embed_client.embeddings.create(**embed_kwargs)
            
            return response.data[0].embedding
            
        except Exception as e:
            logger.warning(f"Failed to generate entity embedding: {e}")
            return None

    # =========================================================================
    # Phase A: Per-Document Entity Extraction
    # =========================================================================

    async def extract_entities_from_document_async(
        self,
        chunks: List[str],
        document_summary: str,
        max_tokens: int = 8192,
    ) -> List[Entity]:
        """Extract entities from an entire document (batched by token budget).

        Processes all chunks as a single document rather than per-chunk.
        Batches intelligently when exceeding context window, with 1-chunk
        overlap between batches for continuity.

        Args:
            chunks: List of chunk text content strings
            document_summary: Summary of the document for context
            max_tokens: Approximate context window budget for the extraction model

        Returns:
            Deduplicated list of Entity objects
        """
        client = self.async_extraction_client or self.async_client
        if not client:
            logger.warning("No extraction client available - returning empty entities")
            return []

        model = self.extraction_model_name
        e_types = self.entity_types

        # Token estimation
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(model)
            def count_tokens(text: str) -> int:
                return len(enc.encode(text))
        except Exception:
            def count_tokens(text: str) -> int:
                return len(text) // 4

        # Calculate prompt overhead
        system_tokens = count_tokens(ENTITY_EXTRACTION_SYSTEM_PROMPT)
        template_tokens = count_tokens(ENTITY_EXTRACTION_USER_PROMPT.format(
            entity_types=", ".join(e_types),
            document_summary=document_summary or "",
            text="",
        ))
        prompt_overhead = system_tokens + template_tokens
        output_reserve = 1500
        available_tokens = int(max_tokens * 0.8) - prompt_overhead - output_reserve

        if available_tokens < 200:
            available_tokens = 2000  # Fallback minimum

        # Batch chunks by token budget
        batches: List[List[str]] = []
        current_batch: List[str] = []
        current_tokens = 0

        for chunk in chunks:
            chunk_tokens = count_tokens(chunk)
            if current_batch and (current_tokens + chunk_tokens) > available_tokens:
                batches.append(current_batch)
                # 1-chunk overlap: include last chunk of previous batch
                current_batch = [current_batch[-1]]
                current_tokens = count_tokens(current_batch[0])
            current_batch.append(chunk)
            current_tokens += chunk_tokens

        if current_batch:
            batches.append(current_batch)

        logger.info(
            f"Entity extraction: {len(chunks)} chunks split into {len(batches)} batch(es) "
            f"(model={model}, budget={max_tokens})"
        )

        # Extract entities from each batch
        all_entities: List[Entity] = []

        for batch_idx, batch in enumerate(batches):
            batch_text = "\n\n".join(batch)
            user_prompt = ENTITY_EXTRACTION_USER_PROMPT.format(
                entity_types=", ".join(e_types),
                document_summary=document_summary or "No summary available.",
                text=batch_text,
            )

            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": ENTITY_EXTRACTION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=8000,
                )

                content = self._extract_response_content(response)
                if not content:
                    logger.warning(f"Batch {batch_idx + 1}/{len(batches)}: empty response")
                    continue

                xml_entities = self._extract_xml_entities(content)
                for e in xml_entities:
                    try:
                        all_entities.append(Entity(
                            name=e["name"],
                            type=e.get("type", "Concept"),
                            description=e.get("description", ""),
                        ))
                    except Exception as ex:
                        logger.warning(f"Failed to parse entity {e}: {ex}")

                logger.info(
                    f"Batch {batch_idx + 1}/{len(batches)}: extracted {len(xml_entities)} entities"
                )

            except Exception as e:
                logger.error(f"Error in entity extraction batch {batch_idx + 1}: {e}")

        # Deduplicate by name (case-insensitive), keep longest description
        seen: dict[str, Entity] = {}
        for entity in all_entities:
            key = entity.name.lower()
            if key not in seen or len(entity.description) > len(seen[key].description):
                seen[key] = entity

        deduplicated = list(seen.values())
        logger.info(f"Entity extraction complete: {len(all_entities)} raw -> {len(deduplicated)} deduplicated")
        return deduplicated

    # =========================================================================
    # Phase B: Cross-Document Relationship Analysis
    # =========================================================================

    def _format_entity_for_prompt(self, entity: dict, max_desc_len: int = 200) -> str:
        """Format a single entity for inclusion in a prompt."""
        name = entity.get("name", "Unknown")
        etype = entity.get("type", "Unknown")
        desc = (entity.get("description") or "")[:max_desc_len]
        return f"- {name} ({etype}): {desc}"

    async def analyze_relationships_async(
        self,
        entities: List[dict],
        context: str = "",
        existing_relationships: List[dict] = None,
        max_output_tokens: int = 8000,
    ) -> List[Relationship]:
        """Analyze entities and discover relationships using the main (large) model.

        Args:
            entities: List of {name, type, description} dicts
            context: Optional collection/document summary context
            existing_relationships: Optional list of already-known relationships to avoid duplicates
            max_output_tokens: Max output tokens for the LLM response

        Returns:
            List of discovered Relationship objects
        """
        if not self.async_client:
            logger.warning("No async client available for relationship analysis")
            return []

        r_types = self.relation_types

        # Format entity list with truncated descriptions
        entity_list = "\n".join([
            self._format_entity_for_prompt(e) for e in entities
        ])

        # Build context section
        context_section = ""
        if context:
            context_section = f"Context:\n{context}\n"
        if existing_relationships:
            existing_text = "\n".join([
                f"- {r.get('source', '')} --[{r.get('type', '')}]--> {r.get('target', '')}"
                for r in existing_relationships[:50]
            ])
            context_section += (
                f"\nThe following relationships are already known — "
                f"focus on discovering NEW relationships not listed here:\n{existing_text}\n"
            )

        user_prompt = RELATIONSHIP_ANALYSIS_USER_PROMPT.format(
            relation_types=", ".join(r_types),
            context_section=context_section,
            entity_list=entity_list,
        )

        entity_name_set = {e.get("name", "").lower() for e in entities}

        try:
            response = await self.async_client.chat.completions.create(
                model=self.current_model,
                messages=[
                    {"role": "system", "content": RELATIONSHIP_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=max_output_tokens,
            )

            content = self._extract_response_content(response)
            if not content:
                return []

            xml_relationships = self._extract_xml_relationships(content)

            relationships = []
            for r in xml_relationships:
                source = r.get("source", "").strip()
                target = r.get("target", "").strip()

                # Only keep relationships where both entities are in the provided list
                if source.lower() in entity_name_set and target.lower() in entity_name_set:
                    relationships.append(Relationship(
                        source=source,
                        target=target,
                        relationship_type=r.get("relationship_type", "RELATED_TO"),
                        description=r.get("description", ""),
                        weight=r.get("weight", 5.0),
                    ))

            logger.info(f"Relationship analysis: discovered {len(relationships)} relationships from {len(entities)} entities")
            return relationships

        except Exception as e:
            logger.error(f"Error during relationship analysis: {e}")
            return []

    async def analyze_relationships_batched_async(
        self,
        all_entities: List[dict],
        context: str = "",
        max_context_tokens: int = 65536,
        max_output_tokens: int = 8000,
        existing_relationships: List[dict] = None,
        on_batch_complete: Optional[Callable[[List[Relationship]], Awaitable[None]]] = None,
    ) -> List[Relationship]:
        """Analyze relationships in batches using token-based context window management.

        Splits entities into batches based on token count (like entity extraction),
        with 10% entity overlap between batches for relationship continuity.

        Args:
            all_entities: List of {name, type, description} dicts
            context: Optional collection/document summary context
            max_context_tokens: Max INPUT context window tokens for batching
            max_output_tokens: Max OUTPUT tokens for LLM responses
            existing_relationships: Optional list of already-known relationships
            on_batch_complete: Optional callback(batch_relationships) called after each batch
                               for incremental storage. Receives list of Relationship objects.

        Returns:
            Deduplicated list of all discovered Relationship objects
        """
        if not all_entities:
            return []

        # Token estimation (same approach as entity extraction)
        model = self.current_model
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(model)
            def count_tokens(text: str) -> int:
                return len(enc.encode(text))
        except Exception:
            def count_tokens(text: str) -> int:
                return len(text) // 4

        r_types = self.relation_types

        # Calculate prompt overhead
        system_tokens = count_tokens(RELATIONSHIP_ANALYSIS_SYSTEM_PROMPT)
        template_tokens = count_tokens(RELATIONSHIP_ANALYSIS_USER_PROMPT.format(
            relation_types=", ".join(r_types),
            context_section=context or "",
            entity_list="",
        ))
        # Account for existing relationships context (up to 50)
        existing_context_tokens = 0
        if existing_relationships:
            existing_text = "\n".join([
                f"- {r.get('source', '')} --[{r.get('type', '')}]--> {r.get('target', '')}"
                for r in existing_relationships[:50]
            ])
            existing_context_tokens = count_tokens(existing_text) + 100  # Extra for header text

        prompt_overhead = system_tokens + template_tokens + existing_context_tokens
        output_reserve = max_output_tokens
        available_tokens = int(max_context_tokens * 0.8) - prompt_overhead - output_reserve

        if available_tokens < 500:
            available_tokens = 2000  # Fallback minimum
            logger.warning(f"Very limited context budget ({available_tokens} tokens), using minimum")

        # Group entities by type for better batch coherence (related entities together)
        by_type: dict[str, List[dict]] = {}
        for e in all_entities:
            t = e.get("type", "Other")
            by_type.setdefault(t, []).append(e)

        # Flatten with type grouping preserved
        sorted_entities = []
        for entities_of_type in by_type.values():
            sorted_entities.extend(entities_of_type)

        # Pre-compute token count for each entity
        entity_tokens = []
        for e in sorted_entities:
            formatted = self._format_entity_for_prompt(e)
            entity_tokens.append((e, count_tokens(formatted)))

        # Batch entities by token budget (not count)
        batches: List[List[dict]] = []
        current_batch: List[dict] = []
        current_tokens = 0

        for entity, tokens in entity_tokens:
            if current_batch and (current_tokens + tokens) > available_tokens:
                batches.append(current_batch)
                # 10% overlap: keep last ~10% of entities for continuity
                overlap_count = max(1, len(current_batch) // 10)
                overlap_entities = current_batch[-overlap_count:]
                overlap_tokens = sum(
                    count_tokens(self._format_entity_for_prompt(e))
                    for e in overlap_entities
                )
                current_batch = overlap_entities.copy()
                current_tokens = overlap_tokens

            current_batch.append(entity)
            current_tokens += tokens

        if current_batch:
            batches.append(current_batch)

        logger.info(
            f"Relationship analysis: {len(all_entities)} entities split into "
            f"{len(batches)} batch(es) (context_budget={max_context_tokens}, "
            f"available={available_tokens} tokens)"
        )

        # Process batches with optional incremental callback
        all_relationships: List[Relationship] = []
        seen_keys: set[tuple] = set()  # For deduplication

        for batch_idx, batch in enumerate(batches):
            rels = await self.analyze_relationships_async(
                batch, context, existing_relationships, max_output_tokens
            )

            # Deduplicate as we go (memory efficient)
            batch_unique: List[Relationship] = []
            for rel in rels:
                key = (rel.source.lower(), rel.target.lower(), rel.relationship_type)
                if key not in seen_keys:
                    seen_keys.add(key)
                    batch_unique.append(rel)
                    all_relationships.append(rel)

            logger.info(
                f"Batch {batch_idx + 1}/{len(batches)}: {len(rels)} discovered, "
                f"{len(batch_unique)} unique ({len(batch)} entities)"
            )

            # Call incremental storage callback if provided
            if on_batch_complete and batch_unique:
                try:
                    await on_batch_complete(batch_unique)
                except Exception as e:
                    logger.error(f"Error in on_batch_complete callback: {e}")

            # Clear batch reference to help GC
            del batch_unique
            del rels

        logger.info(
            f"Relationship analysis complete: {len(all_relationships)} unique relationships"
        )
        return all_relationships


# Singleton instance
_graph_extractor: Optional[GraphExtractor] = None


def get_graph_extractor() -> GraphExtractor:
    """Get the singleton GraphExtractor instance."""
    global _graph_extractor
    if _graph_extractor is None:
        _graph_extractor = GraphExtractor()
    return _graph_extractor
