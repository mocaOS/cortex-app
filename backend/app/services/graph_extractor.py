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
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.models import Entity, Relationship, ExtractionResult
from app.services.llm_config import get_llm_config, get_extraction_llm_config, get_relationship_llm_config, is_turbo_mode_active
from app.services.reasoning_config import (
    ReasoningMode,
    safe_chat_completion,
    safe_chat_completion_sync,
)

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
- Focus especially on entities and technical concepts that might connect to other documents

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
Given text and a document summary for context, identify ALL important entities.

# Rules
- Use ONLY these entity types: Person, Organization, Location, Concept, Technology, Event, Product, Document, System, Process.
- If an entity doesn't clearly fit, classify it as Concept.
- Extract every named entity and important concept. Be exhaustive.
- Use consistent, canonical naming (e.g. always "Neo4j", not "neo4j" or "Neo4J").
- Write rich, standalone descriptions that include the entity's role and context.

Output Format: Output ONLY XML entities. No other text, no explanations.

Format:
<entity name="Entity Name"><type>EntityType</type><description>Comprehensive description here.</description></entity>"""


ENTITY_EXTRACTION_USER_PROMPT = """Extract ALL entities from the following document section. Be exhaustive — include every person, organization, technology, concept, product, event, or system mentioned or implied.

Entity Types (use only these): {entity_types}

Document Summary (for context):
{document_summary}

Text:
{text}

Example Output:
<entity name="Stable Diffusion"><type>Technology</type><description>Stable Diffusion is a latent diffusion model for text-to-image generation released by Stability AI.</description></entity>

Now extract all entities:"""


# =============================================================================
# Relationship Analysis Prompt (Phase B - cross-document relationship discovery)
# =============================================================================

RELATIONSHIP_ANALYSIS_SYSTEM_PROMPT = """You are an expert knowledge graph builder. Your task is to identify meaningful relationships between the given entities.

# Goal
Identify semantic relationships between the provided entities using the allowed relationship types. Each relationship must represent a real, factual connection — not just co-occurrence in text.

# Rules
- ONLY use entities from the provided list.
- ONLY use these relationship types: WORKS_FOR, LOCATED_IN, USES, RELATED_TO, PART_OF, CREATED_BY, IMPLEMENTS, DEPENDS_ON, IS_A, HAS_PROPERTY, FOUNDED_BY, FEATURES, CONTAINS, INTERACTS_WITH. Use RELATED_TO only when no better type fits.
- Create a relationship ONLY when there is a factual, semantic connection between two entities (e.g., one created the other, one is part of the other, one works for the other).
- Do NOT create a relationship just because two entities are mentioned in the same text. Co-occurrence is not a relationship.
- If no clear relationship exists between a pair of entities, do not create one. It is better to have fewer high-quality relationships than many weak ones.
- Prefer direct relationships: if A and B are directly connected, link them directly rather than routing through C.
- Relationship weight: 1-10 (10 = very direct and important, 6+ = meaningful connection).
- Include a confidence score (0.0-1.0) for each relationship: 1.0 = explicitly stated in text, 0.8+ = strongly implied, 0.5 = uncertain. Do not create relationships you are less than 0.5 confident about.

Output ONLY XML relationships. No explanations, no chain-of-thought, no other text."""


RELATIONSHIP_ANALYSIS_USER_PROMPT = """Extract relationships between the following entities based on their descriptions and source text context.

Relation Types (use only these): {relation_types}

{context_section}

=== Entities ===
{entity_list}

Good examples (create these — real factual connections):
<relationship><source>OpenAI</source><target>GPT-4</target><type>CREATED_BY</type><description>GPT-4 was developed by OpenAI.</description><weight>9</weight><confidence>0.95</confidence></relationship>
<relationship><source>Vitalik Buterin</source><target>Ethereum</target><type>FOUNDED_BY</type><description>Vitalik Buterin co-founded the Ethereum blockchain.</description><weight>10</weight><confidence>1.0</confidence></relationship>

Bad examples (do NOT create these — co-occurrence is not a relationship):
- "SuperRare" and "Twitter" both appear in an article → NOT a relationship
- "Museum" and "ChatGPT" are in the same document → NOT a relationship unless one actually uses/features the other

Now extract relationships:"""


# =============================================================================
# Phase 1: Candidate Pair Scanning (fast, uses extraction model)
# =============================================================================

CANDIDATE_SCAN_SYSTEM_PROMPT = """You are a knowledge graph analyst. Your task is to scan a list of entities and identify pairs that share a meaningful, factual relationship.

# Rules
- ONLY use entity names from the provided list — do not invent entities.
- Use entity descriptions AND the provided source text context to judge relatedness.
- Include pairs where there is a described factual connection (e.g., one entity created/uses/employs/is part of/founded the other, they collaborated on something specific).
- Two entities appearing in the same text is NOT enough — there must be a described interaction or factual link between them.
- Do NOT output relationship types, descriptions, or weights — just the pairs.

Output ONLY pairs, one per line in this exact format:
EntityA | EntityB

No explanations, no numbering, no other text."""

CANDIDATE_SCAN_USER_PROMPT = """Identify entity pairs that have a meaningful relationship based on evidence in the descriptions or source text.

{context_section}

=== Entities ===
{entity_list}

{existing_section}

Good examples (pairs with real factual connections):
OpenAI | GPT-4
Vitalik Buterin | Ethereum
Tesla | Elon Musk

Bad examples (do NOT include — co-occurrence is not a relationship):
SuperRare | Twitter
Museum | ChatGPT

Output related pairs (one per line, format: EntityA | EntityB):"""


class GraphExtractor:
    """Extract entities and relationships from text using LLM prompts."""
    
    def __init__(self):
        self.settings = get_settings()
        self._client: Optional[OpenAI] = None
        self._async_client: Optional[AsyncOpenAI] = None
        self._extraction_client: Optional[OpenAI] = None
        self._async_extraction_client: Optional[AsyncOpenAI] = None
        self._async_embed_client: Optional[AsyncOpenAI] = None
        self._async_relationship_client: Optional[AsyncOpenAI] = None
        self._last_config_hash: Optional[str] = None  # Track config changes for turbo mode
        self._last_extraction_config_hash: Optional[str] = None
        self._last_relationship_config_hash: Optional[str] = None
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

    def _get_relationship_config_hash(self) -> str:
        """Get a hash of relationship LLM config to detect changes."""
        config = get_relationship_llm_config()
        return f"{config.base_url}:{config.api_key[:8] if config.api_key else 'none'}"

    def _reset_relationship_clients_if_config_changed(self):
        """Reset relationship clients if the relationship LLM config has changed."""
        current_hash = self._get_relationship_config_hash()
        if self._last_relationship_config_hash and self._last_relationship_config_hash != current_hash:
            logger.info("Relationship LLM configuration changed, recreating relationship clients")
            self._async_relationship_client = None
        self._last_relationship_config_hash = current_hash
    
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
                timeout=600.0,
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
    def async_relationship_client(self) -> Optional[AsyncOpenAI]:
        """
        Lazy initialization of async OpenAI client for per-chunk relationship extraction.
        Uses relationship-specific config (separate model/endpoint to avoid rate-limit
        collisions with entity extraction).
        """
        self._reset_relationship_clients_if_config_changed()

        config = get_relationship_llm_config()
        if self._async_relationship_client is None and config.api_key:
            self._async_relationship_client = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=120.0,
                max_retries=2,
            )
            if config.is_turbo:
                logger.info(f"Async relationship client using Turbo Mode: {config.base_url}")
            else:
                logger.info(f"Relationship client initialized: {config.base_url} / {config.model}")
        return self._async_relationship_client

    @property
    def relationship_model_name(self) -> str:
        """Get the current relationship extraction model name."""
        config = get_relationship_llm_config()
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
    
    @property
    def _extraction_reasoning_mode(self) -> ReasoningMode:
        """Reasoning mode applied to extraction-side LLM calls."""
        return ReasoningMode.parse(self.settings.extraction_reasoning_mode)

    @property
    def _relationship_reasoning_mode(self) -> ReasoningMode:
        """Reasoning mode applied to relationship-side LLM calls."""
        return ReasoningMode.parse(self.settings.relationship_reasoning_mode)

    async def _async_safe_completion(
        self,
        client,
        *,
        model: str,
        mode: ReasoningMode,
        **kwargs,
    ):
        """Async chat.completions.create wrapped with reasoning kwargs + fallback."""
        base_url = str(client.base_url) if getattr(client, "base_url", None) else ""
        return await safe_chat_completion(
            client.chat.completions.create,
            base_url=base_url,
            model=model,
            reasoning_mode=mode,
            overrides=self.settings.parsed_reasoning_overrides,
            **kwargs,
        )

    def _sync_safe_completion(
        self,
        client,
        *,
        model: str,
        mode: ReasoningMode,
        **kwargs,
    ):
        """Sync chat.completions.create wrapped with reasoning kwargs + fallback."""
        base_url = str(client.base_url) if getattr(client, "base_url", None) else ""
        return safe_chat_completion_sync(
            client.chat.completions.create,
            base_url=base_url,
            model=model,
            reasoning_mode=mode,
            overrides=self.settings.parsed_reasoning_overrides,
            **kwargs,
        )

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

    @staticmethod
    def _normalize_entity_type(etype: str) -> str:
        """Normalize entity type to one of the allowed types, defaulting to Concept."""
        etype = etype.strip()
        # Check exact match (case-insensitive)
        for allowed in DEFAULT_ENTITY_TYPES:
            if etype.lower() == allowed.lower():
                return allowed
        # Fuzzy match
        from rapidfuzz import process as fuzz_process
        closest, score, _ = fuzz_process.extractOne(etype, DEFAULT_ENTITY_TYPES)
        if score >= 75:
            return closest
        return "Concept"

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
                "type": self._normalize_entity_type(etype),
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
                    "type": self._normalize_entity_type(etype),
                    "description": ""
                })

        return entities
    
    def _extract_xml_relationships(self, content: str) -> List[dict]:
        """
        Extract relationships from XML-formatted LLM response.
        Handles format: <relationship><source>...</source><target>...</target><type>...</type><description>...</description><weight>...</weight></relationship>
        Also handles flexible element ordering within <relationship> tags.
        """
        relationships = []
        existing = set()
        
        # First, find all <relationship>...</relationship> blocks
        rel_blocks = re.findall(r'<relationship>([\s\S]*?)</relationship>', content, re.IGNORECASE)
        
        for block in rel_blocks:
            # Extract individual fields from the block (order-independent)
            source_match = re.search(r'<source>([^<]+)</source>', block, re.IGNORECASE)
            target_match = re.search(r'<target>([^<]+)</target>', block, re.IGNORECASE)
            type_match = re.search(r'<type>([^<]+)</type>', block, re.IGNORECASE)
            desc_match = re.search(r'<description>([^<]*)</description>', block, re.IGNORECASE)
            weight_match = re.search(r'<weight>([^<]*)</weight>', block, re.IGNORECASE)
            
            # source, target, and type are required
            if source_match and target_match and type_match:
                source = source_match.group(1).strip()
                target = target_match.group(1).strip()
                rtype = type_match.group(1).strip().upper().replace(" ", "_")

                # Normalize to closest standard relationship type
                if rtype not in DEFAULT_RELATION_TYPES:
                    from rapidfuzz import process as fuzz_process
                    closest, score, _ = fuzz_process.extractOne(rtype, DEFAULT_RELATION_TYPES)
                    if score >= 80:
                        rtype = closest
                    else:
                        rtype = "RELATED_TO"

                description = desc_match.group(1).strip() if desc_match else ""
                
                weight_val = 5.0
                if weight_match and weight_match.group(1).strip():
                    try:
                        weight_val = float(weight_match.group(1).strip())
                    except ValueError:
                        pass

                confidence_match = re.search(r'<confidence>([^<]*)</confidence>', block, re.IGNORECASE)
                confidence_val = 1.0
                if confidence_match and confidence_match.group(1).strip():
                    try:
                        confidence_val = min(1.0, max(0.0, float(confidence_match.group(1).strip())))
                    except ValueError:
                        pass

                key = (source.lower(), target.lower(), rtype)
                if key not in existing:
                    existing.add(key)
                    relationships.append({
                        "source": source,
                        "target": target,
                        "relationship_type": rtype,
                        "description": description,
                        "weight": min(10.0, max(0.0, weight_val)),
                        "confidence": confidence_val,
                    })

        # Fallback: parse plaintext arrow format if no XML relationships found
        # Handles: EntityA --[TYPE]--> EntityB - Description
        # and:     **EntityA --[TYPE]--> EntityB** - Description
        if not relationships and content:
            arrow_pattern = r'\*{0,2}(.+?)\s*--\[([A-Z_]+)\]-->\s*(.+?)\*{0,2}\s*(?:[-–—]\s*(.+))?$'
            for line in content.split("\n"):
                line = line.strip()
                if not line or "--[" not in line:
                    continue
                # Strip leading numbering
                line = re.sub(r"^[\d]+[\.\)]\s*", "", line)
                line = re.sub(r"^[-*]\s*", "", line)

                m = re.match(arrow_pattern, line)
                if not m:
                    continue
                source = m.group(1).strip().strip("*")
                rtype = m.group(2).strip().upper().replace(" ", "_")
                target = m.group(3).strip().strip("*")
                description = (m.group(4) or "").strip()

                if rtype not in DEFAULT_RELATION_TYPES:
                    from rapidfuzz import process as fuzz_process
                    closest, score, _ = fuzz_process.extractOne(rtype, DEFAULT_RELATION_TYPES)
                    rtype = closest if score >= 80 else "RELATED_TO"

                key = (source.lower(), target.lower(), rtype)
                if key not in existing and source and target:
                    existing.add(key)
                    relationships.append({
                        "source": source,
                        "target": target,
                        "relationship_type": rtype,
                        "description": description,
                        "weight": 5.0,
                    })

            if relationships:
                logger.info(f"Plaintext fallback parsed {len(relationships)} relationships (no XML found)")

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
            response = self._sync_safe_completion(
                self.client,
                model=self.current_model,
                mode=self._extraction_reasoning_mode,
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
            response = await self._async_safe_completion(
                self.async_client,
                model=self.current_model,
                mode=self._extraction_reasoning_mode,
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
            response = await self._async_safe_completion(
                self.async_client,
                model=self.current_model,
                mode=self._extraction_reasoning_mode,
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
            response = await self._async_safe_completion(
                client,
                model=model,
                mode=self._extraction_reasoning_mode,
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
            response = self._sync_safe_completion(
                self.client,
                model=self.current_model,
                mode=self._extraction_reasoning_mode,
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
            response = self._sync_safe_completion(
                self.client,
                model=self.current_model,
                mode=self._extraction_reasoning_mode,
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
            response = self._sync_safe_completion(
                self.client,
                model=self.current_model,
                mode=self._extraction_reasoning_mode,
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
        # Use extraction model for community summarization
        client = self.extraction_client or self.client
        model = self.extraction_model_name if self.extraction_client else self.current_model
        if not client or not entities:
            return {"name": None, "summary": None}

        # Format entity information
        entity_info = "\n".join([
            f"- {e.get('name', 'Unknown')} ({e.get('type', 'Unknown')}): {e.get('description', '')[:200]}"
            for e in entities[:30]
        ])

        # Format relationship information
        rel_info = "\n".join([
            f"- {r.get('source', '')} --[{r.get('type', '')}]--> {r.get('target', '')}"
            for r in relationships[:40]
        ]) if relationships else "No explicit relationships."
        
        prompt = f"""Analyze the following group of entities and relationships. Create a short, descriptive community name and a clear summary of what this cluster represents.

=== Entities ===
{entity_info}

=== Relationships ===
{rel_info}

Respond with ONLY a JSON object. No thinking, no analysis, no explanation. Just the JSON.
Format: {{"name": "Short Community Name", "summary": "2-4 sentence summary."}}"""
        
        try:
            response = self._sync_safe_completion(
                client,
                model=model,
                mode=self._extraction_reasoning_mode,
                messages=[
                    {"role": "system", "content": "You are a knowledge graph builder. Your task is to create a concise name and summary for a community of related entities and relationships.\n\nOutput ONLY a valid JSON object with exactly two keys: \"name\" and \"summary\". No other text, no explanations, no markdown, no chain-of-thought."},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "{"}
                ],
                temperature=0.3,
                max_tokens=300,
            )

            raw_content = self._extract_response_content(response)
            if not raw_content:
                return {"name": f"Community ({len(entities)} entities)", "summary": ""}
            # Prepend the '{' from assistant prefill since model continues from there
            content = "{" + raw_content.strip()
            # Fix double-brace when model echoes the prefill
            if content.startswith("{{"):
                content = content[1:]

            # Parse JSON response with multiple strategies
            import re

            def _try_parse_community_json(text: str) -> dict | None:
                """Try to parse community summary JSON from text."""
                try:
                    result = json.loads(text)
                    if isinstance(result, dict) and "name" in result:
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass
                return None

            # Strategy 1: Direct JSON parse
            parsed = _try_parse_community_json(content)
            if parsed:
                logger.info(f"Generated community summary: {parsed.get('name', 'Unknown')}")
                return parsed

            # Strategy 2: Strip to first { (handles chain-of-thought before JSON)
            brace_idx = content.find("{")
            if brace_idx > 0:
                parsed = _try_parse_community_json(content[brace_idx:])
                if parsed:
                    logger.info(f"Generated community summary (stripped): {parsed.get('name', 'Unknown')}")
                    return parsed

            # Strategy 3: Extract JSON block from markdown code fence
            json_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_block_match:
                parsed = _try_parse_community_json(json_block_match.group(1))
                if parsed:
                    logger.info(f"Generated community summary (code fence): {parsed.get('name', 'Unknown')}")
                    return parsed

            # Strategy 4: Find any JSON object in the content
            json_match = re.search(r'\{[^{}]*"name"\s*:\s*"[^"]*"[^{}]*\}', content, re.DOTALL)
            if json_match:
                parsed = _try_parse_community_json(json_match.group())
                if parsed:
                    logger.info(f"Generated community summary (regex obj): {parsed.get('name', 'Unknown')}")
                    return parsed

            # Strategy 5: Extract name and summary with regex patterns
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', content)
            summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', content)

            if name_match:
                logger.info(f"Generated community summary (regex fields): {name_match.group(1)}")
                return {
                    "name": name_match.group(1),
                    "summary": summary_match.group(1) if summary_match else content[:500]
                }

            # Fallback: generate name from top entity types, use raw text as summary
            logger.warning("Could not parse community summary as JSON, using fallback")
            type_counts: dict[str, int] = {}
            for e in entities[:30]:
                t = e.get("type", "")
                if t:
                    type_counts[t] = type_counts.get(t, 0) + 1
            top_types = sorted(type_counts, key=type_counts.get, reverse=True)[:3]
            # Use top entity names for a descriptive fallback name
            top_names = [e.get("name", "") for e in entities[:3] if e.get("name")]
            fallback_name = ", ".join(top_names) if top_names else " & ".join(top_types) if top_types else f"Community ({len(entities)} entities)"
            # Clean up chain-of-thought from summary text
            summary_text = content[:500] if content else None
            if summary_text and summary_text.startswith("{"):
                summary_text = summary_text  # Already handled by parser
            elif summary_text:
                # Strip "Let me analyze..." preamble if present
                for marker in ["Looking at", "The entities", "This cluster", "These entities", "Key entities", "1."]:
                    idx = summary_text.find(marker)
                    if idx > 0:
                        summary_text = summary_text[idx:]
                        break
            return {
                "name": fallback_name,
                "summary": summary_text
            }

        except Exception as e:
            logger.error(f"Error generating community summary: {e}")
            return {"name": None, "summary": None}

    async def generate_community_summary_async(
        self,
        entities: List[dict],
        relationships: List[dict]
    ) -> dict:
        """Async version of generate_community_summary using extraction model."""
        # Use extraction model for community summarization
        client = self.async_extraction_client or self.async_client
        model = self.extraction_model_name if self.async_extraction_client else self.current_model
        if not client or not entities:
            return {"name": None, "summary": None}
        
        # Format entity information
        entity_info = "\n".join([
            f"- {e.get('name', 'Unknown')} ({e.get('type', 'Unknown')}): {e.get('description', '')[:200]}"
            for e in entities[:30]
        ])

        # Format relationship information
        rel_info = "\n".join([
            f"- {r.get('source', '')} --[{r.get('type', '')}]--> {r.get('target', '')}"
            for r in relationships[:40]
        ]) if relationships else "No explicit relationships."
        
        prompt = f"""Analyze the following group of entities and relationships. Create a short, descriptive community name and a clear summary of what this cluster represents.

=== Entities ===
{entity_info}

=== Relationships ===
{rel_info}

Respond with ONLY a JSON object. No thinking, no analysis, no explanation. Just the JSON.
Format: {{"name": "Short Community Name", "summary": "2-4 sentence summary."}}"""
        
        try:
            response = await self._async_safe_completion(
                client,
                model=model,
                mode=self._extraction_reasoning_mode,
                messages=[
                    {"role": "system", "content": "You are a knowledge graph builder. Your task is to create a concise name and summary for a community of related entities and relationships.\n\nOutput ONLY a valid JSON object with exactly two keys: \"name\" and \"summary\". No other text, no explanations, no markdown, no chain-of-thought."},
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": "{"}
                ],
                temperature=0.3,
                max_tokens=300,
            )

            raw_content = self._extract_response_content(response)
            if not raw_content:
                return {"name": f"Community ({len(entities)} entities)", "summary": ""}
            # Prepend the '{' from assistant prefill since model continues from there
            content = "{" + raw_content.strip()
            # Fix double-brace when model echoes the prefill
            if content.startswith("{{"):
                content = content[1:]

            # Parse JSON response with multiple strategies
            import re

            def _try_parse_community_json(text: str) -> dict | None:
                """Try to parse community summary JSON from text."""
                try:
                    result = json.loads(text)
                    if isinstance(result, dict) and "name" in result:
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass
                return None

            # Strategy 1: Direct JSON parse
            parsed = _try_parse_community_json(content)
            if parsed:
                logger.info(f"Generated community summary: {parsed.get('name', 'Unknown')}")
                return parsed

            # Strategy 2: Strip to first { (handles chain-of-thought before JSON)
            brace_idx = content.find("{")
            if brace_idx > 0:
                parsed = _try_parse_community_json(content[brace_idx:])
                if parsed:
                    logger.info(f"Generated community summary (stripped): {parsed.get('name', 'Unknown')}")
                    return parsed

            # Strategy 3: Extract JSON block from markdown code fence
            json_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
            if json_block_match:
                parsed = _try_parse_community_json(json_block_match.group(1))
                if parsed:
                    logger.info(f"Generated community summary (code fence): {parsed.get('name', 'Unknown')}")
                    return parsed

            # Strategy 4: Find any JSON object in the content
            json_match = re.search(r'\{[^{}]*"name"\s*:\s*"[^"]*"[^{}]*\}', content, re.DOTALL)
            if json_match:
                parsed = _try_parse_community_json(json_match.group())
                if parsed:
                    logger.info(f"Generated community summary (regex obj): {parsed.get('name', 'Unknown')}")
                    return parsed

            # Strategy 5: Extract name and summary with regex patterns
            name_match = re.search(r'"name"\s*:\s*"([^"]+)"', content)
            summary_match = re.search(r'"summary"\s*:\s*"([^"]+)"', content)

            if name_match:
                logger.info(f"Generated community summary (regex fields): {name_match.group(1)}")
                return {
                    "name": name_match.group(1),
                    "summary": summary_match.group(1) if summary_match else content[:500]
                }

            # Fallback: generate name from top entity types, use raw text as summary
            logger.warning("Could not parse community summary as JSON, using fallback")
            type_counts: dict[str, int] = {}
            for e in entities[:30]:
                t = e.get("type", "")
                if t:
                    type_counts[t] = type_counts.get(t, 0) + 1
            top_types = sorted(type_counts, key=type_counts.get, reverse=True)[:3]
            # Use top entity names for a descriptive fallback name
            top_names = [e.get("name", "") for e in entities[:3] if e.get("name")]
            fallback_name = ", ".join(top_names) if top_names else " & ".join(top_types) if top_types else f"Community ({len(entities)} entities)"
            # Clean up chain-of-thought from summary text
            summary_text = content[:500] if content else None
            if summary_text and summary_text.startswith("{"):
                summary_text = summary_text  # Already handled by parser
            elif summary_text:
                # Strip "Let me analyze..." preamble if present
                for marker in ["Looking at", "The entities", "This cluster", "These entities", "Key entities", "1."]:
                    idx = summary_text.find(marker)
                    if idx > 0:
                        summary_text = summary_text[idx:]
                        break
            return {
                "name": fallback_name,
                "summary": summary_text
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
            response = self._sync_safe_completion(
                self.client,
                model=self.current_model,
                mode=self._extraction_reasoning_mode,
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
                response = await self._async_safe_completion(
                    client,
                    model=model,
                    mode=self._extraction_reasoning_mode,
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

    def _format_entity_for_prompt(self, entity: dict, max_desc_len: int = 500) -> str:
        """Format a single entity for inclusion in a prompt."""
        name = entity.get("name", "Unknown")
        etype = entity.get("type", "Unknown")
        desc = (entity.get("description") or "")[:max_desc_len]
        return f"- {name} ({etype}): {desc}"

    async def scan_candidate_pairs_async(
        self,
        entities: List[dict],
        context: str = "",
        existing_relationships: List[dict] = None,
        max_output_tokens: int = 4000,
    ) -> List[tuple]:
        """Phase 1: Fast candidate pair scanning using the extraction model.

        Scans all entities and identifies pairs that likely have a relationship.
        Outputs only (source, target) tuples — no types, descriptions, or weights.

        Args:
            entities: List of {name, type, description} dicts
            context: Source text context for the batch
            existing_relationships: Already-known relationships to exclude
            max_output_tokens: Max output tokens for the response

        Returns:
            List of (source_name, target_name) candidate pairs
        """
        # Use relationship model (separate rate limit from entity extraction)
        if self.async_relationship_client:
            client = self.async_relationship_client
            model = self.relationship_model_name
        elif self.async_extraction_client:
            client = self.async_extraction_client
            model = self.extraction_model_name
        elif self.async_client:
            client = self.async_client
            model = self.current_model
        else:
            logger.warning("No async client available for candidate scanning")
            return []

        entity_list = "\n".join([
            self._format_entity_for_prompt(e) for e in entities
        ])

        # Build context section
        context_section = ""
        if context:
            context_section = f"=== Source Text Context ===\n{context}\n"

        # Build existing relationships section
        existing_section = ""
        if existing_relationships:
            existing_text = "\n".join([
                f"- {r.get('source', '')} | {r.get('target', '')}"
                for r in existing_relationships[:400]
            ])
            existing_section = (
                f"The following pairs are ALREADY connected — "
                f"find NEW pairs not listed here:\n{existing_text}"
            )

        user_prompt = CANDIDATE_SCAN_USER_PROMPT.format(
            context_section=context_section,
            entity_list=entity_list,
            existing_section=existing_section,
        )

        entity_name_set = {e.get("name", "").lower(): e.get("name", "") for e in entities}

        try:
            response = await self._async_safe_completion(
                client,
                model=model,
                mode=self._relationship_reasoning_mode,
                messages=[
                    {"role": "system", "content": CANDIDATE_SCAN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=max_output_tokens,
            )

            content = self._extract_response_content(response)
            if not content:
                logger.warning("Candidate scan: LLM returned empty content")
                return []

            # Parse "EntityA | EntityB" lines
            pairs = []
            seen_pairs = set()
            for line in content.strip().split("\n"):
                line = line.strip()
                if not line or "|" not in line:
                    continue
                # Remove leading numbering like "1. " or "- "
                line = re.sub(r"^[\d]+[\.\)]\s*", "", line)
                line = re.sub(r"^[-*]\s*", "", line)

                parts = line.split("|", 1)
                if len(parts) != 2:
                    continue

                source = parts[0].strip()
                target = parts[1].strip()

                # Validate both entities exist (case-insensitive)
                source_canonical = entity_name_set.get(source.lower())
                target_canonical = entity_name_set.get(target.lower())

                if source_canonical and target_canonical and source_canonical != target_canonical:
                    pair_key = tuple(sorted([source.lower(), target.lower()]))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        pairs.append((source_canonical, target_canonical))

            logger.info(
                f"Candidate scan: {len(pairs)} candidate pairs from "
                f"{len(entities)} entities (model={model})"
            )

            # Gleaning pass (Microsoft GraphRAG approach): if the initial scan
            # found very few pairs for a large batch, do a second pass with
            # the initial results as context to catch missed relationships.
            if len(pairs) < 5 and len(entities) >= 50:
                found_text = "\n".join([f"- {s} | {t}" for s, t in pairs]) if pairs else "(none)"
                gleaning_prompt = (
                    f"The following pairs were already identified:\n{found_text}\n\n"
                    f"Some relationships may have been missed. Please review the entities "
                    f"and context again carefully and identify any ADDITIONAL pairs not "
                    f"listed above.\n\n{context_section}\n\n=== Entities ===\n{entity_list}\n\n"
                    f"Output additional related pairs (one per line, format: EntityA | EntityB):"
                )

                try:
                    gleaning_response = await self._async_safe_completion(
                        client,
                        model=model,
                        mode=self._relationship_reasoning_mode,
                        messages=[
                            {"role": "system", "content": CANDIDATE_SCAN_SYSTEM_PROMPT},
                            {"role": "user", "content": gleaning_prompt},
                        ],
                        temperature=0.4,
                        max_tokens=max_output_tokens,
                    )
                    gleaning_content = self._extract_response_content(gleaning_response)
                    gleaning_new = 0
                    if gleaning_content:
                        for line in gleaning_content.strip().split("\n"):
                            line = line.strip()
                            if not line or "|" not in line:
                                continue
                            line = re.sub(r"^[\d]+[\.\)]\s*", "", line)
                            line = re.sub(r"^[-*]\s*", "", line)
                            parts = line.split("|", 1)
                            if len(parts) != 2:
                                continue
                            source = parts[0].strip()
                            target = parts[1].strip()
                            source_canonical = entity_name_set.get(source.lower())
                            target_canonical = entity_name_set.get(target.lower())
                            if source_canonical and target_canonical and source_canonical != target_canonical:
                                pair_key = tuple(sorted([source.lower(), target.lower()]))
                                if pair_key not in seen_pairs:
                                    seen_pairs.add(pair_key)
                                    pairs.append((source_canonical, target_canonical))
                                    gleaning_new += 1
                    if gleaning_new > 0:
                        logger.info(
                            f"Gleaning pass: +{gleaning_new} additional pairs "
                            f"(total now {len(pairs)})"
                        )
                except Exception as gleaning_err:
                    logger.warning(f"Gleaning pass failed: {gleaning_err}")

            return pairs

        except Exception as e:
            logger.error(f"Error during candidate pair scanning: {e}")
            return []

    async def extract_chunk_relationships_async(
        self,
        chunk_text: str,
        entities: List[dict],
        max_output_tokens: int = 2000,
    ) -> List[Relationship]:
        """Extract relationships from a single chunk using entities found in it.

        Based on LangChain LLMGraphTransformer approach: the source text IS the
        evidence, so relationships are higher quality than cross-document batches.

        Args:
            chunk_text: The raw text of the chunk.
            entities: Entity dicts ({name, type, description}) found in this chunk.
            max_output_tokens: Max output tokens for the LLM.

        Returns:
            List of discovered Relationship objects.
        """
        if len(entities) < 2 or not chunk_text.strip():
            return []

        # Use dedicated relationship model (falls back to extraction, then main)
        if self.async_relationship_client:
            client = self.async_relationship_client
            model = self.relationship_model_name
        elif self.async_extraction_client:
            client = self.async_extraction_client
            model = self.extraction_model_name
        elif self.async_client:
            client = self.async_client
            model = self.current_model
        else:
            return []

        r_types = self.relation_types
        entity_list = "\n".join([self._format_entity_for_prompt(e) for e in entities])
        entity_name_set = {e.get("name", "").lower() for e in entities}

        user_prompt = f"""Extract relationships between the following entities based on the source text.

Relation Types (use only these): {", ".join(r_types)}

=== Source Text ===
{chunk_text}

=== Entities in this text ===
{entity_list}

Extract relationships supported by the text above:"""

        try:
            # Retry with exponential backoff for rate limit (429) errors.
            # OpenAI SDK raises openai.RateLimitError for 429 responses.
            @retry(
                retry=retry_if_exception_type(Exception),
                stop=stop_after_attempt(4),
                wait=wait_exponential(multiplier=2, min=2, max=30),
                before_sleep=lambda rs: logger.debug(
                    f"Per-chunk extraction retry #{rs.attempt_number} after {rs.outcome.exception().__class__.__name__}"
                ),
                reraise=True,
            )
            async def _call_llm():
                return await self._async_safe_completion(
                    client,
                    model=model,
                    mode=self._relationship_reasoning_mode,
                    messages=[
                        {"role": "system", "content": RELATIONSHIP_ANALYSIS_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                    max_tokens=max_output_tokens,
                )

            response = await _call_llm()

            content = self._extract_response_content(response)
            if not content:
                return []

            xml_relationships = self._extract_xml_relationships(content)
            relationships = []
            for r in xml_relationships:
                source = r.get("source", "").strip()
                target = r.get("target", "").strip()
                if source.lower() in entity_name_set and target.lower() in entity_name_set and source.lower() != target.lower():
                    confidence = r.get("confidence", 1.0)
                    if confidence >= 0.5:
                        relationships.append(Relationship(
                            source=source,
                            target=target,
                            relationship_type=r.get("relationship_type", "RELATED_TO"),
                            description=r.get("description", ""),
                            weight=r.get("weight", 5.0),
                            confidence=confidence,
                        ))
            return relationships

        except Exception as e:
            logger.warning(f"Per-chunk relationship extraction failed after retries: {e}")
            return []

    async def analyze_relationships_async(
        self,
        entities: List[dict],
        context: str = "",
        existing_relationships: List[dict] = None,
        max_output_tokens: int = 8000,
        candidate_pairs: List[tuple] = None,
    ) -> List[Relationship]:
        """Phase 2: Structured relationship extraction using the extraction model.

        Uses the extraction (instruction-following) model for reliable XML output.
        When candidate_pairs is provided (two-phase mode), only analyzes
        the candidate entity pairs — producing a focused, smaller prompt.

        Args:
            entities: Full batch of {name, type, description} dicts
            context: Source text context for the batch
            existing_relationships: Already-known relationships to avoid
            max_output_tokens: Max output tokens for the LLM response
            candidate_pairs: If provided, only analyze these (source, target) pairs.
                             Entities not in any pair are excluded from the prompt.

        Returns:
            List of discovered Relationship objects
        """
        # Use relationship model (separate rate limit from entity extraction)
        # Falls back to extraction model, then main model
        if self.async_relationship_client:
            client = self.async_relationship_client
            model = self.relationship_model_name
        elif self.async_extraction_client:
            client = self.async_extraction_client
            model = self.extraction_model_name
        elif self.async_client:
            client = self.async_client
            model = self.current_model
        else:
            logger.warning("No async client available for relationship analysis")
            return []

        r_types = self.relation_types

        # When candidate pairs provided, only include entities that appear in pairs
        if candidate_pairs:
            candidate_names = set()
            for src, tgt in candidate_pairs:
                candidate_names.add(src.lower())
                candidate_names.add(tgt.lower())
            focused_entities = [
                e for e in entities
                if e.get("name", "").lower() in candidate_names
            ]
            # Also include a hint about which pairs to analyze
            pairs_hint = "\n".join([
                f"- {src} <-> {tgt}" for src, tgt in candidate_pairs
            ])
        else:
            focused_entities = entities
            pairs_hint = None

        # Format entity list with truncated descriptions
        entity_list = "\n".join([
            self._format_entity_for_prompt(e) for e in focused_entities
        ])

        # Build context section
        context_section = ""
        if context:
            context_section = f"=== Source Text Context ===\n{context}\n"
        if pairs_hint:
            context_section += (
                f"\n=== Candidate Pairs to Analyze ===\n"
                f"Focus on confirming and classifying these specific entity pairs:\n{pairs_hint}\n"
            )
        if existing_relationships:
            existing_text = "\n".join([
                f"- {r.get('source', '')} --[{r.get('type', '')}]--> {r.get('target', '')}"
                for r in existing_relationships[:400]
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

        entity_name_set = {e.get("name", "").lower() for e in focused_entities}

        try:
            response = await self._async_safe_completion(
                client,
                model=model,
                mode=self._relationship_reasoning_mode,
                messages=[
                    {"role": "system", "content": RELATIONSHIP_ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=max_output_tokens,
            )

            content = self._extract_response_content(response)
            if not content:
                logger.warning(
                    f"Relationship analysis: LLM returned empty content after extraction "
                    f"(finish_reason={response.choices[0].finish_reason})"
                )
                return []

            xml_relationships = self._extract_xml_relationships(content)

            # Log if parsing failed to extract any relationships
            if not xml_relationships and content:
                # Check if there are ANY relationship tags in the content
                has_rel_tags = "<relationship>" in content.lower()
                # Log beginning and end to help debug
                logger.warning(
                    f"Relationship analysis: no XML relationships parsed from response. "
                    f"Content length={len(content)}, has_relationship_tags={has_rel_tags}, "
                    f"start: {content[:500]!r}, end: {content[-500:]!r}"
                )

            relationships = []
            for r in xml_relationships:
                source = r.get("source", "").strip()
                target = r.get("target", "").strip()

                # Only keep relationships where both entities are in the provided list and not self-referential
                if source.lower() in entity_name_set and target.lower() in entity_name_set and source.lower() != target.lower():
                    relationships.append(Relationship(
                        source=source,
                        target=target,
                        relationship_type=r.get("relationship_type", "RELATED_TO"),
                        description=r.get("description", ""),
                        weight=r.get("weight", 5.0),
                        confidence=r.get("confidence", 1.0),
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
        get_batch_context: Optional[Callable[[List[dict], int], Awaitable[str]]] = None,
        progress_stats: Optional[dict] = None,
        parallel_batches: int = 1,
        entity_co_occurrence: Optional[dict] = None,
        extraction_max_context: int = 0,
    ) -> List[Relationship]:
        """Two-phase relationship analysis in batches.

        Phase 1 (Extraction Model): Scans entities to find candidate pairs.
        Phase 2 (Main Model): Deep analysis of candidate pairs to confirm
        relationships and assign types, descriptions, weights.

        Batching uses the extraction model's context window (larger) for Phase 1,
        and the main model's context window for Phase 2.

        Args:
            all_entities: List of {name, type, description} dicts
            context: Optional collection/document summary context
            max_context_tokens: Max INPUT context tokens for Phase 2 (main model)
            max_output_tokens: Max OUTPUT tokens for Phase 2 LLM responses
            existing_relationships: Already-known relationships
            on_batch_complete: Callback(batch_relationships) after each batch
            get_batch_context: Async callback(entity_batch, token_budget) -> str
            progress_stats: Dict for progress tracking
            parallel_batches: Number of batches to process in parallel
            entity_co_occurrence: Entity name -> chunk IDs for co-occurrence batching
            extraction_max_context: Max context tokens for Phase 1 (extraction model).
                                    If 0, uses max_context_tokens as fallback.

        Returns:
            Deduplicated list of all discovered Relationship objects
        """
        if not all_entities:
            return []

        # Phase 1 uses extraction model context (typically larger)
        # Phase 2 uses main model context
        phase1_context_budget = extraction_max_context if extraction_max_context > 0 else max_context_tokens

        # Token estimation (use fallback estimator — works across models)
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(self.current_model)
            def count_tokens(text: str) -> int:
                return len(enc.encode(text))
        except Exception:
            def count_tokens(text: str) -> int:
                return len(text) // 4

        r_types = self.relation_types

        # Calculate prompt overhead for Phase 1 (candidate scan)
        p1_system_tokens = count_tokens(CANDIDATE_SCAN_SYSTEM_PROMPT)
        p1_template_tokens = count_tokens(CANDIDATE_SCAN_USER_PROMPT.format(
            context_section="", entity_list="", existing_section="",
        ))
        existing_context_tokens = 0
        if existing_relationships:
            existing_text = "\n".join([
                f"- {r.get('source', '')} | {r.get('target', '')}"
                for r in existing_relationships[:400]
            ])
            existing_context_tokens = count_tokens(existing_text) + 100

        p1_overhead = p1_system_tokens + p1_template_tokens + existing_context_tokens
        p1_output_reserve = 4000  # Candidate pairs are compact
        p1_available = int(phase1_context_budget * 0.8) - p1_overhead - p1_output_reserve

        if p1_available < 500:
            p1_available = 2000
            logger.warning(f"Very limited Phase 1 context budget ({p1_available} tokens)")

        # Phase 1: 60% entities, 40% context
        entity_token_budget = int(p1_available * 0.6)
        context_token_budget = int(p1_available * 0.4)

        logger.info(
            f"Two-phase token budget — Phase 1 (extraction): total={p1_available}, "
            f"entities={entity_token_budget}, context={context_token_budget} | "
            f"Phase 2 (main): max_context={max_context_tokens}, max_output={max_output_tokens}"
        )

        # --- Co-occurrence based entity ordering ---
        co_connection_count: dict[str, int] = {}
        if entity_co_occurrence:
            sorted_entities, co_connection_count = self._sort_entities_by_cooccurrence(
                all_entities, entity_co_occurrence
            )
        else:
            by_type: dict[str, List[dict]] = {}
            for e in all_entities:
                t = e.get("type", "Other")
                by_type.setdefault(t, []).append(e)
            sorted_entities = []
            iterators = [iter(v) for v in by_type.values()]
            while iterators:
                next_round = []
                for it in iterators:
                    val = next(it, None)
                    if val is not None:
                        sorted_entities.append(val)
                        next_round.append(it)
                iterators = next_round

        # Pre-compute token count for each entity
        entity_tokens = []
        for e in sorted_entities:
            formatted = self._format_entity_for_prompt(e)
            entity_tokens.append((e, count_tokens(formatted)))

        # Batch entities by Phase 1 entity token budget AND hard entity cap.
        # Overlap reduced from 15% to 5% and excludes entities already in 2+ batches.
        MAX_ENTITIES_PER_BATCH = 120
        batches: List[List[dict]] = []
        current_batch: List[dict] = []
        current_tokens = 0
        entity_batch_count: dict[str, int] = {}

        for entity, tokens in entity_tokens:
            if current_batch and ((current_tokens + tokens) > entity_token_budget or len(current_batch) >= MAX_ENTITIES_PER_BATCH):
                batches.append(current_batch)
                # Reduced overlap: 5% instead of 15%, prefer low-connection entities
                overlap_target = max(2, len(current_batch) * 5 // 100)
                # Wider candidate pool from tail, filtered by batch appearance
                candidate_pool = current_batch[-(overlap_target * 3):]
                overlap_candidates = [
                    e for e in candidate_pool
                    if entity_batch_count.get(e.get("name", ""), 0) < 2
                ]
                # Sort by co-occurrence connection count ASC (prefer low-degree for overlap)
                overlap_candidates.sort(
                    key=lambda e: co_connection_count.get(e.get("name", ""), 0)
                )
                overlap_entities = overlap_candidates[:overlap_target]
                if not overlap_entities:
                    # Fallback: just take the last 2 entities
                    overlap_entities = current_batch[-2:]
                overlap_tokens = sum(
                    count_tokens(self._format_entity_for_prompt(e))
                    for e in overlap_entities
                )
                current_batch = overlap_entities.copy()
                current_tokens = overlap_tokens

            current_batch.append(entity)
            current_tokens += tokens
            entity_batch_count[entity.get("name", "")] = (
                entity_batch_count.get(entity.get("name", ""), 0) + 1
            )

        if current_batch:
            batches.append(current_batch)

        logger.info(
            f"Relationship analysis: {len(all_entities)} entities split into "
            f"{len(batches)} batch(es) (Phase 1 budget={entity_token_budget} entity tokens)"
        )

        if progress_stats is not None:
            progress_stats["total_batches"] = len(batches)

        # Process batches
        all_relationships: List[Relationship] = []
        seen_keys: set[tuple] = set()
        parallel_batches = max(1, parallel_batches)

        async def process_single_batch(batch_idx: int, batch: List[dict]) -> List[Relationship]:
            """Two-phase processing of a single batch."""
            # Context budget for Phase 1
            remaining_for_context = min(context_token_budget, max(1000, p1_available // 3))

            # Fetch per-batch source text context
            if get_batch_context:
                try:
                    batch_context = await get_batch_context(batch, remaining_for_context)
                except Exception as e:
                    logger.warning(f"Failed to fetch batch context: {e}")
                    batch_context = context
            else:
                batch_context = context

            # Filter existing relationships to this batch
            batch_existing = existing_relationships
            if existing_relationships:
                batch_names = {e.get("name", "").lower() for e in batch}
                batch_existing = [
                    r for r in existing_relationships
                    if r.get("source", "").lower() in batch_names
                    or r.get("target", "").lower() in batch_names
                ][:400]

            # --- Phase 1: Candidate scan (extraction model) ---
            candidates = await self.scan_candidate_pairs_async(
                batch, batch_context, batch_existing, max_output_tokens=4000,
            )

            if not candidates:
                logger.info(
                    f"Batch {batch_idx + 1}/{len(batches)}: Phase 1 found 0 candidates "
                    f"({len(batch)} entities) — skipping Phase 2"
                )
                return []

            # --- Phase 2: Deep analysis (main model) ---
            rels = await self.analyze_relationships_async(
                batch, batch_context, batch_existing, max_output_tokens,
                candidate_pairs=candidates,
            )

            logger.info(
                f"Batch {batch_idx + 1}/{len(batches)}: "
                f"Phase 1: {len(candidates)} candidates → "
                f"Phase 2: {len(rels)} relationships "
                f"({len(batch)} entities)"
            )
            return rels

        if parallel_batches <= 1:
            for batch_idx, batch in enumerate(batches):
                rels = await process_single_batch(batch_idx, batch)

                batch_unique: List[Relationship] = []
                for rel in rels:
                    key = (rel.source.lower(), rel.target.lower(), rel.relationship_type)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        batch_unique.append(rel)
                        all_relationships.append(rel)

                if on_batch_complete:
                    try:
                        await on_batch_complete(batch_unique)
                    except Exception as e:
                        logger.error(f"Error in on_batch_complete callback: {e}")

                del batch_unique
                del rels
        else:
            import asyncio as _asyncio
            semaphore = _asyncio.Semaphore(parallel_batches)
            dedup_lock = _asyncio.Lock()
            logger.info(f"Processing {len(batches)} batches with parallelism={parallel_batches}")

            async def sem_process(batch_idx: int, batch: List[dict]) -> None:
                async with semaphore:
                    rels = await process_single_batch(batch_idx, batch)

                async with dedup_lock:
                    batch_unique: List[Relationship] = []
                    for rel in rels:
                        key = (rel.source.lower(), rel.target.lower(), rel.relationship_type)
                        if key not in seen_keys:
                            seen_keys.add(key)
                            batch_unique.append(rel)
                            all_relationships.append(rel)

                    if on_batch_complete:
                        try:
                            await on_batch_complete(batch_unique)
                        except Exception as e:
                            logger.error(f"Error in on_batch_complete callback: {e}")

            tasks = [sem_process(i, b) for i, b in enumerate(batches)]
            await _asyncio.gather(*tasks)

        logger.info(
            f"Relationship analysis complete: {len(all_relationships)} unique relationships"
        )
        return all_relationships

    def _sort_entities_by_cooccurrence(
        self,
        entities: List[dict],
        co_occurrence: dict,
    ) -> tuple:
        """Sort entities so those sharing chunks are adjacent.

        Uses Union-Find to cluster entities that share chunks, then outputs
        clusters largest-first with entities interleaved by connection count
        (high/low alternating) to prevent hub entities from concentrating at
        the front of batches.

        Returns:
            (sorted_entities, connection_count_dict)
        """
        # Union-Find with path compression
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Build chunk→entities map and union entities sharing chunks
        chunk_to_entities: dict[str, list] = {}
        for name, chunks in co_occurrence.items():
            for cid in chunks:
                chunk_to_entities.setdefault(cid, []).append(name)

        for entities_in_chunk in chunk_to_entities.values():
            first = entities_in_chunk[0]
            for other in entities_in_chunk[1:]:
                union(first, other)

        # Group entities into clusters by root
        entity_map = {e.get("name", ""): e for e in entities}
        clusters: dict[str, list] = {}
        for name in entity_map:
            root = find(name)
            clusters.setdefault(root, []).append(name)

        # Pre-compute connection count per entity (how many co-occurring entities)
        connection_count: dict[str, int] = {}
        for name, chunks in co_occurrence.items():
            total = sum(len(chunk_to_entities.get(cid, [])) - 1 for cid in chunks)
            connection_count[name] = total

        # Output: largest clusters first, within cluster by connection count DESC.
        # Keeps co-occurring entities adjacent so they land in the same batch
        # and the LLM sees shared chunk context. Hub accumulation is handled
        # structurally by the per-entity relationship cap.
        ordered: List[dict] = []
        for cluster in sorted(clusters.values(), key=len, reverse=True):
            cluster.sort(key=lambda n: connection_count.get(n, 0), reverse=True)
            for name in cluster:
                if name in entity_map:
                    ordered.append(entity_map[name])

        return ordered, connection_count


# Singleton instance
_graph_extractor: Optional[GraphExtractor] = None


def get_graph_extractor() -> GraphExtractor:
    """Get the singleton GraphExtractor instance."""
    global _graph_extractor
    if _graph_extractor is None:
        _graph_extractor = GraphExtractor()
    return _graph_extractor
